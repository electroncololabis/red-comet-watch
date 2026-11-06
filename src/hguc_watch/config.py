from __future__ import annotations

import os
from pathlib import Path


INSTALLED = os.environ.get("HGUC_WATCH_INSTALLED") == "1"
PROJECT_ROOT = Path(os.environ["HGUC_WATCH_HOME"]) if INSTALLED else Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "RedCometWatch" / "data" if INSTALLED else PROJECT_ROOT / "data"
DATA_DIR = Path(os.environ.get("HGUC_WATCH_DATA_DIR", str(DEFAULT_DATA_DIR))).resolve()
DB_PATH = DATA_DIR / "hguc_watch.db"
BROWSER_PROFILE_DIR = DATA_DIR / "browser-profile"
PDD_BROWSER_PROFILE_DIR = DATA_DIR / "pdd-browser-profile"
RUN_LOCK_PATH = DATA_DIR / "tracking.lock"
LOGIN_LOCK_PATHS = (DATA_DIR / "xianyu-login.lock", DATA_DIR / "pdd-login.lock")

APP_HOST = "127.0.0.1"
APP_PORT = int(os.environ.get("HGUC_WATCH_PORT", "8765"))
SCAN_TASK_NAME = "RedCometWatch_WeeklyScan" if INSTALLED else "HGUC闲鱼价格搜索"
DASHBOARD_TASK_NAME = "RedCometWatch_Dashboard" if INSTALLED else "HGUC价格战情室"
LEGACY_TASK_NAME = "HGUC闲鱼价格周报"


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
