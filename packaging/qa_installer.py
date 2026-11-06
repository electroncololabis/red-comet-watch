"""Exercise fresh installation, reinstall or upgrade, and both uninstall modes."""
import argparse
import asyncio
import csv
from contextlib import contextmanager
import ctypes
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import time
import winreg

import httpx
from filelock import FileLock
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / 'build/windows-latest.json').read_text(encoding='utf-8'))
QA = Path(MANIFEST['stage']) / '隔离安装验证 空格'
SETUP = Path(MANIFEST['setup'])
APP_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{D62E7A61-01CC-45D6-9E50-716082FBB761}_is1'
URL = 'http://127.0.0.1:8767'


def run(args, env=None, *, success=True):
    result = subprocess.run([str(arg) for arg in args], env=env, cwd=QA, timeout=90,
                            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    assert (result.returncode == 0) == success, (args, result.returncode, result.stdout, result.stderr)
    return result


def registry_values(root, key, view=0):
    try:
        with winreg.OpenKey(root, key, 0, winreg.KEY_READ | view) as handle:
            return sorted(winreg.EnumValue(handle, index) for index in range(winreg.QueryInfoKey(handle)[1]))
    except FileNotFoundError:
        return []


def windows_settings_snapshot():
    user = winreg.HKEY_CURRENT_USER
    machine = winreg.HKEY_LOCAL_MACHINE
    keys = [
        (user, r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice', 0),
        (user, r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice', 0),
        (user, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings', 0),
        (user, r'Environment', 0),
        (machine, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 0),
        (machine, r'SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\FirewallRules', 0),
    ]
    keys += [(root, rf'Software\Microsoft\Windows\CurrentVersion\{name}', view)
             for root in (user, machine) for name in ('Run', 'RunOnce')
             for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY)]
    tasks = run(['schtasks.exe', '/Query', '/FO', 'CSV', '/NH']).stdout.decode('mbcs', errors='replace')
    startup = Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/Startup'
    # A silent isolated install must leave the user's existing desktop shortcut alone.
    desktop = ctypes.create_unicode_buffer(32768)
    assert ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, desktop) == 0
    link = Path(desktop.value) / '赤色彗星补给战情室.lnk'
    return {
        'registry': [registry_values(*key) for key in keys],
        'scheduled_tasks': sorted(row[0] for row in csv.reader(tasks.splitlines()) if len(row) >= 3),
        'startup': sorted(path.name for path in startup.iterdir()) if startup.exists() else [],
        'desktop_link': hashlib.sha256(link.read_bytes()).hexdigest() if link.exists() else None,
    }


def setup_args(setup, directory, log):
    return [setup, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS', '/TASKS=',
            '/DIR=' + str(directory), '/LOG=' + str(log)]


def registration(directory):
    values = {name: value for name, value, _ in registry_values(winreg.HKEY_CURRENT_USER, APP_KEY)}
    assert Path(values['InstallLocation']).resolve() == directory.resolve()
    return values


def uninstall(directory, env, log, *, purge=False, success=True):
    command = Path(registration(directory)['UninstallString'].strip('"')).resolve()
    assert command.parent == directory.resolve() and command.name.startswith('unins')
    args = [command, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/LOG=' + str(log)]
    if purge:
        args.append('/PURGEDATA')
    run(args, env, success=success)
    if success:
        for _ in range(30):
            if not directory.exists() or not list(directory.iterdir()):
                break
            # Inno's uninstaller removes its own executable after its main process exits.
            time.sleep(.2)
        else:
            raise AssertionError(list(directory.iterdir()))
        assert not registry_values(winreg.HKEY_CURRENT_USER, APP_KEY)
    else:
        registration(directory)
        assert (directory / 'RedCometWatch.exe').exists()


def local_env(data):
    windows = os.environ['WINDIR']
    return {**os.environ, 'HGUC_WATCH_DATA_DIR': str(data), 'HGUC_WATCH_PORT': '8767',
            'PATH': windows + '\\System32;' + windows,
            'PYTHONPATH': 'X:\\no-source-available', 'PYTHONHOME': 'X:\\no-python-available'}


@contextmanager
def test_uninstall_tasks(exe):
    owned, foreign = 'RedCometWatch_WeeklyScan', 'RedCometWatch_Dashboard'
    actions = ((owned, subprocess.list2cmdline([str(exe), '--scan'])),
               (foreign, subprocess.list2cmdline([str(exe), '--stop'])))
    scheduled = lambda: set(windows_settings_snapshot()['scheduled_tasks'])
    assert not {f'\\{name}' for name, _ in actions} & scheduled(), 'Existing tasks must not be changed.'
    tomorrow = ('MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN')[(date.today() + timedelta(days=1)).weekday()]
    created = []
    try:
        for name, action in actions:
            run(['schtasks.exe', '/Create', '/TN', name, '/TR', action, '/SC', 'WEEKLY',
                 '/D', tomorrow, '/ST', '23:59', '/RL', 'LIMITED', '/IT'])
            created.append(name)
            run(['schtasks.exe', '/Change', '/TN', name, '/DISABLE'])
        yield
        after = scheduled()
        assert f'\\{owned}' not in after and f'\\{foreign}' in after
    finally:
        remaining = scheduled()
        for name in created:
            if f'\\{name}' in remaining:
                run(['schtasks.exe', '/Delete', '/TN', name, '/F'])


def api(method, route, **kwargs):
    return httpx.request(method, URL + route, trust_env=False, timeout=10, **kwargs)


def seed_user_data():
    item = {'name': '测试 MG 主天使', 'search_query': 'MG 主天使', 'msrp_jpy': 5500}
    api('POST', '/api/items', json=item).raise_for_status()
    settings = api('GET', '/api/settings').json()
    settings.update(scan_time='07:41', destination_city='测试保留城市', max_results=19)
    api('PUT', '/api/settings', json=settings,
        headers={'Origin': URL, 'Sec-Fetch-Site': 'same-origin'}).raise_for_status()
    return settings


async def check_interface():
    from hguc_watch.xianyu import _installed_browser

    errors = []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True, chromium_sandbox=True,
                                               executable_path=_installed_browser().executable_path)
        page = await browser.new_page(viewport={'width': 1920, 'height': 1200})
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(URL, wait_until='networkidle')
        await page.wait_for_selector('.model-card')
        assert await page.locator('.model-card').count() == 8
        assert not await page.locator('#runButton').is_disabled()
        for theme in ('zaku', 'haman', 'federation', 'murasame'):
            await page.locator('[data-view="themes"]').click()
            await page.select_option('#skinSelect', theme)
            await page.locator('[data-view="watch"]').click()
            assert await page.locator('html').get_attribute('data-skin') == theme
        for asset in ('terminal.svg', 'radar.svg'):
            assert (await page.request.get(f'{URL}/static/assets/{asset}')).ok
        await page.screenshot(path=str(QA / 'installed-dashboard.png'))
        await browser.close()
    assert not errors, errors


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--previous-setup', type=Path, default=SETUP)
    parser.add_argument('--previous-version', default=MANIFEST['version'])
    args = parser.parse_args()
    QA.mkdir(parents=True, exist_ok=False)
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            assert not registry_values(root, APP_KEY, view), 'A real installation is registered. Do not replace it.'
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 8767))
    original_windows = windows_settings_snapshot()

    occupied = QA / '无关目录'
    occupied.mkdir()
    sentinel = occupied / '原有文件.txt'
    sentinel.write_bytes(b'existing files must survive')
    run(setup_args(SETUP, occupied, QA / 'rejected-directory.log'), local_env(QA / 'unused'), success=False)
    assert list(occupied.iterdir()) == [sentinel] and sentinel.read_bytes() == b'existing files must survive'
    assert not registry_values(winreg.HKEY_CURRENT_USER, APP_KEY)

    fresh = QA / '程序'
    data = QA / '用户数据'
    env = local_env(data)
    exe = fresh / 'RedCometWatch.exe'
    run(setup_args(SETUP, fresh, QA / 'install.log'), env)
    try:
        assert not data.exists(), 'Installation should leave first launch to the user.'
        assert registration(fresh)['DisplayVersion'] == MANIFEST['version']
        assert 'Administrative install mode: No' in (QA / 'install.log').read_text(encoding='utf-8-sig')
        run([exe, '--self-test', QA / 'self-test.json'], env)
        report = json.loads((QA / 'self-test.json').read_text(encoding='utf-8'))
        assert report['ok'] and report['items'] == 7 and Path(report['python']).is_relative_to(fresh)
        assert report['sandboxed_browsers'] == ['xianyu', 'pdd']
        run([exe, '--no-browser'], env)
        first = api('GET', '/api/dashboard').json()
        defaults = api('GET', '/api/settings').json()
        assert not defaults['destination_province'] and not defaults['destination_city']
        assert len(first['items']) == 7 and not first['blocked_listings'] and not first['runs']
        assert first['fx']['rate'] is None
        assert api('GET', '/api/health', headers={'Host': 'other.example'}).status_code == 400
        assert api('POST', '/api/items', headers={'Origin': 'https://other.example'},
                   json={'name': 'blocked', 'search_query': 'blocked'}).status_code == 403
        assert api('POST', '/api/items', headers={'Sec-Fetch-Site': 'cross-site'},
                   json={'name': 'blocked', 'search_query': 'blocked'}).status_code == 403
        fresh_settings = seed_user_data()
        run([exe, '--no-browser'], env)
        assert len(api('GET', '/api/dashboard').json()['items']) == 8
        await check_interface()
        assert windows_settings_snapshot() == original_windows
    finally:
        if exe.exists():
            uninstall(fresh, env, QA / 'uninstall-fresh.log')
    with sqlite3.connect(data / 'hguc_watch.db') as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT value FROM settings WHERE key="scan_time"').fetchone()[0] == fresh_settings['scan_time']
    assert all((data / name / 'Local State').exists() for name in ('browser-profile', 'pdd-browser-profile'))

    upgraded = QA / '旧版升级目录'
    local_app_data = QA / '模拟本地用户'
    local_app_data.mkdir()
    outside = local_app_data / '普通浏览器数据.txt'
    outside.write_text('keep', encoding='utf-8')
    old_data = local_app_data / 'RedCometWatch' / 'data'
    old_env = local_env(old_data)
    old_env['LOCALAPPDATA'] = str(local_app_data)
    exe = upgraded / 'RedCometWatch.exe'
    run(setup_args(args.previous_setup.resolve(), upgraded, QA / 'install-old.log'), old_env)
    try:
        run([exe, '--no-browser'], old_env)
        assert api('GET', '/api/health').json()['version'] == args.previous_version
        settings = seed_user_data()
        run([exe, '--stop', '--no-browser'], old_env)
        with FileLock(str(old_data / 'tracking.lock'), timeout=0):
            run(setup_args(SETUP, upgraded, QA / 'rejected-active-scan.log'), old_env, success=False)
            assert registration(upgraded)['DisplayVersion'] == args.previous_version
        run([exe, '--no-browser'], old_env)
        run(setup_args(SETUP, upgraded, QA / 'upgrade.log'), old_env)
        assert registration(upgraded)['DisplayVersion'] == MANIFEST['version']
        run([exe, '--no-browser'], old_env)
        assert api('GET', '/api/health').json()['version'] == MANIFEST['version']
        assert api('GET', '/api/settings').json() == settings
        assert len(api('GET', '/api/dashboard').json()['items']) == 8
        run([exe, '--stop', '--no-browser'], old_env)
        run([exe, '--self-test', QA / 'purge-self-test.json'], old_env)

        migration_db = data / 'hguc_watch.db'
        migration_data = QA / '迁移验证'
        migration_data.mkdir()
        shutil.copy2(migration_db, migration_data / 'hguc_watch.db')
        with sqlite3.connect(migration_db) as db:
            expected = db.execute('SELECT COUNT(*) FROM watch_items').fetchone()[0]
        migration_env = local_env(migration_data)
        run([exe, '--no-browser'], migration_env)
        try:
            assert len(api('GET', '/api/dashboard').json()['items']) == expected
        finally:
            run([exe, '--stop', '--no-browser'], migration_env)
        with FileLock(str(old_data / 'pdd-login.lock'), timeout=0):
            uninstall(upgraded, old_env, QA / 'uninstall-blocked-login.log', purge=True, success=False)
            assert (old_data / 'hguc_watch.db').is_file()
        foreign_env = local_env(migration_data)
        uninstall(upgraded, foreign_env, QA / 'uninstall-blocked-custom-dir.log', purge=True, success=False)
        assert (migration_data / 'hguc_watch.db').is_file()
        with test_uninstall_tasks(exe):
            uninstall(upgraded, old_env, QA / 'uninstall-purge.log', purge=True)
    finally:
        if exe.exists():
            uninstall(upgraded, old_env, QA / 'uninstall-upgraded.log')
    assert not old_data.parent.exists() and outside.read_text(encoding='utf-8') == 'keep'
    assert windows_settings_snapshot() == original_windows
    report.update(version=MANIFEST['version'], fresh_install=True, upgrade_or_reinstall_from=args.previous_version,
                  foreign_directory_refused=True, active_scan_upgrade_refused=True,
                  uninstall_preserves_data=True, uninstall_purges_data=True, active_login_blocks_uninstall=True,
                  custom_data_purge_refused=True, outside_files_preserved=True, owned_task_removed=True,
                  foreign_task_preserved=True,
                  neutral_default_region=True, settings_preserved=True, windows_settings_unchanged=True,
                  local_request_guards=True, four_color_schemes_verified=True,
                  migrated_items=expected, ui_errors=[])
    (QA / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


asyncio.run(main())
