import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlencode

import pytest
from playwright.async_api import Error, TimeoutError

from hguc_watch import browser, xianyu
from hguc_watch.search_response import parse_search_payload, SearchResponses, SEARCH_API
from hguc_watch.search_response import SearchPayload


def payload(price='179.89', key='123'):
    return {'ret':['SUCCESS::调用成功'],'data':{'resultList':[{'data':{'item':{'main':{
        'exContent':{'itemId':key,'title':'万代 MG 迅雷 全新未拼','price':[{'text':price}]},
        'clickParam':{'args':{'tag':'freeship'}}}}}}]}}


def test_structured_search_retains_decimal_id_and_shipping():
    result=parse_search_payload(payload())
    assert not result.failure
    assert result.cards[0]['price']==179.89
    assert result.cards[0]['href']=='https://www.goofish.com/item?id=123'
    assert result.cards[0]['free_shipping']


@pytest.mark.parametrize('data,cause',[
    ({'ret':['FAIL_SYS_USER_VALIDATE'],'data':{'resultList':[]}},'verification_required'),
    ({'ret':['FAIL_SYS_SESSION_EXPIRED']},'login_required'),
    ({'ret':['SUCCESS::OK'],'data':{}},'parse_failed'),
    ({'ret':['FAIL_SYS_TRAFFIC_LIMIT']},'service_busy'),
    (payload('100起'),'parse_failed'),
    (payload('50-100'),'parse_failed'),
    (payload('0'),'parse_failed'),
])
def test_failed_or_ambiguous_response_is_never_a_valid_empty_result(data,cause):
    assert parse_search_payload(data).failure==cause


async def test_capture_isolates_keyword_method_and_page():
    page=SimpleNamespace(on=Mock(),remove_listener=Mock())
    capture=SearchResponses(page,'MG 迅雷')
    def response(keyword,number,method='POST'):
        return SimpleNamespace(url='https://h5api.m.goofish.com'+SEARCH_API,ok=True,
            request=SimpleNamespace(method=method,post_data=urlencode({'data':json.dumps({'keyword':keyword,'pageNumber':number})})),json=AsyncMock(return_value=payload()))
    for args in [('其他商品',1),('MG 迅雷',2,'GET'),('MG 迅雷',4)]:
        capture.on_response(response(*args))
    assert not capture.tasks
    capture.on_response(response('MG 迅雷',2))
    result=await capture.wait(2)
    assert result.cards[0]['price']==179.89
    assert await capture.wait(1,timeout=0) is None
    await capture.close()
    page.remove_listener.assert_called_once()


async def test_navigation_retries_transport_but_not_closed_browser(monkeypatch):
    monkeypatch.setattr(browser.asyncio,'sleep',AsyncMock())
    page=SimpleNamespace(goto=AsyncMock(side_effect=[TimeoutError('slow'),None]))
    await browser.navigate(page,'https://example.test')
    assert page.goto.await_count==2
    page.goto=AsyncMock(side_effect=Error('Target page, context or browser has been closed'))
    with pytest.raises(Error): await browser.navigate(page,'https://example.test')
    assert page.goto.await_count==1


async def test_profile_lock_blocks_second_owner_and_releases_after_stop(tmp_path,monkeypatch):
    driver=SimpleNamespace(stop=AsyncMock(),chromium=SimpleNamespace(launch_persistent_context=AsyncMock(return_value=object())))
    monkeypatch.setattr(browser,'async_playwright',lambda:SimpleNamespace(start=AsyncMock(return_value=driver)))
    profile=tmp_path/'profile'
    owner,_=await browser.launch_profile(profile)
    with pytest.raises(browser.BrowserBusyError): await browser.launch_profile(profile)
    await owner.stop()
    owner,_=await browser.launch_profile(profile)
    await owner.stop()


async def test_profile_lock_released_after_cancelled_start(tmp_path,monkeypatch):
    driver=SimpleNamespace(stop=AsyncMock(),chromium=SimpleNamespace(launch_persistent_context=AsyncMock(side_effect=asyncio.CancelledError)))
    monkeypatch.setattr(browser,'async_playwright',lambda:SimpleNamespace(start=AsyncMock(return_value=driver)))
    profile=tmp_path/'profile'
    with pytest.raises(asyncio.CancelledError): await browser.launch_profile(profile)
    driver.chromium.launch_persistent_context=AsyncMock(return_value=object())
    owner,_=await browser.launch_profile(profile)
    await owner.stop()


async def test_detail_failure_keeps_price_but_reports_incomplete(monkeypatch):
    card={'href':'https://www.goofish.com/item?id=123','title':'万代 MG 迅雷 全新未拼 包邮','price':100,'text':'万代 MG 迅雷 全新未拼 包邮'}
    monkeypatch.setattr(xianyu,'_search_cards',AsyncMock(return_value=[card]))
    monkeypatch.setattr(xianyu,'_load_detail',AsyncMock(return_value=('',card['href'],xianyu.NetworkError('详情超时'))))
    monkeypatch.setattr(xianyu.asyncio,'sleep',AsyncMock())
    rows=await xianyu.scrape_watch_item(None,{'name':'迅雷','aliases':['迅雷'],'search_query':'MG 迅雷'},detail_checks=1)
    assert rows[0]['item_price']==100
    assert not rows[0]['eligible']
    assert rows[0]['condition_status']=='详情待核实'
    assert rows.errors[0].cause=='network'


async def test_detail_challenge_stops_following_requests(monkeypatch):
    cards=[{'href':f'https://www.goofish.com/item?id={i}','title':'万代 MG 迅雷 全新未拼','price':100+i,'text':'万代 MG 迅雷 全新未拼'} for i in range(2)]
    monkeypatch.setattr(xianyu,'_search_cards',AsyncMock(return_value=cards))
    detail=AsyncMock(return_value=('',cards[0]['href'],xianyu.VerificationRequiredError('验证')))
    monkeypatch.setattr(xianyu,'_load_detail',detail)
    rows=await xianyu.scrape_watch_item(None,{'name':'迅雷','aliases':['迅雷'],'search_query':'MG 迅雷'},detail_checks=2)
    detail.assert_awaited_once()
    assert rows.errors[0].cause=='verification_required'


@pytest.mark.parametrize('second_page', [None, 'cheaper'])
async def test_pagination_preserves_first_page_and_finds_later_low_price(monkeypatch, second_page):
    first=parse_search_payload(payload('200','1'))
    second=parse_search_payload(payload('150','2')) if second_page else None
    capture=SimpleNamespace(wait=AsyncMock(side_effect=[first,None,second,SearchPayload([])]),close=AsyncMock())
    button=SimpleNamespace(count=AsyncMock(return_value=1),wait_for=AsyncMock(),click=AsyncMock())
    page=SimpleNamespace(bring_to_front=AsyncMock(),goto=AsyncMock(),close=AsyncMock(),locator=Mock(return_value=SimpleNamespace(first=button)))
    monkeypatch.setattr(xianyu,'SearchResponses',lambda *args:capture)
    monkeypatch.setattr(xianyu,'_login_overlay',AsyncMock(return_value=False))
    monkeypatch.setattr(xianyu.asyncio,'sleep',AsyncMock())
    rows=await xianyu._search_cards(SimpleNamespace(new_page=AsyncMock(return_value=page)),'MG 迅雷',30)
    assert rows[0]['price']==200
    if second_page:
        assert rows[1]['price']==150
        assert not rows.errors
    else:
        assert len(rows)==1 and rows.errors[0].cause=='network'
        button.click.assert_awaited_once()
    capture.close.assert_awaited_once()
