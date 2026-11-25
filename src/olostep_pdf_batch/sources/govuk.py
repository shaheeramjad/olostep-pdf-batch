"""Collect public PDF URLs from GOV.UK publications.

Two documented, public GOV.UK APIs are used:

* Search API (``/api/search.json``) lists publications of the chosen types.
* Content API (``/api/content/<path>``) returns one publication, including its
  attachments with ``url``, ``content_type``, ``number_of_pages`` and
  ``file_size``.

GOV.UK content is published under the Open Government Licence v3.0.
Requests are polite: a few at a time, a short delay, retries that respect
``Retry-After``, and a User-Agent naming this project.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

import httpx

from ..config import (
    GOVUK_BASE,
    GOVUK_CONCURRENCY,
    GOVUK_DOCUMENT_TYPES,
    GOVUK_PAGE_SIZE,
    GOVUK_REQUEST_DELAY,
    GOVUK_TIMEOUT,
    MAX_PDFS_PER_PUBLICATION,
    USER_AGENT,
)
from . import PdfRecord, is_pdf_url

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]  # (pdfs_found, target)

SEARCH_URL = f"{GOVUK_BASE}/api/search.json"
CONTENT_URL = f"{GOVUK_BASE}/api/content"
# About 40% of these publications attach a PDF, so list ~3 per PDF wanted.
CANDIDATE_FACTOR = 3
MAX_RETRIES = 2


async def collect_govuk(
    limit: int, *, seed: int, on_progress: ProgressCallback | None = None
) -> list[PdfRecord]:
    """Return ``limit`` PDF records (or all GOV.UK has, if fewer).

    Publications are listed newest first in rounds. Each round is shuffled
    with ``seed`` and opened until enough PDFs are found; if a round runs
    out, the next page of publications is listed.
    """
    found: list[PdfRecord] = []
    offset = 0
    rng = random.Random(seed)
    round_size = max(GOVUK_PAGE_SIZE // 4, limit * CANDIDATE_FACTOR)
    async with _client() as client:
        while len(found) < limit:
            links = await list_publications(client, start=offset, count=round_size)
            if not links:
                break  # GOV.UK has no more publications of these types
            offset += len(links)
            rng.shuffle(links)
            logger.info("Reading %s publications...", f"{len(links):,}")
            found += await _gather_pdfs(client, links, limit, found, on_progress)
    return found


async def list_publications(
    client: httpx.AsyncClient, *, start: int, count: int
) -> list[str]:
    """Return up to ``count`` publication paths (e.g. ``/government/...``)."""
    links: list[str] = []
    while len(links) < count:
        params: list[tuple[str, str | int]] = [
            ("count", min(GOVUK_PAGE_SIZE, count - len(links))),
            ("start", start + len(links)),
            ("fields", "link"),
            ("order", "-public_timestamp"),
        ]
        params += [
            ("filter_content_store_document_type", t) for t in GOVUK_DOCUMENT_TYPES
        ]
        results = (await _get_json(client, SEARCH_URL, params=params))["results"]
        if not results:
            break
        links += [r["link"] for r in results if r.get("link", "").startswith("/")]
    return links


def pdfs_from_content(content: dict[str, Any]) -> list[PdfRecord]:
    """Extract PDF attachments from one Content API response.

    Pure function (no network) so it is easy to test.
    """
    details = content.get("details") or {}
    publication_url = urljoin(GOVUK_BASE, content.get("base_path", ""))
    organisations = (content.get("links") or {}).get("organisations") or []
    common = {
        "publication_url": publication_url,
        "publication_title": content.get("title", ""),
        "organisation": organisations[0].get("title", "") if organisations else "",
        "published": (content.get("first_published_at") or "")[:10],
    }
    records: list[PdfRecord] = []
    for attachment in details.get("attachments") or []:
        url = urljoin(GOVUK_BASE, attachment.get("url") or "")
        if not is_pdf_url(url):
            continue
        records.append(
            PdfRecord(
                pdf_url=url,
                title=attachment.get("title") or common["publication_title"],
                pages=_positive_int(attachment.get("number_of_pages")),
                file_size_bytes=_positive_int(attachment.get("file_size")),
                **common,
            )
        )
        if len(records) == MAX_PDFS_PER_PUBLICATION:
            break
    return records


async def _gather_pdfs(
    client: httpx.AsyncClient,
    links: list[str],
    limit: int,
    already: list[PdfRecord],
    on_progress: ProgressCallback | None,
) -> list[PdfRecord]:
    """Open publications with a few workers until ``limit`` PDFs exist in total."""
    queue: asyncio.Queue[str] = asyncio.Queue()
    for link in links:
        queue.put_nowait(link)
    found: list[PdfRecord] = []
    seen = {r.pdf_url for r in already}

    async def worker() -> None:
        while len(already) + len(found) < limit and not queue.empty():
            link = queue.get_nowait()
            try:
                content = await _get_json(client, f"{CONTENT_URL}{link}")
            except httpx.HTTPError as exc:
                logger.debug("Skipping %s: %s", link, exc)
                continue
            for record in pdfs_from_content(content):
                # One PDF can be attached to several publications; keep it once.
                if record.pdf_url not in seen:
                    seen.add(record.pdf_url)
                    found.append(record)
            if on_progress:
                on_progress(min(len(already) + len(found), limit), limit)
            await asyncio.sleep(GOVUK_REQUEST_DELAY)

    await asyncio.gather(*(worker() for _ in range(GOVUK_CONCURRENCY)))
    return found


async def _get_json(
    client: httpx.AsyncClient, url: str, params: Any = None
) -> dict[str, Any]:
    """GET JSON with retries on 429 and 5xx (honouring ``Retry-After``)."""
    for attempt in range(MAX_RETRIES + 1):
        response = await client.get(url, params=params)
        retryable = response.status_code == 429 or response.status_code >= 500
        if not retryable or attempt == MAX_RETRIES:
            response.raise_for_status()
            return response.json()
        await asyncio.sleep(_retry_delay(response, attempt))
    raise AssertionError("unreachable")


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait: ``Retry-After`` when given, else 1s, 2s, 4s..."""
    header = response.headers.get("Retry-After", "")
    return float(header) if header.isdigit() else 2.0**attempt


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=GOVUK_TIMEOUT,
        follow_redirects=True,
    )


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value > 0 else None
