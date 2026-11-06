from hguc_watch import pdd
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest


ITEM = {'name':'主天使', 'search_query':'MG主天使 高达', 'aliases':['主天使']}
CARD = {'title':'万代 MG 主天使 拼装模型', 'price_text':'¥\n275', 'image':''}
DETAIL = {
    'goods_id':'43',
    'title':'【万代】 1/100 MG 主天使 高达 GN-003 kyiros 00 拼装模型',
    'description':'品牌BANDAI/万代 模型系列MG 是否需拼装拼装',
    'price_text':'¥\n275', 'shipping':'全场包邮', 'sku_price':'¥275',
    'selected':'已选：盒损发货 不包运损',
    'groups':[{'name':'款式','options':['盒损发货 不包运损']}],
}


def test_real_single_sku_retail_listing_has_verified_price_and_source():
    row = pdd.build_listing(CARD, DETAIL, ITEM)
    assert row['eligible']
    assert row['total_price'] == 275
    assert row['platform'] == 'pdd'
    assert row['item_key'] == 'pdd:43'
    assert row['detail_url'] == 'https://mobile.yangkeduo.com/goods.html?goods_id=43'
    assert row['price_note'] == '拼单价'


def test_multiple_skus_and_coupon_conditions_are_pending():
    detail = {**DETAIL, 'groups':[{'name':'型号','options':['主天使','力天使']}]}
    row = pdd.build_listing(CARD, detail, ITEM)
    assert not row['eligible']
    assert row['condition_status'] == '规格价格待确认'
    row = pdd.build_listing({**CARD, 'price_text':'券后\n¥269'}, DETAIL, ITEM)
    assert not row['eligible']
    assert '券后' in row['reject_reason']


def test_mismatching_sku_price_is_never_confirmed():
    assert not pdd.build_listing(CARD, {**DETAIL, 'sku_price':'¥280'}, ITEM)['eligible']
    assert not pdd.build_listing(CARD, {**DETAIL, 'sku_price':'¥275-280'}, ITEM)['eligible']


def test_returns_insurance_is_not_outbound_free_shipping():
    row = pdd.build_listing(CARD, {**DETAIL, 'shipping':'退货包运费'}, ITEM)
    assert row['shipping_price'] is None
    assert row['total_price'] is None


def test_old_hg_is_excluded_from_pdd_too():
    item = {'name':'斑豹', 'search_query':'HG斑豹', 'aliases':['斑豹']}
    detail = {**DETAIL,'title':'万代旧HG 斑豹 拼装模型','description':'全新未拼'}
    row = pdd.build_listing(CARD,detail,item)
    assert not row['eligible']
    assert row['condition_status'] == '旧HG已屏蔽'


def test_shop_sales_statistics_are_not_used_as_assembly_condition():
    card = {**CARD,'text':'本店已拼3751件'}
    assert pdd.build_listing(card,DETAIL,ITEM)['eligible']


async def test_system_busy_stops_without_more_requests():
    body = SimpleNamespace(inner_text=AsyncMock(return_value='系统繁忙 请稍后再试'))
    page = SimpleNamespace(url='https://mobile.yangkeduo.com/search_result.html',
                           locator=Mock(return_value=body),wait_for_timeout=AsyncMock())
    with pytest.raises(pdd.PlatformBusyError):
        await pdd._wait_page(page,search=True)
    page.wait_for_timeout.assert_not_awaited()


async def test_blocked_cheapest_pdd_goods_do_not_trigger_three_verified_stop(monkeypatch):
    cards = [{**CARD, 'index': i, 'price_text': f'¥{100+i}'} for i in range(7)]
    page = SimpleNamespace(goto=AsyncMock(), bring_to_front=AsyncMock(), close=AsyncMock(),
                           mouse=SimpleNamespace(wheel=AsyncMock()), wait_for_timeout=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    monkeypatch.setattr(pdd, '_wait_page', AsyncMock())
    monkeypatch.setattr(pdd, '_cards', AsyncMock(return_value=cards))

    async def detail(_page, card):
        price = 100 + card['index']
        return {**DETAIL, 'goods_id': str(card['index']+1), 'price_text': f'¥{price}', 'sku_price': f'¥{price}'}

    details = AsyncMock(side_effect=detail)
    monkeypatch.setattr(pdd, '_detail', details)
    monkeypatch.setattr(pdd.asyncio, 'sleep', AsyncMock())
    rows = await pdd._scrape_query(context, ITEM, '万代 MG 主天使', max_results=10, detail_checks=7,
                                   blocked_keys={'pdd:1', 'pdd:2', 'pdd:3'})
    assert [row['item_key'] for row in rows] == ['pdd:4', 'pdd:5', 'pdd:6']
    assert details.await_count == 6
