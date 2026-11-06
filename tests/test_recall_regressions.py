import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from hguc_watch.filtering import classify_listing, parse_shipping, watch_requirements
from hguc_watch import xianyu


CASES = json.loads((Path(__file__).parent / 'fixtures' / 'recall_20261012.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('listing_id,aliases', [(980, ['帕拉斯·雅典娜']), (1160, ['赛克艾恩'])])
def test_real_single_kit_survives_repeated_title_and_platform_series(listing_id, aliases):
    row = CASES[str(listing_id)]
    assert classify_listing(row['source_text'], aliases, title=row['title']).eligible


def test_real_mg_kyrios_is_compared_as_mg():
    row = CASES['1064']
    assert classify_listing(row['source_text'], ['主天使'], title=row['title'], expected_grade='MG').eligible
    assert not classify_listing(row['source_text'], ['主天使'], title=row['title']).eligible


@pytest.mark.parametrize('listing_id', [1046, 1054, 1058])
def test_real_hg_fg_and_counterfeit_do_not_fill_mg_ranking(listing_id):
    row = CASES[str(listing_id)]
    assert not classify_listing(row['source_text'], ['主天使'], title=row['title'], expected_grade='MG').eligible


@pytest.mark.parametrize('grade,title', [
    ('MG', '万代MG1/100 迅雷高达 全新未拆'),
    ('RE', '万代 RE/100 德南格 全新未拆'),
])
def test_requested_larger_grade_is_accepted(grade, title):
    assert classify_listing(title, [], expected_grade=grade).eligible


def test_empty_aliases_use_watch_name_and_query_declares_grade():
    assert watch_requirements({'name': '德南格', 'aliases': [], 'search_query': 're德南格'}) == (['德南格'], 'RE')
    assert not classify_listing('万代 HG 扎古 全新未拼', ['德南格'], expected_grade='RE').eligible


def test_missing_mg_evidence_is_pending_instead_of_wrongly_accepted():
    assert classify_listing('万代主天使 全新未拼', ['主天使'], expected_grade='MG').condition_status == '系列待确认'


def test_repeated_selling_terms_are_not_evidence_of_placeholder_price():
    row = CASES['1179']
    assert classify_listing(row['source_text'], ['赛克艾恩'], title=row['title']).eligible


def test_return_shipping_policy_is_not_free_outbound_shipping():
    assert parse_shipping('描述不符包邮退 满足条件时买家可退货') == (None, 'unknown')


def test_counterfeit_is_not_made_bandai_by_negated_brand_or_platform_chip():
    assert not classify_listing('国产拼装模型 全新未拼 非万代正版 主天使', ['主天使']).eligible
    assert not classify_listing('大班 MG 迅雷 全新未拼 Bandai/万代', ['迅雷'], expected_grade='MG').eligible


def test_synonyms_are_searched_separately_with_small_request_budget():
    queries = xianyu.search_queries({'name': '卡普斯利', 'model_code': 'HGUC 058', 'search_query': 'HGUC 卡普斯利 加布斯雷 万代', 'aliases': ['卡普斯利', '加布斯雷']})
    assert queries == ['HGUC 卡普斯利 加布斯雷 万代', 'HGUC 卡普斯利', 'HGUC 加布斯雷']


def test_plain_watch_name_accepts_omitted_generic_gundam_suffix():
    aliases, grade = watch_requirements({'name': '斑豹高达', 'search_query': 'hg斑豹高达', 'aliases': []})
    assert classify_listing('万代hg斑豹全新未拆', aliases, expected_grade=grade).eligible


def test_explicitly_sold_item_cannot_be_new_floor():
    assert not classify_listing('已出 万代RE1/100德南格 全新未拼', ['德南格'], expected_grade='RE').eligible


def test_zz_requirement_does_not_mix_unicorn_or_unconfirmed_edition():
    assert classify_listing('万代 HGUC 德莱森 ZZ版 全新未拆', ['德莱森'], expected_edition='ZZ').eligible
    assert classify_listing('万代 HGUC 德莱森 独角兽版 全新未拆', ['德莱森'], expected_edition='ZZ').condition_status == '版本不符'
    assert classify_listing('万代 HGUC 德莱森 全新未拆', ['德莱森'], expected_edition='ZZ').condition_status == '版本待确认'


@pytest.mark.parametrize('word', ['旧hg', '旧 HG', '老HG', '旧版hg', '老款 HG', 'HG旧版', '旧HGUC', '老版HGAW'])
def test_user_excludes_old_hg_even_if_new_unbuilt(word):
    result = classify_listing(f'万代 {word} 斑豹高达 全新未拆 包邮', ['斑豹'], expected_grade='HG')
    assert not result.eligible
    assert result.condition_status == '旧HG已屏蔽'


def test_old_box_and_explicit_not_old_hg_are_not_excluded():
    assert classify_listing('万代HG斑豹 全新未拼 旧盒', ['斑豹']).eligible
    assert classify_listing('万代HG斑豹 全新未拼 不是旧HG', ['斑豹']).eligible


def test_real_56_yuan_leopard_destroy_is_not_the_watched_base_leopard():
    title = '万代 HG 1/144 斑豹毁灭者 机动新世纪高达X 拼装模型，全新未拼装'
    aliases = ['斑豹高达', '斑豹']
    rejected = classify_listing(title, aliases, expected_grade='HG')
    assert not rejected.eligible
    assert rejected.condition_status == '型号不符'
    assert classify_listing('万代hg 高达x 斑豹高达 全新未拆封，包邮', aliases, expected_grade='HG').eligible
    assert classify_listing(title, ['斑豹毁灭者', '斑豹'], expected_grade='HG').eligible
    assert classify_listing('万代HG 斑豹 全新未拼 不是斑豹毁灭者', aliases, expected_grade='HG').eligible


def test_real_alloy_frame_and_gk_style_are_not_plain_bandai_kyrios():
    frame = '铁创1/100 mg主天使合金骨架，全新未拼，非偏远包邮'
    gk = '［个人堆积］超兵计划 哈雷路亚，GK样式MG主天使，已点件，未拼装，顺丰包邮'
    assert not classify_listing(frame,['主天使'],expected_grade='MG').eligible
    assert not classify_listing(gk,['主天使'],expected_grade='MG').eligible
    assert classify_listing('万代MG主天使 全新未拼 送合金骨架',['主天使'],expected_grade='MG').eligible


def test_explicit_shipping_cost_after_not_free_is_known():
    assert parse_shipping('万代MG主天使 不包邮 运费10元') == (10, 'platform')
    assert parse_shipping('运费10元起') == (None, 'unknown')


def test_after_sales_policy_does_not_claim_current_missing_parts():
    title = '【万代】 HGUC HG 南瓜饼1/144 NRX-044 Asshimar 阿斯玛 拼装'
    policy = '收到货不要拆开板件塑料袋检查板件,发现断件或缺件联系我有售后。拆开板件塑料袋没有售后'
    assert classify_listing(title + policy, ['NRX-044'], title=title, retail=True).eligible
    assert not classify_listing(title + '全新未拼 现在缺件', ['NRX-044'], title=title, retail=True).eligible


def test_re_denan_gei_base_pb_channel_is_not_a_colour_variant():
    assert classify_listing('万代 PB限定 RE/100 德南格 全新未拼',['德南格'],expected_grade='RE').eligible
    assert not classify_listing('万代 PB限定 RE/100 德南格 彩透 全新未拼',['德南格'],expected_grade='RE').eligible


async def test_spare_parts_do_not_consume_detail_budget_and_alternate_cheapest_survives(monkeypatch):
    def card(key, title, price):
        return {'href': f'https://www.goofish.com/item?id={key}', 'title': title, 'text': title, 'price': price}
    spare = card(1, '万代 MG 迅雷补件 全新', 5)
    kit = card(2, '万代 MG 迅雷 全新未拼', 180)
    cheaper = card(3, '万代 MG 迅雷 全新未拼', 160)
    search = AsyncMock(side_effect=[[spare, kit], [kit, cheaper]])
    details = AsyncMock(return_value=('运费10元', cheaper['href'], ''))
    monkeypatch.setattr(xianyu, '_search_cards', search)
    monkeypatch.setattr(xianyu, '_load_detail', details)
    monkeypatch.setattr(xianyu.asyncio, 'sleep', AsyncMock())
    result = await xianyu.scrape_watch_item(None, {'name': '迅雷', 'search_query': 'mg 迅雷高达', 'aliases': ['迅雷']}, max_results=2, detail_checks=1)
    assert len(result) == 2
    assert {r['item_key'] for r in result} == {'2', '3'}
    assert next(r for r in result if r['item_key'] == '3')['total_price'] == 170
    details.assert_awaited_once_with(None, cheaper['href'])


async def test_blocked_xianyu_item_does_not_consume_result_or_detail_budget(monkeypatch):
    cards = [{'href': f'https://www.goofish.com/item?id={key}', 'title': '万代 MG 迅雷 全新未拼',
              'text': '万代 MG 迅雷 全新未拼', 'price': price} for key, price in [('blocked', 100), ('next', 150)]]
    monkeypatch.setattr(xianyu, '_search_cards', AsyncMock(return_value=cards))
    details = AsyncMock(return_value=('包邮', cards[1]['href'], ''))
    monkeypatch.setattr(xianyu, '_load_detail', details)
    monkeypatch.setattr(xianyu.asyncio, 'sleep', AsyncMock())
    rows = await xianyu.scrape_watch_item(None, {'name': '迅雷', 'search_query': 'MG 迅雷', 'aliases': ['迅雷']},
                                        max_results=1, detail_checks=1, blocked_keys={'blocked'})
    assert [row['item_key'] for row in rows] == ['next']
    details.assert_awaited_once_with(None, cards[1]['href'])
