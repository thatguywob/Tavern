# SPDX-License-Identifier: GPL-3.0-or-later

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from gi.repository import GLib

from tavern.backend_lifecycle import BackendLifecycle
from tavern.task_manager import TaskManager, TaskStatus


@pytest.fixture
def lifecycle(monkeypatch):
    backend = Mock()
    manager = TaskManager(backend)
    settings = Mock()
    settings.get_int.return_value = 3
    settings.get_boolean.return_value = True
    timer = Mock(return_value=42)
    remove = Mock()
    monkeypatch.setattr(GLib, 'timeout_add_seconds', timer)
    monkeypatch.setattr(GLib, 'source_remove', remove)
    controller = BackendLifecycle(backend, manager, settings)
    yield SimpleNamespace(controller=controller, backend=backend, manager=manager,
                          settings=settings, timer=timer, remove=remove)
    controller.stop()


def test_start_is_idempotent_and_refresh_can_be_requested_again(lifecycle):
    ctx = lifecycle
    ctx.backend.load_all_async.assert_not_called()
    ctx.controller.start()
    ctx.controller.start()
    ctx.timer.assert_called_once()
    assert ctx.timer.call_args.args[0] == 3 * 3600
    ctx.backend.load_all_async.assert_called_once_with()
    ctx.controller.refresh()
    assert ctx.backend.load_all_async.call_count == 2


@pytest.mark.parametrize('hours, expected', [(0, 24), (-1, 1), (168, 168)])
def test_interval_defaults_and_minimum(lifecycle, hours, expected):
    lifecycle.settings.get_int.return_value = hours
    lifecycle.controller.start()
    assert lifecycle.timer.call_args.args[0] == expected * 3600


def test_periodic_check_reads_current_enabled_setting_and_runs_on_worker(lifecycle, monkeypatch):
    thread = Mock()
    monkeypatch.setattr('tavern.backend_lifecycle.threading.Thread', thread)
    lifecycle.controller.start()
    tick = lifecycle.timer.call_args.args[1]
    lifecycle.settings.get_boolean.return_value = False
    assert tick() == GLib.SOURCE_CONTINUE
    thread.assert_not_called()
    lifecycle.settings.get_boolean.return_value = True
    assert tick() == GLib.SOURCE_CONTINUE
    thread.assert_called_once_with(target=lifecycle.backend._check_outdated, daemon=True)
    thread.return_value.start.assert_called_once_with()
    lifecycle.backend._check_outdated.assert_not_called()


@pytest.mark.parametrize('status', [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED])
@pytest.mark.parametrize('package', [None, object()])
def test_task_completion_refreshes_only_when_queue_drains(lifecycle, status, package):
    lifecycle.controller.start()
    task = SimpleNamespace(status=status, package=package)
    lifecycle.manager.active_count = 1
    lifecycle.manager.emit('task-finished', task)
    lifecycle.backend.refresh_installed_async.assert_not_called()
    lifecycle.manager.active_count = 0
    lifecycle.manager.emit('task-finished', task)
    lifecycle.backend.refresh_installed_async.assert_called_once_with()


def test_follow_up_queued_by_view_defers_refresh(lifecycle):
    lifecycle.controller.start()
    # A view can connect after start(), yet still run before the lifecycle
    # handler. This models queuing a reinstall after a tap-switch removal.
    lifecycle.manager.connect('task-finished',
                              lambda manager, task: setattr(manager, 'active_count', 1))
    lifecycle.manager.emit('task-finished', object())
    lifecycle.backend.refresh_installed_async.assert_not_called()


def test_stop_disconnects_and_restart_does_not_duplicate_handlers(lifecycle):
    ctx = lifecycle
    ctx.controller.stop()
    ctx.remove.assert_not_called()
    ctx.controller.start()
    ctx.controller.stop()
    ctx.controller.stop()
    ctx.remove.assert_called_once_with(42)
    ctx.manager.emit('task-finished', object())
    ctx.backend.refresh_installed_async.assert_not_called()
    ctx.controller.start()
    ctx.manager.emit('task-finished', object())
    ctx.backend.refresh_installed_async.assert_called_once_with()
