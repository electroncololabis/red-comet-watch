from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from filelock import FileLock, Timeout
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import DESKTOP_VERSION
from .catalog import DEFAULT_SETTINGS
from .config import APP_PORT, PROJECT_ROOT, DATA_DIR, RUN_LOCK_PATH, INSTALLED
from .db import (
    block_listing,
    create_watch_item,
    delete_settings,
    get_item_summary,
    get_latest_run,
    get_latest_successful_run_id,
    get_month_purchase_summary,
    get_marketplace_status,
    get_settings,
    init_db,
    list_runs,
    list_blocked_listings,
    list_watch_items,
    set_purchased,
    set_settings,
    update_watch_item,
    unblock_listing,
)
from .filtering import FILTER_RULE_VERSION
from .fx import convert_msrp_to_cny
from .scheduler import install_weekly_tasks, task_status
from .service import get_tracking_progress, run_tracking


STATIC_DIR = Path(__file__).parent / "static"
app = FastAPI(title="HGUC 闲鱼价格观察站", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

if INSTALLED:
    @app.middleware("http")
    async def reject_cross_origin_writes(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            local_origins = {f"http://127.0.0.1:{APP_PORT}", f"http://localhost:{APP_PORT}"}
            if ((origin is not None and origin not in local_origins)
                    or request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}):
                return JSONResponse({"detail": "请从本机战情室页面执行此操作"}, status_code=403)
        return await call_next(request)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

_tracking_task: asyncio.Task[Any] | None = None


class WatchItemPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    model_code: str = Field(default="", max_length=80)
    search_query: str = Field(min_length=1, max_length=160)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    msrp_jpy: int = Field(default=0, ge=0, le=1_000_000)
    official_url: str = Field(default="", max_length=500)

    @field_validator("aliases")
    @classmethod
    def clean_aliases(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(alias.strip() for alias in value if alias.strip()))


class PurchasedPayload(BaseModel):
    purchased: bool = True
    price_cny: float | None = Field(default=None, ge=0, le=1_000_000)


class RunPayload(BaseModel):
    platform: Literal['all', 'xianyu', 'pdd'] = 'all'


class SettingsPayload(BaseModel):
    scan_weekday: str = "MON"
    scan_time: str = "03:00"
    dashboard_time: str = "08:00"
    destination_province: str = DEFAULT_SETTINGS["destination_province"]
    destination_city: str = DEFAULT_SETTINGS["destination_city"]
    max_results: int = Field(default=30, ge=5, le=80)
    detail_checks: int = Field(default=12, ge=1, le=50)
    headless: bool = True


@app.on_event("startup")
def startup() -> None:
    init_db()
    delete_settings([
        "sender_email",
        "recipient_email",
        "gmail_app_password_protected",
        "report_weekday",
        "report_time",
    ])


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"app": "red-comet-watch", "data_dir": str(DATA_DIR), "version": DESKTOP_VERSION}


@app.post("/api/desktop/stop")
def desktop_stop(x_desktop_token: str = Header(default="")) -> dict[str, bool]:
    token = os.environ.get("HGUC_WATCH_CONTROL_TOKEN", "")
    if not INSTALLED or not token or not secrets.compare_digest(token, x_desktop_token):
        raise HTTPException(status_code=403, detail="只能通过本机程序入口关闭")
    if _tracking_task and not _tracking_task.done():
        raise HTTPException(status_code=409, detail="正在搜索，请等待完成后再退出")
    try:
        with FileLock(str(RUN_LOCK_PATH), timeout=0):
            callback = getattr(app.state, "desktop_shutdown", None)
            if callback is None:
                raise HTTPException(status_code=409, detail="当前后台不支持此退出方式")
            callback()
    except Timeout as exc:
        raise HTTPException(status_code=409, detail="自动搜索正在运行，请等待完成后再退出") from exc
    return {"ok": True}


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    settings = get_settings()
    rate = float(settings.get("last_fx_rate") or 0)
    latest_success_id = get_latest_successful_run_id()
    latest_attempt = get_latest_run()
    latest_attempt_id = latest_attempt["id"] if latest_attempt else None
    latest_attempt_refreshed = bool(
        latest_attempt and latest_attempt["status"] in ("success", "partial")
    )
    items: list[dict[str, Any]] = []
    for item in list_watch_items(include_purchased=True):
        summary = get_item_summary(item["id"])
        # 「这张卡片的报价是不是旧批次」= **最近一次侦察没有刷新它**。
        #   最近一次侦察失败/被中断       -> 谁都没刷新 -> 全是旧批次
        #   最近一次部分成功              -> 只有没被覆盖到的型号是旧批次
        #   卡片压根没有数据 (latest_run_id is None) -> 也算旧
        # 搜索失败时，仍可显示上一批报价并标明它的实际采集时间。
        summary["is_stale"] = (
            summary["latest_run_id"] is None
            or summary["latest_run_id"] != latest_attempt_id
            or not latest_attempt_refreshed
        )
        main_quote = summary['current'] or summary['unknown_shipping']
        if main_quote and 'is_stale' in main_quote:
            summary['is_stale'] = main_quote['is_stale']
        item["summary"] = summary
        item["msrp_cny"] = convert_msrp_to_cny(item["msrp_jpy"], rate or None)
        items.append(item)
    return {
        "items": items,
        "latest_run": latest_attempt,
        "latest_successful_run_id": latest_success_id,
        "runs": list_runs(),
        "tracking": bool(_tracking_task and not _tracking_task.done()),
        "tracking_progress": get_tracking_progress(),
        "filter_rule_version": FILTER_RULE_VERSION,
        "marketplaces": get_marketplace_status(),
        "blocked_terms": ["旧HG", "老HG", "旧版HG", "老款HG"],
        "blocked_listings": list_blocked_listings(),
        "fx": {
            "rate": rate or None,
            "date": settings.get("last_fx_date", ""),
            "source": settings.get("last_fx_source", ""),
            "checked_at": settings.get("last_fx_checked_at", ""),
            "fetched_at": settings.get("last_fx_fetched_at", ""),
            "stale": settings.get("last_fx_stale") == "true" if settings.get("last_fx_stale") else None,
        },
        "task": task_status(),
        "purchase_summary": get_month_purchase_summary(),
    }


@app.post("/api/items")
def add_item(payload: WatchItemPayload) -> dict[str, Any]:
    try:
        item = create_watch_item(payload.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": item}


@app.post("/api/listings/{listing_id}/block")
def hide_listing(listing_id: int) -> dict[str, Any]:
    entry = block_listing(listing_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="未找到这条商品，请刷新页面后重试")
    return {"ok": True, "blocked": entry}


@app.get("/api/blocked-listings")
def blocked_listings() -> dict[str, Any]:
    return {"items": list_blocked_listings()}


@app.delete("/api/blocked-listings/{block_id}")
def restore_listing(block_id: int) -> dict[str, Any]:
    if not unblock_listing(block_id):
        raise HTTPException(status_code=404, detail="这条商品已恢复或不存在，请刷新页面")
    return {"ok": True}


@app.put("/api/items/{item_id}")
def edit_item(item_id: int, payload: WatchItemPayload) -> dict[str, Any]:
    try:
        item = update_watch_item(item_id, payload.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="未找到关注型号")
    return {"ok": True, "item": item}


@app.post("/api/items/{item_id}/purchased")
def purchased(item_id: int, payload: PurchasedPayload) -> dict[str, Any]:
    if not set_purchased(item_id, payload.purchased, payload.price_cny):
        raise HTTPException(status_code=404, detail="未找到关注型号")
    return {"ok": True, "purchase_summary": get_month_purchase_summary()}


@app.get("/api/settings")
def read_settings() -> dict[str, Any]:
    settings = get_settings()
    return {
        "scan_weekday": settings.get("scan_weekday", "MON"),
        "scan_time": settings.get("scan_time", "03:00"),
        "dashboard_time": settings.get("dashboard_time", "08:00"),
        "destination_province": settings.get("destination_province", DEFAULT_SETTINGS["destination_province"]),
        "destination_city": settings.get("destination_city", DEFAULT_SETTINGS["destination_city"]),
        "max_results": int(settings.get("max_results", "30")),
        "detail_checks": int(settings.get("detail_checks", "12")),
        "headless": settings.get("headless", "false").lower() == "true",
    }


@app.put("/api/settings")
def save_settings(payload: SettingsPayload) -> dict[str, Any]:
    values: dict[str, Any] = {
        "scan_weekday": payload.scan_weekday.upper(),
        "scan_time": payload.scan_time,
        "dashboard_time": payload.dashboard_time,
        "destination_province": payload.destination_province.strip(),
        "destination_city": payload.destination_city.strip(),
        "max_results": payload.max_results,
        "detail_checks": payload.detail_checks,
        "headless": str(payload.headless).lower(),
    }
    set_settings(values)
    return {"ok": True}


async def _track_in_background(platform: str = 'all') -> Any:
    platforms = ('xianyu', 'pdd') if platform == 'all' else (platform,)
    return await run_tracking(platforms=platforms)


@app.post("/api/run")
async def start_run(payload: RunPayload | None = None) -> dict[str, Any]:
    global _tracking_task
    if _tracking_task and not _tracking_task.done():
        raise HTTPException(status_code=409, detail="价格检查正在运行")
    _tracking_task = asyncio.create_task(_track_in_background(payload.platform if payload else 'all'))
    return {"ok": True, "message": "价格检查已经开始"}


@app.get("/api/run/status")
def run_status() -> dict[str, Any]:
    if not _tracking_task:
        return {"running": False, "result": None}
    if not _tracking_task.done():
        return {"running": True, "result": None, "progress": get_tracking_progress()}
    try:
        result = _tracking_task.result()
        return {"running": False, "result": result.__dict__}
    except Exception as exc:
        return {"running": False, "error": str(exc)}


@app.post("/api/login-window")
def login_window() -> dict[str, Any]:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [sys.executable, "-B", "-m", "hguc_watch.login"],
        cwd=PROJECT_ROOT,
        creationflags=creation_flags,
    )
    return {"ok": True, "message": "闲鱼登录窗口已打开，完成登录后直接关闭浏览器即可"}


@app.post("/api/pdd/login-window")
def pdd_login_window() -> dict[str, Any]:
    subprocess.Popen([sys.executable, '-B', '-m', 'hguc_watch.pdd'], cwd=PROJECT_ROOT,
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return {"ok": True, "message": "拼多多登录窗口已打开，完成登录后关闭窗口"}


@app.post("/api/schedule/install")
def install_schedule() -> dict[str, Any]:
    settings = get_settings()
    try:
        message = install_weekly_tasks(
            settings.get("scan_weekday", "MON"),
            settings.get("scan_time", "03:00"),
            settings.get("dashboard_time", "08:00"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "message": message}
