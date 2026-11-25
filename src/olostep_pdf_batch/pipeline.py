"""The pipeline in one place: collect -> batch -> stream download -> verify.

Both the CLI and the Streamlit app call these functions, so there is exactly
one implementation of every step.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

from olostep import AsyncOlostep, RetryStrategy

from .batch import BatchState, create_batch, milestone_line, track_batch
from .config import (
    COLLECT_META,
    DOWNLOAD_RETRIES,
    FEED_INTERVAL,
    MAX_CONCURRENCY,
    RESCRAPE_CONCURRENCY,
    SHUFFLE_SEED,
    URLS_CSV,
    Paths,
    RunSettings,
)
from .download import (
    PLACEHOLDER_REASON,
    Downloader,
    DownloadResult,
    feed_downloads,
    list_items_with_retry,
    rescrape,
)
from .events import RunListener
from .report import Summary, Timings, load_known_errors, summarize, write_reports
from .sources import PdfRecord, finalize, load_records, save_records
from .sources.file import load_file, parse_text
from .sources.govuk import collect_govuk
from .utils import Timer, atomic_write_text
from .verify import verify

logger = logging.getLogger(__name__)

SOURCES = ("govuk", "file")
BATCH_FAILED_REASON = "scrape failed in batch (the API gives no reason)"


# --- Collect -----------------------------------------------------------------


async def collect(
    source: str,
    *,
    limit: int,
    input_path: Path | None = None,
    text: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[PdfRecord]:
    """Collect PDF URLs from a source and save them to ``data/pdf_urls.csv``.

    Args:
        source: ``"govuk"`` or ``"file"``.
        limit: Maximum number of PDFs to keep.
        input_path: CSV or text file, for ``source="file"``.
        text: Pasted CSV or one URL per line, for ``source="file"``.
        on_progress: Called with ``(found, target)`` while collecting.
    """
    with Timer() as timer:
        if source == "govuk":
            raw = await collect_govuk(limit, seed=SHUFFLE_SEED, on_progress=on_progress)
        elif source == "file" and input_path is not None:
            raw = load_file(input_path)
        elif source == "file" and text is not None:
            raw = parse_text(text)
        else:
            raise ValueError(f"unknown source {source!r} or missing input")
        records = finalize(raw, limit=limit, seed=SHUFFLE_SEED)
    save_records(URLS_CSV, records)
    meta = {"source": source, "count": len(records), "seconds": round(timer.elapsed, 2)}
    atomic_write_text(COLLECT_META, json.dumps(meta, indent=2) + "\n")
    logger.info("Saved %s PDF URLs to %s", f"{len(records):,}", URLS_CSV)
    return records


# --- Run / resume ------------------------------------------------------------


async def run_batch(
    settings: RunSettings, listener: RunListener, *, resume: bool = False
) -> Summary:
    """Submit (or resume) the batch, stream downloads, then verify and report."""
    paths = settings.paths
    retry = RetryStrategy(max_retries=2, initial_delay=1.0)
    async with AsyncOlostep(api_key=settings.api_key, retry_strategy=retry) as client:
        if resume:
            state, records = load_previous_run(paths)
        else:
            records = load_records(URLS_CSV)
            state = await _submit(client, records, paths)
        listener.batch_created(state.batch_id, state.total)
        for percent, seconds in state.milestones.items():  # replayed on resume
            listener.milestone(milestone_line(state.total, int(percent), seconds))
        errors = await _scrape_and_download(client, settings, state, records, listener)
        if settings.rescrape and errors:
            errors = await _rescrape_failures(client, state, records, errors, paths)
    return _verify_and_report(paths, state, records, errors)


def report_from_disk(paths: Paths) -> Summary:
    """Re-verify a finished run from the files on disk (no API calls)."""
    state, records = load_previous_run(paths)
    return _verify_and_report(paths, state, records, load_known_errors(paths))


def load_previous_run(paths: Paths) -> tuple[BatchState, list[PdfRecord]]:
    """Load ``batch.json`` and ``submitted.csv`` from an earlier run."""
    if not paths.state_file.exists():
        raise FileNotFoundError(f"No previous run: {paths.state_file} not found")
    state = BatchState.load(paths.state_file)
    return state, load_records(paths.submitted_csv)


async def _submit(
    client: AsyncOlostep, records: list[PdfRecord], paths: Paths
) -> BatchState:
    """Create the batch and save its id straight away (so resume works)."""
    if not records:
        raise ValueError(f"No PDF URLs in {URLS_CSV}. Run `collect` first.")
    save_records(paths.submitted_csv, records)
    created_at = time.time()
    with Timer() as timer:
        batch_id = await create_batch(client, records)
    state = BatchState(
        batch_id=batch_id,
        total=len(records),
        created_at=created_at,
        collect_seconds=_collect_seconds(),
        create_seconds=round(timer.elapsed, 2),
    )
    state.save(paths.state_file)
    return state


async def _scrape_and_download(
    client: AsyncOlostep,
    settings: RunSettings,
    state: BatchState,
    records: list[PdfRecord],
    listener: RunListener,
) -> dict[str, str]:
    """Track the batch and download finished PDFs at the same time.

    Returns failure reasons by ``custom_id``.
    """
    paths = settings.paths
    downloader = Downloader(
        client,
        {r.custom_id: r for r in records},
        paths.markdown_dir,
        concurrency=_capped(settings.concurrency),
        retries=DOWNLOAD_RETRIES,
        listener=listener,
    )
    batch_done = asyncio.Event()
    feeder = asyncio.create_task(
        feed_downloads(
            client, state.batch_id, downloader, batch_done, interval=FEED_INTERVAL
        )
    )
    try:
        if state.scrape_seconds is None:
            scrape = await track_batch(
                client, state, listener, state_file=paths.state_file
            )
            state.scrape_seconds = round(scrape, 2)
            state.save(paths.state_file)
        else:
            listener.scrape_progress(state.total, state.total, state.scrape_seconds)
        batch_done.set()
        with Timer() as tail:
            await feeder  # final listing: every completed PDF is now queued
            downloads = await downloader.drain()
            failed_items = await list_items_with_retry(client, state.batch_id, "failed")
    finally:
        feeder.cancel()

    state.download_tail_seconds = round(tail.elapsed, 2)
    state.save(paths.state_file)
    return _failure_reasons(downloads, [i.custom_id for i in failed_items])


async def _rescrape_failures(
    client: AsyncOlostep,
    state: BatchState,
    records: list[PdfRecord],
    errors: dict[str, str],
    paths: Paths,
) -> dict[str, str]:
    """Retry each failed PDF with a single ``/scrapes`` call.

    Returns the failures that remain.
    """
    failed = [r for r in records if r.custom_id in errors]
    logger.info("Re-scraping %s failed PDFs one by one...", f"{len(failed):,}")
    with Timer() as timer:
        results = await rescrape(
            client,
            failed,
            paths.markdown_dir,
            concurrency=RESCRAPE_CONCURRENCY,
        )
    fixed = [r.custom_id for r in results if r.ok]
    state.rescraped = sorted({*state.rescraped, *fixed})
    state.rescrape_seconds = round(state.rescrape_seconds + timer.elapsed, 2)
    state.save(paths.state_file)
    logger.info("Recovered %s of %s with /scrapes", len(fixed), len(failed))
    remaining = {
        r.custom_id: f"{errors[r.custom_id]}; {r.error}" for r in results if not r.ok
    }
    return remaining


def _capped(concurrency: int) -> int:
    """Keep downloads under the API's HTTP/2 stream limit."""
    if concurrency > MAX_CONCURRENCY:
        logger.warning(
            "Concurrency %d is above the API's limit; using %d",
            concurrency,
            MAX_CONCURRENCY,
        )
    return min(concurrency, MAX_CONCURRENCY)


def _failure_reasons(
    downloads: list[DownloadResult], batch_failed_ids: list[str]
) -> dict[str, str]:
    reasons = {cid: BATCH_FAILED_REASON for cid in batch_failed_ids}
    for result in downloads:
        if result.error == PLACEHOLDER_REASON:
            reasons[result.custom_id] = PLACEHOLDER_REASON
        elif not result.ok:
            reasons[result.custom_id] = f"download failed: {result.error}"
    return reasons


def _verify_and_report(
    paths: Paths, state: BatchState, records: list[PdfRecord], errors: dict[str, str]
) -> Summary:
    rows = verify(records, paths.markdown_dir, errors)
    timings = Timings(
        collect=state.collect_seconds,
        batch_create=state.create_seconds,
        scrape=state.scrape_seconds or 0.0,
        download_tail=state.download_tail_seconds,
        rescrape=state.rescrape_seconds,
        milestones=state.milestones,
    )
    summary = summarize(state.batch_id, rows, timings, state.timeline, state.rescraped)
    write_reports(paths, rows, summary)
    return summary


def _collect_seconds() -> float:
    if not COLLECT_META.exists():
        return 0.0
    return float(json.loads(COLLECT_META.read_text(encoding="utf-8"))["seconds"])
