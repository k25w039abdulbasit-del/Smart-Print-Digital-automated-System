"""Run blocking HTTP calls off the UI thread and deliver results back on it.

Design note: results travel through ONE long-lived `_Bridge` QObject that lives in the
GUI thread. A worker thread only ever calls `bridge.delivered.emit(...)`; Qt queues that
to the GUI thread, where the callback runs. (Per-task signal objects are fragile - they
can be destroyed while a worker is still emitting - so none are used.)
"""
from __future__ import annotations

import itertools
import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class _Bridge(QObject):
    delivered = Signal(int, object, object)      # task id, result, error

    def __init__(self) -> None:
        super().__init__()
        self._callbacks: dict[int, tuple[Optional[Callable], Optional[Callable]]] = {}
        self._lock = threading.Lock()
        self.delivered.connect(self._on_delivered)   # AutoConnection -> queued when emitted from workers

    def register(self, task_id: int, ok: Optional[Callable], fail: Optional[Callable]) -> None:
        with self._lock:
            self._callbacks[task_id] = (ok, fail)

    @Slot(int, object, object)
    def _on_delivered(self, task_id: int, result: object, error: object) -> None:
        with self._lock:
            ok, fail = self._callbacks.pop(task_id, (None, None))
        cb = fail if error is not None else ok
        if cb is not None:
            cb(error if error is not None else result)


class _Task(QRunnable):
    def __init__(self, task_id: int, fn: Callable, bridge: _Bridge):
        super().__init__()
        self.task_id, self.fn, self.bridge = task_id, fn, bridge

    def run(self) -> None:
        try:
            result, error = self.fn(), None
        except Exception as e:  # noqa: BLE001 - everything is reported to the UI
            result, error = None, e
        try:
            self.bridge.delivered.emit(self.task_id, result, error)
        except RuntimeError:             # application is shutting down; nobody is listening any more
            pass


_bridge: Optional[_Bridge] = None
_ids = itertools.count(1)


def run_async(fn: Callable, ok: Optional[Callable] = None, fail: Optional[Callable] = None) -> None:
    """Call from the GUI thread. `ok(result)` / `fail(exception)` run on the GUI thread."""
    global _bridge
    if _bridge is None:
        _bridge = _Bridge()              # created on the GUI thread -> its slot runs there
    task_id = next(_ids)
    _bridge.register(task_id, ok, fail)
    QThreadPool.globalInstance().start(_Task(task_id, fn, _bridge))
