# backend_lifecycle.py - Backend loading and task-driven refresh coordination
# SPDX-License-Identifier: GPL-3.0-or-later

"""Coordinate backend work without depending on a window or page widget."""

import threading

from gi.repository import GLib

from .logging_util import get_logger

_log = get_logger('backend_lifecycle')


class BackendLifecycle:
    """Own the refresh timer and task-completion subscription.

    The caller supplies the shared backend, task manager, and settings, wires
    presentation callbacks, then calls start(). stop() releases this object's
    subscriptions; it does not cancel running package operations or stop a
    backend that other consumers may still be using.
    """

    def __init__(self, backend, task_manager, settings):
        self.backend = backend
        self.task_manager = task_manager
        self._settings = settings
        self._timer_id = None
        self._task_finished_id = None

    def start(self):
        """Begin loading and periodic checks once, after views are connected."""
        if self._task_finished_id is not None:
            return
        # Run after presentation handlers, which may enqueue follow-up work
        # (for example, reinstalling a package after switching its tap).
        self._task_finished_id = self.task_manager.connect_after(
            'task-finished', self._on_task_finished)
        interval_h = max(1, self._settings.get_int('outdated-check-interval-hours') or 24)
        self._timer_id = GLib.timeout_add_seconds(
            interval_h * 3600, self._periodic_outdated_check)
        self.refresh()

    def stop(self):
        """Release the timer and signal subscription; safe to call twice."""
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        if self._task_finished_id is not None:
            self.task_manager.disconnect(self._task_finished_id)
            self._task_finished_id = None

    def refresh(self):
        """Reload catalog and installed state through the async backend API."""
        self.backend.load_all_async()

    def _periodic_outdated_check(self):
        if self._settings.get_boolean('outdated-check-enabled'):
            threading.Thread(target=self.backend._check_outdated, daemon=True).start()
        return GLib.SOURCE_CONTINUE

    def _on_task_finished(self, manager, _task):
        # Refresh even after failures or cancellation: brew may have changed
        # some installed state before the operation stopped.
        if manager.active_count == 0:
            _log.info('All queued tasks finished; refreshing installed state')
            self.backend.refresh_installed_async()
