from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.utils import parsedate_to_datetime
import math

import httpx

from .db import get_settings, set_settings


@dataclass(frozen=True)
class FxRate:
    rate: float
    rate_date: str
    source: str
    stale: bool = False
    checked_at: str = ""
    fetched_at: str = ""


def _positive_rate(value: object) -> float:
    rate = float(value)
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("汇率必须是有效的正数")
    return rate


def convert_msrp_to_cny(msrp_jpy: int, rate: float | None) -> float | None:
    """Use one rate for every item; missing MSRP/rate is not a zero-yuan price."""
    if msrp_jpy <= 0 or rate is None:
        return None
    try:
        _positive_rate(rate)
        return float((Decimal(str(msrp_jpy)) * Decimal(str(rate))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (ValueError, TypeError, InvalidOperation):
        return None


async def get_jpy_cny_rate() -> FxRate:
    """Return CNY for one JPY, with a cached last-known-good fallback."""
    errors: list[str] = []
    checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        try:
            response = await client.get(
                "https://api.frankfurter.dev/v1/latest",
                params={"base": "JPY", "symbols": "CNY"},
            )
            response.raise_for_status()
            payload = response.json()
            rate = _positive_rate(payload["rates"]["CNY"])
            rate_date = date.fromisoformat(payload["date"]).isoformat()
            fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
            result = FxRate(rate, rate_date, "Frankfurter/ECB", checked_at=checked_at, fetched_at=fetched_at)
            _cache(result)
            return result
        except Exception as exc:  # noqa: BLE001 - fallback is intentional
            errors.append(f"Frankfurter: {exc}")

        try:
            response = await client.get("https://open.er-api.com/v6/latest/JPY")
            response.raise_for_status()
            payload = response.json()
            rate = _positive_rate(payload["rates"]["CNY"])
            if payload.get("time_last_update_unix"):
                rate_date = datetime.fromtimestamp(int(payload["time_last_update_unix"]), timezone.utc).date().isoformat()
            else:
                rate_date = parsedate_to_datetime(payload["time_last_update_utc"]).date().isoformat()
            fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
            result = FxRate(rate, rate_date, "ExchangeRate-API", checked_at=checked_at, fetched_at=fetched_at)
            _cache(result)
            return result
        except Exception as exc:  # noqa: BLE001 - fallback is intentional
            errors.append(f"ExchangeRate-API: {exc}")

    settings = get_settings()
    set_settings({"last_fx_checked_at": checked_at, "last_fx_stale": "true"})
    try:
        rate = _positive_rate(settings.get("last_fx_rate"))
    except (ValueError, TypeError):
        rate = None
    if rate is not None:
        return FxRate(
            rate,
            settings.get("last_fx_date", "未知"),
            settings.get("last_fx_source", "本地缓存"),
            stale=True,
            checked_at=checked_at,
            fetched_at=settings.get("last_fx_fetched_at", ""),
        )
    raise RuntimeError("无法获取日元汇率，且本机没有历史缓存：" + "；".join(errors))


def _cache(result: FxRate) -> None:
    set_settings(
        {
            "last_fx_rate": result.rate,
            "last_fx_date": result.rate_date,
            "last_fx_source": result.source,
            "last_fx_checked_at": result.checked_at,
            "last_fx_fetched_at": result.fetched_at,
            "last_fx_stale": "false",
        }
    )
