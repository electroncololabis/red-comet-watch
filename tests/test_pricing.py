from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from hguc_watch import db, fx, service
from hguc_watch.reference_prices import fill_reference_price


@pytest.fixture
def pricing_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "pricing.db")
    db.init_db()
    return tmp_path


def mock_fx_http(monkeypatch, handler):
    client_class = httpx.AsyncClient
    monkeypatch.setattr(fx.httpx, "AsyncClient", lambda **kwargs: client_class(
        **kwargs, transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("name,code,query,price", [
    ("德莱森", "zz版", "德莱森", 2640),
    ("主天使", "", "Mg主天使 高达", 5500),
    ("迅雷高达", "", "mg 迅雷高达", 4400),
    ("斑豹高达", "", "hg斑豹高达", 2420),
    ("德南格", "", "re德南格", 3300),
    ("赛克艾恩", "", "赛克艾恩", 1760),
])
def test_new_models_get_verified_price_without_manual_entry(pricing_db, name, code, query, price):
    item = db.create_watch_item(dict(name=name, model_code=code, search_query=query))
    assert item["msrp_jpy"] == price
    assert item["official_url"].startswith("https://")


@pytest.mark.parametrize("name,code,query", [
    ("主天使", "", "HG 主天使"),
    ("主天使", "", "主天使"),  # grade ambiguous
    ("迅雷高达", "", "HG 迅雷高达"),
    ("德莱森", "UC版", "HGUC 德莱森"),
    ("德莱森", "", "HGUC 德莱森"),  # edition ambiguous
    ("德莱森", "ZZ", "HGUC 德莱森 UC版"),
    ("斑豹毁灭者", "", "HG 斑豹毁灭者"),
    ("斑豹高达", "旧HG", "HG斑豹高达"),
    ("斑豹高达", "", "HG 斑豹 达芬奇"),
    ("主天使", "", "MG 主天使 透明版"),
    ("主天使", "", "MGSD 主天使"),
    ("未知新型号", "", "HG 未知新型号"),
])
def test_reference_price_is_not_borrowed_from_a_different_version(name, code, query):
    item = fill_reference_price(dict(name=name, model_code=code, search_query=query))
    assert not item.get("msrp_jpy")


def test_backfill_repairs_old_and_purchased_rows_without_overwriting_user_prices(pricing_db):
    item = db.create_watch_item(dict(name="主天使", search_query="MG 主天使"))
    db.set_purchased(item["id"], True, 189)
    with db.connect() as connection:
        connection.execute("UPDATE watch_items SET msrp_jpy=0, official_url='' WHERE id=?", (item["id"],))
    other = db.create_watch_item(dict(name="迅雷高达", search_query="MG 迅雷高达",
                                    msrp_jpy=4700, official_url="https://example.com/manual"))
    assert db.backfill_reference_prices() == 1
    repaired = db.get_watch_item(item["id"])
    assert repaired["msrp_jpy"] == 5500
    assert repaired["official_url"].startswith("https://www.bandaispirits.co.jp/")
    assert not repaired["active"] and repaired["purchase_price_cny"] == 189
    assert db.get_watch_item(other["id"])["msrp_jpy"] == 4700
    assert db.get_watch_item(other["id"])["official_url"] == "https://example.com/manual"
    assert db.backfill_reference_prices() == 0
    # Correcting an ambiguous grade and saving can fill the missing price too.
    missing = db.create_watch_item(dict(name="MG主天使", search_query="HG 主天使"))
    assert missing["msrp_jpy"] == 0
    assert db.update_watch_item(missing["id"], {"search_query": "MG 主天使"})["msrp_jpy"] == 5500


async def test_latest_published_workday_rate_is_fresh_and_fetched_on_every_call(pricing_db, monkeypatch):
    rates = iter([0.04231, 0.04321])
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.params["base"] == "JPY"
        return httpx.Response(200, json={"date": "2026-10-16", "rates": {"CNY": next(rates)}})

    mock_fx_http(monkeypatch, handler)
    first = await fx.get_jpy_cny_rate()
    second = await fx.get_jpy_cny_rate()
    assert first.rate != second.rate and len(requests) == 2
    assert not first.stale and not second.stale
    assert first.rate_date == "2026-10-16"
    settings = db.get_settings()
    assert settings["last_fx_rate"] == "0.04321"
    assert settings["last_fx_stale"] == "false"
    assert settings["last_fx_checked_at"] and settings["last_fx_fetched_at"]


@pytest.mark.parametrize("bad_rate", [0, -1, "nan", "inf"])
async def test_invalid_rate_never_replaces_good_data_and_secondary_source_is_used(pricing_db, monkeypatch, bad_rate):
    timestamp = int(datetime(2026, 10, 17, tzinfo=timezone.utc).timestamp())

    def handler(request):
        payload = ({"date": "2026-10-17", "rates": {"CNY": bad_rate}}
                   if request.url.host == "api.frankfurter.dev" else
                   {"time_last_update_unix": timestamp, "rates": {"CNY": 0.0425}})
        return httpx.Response(200, json=payload)

    mock_fx_http(monkeypatch, handler)
    rate = await fx.get_jpy_cny_rate()
    assert rate.rate == 0.0425 and rate.rate_date == "2026-10-17"
    assert rate.source == "ExchangeRate-API" and not rate.stale


async def test_network_failure_marks_cache_without_changing_original_fetch_time(pricing_db, monkeypatch):
    db.set_settings(dict(last_fx_rate=0.041, last_fx_date="2026-10-14", last_fx_source="fixture",
                         last_fx_fetched_at="2026-10-14T20:00:00+08:00", last_fx_stale="false"))
    mock_fx_http(monkeypatch, lambda request: httpx.Response(503))
    rate = await fx.get_jpy_cny_rate()
    assert rate.rate == .041 and rate.stale
    assert rate.rate_date == "2026-10-14"
    assert rate.fetched_at == "2026-10-14T20:00:00+08:00"
    settings = db.get_settings()
    assert settings["last_fx_stale"] == "true"
    assert settings["last_fx_checked_at"] == rate.checked_at
    assert settings["last_fx_fetched_at"] == rate.fetched_at


@pytest.mark.parametrize("cache", ["", "0", "nan"])
async def test_failure_without_valid_cache_does_not_invent_a_rate(pricing_db, monkeypatch, cache):
    db.set_settings({"last_fx_rate": cache})
    mock_fx_http(monkeypatch, lambda request: httpx.Response(503))
    with pytest.raises(RuntimeError, match="无法获取日元汇率"):
        await fx.get_jpy_cny_rate()


def test_conversion_rounds_half_cent_and_distinguishes_missing_data():
    assert fx.convert_msrp_to_cny(1005, .005) == 5.03
    assert fx.convert_msrp_to_cny(5500, .04231) == 232.71
    assert fx.convert_msrp_to_cny(0, .04231) is None
    assert fx.convert_msrp_to_cny(5500, None) is None
    assert fx.convert_msrp_to_cny(5500, float("nan")) is None


async def test_every_scan_reprices_all_watches_even_when_marketplaces_fail(pricing_db, monkeypatch):
    from hguc_watch import app as app_module

    monkeypatch.setattr(app_module, "task_status", lambda: {})
    monkeypatch.setattr(service, "RUN_LOCK_PATH", pricing_db / "scan.lock")
    monkeypatch.setattr(service, "launch_context", AsyncMock(side_effect=RuntimeError("fixture offline")))
    monkeypatch.setattr(service.pdd, "launch_context", AsyncMock(side_effect=RuntimeError("fixture offline")))
    purchased = db.list_watch_items()[0]
    db.set_purchased(purchased["id"], True, 100)
    added = db.create_watch_item(dict(name="主天使", search_query="MG主天使"))
    # Simulate a row written by the previous version without an MSRP.
    with db.connect() as connection:
        connection.execute("UPDATE watch_items SET msrp_jpy=0 WHERE id=?", (added["id"],))
    missing = db.create_watch_item(dict(name="未知型号", search_query="HG未知型号"))
    rates = iter([.04, .04321])
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"date": "2026-10-16", "rates": {"CNY": next(rates)}})

    mock_fx_http(monkeypatch, handler)
    for rate in [.04, .04321]:
        result = await service.run_tracking()
        assert result.status == "failed"  # FX still succeeds independently.
        dashboard = app_module.dashboard()
        assert dashboard["fx"]["rate"] == rate
        assert dashboard["fx"]["stale"] is False
        assert dashboard["fx"]["checked_at"]
        for item in dashboard["items"]:
            if item["id"] == missing["id"]:
                assert item["msrp_cny"] is None
            else:
                assert item["msrp_cny"] == fx.convert_msrp_to_cny(item["msrp_jpy"], rate)
        assert next(item for item in dashboard["items"] if item["id"] == added["id"])["msrp_jpy"] == 5500
        assert not next(item for item in dashboard["items"] if item["id"] == purchased["id"])["active"]
    assert len(calls) == 2
