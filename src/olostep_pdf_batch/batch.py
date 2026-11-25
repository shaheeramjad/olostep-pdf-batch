"""Step 2: submit one Olostep batch, save its id, and track progress."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from olostep import AsyncOlostep, Olostep_BaseError

from .config import MILESTONES, POLL_INTERVAL
from .events import RunListener
from .sources import PdfRecord
from .utils import atomic_write_text, format_duration

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = ("completed", "failed")
STATUS_RETRY_SECONDS = 2.0


@dataclass
class BatchState:
    """Persisted to ``batch.json`` so an interrupted run can resume."""

    batch_id: str
    total: int
    created_at: float  # epoch seconds when the batch was submitted
    collect_seconds: float = 0.0
    create_seconds: float = 0.0  # how long the create request took
    scrape_seconds: float | None = None  # set once the batch completes
    download_tail_seconds: float = 0.0  # downloading left after completion
    rescrape_seconds: float = 0.0
    rescraped: list[str] = field(default_factory=list)  # custom_ids fixed by /scrapes
    # seconds from submission until N% of PDFs were scraped, e.g. {"90": 61.2}
    milestones: dict[str, float] = field(default_factory=dict)
    # [elapsed_seconds, completed] samples, for the "PDFs over time" chart
    timeline: list[list[float]] = field(default_factory=list)

    def save(self, path: Path) -> None:
        """Write the state as JSON (atomically)."""
        atomic_write_text(path, json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> BatchState:
        """Read state written by :meth:`save`."""
        return cls(**json.loads(path.read_text(encoding="utf-8")))

    @property
    def elapsed(self) -> float:
        """Seconds since the batch was submitted."""
        return max(0.0, time.time() - self.created_at)


def milestone_line(total: int, percent: int, seconds: float) -> str:
    """Headline for the video, e.g. ``5,000 PDFs: 99% done in 7m 12s``."""
    return f"{total:,} PDFs: {percent}% done in {format_duration(seconds)}"


async def create_batch(client: AsyncOlostep, records: list[PdfRecord]) -> str:
    """Submit every PDF as one batch item (with its ``custom_id``)."""
    items = [{"url": r.pdf_url, "custom_id": r.custom_id} for r in records]
    logger.info("Submitting %s PDFs as one batch...", f"{len(items):,}")
    batch = await client.batches.create(items)
    logger.info("Batch created: %s", batch.id)
    return batch.id


async def track_batch(
    client: AsyncOlostep,
    state: BatchState,
    listener: RunListener,
    *,
    state_file: Path,
) -> float:
    """Poll the batch until it finishes. Returns the scrape time in seconds."""
    while True:
        status, completed = await _poll(client, state.batch_id)
        state.timeline.append([round(state.elapsed, 1), completed])
        listener.scrape_progress(completed, state.total, state.elapsed)
        if _record_milestones(state, completed, listener):
            state.save(state_file)

        if status in TERMINAL_STATUSES:
            if status == "failed":
                logger.warning("Batch %s ended as 'failed'", state.batch_id)
            _finish_milestones(state, listener)
            return state.elapsed
        await asyncio.sleep(POLL_INTERVAL)


async def _poll(client: AsyncOlostep, batch_id: str) -> tuple[str, int]:
    """Return ``(status, completed_urls)``; transient errors are retried."""
    while True:
        try:
            info = await client.batches.info(batch_id)
            return str(info.status), info.completed_urls
        except Olostep_BaseError as exc:
            logger.warning("Status check failed (%s), retrying...", exc)
            await asyncio.sleep(STATUS_RETRY_SECONDS)


def _record_milestones(
    state: BatchState, completed: int, listener: RunListener
) -> bool:
    """Record the first time each milestone is passed. True if any was new."""
    new = False
    for percent in MILESTONES:
        key = str(percent)
        if key in state.milestones or completed * 100 < percent * state.total:
            continue
        state.milestones[key] = round(state.elapsed, 2)
        listener.milestone(milestone_line(state.total, percent, state.elapsed))
        new = True
    return new


def _finish_milestones(state: BatchState, listener: RunListener) -> None:
    """Mark 100% when the batch ends, even if some PDFs failed to scrape."""
    if "100" not in state.milestones:
        state.milestones["100"] = round(state.elapsed, 2)
        listener.milestone(milestone_line(state.total, 100, state.elapsed))
