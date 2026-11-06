from __future__ import annotations

import os
import subprocess
from pathlib import Path
from xml.etree import ElementTree

from .config import (
    DASHBOARD_TASK_NAME,
    LEGACY_TASK_NAME,
    PROJECT_ROOT,
    SCAN_TASK_NAME,
    INSTALLED,
)


WEEKDAYS = {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}


def _validate_time(value: str) -> None:
    if len(value) != 5 or value[2] != ":":
        raise ValueError("时间必须是 HH:MM")
    hour, minute = (int(part) for part in value.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("时间超出范围")


def installed_task_owned(name: str, mode: str) -> bool | None:
    """True for our exact action, False for a foreign task, None if not found."""
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    # Redirected schtasks /XML may use an ANSI code page even though its XML declares UTF-16.
    command = (
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        "$name=$env:RED_COMET_TASK_NAME;"
        "$task=Get-ScheduledTask -TaskPath '\\' -ErrorAction Stop | Where-Object TaskName -eq $name;"
        "if ($null -eq $task) {exit 3};"
        "Export-ScheduledTask -TaskPath '\\' -TaskName $name -ErrorAction Stop"
    )
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
                            env={**os.environ, "RED_COMET_TASK_NAME": name},
                            capture_output=True, encoding="utf-8", timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode == 3:
        return None
    result.check_returncode()
    try:
        root = ElementTree.fromstring(result.stdout)
    except ElementTree.ParseError:
        return False
    actions = root.findall("./{*}Actions/*")
    if len(actions) != 1 or actions[0].tag.rsplit("}", 1)[-1] != "Exec":
        return False
    command = Path(actions[0].findtext("{*}Command", "").strip('"'))
    arguments = actions[0].findtext("{*}Arguments", "").strip()
    return (command.is_absolute() and command.resolve() == (PROJECT_ROOT / "RedCometWatch.exe").resolve()
            and arguments == mode)


def _create_task(name: str, script: Path, weekday: str, run_time: str, limit: str, *, replace: bool) -> str:
    action = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{script}"'
    if INSTALLED:
        mode = "--scan" if name == SCAN_TASK_NAME else "--open"
        action = subprocess.list2cmdline([str(PROJECT_ROOT / "RedCometWatch.exe"), mode])
    command = [
        "schtasks.exe", "/Create", "/TN", name, "/TR", action,
        "/SC", "WEEKLY", "/D", weekday, "/ST", run_time,
        "/RL", "LIMITED", "/IT",
    ]
    if replace:
        command.append("/F")
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="gbk", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or f"创建任务 {name} 失败").strip())

    settings = [
        "powershell.exe", "-NoProfile", "-Command",
        (
            f"$t=Get-ScheduledTask -TaskPath '\\' -TaskName '{name}' -ErrorAction Stop;"
            "$t.Settings.StartWhenAvailable=$true;"
            f"$t.Settings.ExecutionTimeLimit='{limit}';"
            "$t | Set-ScheduledTask | Out-Null"
        ),
    ]
    subprocess.run(settings, capture_output=True, text=True, timeout=30,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return (completed.stdout or f"{name} 已安装").strip()


def install_weekly_tasks(weekday: str, scan_time: str, dashboard_time: str) -> str:
    weekday = weekday.upper()
    if weekday not in WEEKDAYS:
        raise ValueError("无效的星期设置")
    _validate_time(scan_time)
    _validate_time(dashboard_time)

    scripts = Path(PROJECT_ROOT, "scripts")
    replace_scan = replace_dashboard = True
    if INSTALLED:
        replace_scan = installed_task_owned(SCAN_TASK_NAME, "--scan")
        replace_dashboard = installed_task_owned(DASHBOARD_TASK_NAME, "--open")
        if replace_scan is False or replace_dashboard is False:
            raise RuntimeError("发现同名的其他计划任务，请在任务计划程序中核对后再部署。")
    messages = [
        _create_task(SCAN_TASK_NAME, scripts / "run-weekly.ps1", weekday, scan_time, "PT2H",
                     replace=bool(replace_scan)),
        _create_task(
            DASHBOARD_TASK_NAME,
            scripts / "open-dashboard.ps1",
            weekday,
            dashboard_time,
            "PT12H",
            replace=bool(replace_dashboard),
        ),
    ]

    # Remove the former Friday email-report task during migration.
    if not INSTALLED:
        subprocess.run(
            ["schtasks.exe", "/Delete", "/TN", LEGACY_TASK_NAME, "/F"],
            capture_output=True,
            text=True,
            encoding="gbk",
            errors="replace",
        )
    return "\n".join(messages)


def _query_task(name: str) -> tuple[bool, str]:
    completed = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", name, "/FO", "LIST", "/V"],
        capture_output=True,
        text=True,
        encoding="gbk",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return completed.returncode == 0, (completed.stdout if completed.returncode == 0 else "").strip()


def task_status() -> dict[str, str | bool]:
    scan_installed, scan_detail = _query_task(SCAN_TASK_NAME)
    dashboard_installed, dashboard_detail = _query_task(DASHBOARD_TASK_NAME)
    return {
        "installed": scan_installed and dashboard_installed,
        "scan_installed": scan_installed,
        "dashboard_installed": dashboard_installed,
        "detail": "\n\n".join(detail for detail in (scan_detail, dashboard_detail) if detail)
        or "尚未安装周一自动任务",
    }
