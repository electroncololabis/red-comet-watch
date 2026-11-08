"""Installed Windows entry point, using a private real Python runtime."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
import ctypes
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import time
import traceback
import webbrowser

import httpx
from filelock import FileLock, Timeout

from .config import (
    APP_HOST, APP_PORT, BROWSER_PROFILE_DIR, DATA_DIR, DB_PATH, DEFAULT_DATA_DIR,
    INSTALLED, LOGIN_LOCK_PATHS, PDD_BROWSER_PROFILE_DIR, PROJECT_ROOT, RUN_LOCK_PATH,
    ensure_data_dirs,
)

URL = f"http://{APP_HOST}:{APP_PORT}"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _health() -> dict | None:
    try:
        response = httpx.get(URL + "/api/health", timeout=1, trust_env=False)
        response.raise_for_status()
        info = response.json()
        if info.get("app") == "red-comet-watch" and Path(info.get("data_dir", "")).resolve() == DATA_DIR:
            return info
    except (httpx.HTTPError, ValueError):
        pass
    return None


def open_dashboard(open_browser: bool = True) -> None:
    ensure_data_dirs()
    with FileLock(str(DATA_DIR / "launch.lock"), timeout=40):
        if not _health():
            with socket.socket() as probe:
                if probe.connect_ex((APP_HOST, APP_PORT)) == 0:
                    raise RuntimeError(f"端口 {APP_PORT} 已被其他程序或旧版战情室占用，请先退出旧版再打开安装版。")
            subprocess.Popen([sys.executable, "-I", "-B", "-m", "hguc_watch.desktop", "--serve"],
                             cwd=PROJECT_ROOT, creationflags=NO_WINDOW)
            for _ in range(60):
                if _health():
                    break
                time.sleep(.5)
            else:
                raise RuntimeError(f"后台启动失败，请查看 {DATA_DIR / 'desktop.log'}")
    if open_browser:
        chrome = next((base / "Google/Chrome/Application/chrome.exe" for base in (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
            Path(os.environ.get("LOCALAPPDATA", str(Path.home()))),
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        ) if (base / "Google/Chrome/Application/chrome.exe").is_file()), None)
        if chrome:
            subprocess.Popen([str(chrome), URL])
        else:
            webbrowser.open(URL)


def serve() -> None:
    import uvicorn
    from .app import app
    with FileLock(str(DATA_DIR / "server.lock"), timeout=0):
        token = secrets.token_urlsafe(32)
        os.environ["HGUC_WATCH_CONTROL_TOKEN"] = token
        (DATA_DIR / "desktop-control.json").write_text(json.dumps({"token": token}), encoding="utf-8")
        server = uvicorn.Server(uvicorn.Config(app, host=APP_HOST, port=APP_PORT, log_level="info", proxy_headers=False))
        app.state.desktop_shutdown = lambda: setattr(server, "should_exit", True)
        server.run()


def stop() -> None:
    if not _health():
        if not DATA_DIR.exists():
            return
        try:
            with FileLock(str(RUN_LOCK_PATH), timeout=0):
                pass
        except Timeout as exc:
            raise RuntimeError("自动搜索正在运行，请等待完成后再退出。") from exc
        return
    control = json.loads((DATA_DIR / "desktop-control.json").read_text(encoding="utf-8"))
    response = httpx.post(URL + "/api/desktop/stop", headers={"X-Desktop-Token": control["token"]},
                          timeout=10, trust_env=False)
    if response.status_code != 200:
        raise RuntimeError(response.json().get("detail", "后台暂时无法退出"))
    for _ in range(30):
        if not _health():
            return
        time.sleep(.5)
    raise RuntimeError("后台尚未完全退出，请稍后重试")


def remove_tasks() -> None:
    from .config import SCAN_TASK_NAME, DASHBOARD_TASK_NAME
    from .scheduler import installed_task_owned

    for name, mode in ((SCAN_TASK_NAME, "--scan"), (DASHBOARD_TASK_NAME, "--open")):
        if installed_task_owned(name, mode):
            subprocess.run(["schtasks.exe", "/Delete", "/TN", name, "/F"],
                           capture_output=True, creationflags=NO_WINDOW, check=True, timeout=15)


def _owned_entry(path: Path, directory: bool) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    matches_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (not matches_type or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or path.resolve(strict=True) != path):
        raise OSError(f"卸载遇到重定向或类型异常的路径：{path}")
    return True


def _remove_legacy_bytecode() -> None:
    runtime = PROJECT_ROOT.resolve(strict=True) / "runtime"
    if not _owned_entry(runtime, directory=True):
        return
    for root, dirs, _ in os.walk(runtime):
        parent = Path(root)
        dirs[:] = [name for name in dirs if not (
            (parent / name).lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)]
        if parent.name != "__pycache__":
            continue
        for path in parent.glob("*.cpython-312.pyc"):
            source = parent.parent / (path.name.removesuffix(".cpython-312.pyc") + ".py")
            if source.is_file() and _owned_entry(path, directory=False):
                path.unlink()
        if not any(parent.iterdir()):
            parent.rmdir()


def prepare_uninstall(purge: bool) -> None:
    if not INSTALLED:
        raise RuntimeError("只能清理已安装版本。")
    if purge:
        # Resolve the Windows user directory first, then check the two app-owned path components.
        app_data = DEFAULT_DATA_DIR.parent.parent.resolve(strict=True) / "RedCometWatch"
        expected_data = app_data / "data"
        if DATA_DIR != expected_data:
            raise OSError("自定义或重定向的数据目录需要手动清理。")
        if _owned_entry(app_data, directory=True):
            _owned_entry(expected_data, directory=True)

    stop()
    if DATA_DIR.exists():
        locks = (
            RUN_LOCK_PATH, DATA_DIR / "launch.lock", DATA_DIR / "server.lock",
            *LOGIN_LOCK_PATHS,
            Path(str(BROWSER_PROFILE_DIR) + '.lock'),
            Path(str(PDD_BROWSER_PROFILE_DIR) + '.lock'),
        )
        with ExitStack() as held:
            for path in locks:
                held.enter_context(FileLock(str(path), timeout=0))
            if purge:
                profiles = [path for path in (BROWSER_PROFILE_DIR, PDD_BROWSER_PROFILE_DIR)
                            if _owned_entry(path, directory=True)]
                files = [path for path in (
                    DB_PATH, DB_PATH.with_name("hguc_watch.db-wal"), DB_PATH.with_name("hguc_watch.db-shm"),
                    DB_PATH.with_name("hguc_watch.db-journal"), DATA_DIR / "desktop.log",
                    DATA_DIR / "scan-history.log", DATA_DIR / "desktop-control.json",
                ) if _owned_entry(path, directory=False)]
            remove_tasks()
            _remove_legacy_bytecode()
            if purge:
                for profile in profiles:
                    # On Windows, rmtree removes junctions within the profile without traversing them.
                    shutil.rmtree(profile)
                for path in files:
                    path.unlink()
        if purge and not any(DATA_DIR.iterdir()):
            DATA_DIR.rmdir()
            if not any(app_data.iterdir()):
                app_data.rmdir()
    else:
        remove_tasks()
        _remove_legacy_bytecode()


async def self_test(report: Path) -> None:
    from .db import init_db, list_watch_items
    from .xianyu import launch_context
    from . import pdd
    init_db()
    results = []
    for label, launcher in (("xianyu", launch_context), ("pdd", pdd.launch_context)):
        driver, context = await launcher(headless=True)
        try:
            page = await context.new_page()
            await page.set_content("<title>package-ok</title><p>offline browser test</p>")
            assert await page.title() == "package-ok"
            await page.goto("chrome://version")
            assert "--no-sandbox" not in await page.locator("#command_line").inner_text()
            await page.goto("chrome://sandbox")
            await page.get_by_role("cell", name="Renderer", exact=True).first.wait_for(timeout=10_000)
            rows = await page.locator("table tr").all_inner_texts()
            renderers = [row.split("\t") for row in rows if row.split("\t")[1:2] == ["Renderer"]]
            assert renderers and all(row[3:5] == ["Renderer", "Lockdown"] for row in renderers)
            results.append(label)
        finally:
            await context.close()
            await driver.stop()
    report.write_text(json.dumps({"ok": True, "python": sys.executable, "data": str(DATA_DIR),
                                 "items": len(list_watch_items()), "sandboxed_browsers": results}, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    for flag in ("open", "serve", "scan", "stop", "remove-tasks", "data-folder", "uninstall"):
        modes.add_argument("--" + flag, action="store_true")
    modes.add_argument("--self-test", type=Path)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--purge-data", action="store_true")
    args = parser.parse_args()
    if args.uninstall or args.remove_tasks:
        try:
            if args.uninstall:
                prepare_uninstall(args.purge_data)
            else:
                remove_tasks()
            return 0
        except Timeout:
            return 2
        except Exception:
            return 1
    ensure_data_dirs()
    with (DATA_DIR / "desktop.log").open("a", encoding="utf-8", buffering=1) as log:
        original_streams = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = log
        try:
            if args.serve:
                serve()
            elif args.scan:
                from .runner import main as scan
                return scan()
            elif args.stop:
                stop()
            elif args.data_folder:
                os.startfile(DATA_DIR)
            elif args.self_test:
                asyncio.run(self_test(args.self_test))
            else:
                open_dashboard(not args.no_browser)
            return 0
        except Exception as exc:
            traceback.print_exc()
            if not (args.serve or args.self_test or args.no_browser or args.scan):
                ctypes.windll.user32.MessageBoxW(0, str(exc), "赤色彗星补给战情室", 0x10)
            return 1
        finally:
            sys.stdout, sys.stderr = original_streams


if __name__ == "__main__":
    raise SystemExit(main())
