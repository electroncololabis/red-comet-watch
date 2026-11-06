import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from filelock import FileLock

from hguc_watch import config, scheduler


def test_installed_paths_are_per_user_and_dev_paths_remain_unchanged(monkeypatch, tmp_path):
    with monkeypatch.context() as patch:
        patch.setenv('HGUC_WATCH_INSTALLED', '1')
        patch.setenv('HGUC_WATCH_HOME', str(tmp_path / '安装目录'))
        patch.setenv('LOCALAPPDATA', str(tmp_path / '用户目录'))
        patch.delenv('HGUC_WATCH_DATA_DIR', raising=False)
        importlib.reload(config)
        assert config.PROJECT_ROOT == tmp_path / '安装目录'
        assert config.DATA_DIR == tmp_path / '用户目录/RedCometWatch/data'
        assert config.SCAN_TASK_NAME == 'RedCometWatch_WeeklyScan'
    importlib.reload(config)
    assert not config.INSTALLED
    assert config.DATA_DIR == config.PROJECT_ROOT / 'data'


def test_installed_tasks_run_bundled_launcher_without_uv_or_old_task_deletion(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, 'INSTALLED', True)
    monkeypatch.setattr(scheduler, 'PROJECT_ROOT', tmp_path / '程序 空格')
    monkeypatch.setattr(scheduler, 'SCAN_TASK_NAME', 'RedCometWatch_WeeklyScan')
    monkeypatch.setattr(scheduler, 'DASHBOARD_TASK_NAME', 'RedCometWatch_Dashboard')
    def simulate_task_scheduler(command, **kwargs):
        if 'RED_COMET_TASK_NAME' in kwargs.get('env', {}):
            return SimpleNamespace(returncode=3, stdout='', stderr='')
        return SimpleNamespace(returncode=0, stdout='ok', stderr='')

    run = Mock(side_effect=simulate_task_scheduler)
    monkeypatch.setattr(scheduler.subprocess, 'run', run)
    scheduler.install_weekly_tasks('MON', '03:00', '08:00')
    creates = [call.args[0] for call in run.call_args_list if '/Create' in call.args[0]]
    assert len(creates) == 2
    actions = [call[call.index('/TR') + 1] for call in creates]
    assert 'RedCometWatch.exe" --scan' in actions[0]
    assert 'RedCometWatch.exe" --open' in actions[1]
    assert all('/F' not in command for command in creates)
    assert not any('/Delete' in call.args[0] for call in run.call_args_list)


def test_installed_task_collision_prevents_both_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, 'INSTALLED', True)
    monkeypatch.setattr(scheduler, 'PROJECT_ROOT', tmp_path)
    own = f'<Task><Actions><Exec><Command>{tmp_path / "RedCometWatch.exe"}</Command><Arguments>--scan</Arguments></Exec></Actions></Task>'
    foreign = own.replace('--scan', '--unrelated')
    run = Mock(side_effect=[Mock(returncode=0, stdout=xml) for xml in (own, foreign)])
    monkeypatch.setattr(scheduler.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='同名'):
        scheduler.install_weekly_tasks('MON', '03:00', '08:00')
    assert [call.kwargs['env']['RED_COMET_TASK_NAME'] for call in run.call_args_list] == [
        scheduler.SCAN_TASK_NAME, scheduler.DASHBOARD_TASK_NAME]


async def test_stop_requires_secret_and_refuses_foreground_or_scheduled_scan(monkeypatch, tmp_path):
    from hguc_watch import app as app_module
    monkeypatch.setattr(app_module, 'INSTALLED', True)
    monkeypatch.setattr(app_module, 'RUN_LOCK_PATH', tmp_path / 'scan.lock')
    monkeypatch.setenv('HGUC_WATCH_CONTROL_TOKEN', 'unit-test-secret')
    shutdown = Mock()
    monkeypatch.setattr(app_module.app.state, 'desktop_shutdown', shutdown, raising=False)
    monkeypatch.setattr(app_module, '_tracking_task', None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url='http://test') as client:
        assert (await client.post('/api/desktop/stop')).status_code == 403
        with FileLock(str(tmp_path / 'scan.lock')):
            assert (await client.post('/api/desktop/stop', headers={'X-Desktop-Token': 'unit-test-secret'})).status_code == 409
        monkeypatch.setattr(app_module, '_tracking_task', SimpleNamespace(done=lambda: False))
        assert (await client.post('/api/desktop/stop', headers={'X-Desktop-Token': 'unit-test-secret'})).status_code == 409
        shutdown.assert_not_called()
        monkeypatch.setattr(app_module, '_tracking_task', None)
        assert (await client.post('/api/desktop/stop', headers={'X-Desktop-Token': 'unit-test-secret'})).status_code == 200
        shutdown.assert_called_once()


def test_launcher_does_not_accept_a_different_data_directory(monkeypatch, tmp_path):
    from hguc_watch import desktop
    monkeypatch.setattr(desktop, 'DATA_DIR', tmp_path / 'mine')
    reply = Mock()
    reply.json.return_value = {'app': 'red-comet-watch', 'data_dir': str(tmp_path / 'other')}
    monkeypatch.setattr(desktop.httpx, 'get', Mock(return_value=reply))
    assert desktop._health() is None
    reply.json.return_value['data_dir'] = str(tmp_path / 'mine')
    assert desktop._health()
