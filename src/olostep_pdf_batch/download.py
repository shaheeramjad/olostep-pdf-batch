"""Step 3: download each PDF's Markdown while the batch is still running."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from olostep import AsyncOlostep, BatchItemResult, Olostep_BaseError

from .config import (
    BACKOFF_SECONDS,
    ITEMS_PAGE_SIZE,
    LISTING_BACKOFF_SECONDS,
    LISTING_RETRIES,
)
from .events import RunListener
from .sources import PdfRecord
from .utils import atomic_write_text

logger = logging.getLogger(__name__)

HOSTED_FETCH_TIMEOUT = 60  # seconds
# Seen in real batch results: the item is "completed" with success=true, but
# the content is this literal string instead of Markdown. A single /scrapes
# call on the same PDF usually returns the real text (`--rescrape`).
PLACEHOLDER_CONTENT = frozenset({"not-implemented"})
PLACEHOLDER_REASON = "Olostep returned 'not-implemented' instead of Markdown"
BATCH_FAILED_REASON = "scrape failed in batch (the API gives no reason)"


class NoContentError(Exception):
    """The API answered, but with a placeholder instead of content."""


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of downloading one PDF's Markdown."""

    custom_id: str
    status: str  # "downloaded", "skipped", "rescraped" or "failed"
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status != "failed"


async def list_items(
    client: AsyncOlostep, batch_id: str, status: str
) -> list[BatchItemResult]:
    """Return all batch items with ``status`` (``completed`` or ``failed``).

    This works while the batch is still running: finished items are listed,
    and can be retrieved, as soon as Olostep completes them.
    """
    items = client.batches.items(
        batch_id,
        batch_size=ITEMS_PAGE_SIZE,
        status=status,
        wait_for_completion=False,  # completion is tracked by batch.track_batch
    )
    return [item async for item in items]


async def list_items_with_retry(
    client: AsyncOlostep, batch_id: str, status: str
) -> list[BatchItemResult]:
    """:func:`list_items` with a few retries and backoff (5s, 10s, 20s)."""
    for attempt in range(LISTING_RETRIES + 1):
        try:
            return await list_items(client, batch_id, status)
        except Olostep_BaseError as exc:
            if attempt == LISTING_RETRIES:
                raise
            delay = LISTING_BACKOFF_SECONDS * 2**attempt
            logger.warning("Listing items failed (%s), retrying in %ss", exc, delay)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")


class Downloader:
    """Downloads batch items with bounded concurrency as they are queued.

    Call :meth:`add` as often as you like (duplicates are ignored), then
    :meth:`drain` to wait for everything queued so far.
    """

    def __init__(
        self,
        client: AsyncOlostep,
        records: dict[str, PdfRecord],
        markdown_dir: Path,
        *,
        concurrency: int,
        retries: int,
        listener: RunListener,
    ) -> None:
        self._client = client
        self._records = records
        self._markdown_dir = markdown_dir
        self._semaphore = asyncio.Semaphore(concurrency)
        self._retries = retries
        self._listener = listener
        self._jobs: dict[str, asyncio.Task[DownloadResult]] = {}
        self._finished = 0
        self._failed = 0
        markdown_dir.mkdir(parents=True, exist_ok=True)

    def add(self, items: list[BatchItemResult]) -> int:
        """Queue items that are not queued yet. Returns how many were new."""
        new = [
            item
            for item in items
            if item.custom_id not in self._jobs and item.custom_id in self._records
        ]
        for item in new:
            self._jobs[item.custom_id] = asyncio.create_task(self._run(item))
        return len(new)

    async def drain(self) -> list[DownloadResult]:
        """Wait for every queued download and return the results."""
        return list(await asyncio.gather(*self._jobs.values()))

    async def _run(self, item: BatchItemResult) -> DownloadResult:
        async with self._semaphore:
            result = await download_one(
                self._client,
                item,
                self._records[item.custom_id],
                self._markdown_dir,
                retries=self._retries,
            )
        self._finished += 1
        self._failed += not result.ok
        self._listener.download_progress(self._finished, self._failed)
        return result


async def feed_downloads(
    client: AsyncOlostep,
    batch_id: str,
    downloader: Downloader,
    batch_done: asyncio.Event,
    *,
    interval: float,
) -> None:
    """Queue completed items for download while the batch is still running.

    Re-lists completed items every ``interval`` seconds. After ``batch_done``
    is set it lists one final time, so no item is missed.
    """
    while True:
        if batch_done.is_set():
            # The final listing must succeed, so it gets its own retries.
            downloader.add(await list_items_with_retry(client, batch_id, "completed"))
            return
        try:
            added = downloader.add(await list_items(client, batch_id, "completed"))
            logger.debug("Queued %d new PDFs for download", added)
        except Olostep_BaseError as exc:
            logger.warning("Listing items failed (%s), will retry", exc)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(batch_done.wait(), timeout=interval)


async def download_one(
    client: AsyncOlostep,
    item: BatchItemResult,
    record: PdfRecord,
    markdown_dir: Path,
    *,
    retries: int,
) -> DownloadResult:
    """Fetch one PDF's Markdown (with retries) and save it atomically.

    An empty result is still saved: it is a real outcome (often a scanned PDF)
    that the verify step reports, not a download error.
    """
    path = markdown_dir / f"{record.custom_id}.md"
    if path.exists():
        return DownloadResult(record.custom_id, "skipped")

    last_error = ""
    for attempt in range(retries + 1):
        try:
            markdown = await _fetch_markdown(client, item)
            atomic_write_text(path, with_front_matter(record, markdown))
            return DownloadResult(record.custom_id, "downloaded")
        except NoContentError as exc:
            # Retrying returns the same stored placeholder, so stop here.
            return DownloadResult(record.custom_id, "failed", error=str(exc))
        except Exception as exc:  # one bad PDF must never crash the run
            last_error = f"{type(exc).__name__}: {exc}".strip(": ")
            if attempt < retries:
                await asyncio.sleep(BACKOFF_SECONDS * 2**attempt)

    logger.debug("Giving up on %s: %s", record.pdf_url, last_error)
    return DownloadResult(record.custom_id, "failed", error=last_error)


def with_front_matter(record: PdfRecord, markdown: str) -> str:
    """Prefix Markdown with a small YAML header naming its source.

    Values are JSON-encoded strings, which are valid YAML and safe for
    titles containing colons or quotes.
    """
    header = {
        "source_pdf": record.pdf_url,
        "publication": record.publication_url,
        "title": record.title,
    }
    lines = [
        f"{key}: {json.dumps(value, ensure_ascii=False)}"
        for key, value in header.items()
    ]
    return "---\n" + "\n".join(lines) + "\n---\n\n" + markdown.strip() + "\n"


async def rescrape(
    client: AsyncOlostep,
    records: list[PdfRecord],
    markdown_dir: Path,
    *,
    concurrency: int,
) -> list[DownloadResult]:
    """Scrape PDFs one by one with ``/scrapes`` (1 credit each).

    A fallback for PDFs the batch could not turn into Markdown.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def one(record: PdfRecord) -> DownloadResult:
        async with semaphore:
            try:
                result = await client.scrapes.create(
                    url_to_scrape=record.pdf_url, formats=["markdown"]
                )
                markdown = await markdown_from_result(result)
            except Exception as exc:  # one bad PDF must never crash the run
                error = f"re-scrape failed: {type(exc).__name__}: {exc}".strip(": ")
                return DownloadResult(record.custom_id, "failed", error=error)
        path = markdown_dir / f"{record.custom_id}.md"
        atomic_write_text(path, with_front_matter(record, markdown))
        return DownloadResult(record.custom_id, "rescraped")

    return list(await asyncio.gather(*(one(r) for r in records)))


async def markdown_from_result(result: object) -> str:
    """Markdown from a scrape or retrieve result, inline or hosted.

    Raises:
        NoContentError: If the API sent a placeholder instead of Markdown.
    """
    # ScrapeResult only sets attributes for non-null fields, hence getattr.
    markdown = getattr(result, "markdown_content", None)
    hosted_url = getattr(result, "markdown_hosted_url", None)
    if not markdown and hosted_url:
        # Results over ~6MB come back only as a hosted file (`size_exceeded`,
        # per docs.olostep.com). That URL is plain file storage, not the
        # Olostep API, so a stdlib GET in a worker thread is enough.
        markdown = await asyncio.to_thread(_http_get_text, hosted_url)
    if markdown and markdown.strip() in PLACEHOLDER_CONTENT:
        raise NoContentError(PLACEHOLDER_REASON)
    return markdown or ""


async def _fetch_markdown(client: AsyncOlostep, item: BatchItemResult) -> str:
    """Return the Markdown for a batch item via ``/retrieve``."""
    if not item.retrieve_id:
        raise ValueError("item has no retrieve_id")
    result = await client.retrieve.get(item.retrieve_id, formats=["markdown"])
    return await markdown_from_result(result)


def _http_get_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=HOSTED_FETCH_TIMEOUT) as response:
        return response.read().decode("utf-8", errors="replace")
