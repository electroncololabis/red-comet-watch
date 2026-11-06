from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass

from filelock import FileLock, Timeout
from playwright.async_api import Error as PlaywrightError

from .config import RUN_LOCK_PATH, ensure_data_dirs
from .db import (
    add_listings,
    backfill_reference_prices,
    create_run,
    finish_run,
    get_settings,
    get_blocked_item_keys,
    list_watch_items,
    set_marketplace_status,
)
from .fx import FxRate, get_jpy_cny_rate
from . import pdd
from .xianyu import (
    IllegalAccessError,
    LoginRequiredError,
    VerificationRequiredError,
    XianyuError,
    PlatformBusyError,
    launch_context,
    scrape_watch_item,
)

# 这几种是**会话级**故障：一旦出现，剩下六个型号也一定是同样结果，
# 继续跑只是白白多打六次请求（run 10/13 就是这样）。
SESSION_LEVEL_ERRORS = (IllegalAccessError, LoginRequiredError, VerificationRequiredError, PlatformBusyError)

_tracking_progress: dict = {}


def get_tracking_progress() -> dict | None:
    return dict(_tracking_progress) if _tracking_progress else None


@dataclass(frozen=True)
class TrackingResult:
    run_id: int
    status: str
    item_count: int
    listing_count: int
    errors: list[str]
    # 机器可读的失败分类（login_required / verification_required /
    # empty_result / parse_failed / network / ...），供 UI 区分四种情况。
    causes: list[str]


async def run_tracking(*, platforms: tuple[str, ...] = ('xianyu', 'pdd')) -> TrackingResult:
    if not platforms or any(platform not in ('xianyu', 'pdd') for platform in platforms):
        raise ValueError('请选择闲鱼或拼多多')
    ensure_data_dirs()
    lock = FileLock(str(RUN_LOCK_PATH))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise RuntimeError("已有一次价格检查正在运行，请稍后再试") from exc

    run_id = create_run()
    fx: FxRate | None = None
    errors: list[str] = []
    causes: list[str] = []
    listing_count = 0
    items = list_watch_items(include_purchased=False)
    playwright = None
    context = None
    _tracking_progress.update(run_id=run_id, platform_label='更新汇率与发售价', item_name='',
                              item_index=0, item_count=len(items), listing_count=0)
    try:
        backfill_reference_prices()
        fx = await get_jpy_cny_rate()
        settings = get_settings()
        headless = settings.get("headless", "true").lower() == "true"
        max_results = max(5, min(80, int(settings.get("max_results", "30"))))
        detail_checks = max(1, min(max_results, int(settings.get("detail_checks", "12"))))
        for platform, label, launcher, scraper in (
            ('xianyu', '闲鱼', launch_context, scrape_watch_item),
            ('pdd', '拼多多', pdd.launch_context, pdd.scrape_watch_item),
        ):
            if platform not in platforms:
                continue
            set_marketplace_status(run_id, platform, 'running')
            _tracking_progress.update(platform_label=label, item_name='', item_index=0)
            platform_errors = []
            platform_count = 0
            try:
                playwright, context = await launcher(headless=headless)
                blocked_keys = get_blocked_item_keys(platform)
                for item_index, item in enumerate(items, start=1):
                    _tracking_progress.update(item_name=item['name'], item_index=item_index)
                    try:
                        listings = await scraper(context, item, max_results=max_results, detail_checks=detail_checks,
                                                 blocked_keys=blocked_keys)
                        add_listings(run_id, item['id'], listings)
                        platform_count += len(listings)
                        listing_count += len(listings)
                        _tracking_progress['listing_count'] = listing_count
                        incomplete = getattr(listings, 'errors', [])
                        for exc in incomplete:
                            causes.append(exc.cause)
                            platform_errors.append(f"{label} / {item['name']}：{exc.describe()}")
                        if any(isinstance(exc, SESSION_LEVEL_ERRORS) for exc in incomplete):
                            break
                    except SESSION_LEVEL_ERRORS as exc:
                        causes.append(exc.cause)
                        platform_errors.append(f"{label} / {item['name']}：{exc.describe()}")
                        break
                    except XianyuError as exc:
                        causes.append(exc.cause)
                        platform_errors.append(f"{label} / {item['name']}：{exc.describe()}")
                    except PlaywrightError as exc:
                        if 'target page, context or browser has been closed' in str(exc).lower():
                            causes.append('browser_closed')
                            platform_errors.append(f"{label} / {item['name']}：[browser_closed/采集窗口已关闭] 采集浏览器被关闭，本平台搜索已停止。请重新搜索并保留自动打开的窗口直到完成。")
                            break
                        causes.append('unexpected')
                        platform_errors.append(f"{label} / {item['name']}：[unexpected] {exc}")
                    except Exception as exc:
                        causes.append('unexpected')
                        platform_errors.append(f"{label} / {item['name']}：[unexpected] {exc}")
                    await asyncio.sleep(2.5)
            except Exception as exc:
                causes.append('browser_launch')
                platform_errors.append(f'{label}：浏览器启动失败（{exc}）')
            finally:
                if context is not None:
                    await context.close()
                    context = None
                if playwright is not None:
                    await playwright.stop()
                    playwright = None
            errors.extend(platform_errors)
            platform_status = ('partial' if platform_count else 'failed') if platform_errors else 'success'
            set_marketplace_status(run_id, platform, platform_status, '\n'.join(platform_errors))

        if errors and listing_count == 0:
            status = "failed"
        elif errors:
            status = "partial"
        else:
            status = "success"
        finish_run(
            run_id,
            status,
            error="\n".join(errors) or None,
            fx_rate=fx.rate,
            fx_date=fx.rate_date,
            fx_source=fx.source,
        )

        return TrackingResult(run_id, status, len(items), listing_count, errors, causes)
    except Exception as exc:
        detail = f"{exc}\n{traceback.format_exc(limit=6)}"
        finish_run(
            run_id,
            "failed",
            error=detail,
            fx_rate=fx.rate if fx else None,
            fx_date=fx.rate_date if fx else None,
            fx_source=fx.source if fx else None,
        )
        raise
    finally:
        _tracking_progress.clear()
        if context is not None:
            await context.close()
        if playwright is not None:
            await playwright.stop()
        lock.release()
