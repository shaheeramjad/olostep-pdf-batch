"""Run the async pipeline in a background thread so the page never freezes.

:class:`RunState` implements the pipeline's ``RunListener``: the pipeline
writes progress into it from the worker thread, and the page reads a
consistent snapshot once a second.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field, replace

from olostep_pdf_batch.config import RunSettings
from olostep_pdf_batch.pipeline import run_batch

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Snapshot:
    """What the page shows about the current (or last) run."""

    status: str = "idle"  # idle | running | done | error
    batch_id: str = ""
    submitted: int = 0
    completed: int = 0
    downloaded: int = 0
    failed: int = 0
    elapsed: float = 0.0
    milestones: tuple[str, ...] = ()
    error: str = ""


@dataclass
class RunState:
    """Thread-safe run progress, shared between the worker and the page."""

    _snapshot: Snapshot = field(default_factory=Snapshot)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _started: float = 0.0  # monotonic clock when the run started
    _offset: float = 0.0  # batch age when this process joined it (resume)

    # --- Read by the page ----------------------------------------------------

    def snapshot(self) -> Snapshot:
        with self._lock:
            if self._snapshot.status != "running":
                return self._snapshot
            elapsed = self._offset + time.monotonic() - self._started
            return replace(self._snapshot, elapsed=elapsed)

    @property
    def running(self) -> bool:
        return self.snapshot().status == "running"

    # --- RunListener, called from the worker thread --------------------------

    def batch_created(self, batch_id: str, total: int) -> None:
        self._update(batch_id=batch_id, submitted=total)

    def scrape_progress(self, completed: int, total: int, elapsed: float) -> None:
        with self._lock:
            if self._offset == 0.0:
                self._offset = elapsed - (time.monotonic() - self._started)
            self._snapshot = replace(self._snapshot, completed=completed)

    def download_progress(self, finished: int, failed: int) -> None:
        self._update(downloaded=finished - failed, failed=failed)

    def milestone(self, line: str) -> None:
        with self._lock:
            lines = (*self._snapshot.milestones, line)
            self._snapshot = replace(self._snapshot, milestones=lines)

    # --- Lifecycle -----------------------------------------------------------

    def start(self, settings: RunSettings, *, resume: bool) -> None:
        """Start the pipeline in a daemon thread (no-op if already running)."""
        with self._lock:
            if self._snapshot.status == "running":
                return
            self._snapshot = Snapshot(status="running")
            self._started = time.monotonic()
            self._offset = 0.0
        thread = threading.Thread(
            target=self._work, args=(settings, resume), daemon=True
        )
        thread.start()

    def _work(self, settings: RunSettings, resume: bool) -> None:
        try:
            asyncio.run(run_batch(settings, self, resume=resume))
        except Exception as exc:  # shown on the page instead of a traceback
            logger.exception("Run failed")
            self._finish("error", error=f"{type(exc).__name__}: {exc}")
        else:
            self._finish("done")

    def _finish(self, status: str, error: str = "") -> None:
        with self._lock:
            elapsed = self._offset + time.monotonic() - self._started
            self._snapshot = replace(
                self._snapshot, status=status, error=error, elapsed=elapsed
            )

    def _update(self, **changes: object) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, **changes)
