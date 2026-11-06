from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from hguc_watch import service
from hguc_watch.xianyu import LoginRequiredError, PlatformBusyError
import pytest
from playwright.async_api import Error as PlaywrightError


@pytest.mark.parametrize('error,cause',[(LoginRequiredError('登录已过期'),'login_required'),
                                      (PlatformBusyError('系统繁忙'),'service_busy')])
async def test_pdd_failure_keeps_xianyu_rows_and_marks_partial(tmp_path, monkeypatch, error, cause):
    monkeypatch.setattr(service, 'RUN_LOCK_PATH', tmp_path/'scan.lock')
    monkeypatch.setattr(service, 'ensure_data_dirs', lambda: None)
    monkeypatch.setattr(service, 'create_run', lambda: 10)
    monkeypatch.setattr(service, 'list_watch_items', lambda **kwargs: [{'id':1,'name':'主天使'}])
    monkeypatch.setattr(service, 'get_settings', lambda: {})
    monkeypatch.setattr(service, 'backfill_reference_prices', Mock())
    monkeypatch.setattr(service, 'get_blocked_item_keys', lambda platform: set())
    fx = SimpleNamespace(rate=.05,rate_date='2026-10-14',source='fixture')
    monkeypatch.setattr(service, 'get_jpy_cny_rate', AsyncMock(return_value=fx))
    driver, context = SimpleNamespace(stop=AsyncMock()), SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(service, 'launch_context', AsyncMock(return_value=(driver,context)))
    monkeypatch.setattr(service.pdd, 'launch_context', AsyncMock(return_value=(driver,context)))
    rows = [{'item_key':'1'}]
    monkeypatch.setattr(service, 'scrape_watch_item', AsyncMock(return_value=rows))
    monkeypatch.setattr(service.pdd, 'scrape_watch_item', AsyncMock(side_effect=error))
    save, finish, market = Mock(), Mock(), Mock()
    monkeypatch.setattr(service, 'add_listings', save)
    monkeypatch.setattr(service, 'finish_run', finish)
    monkeypatch.setattr(service, 'set_marketplace_status', market)
    monkeypatch.setattr(service.asyncio, 'sleep', AsyncMock())
    result = await service.run_tracking()
    assert result.status == 'partial'
    assert result.listing_count == 1
    assert result.causes == [cause]
    service.get_jpy_cny_rate.assert_awaited_once()
    service.backfill_reference_prices.assert_called_once()
    save.assert_called_once_with(10,1,rows)
    assert any(call.args[:3] == (10,'pdd','failed') for call in market.call_args_list)


@pytest.fixture
def two_item_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'RUN_LOCK_PATH', tmp_path / 'scan.lock')
    monkeypatch.setattr(service, 'ensure_data_dirs', lambda: None)
    monkeypatch.setattr(service, 'create_run', lambda: 21)
    items = [{'id': 1, 'name': '亚希玛'}, {'id': 2, 'name': '主天使'}]
    monkeypatch.setattr(service, 'list_watch_items', lambda **kwargs: items)
    monkeypatch.setattr(service, 'get_settings', lambda: {})
    monkeypatch.setattr(service, 'backfill_reference_prices', Mock())
    monkeypatch.setattr(service, 'get_blocked_item_keys', lambda platform: set())
    fx = SimpleNamespace(rate=.05, rate_date='2026-10-14', source='fixture')
    monkeypatch.setattr(service, 'get_jpy_cny_rate', AsyncMock(return_value=fx))
    driver, context = SimpleNamespace(stop=AsyncMock()), SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(service, 'launch_context', AsyncMock(return_value=(driver, context)))
    monkeypatch.setattr(service.pdd, 'launch_context', AsyncMock(return_value=(driver, context)))
    xianyu, pdd = AsyncMock(return_value=[{'item_key': 'x'}]), AsyncMock(return_value=[{'item_key': 'p'}])
    monkeypatch.setattr(service, 'scrape_watch_item', xianyu)
    monkeypatch.setattr(service.pdd, 'scrape_watch_item', pdd)
    save, finish, market = Mock(), Mock(), Mock()
    monkeypatch.setattr(service, 'add_listings', save)
    monkeypatch.setattr(service, 'finish_run', finish)
    monkeypatch.setattr(service, 'set_marketplace_status', market)
    monkeypatch.setattr(service.asyncio, 'sleep', AsyncMock())
    return SimpleNamespace(xianyu=xianyu, pdd=pdd, save=save, market=market)


async def test_closed_browser_stops_its_platform_and_other_market_still_finishes(two_item_scan):
    two_item_scan.xianyu.side_effect = PlaywrightError('BrowserContext.new_page: Target page, context or browser has been closed')
    result = await service.run_tracking()
    assert result.status == 'partial'
    assert result.listing_count == 2
    assert result.causes == ['browser_closed']
    assert '保留自动打开的窗口' in result.errors[0]
    assert 'Target page' not in result.errors[0]
    assert two_item_scan.xianyu.await_count == 1
    assert two_item_scan.pdd.await_count == 2
    assert any(call.args[:3] == (21, 'pdd', 'success') for call in two_item_scan.market.call_args_list)
    assert service.get_tracking_progress() is None


async def test_search_progress_identifies_current_model_and_persisted_count(two_item_scan):
    snapshots = []

    async def collect(*args, **kwargs):
        snapshots.append(service.get_tracking_progress())
        return [{'item_key': 'fixture'}]

    two_item_scan.xianyu.side_effect = collect
    two_item_scan.pdd.side_effect = collect
    result = await service.run_tracking()
    assert result.status == 'success'
    assert [(row['platform_label'], row['item_name'], row['item_index'], row['listing_count']) for row in snapshots] == [
        ('闲鱼', '亚希玛', 1, 0), ('闲鱼', '主天使', 2, 1),
        ('拼多多', '亚希玛', 1, 2), ('拼多多', '主天使', 2, 3),
    ]
    assert all(row['item_count'] == 2 for row in snapshots)
    assert service.get_tracking_progress() is None


async def test_pdd_only_retry_does_not_launch_or_change_xianyu(two_item_scan):
    result = await service.run_tracking(platforms=('pdd',))
    assert result.status == 'success'
    assert result.listing_count == 2
    two_item_scan.xianyu.assert_not_awaited()
    service.launch_context.assert_not_awaited()
    assert two_item_scan.pdd.await_count == 2
    assert all(call.args[1] == 'pdd' for call in two_item_scan.market.call_args_list)
