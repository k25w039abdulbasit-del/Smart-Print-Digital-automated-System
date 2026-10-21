"""Background print worker + janitor (runs inside the server process)."""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from sqlalchemy.orm.exc import StaleDataError

from .adapters.printer import PrintError
from .context import AppContext
from .services import jobs

log = logging.getLogger("smartprint.worker")


class PrintWorker:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_janitor = 0.0
        self.last_tick = 0.0
        self.printed = 0

    # -- one deterministic step; tests call this directly ------------------
    def run_once(self) -> bool:
        try:
            with self.ctx.db.session() as db:
                claimed = jobs.claim_next(db, self.ctx)
                if claimed is None:
                    return False
                job, req = claimed
                job_id = job.id
        except StaleDataError:
            # Another worker claimed the job first (optimistic lock). SQLite has no row locks;
            # PostgreSQL never gets here thanks to FOR UPDATE SKIP LOCKED. Either way: no double print.
            return False
        # The (possibly slow) hardware call happens OUTSIDE any DB transaction.
        ref, error = None, None
        try:
            ref = self.ctx.printer.submit(req)
        except PrintError as e:
            error = e
        except Exception as e:  # noqa: BLE001 - unknown failure: we cannot know if paper came out
            error = PrintError(f"unexpected printer error: {e}", retryable=False)
        with self.ctx.db.session() as db:
            jobs.finish_print(db, self.ctx, job_id, ref, error)
        if error is None:
            self.printed += 1
        return True

    def run_janitor(self) -> dict[str, int]:
        with self.ctx.db.session() as db:
            return jobs.recover(db, self.ctx)

    def _loop(self) -> None:
        s = self.ctx.settings
        while not self._stop.is_set():
            try:
                self.last_tick = time.time()
                if time.time() - self._last_janitor >= s.janitor_interval_s:
                    self._last_janitor = time.time()
                    self.run_janitor()
                if not self.run_once():
                    self._stop.wait(s.worker_poll_s)
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                self._stop.wait(1.0)

    def start(self) -> None:
        self.run_janitor()           # startup recovery: resolve anything the last run left hanging
        self._thread = threading.Thread(target=self._loop, name="print-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and time.time() - self.last_tick < 30)
