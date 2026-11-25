"""Optional step: turn the Markdown files into one structured ``dataset.csv``.

Why local extraction is the default
-----------------------------------
The SDK (olostep 1.2.3) has no way to run structured extraction over content
you already downloaded:

* ``client.answers.create(task, json_format=...)`` takes only a task. It
  searches the live web; it cannot be pointed at our Markdown (20 credits).
* ``client.scrapes.create(..., llm_extract={"schema": ...})`` works, but it
  scrapes the PDF again (10 credits per PDF, measured on a real run).

For GOV.UK most fields are already known exactly: the Content API gave us the
title, organisation, publication date and page count during ``collect``. Only
the summary has to come from the text, and a simple local heuristic is free,
fast and deterministic. So ``--method local`` is the default and
``--method olostep`` (LLM extraction, re-scrapes each PDF) is opt-in for a
sample, with the credit cost shown first.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from olostep import AsyncOlostep

from .config import SUMMARY_MAX_CHARS, Paths
from .sources import PdfRecord
from .utils import read_csv, write_csv
from .verify import PAGE_MARKER, strip_front_matter

logger = logging.getLogger(__name__)

METHODS = ("local", "olostep")
LLM_CONCURRENCY = 5

LLM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "organisation": {"type": "string", "description": "Publishing body"},
        "publication_date": {"type": "string", "description": "YYYY-MM-DD"},
        "page_count": {"type": "integer"},
        "summary": {"type": "string", "description": "One sentence"},
    },
}

_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.M)
_DATE = re.compile(
    r"\b(\d{1,2}\s+)?(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(19|20)\d{2}\b"
)
_PAGE_TITLE = re.compile(r"^Page \d+$")  # heading text of a "# Page N" marker
_DOC_NOUNS = (
    "guidance|report|document|paper|plan|strategy|notice|publication|"
    "consultation|review|framework|policy|memo|standard|guide|summary|order"
)
_DOC_VERBS = "sets out|describes|provides|explains|summarises|outlines|presents|covers"
_PURPOSE = re.compile(
    rf"\b(?:This|The)\s+(?:[\w\u2011-]+\s+){{0,3}}?(?:{_DOC_NOUNS})\s+"
    rf"(?:{_DOC_VERBS})\b[^.]{{10,300}}\.",
    re.I,
)
_SENTENCE = re.compile(r"[A-Z][^.!?]*[.!?](?=\s|$)")
# Front-matter sentences every GOV.UK PDF has; never a useful summary.
_BOILERPLATE = re.compile(
    r"copyright|licen[cs]e|permission|@|www\.|https?:|available (?:at|from)|"
    r"formatting errors|this publication|enquiries|accessible version",
    re.I,
)
_SUMMARY_SEARCH_CHARS = 20_000
_MIN_SENTENCE_WORDS = 12
_MAX_SENTENCE_WORDS = 60


@dataclass(frozen=True)
class DatasetRow:
    """One row of ``dataset.csv``."""

    custom_id: str
    title: str
    organisation: str
    publication_date: str
    pages: str
    summary: str
    pdf_url: str
    method: str


def pick_sample(paths: Paths, size: int) -> list[tuple[PdfRecord, Path]]:
    """The first ``size`` succeeded PDFs from ``manifest.csv`` (already shuffled)."""
    if not paths.manifest_csv.exists():
        raise FileNotFoundError("No manifest.csv yet. Run `run` (or `report`) first.")
    chosen = []
    for row in read_csv(paths.manifest_csv):
        if row["status"] == "succeeded" and row["quality"] != "empty":
            chosen.append((_record_from_manifest(row), Path(row["file"])))
        if len(chosen) == size:
            break
    return chosen


async def extract_dataset(
    sample: list[tuple[PdfRecord, Path]],
    paths: Paths,
    *,
    method: str = "local",
    api_key: str | None = None,
) -> list[DatasetRow]:
    """Extract one row per PDF and write ``dataset.csv``."""
    if method == "local":
        rows = [extract_local(record, path.read_text(encoding="utf-8"))
                for record, path in sample]  # fmt: skip
    elif method == "olostep" and api_key:
        rows = await _extract_with_olostep(sample, api_key)
    else:
        raise ValueError(f"unknown method {method!r} (or missing API key)")
    fields = list(DatasetRow.__dataclass_fields__)
    write_csv(paths.dataset_csv, fields, [list(asdict(r).values()) for r in rows])
    return rows


def extract_local(record: PdfRecord, markdown: str) -> DatasetRow:
    """Fill fields from source metadata first, then from the text itself."""
    body = strip_front_matter(markdown)
    return DatasetRow(
        custom_id=record.custom_id,
        title=record.title or first_heading(body),
        organisation=record.organisation,
        publication_date=record.published or first_date(body),
        pages=str(record.pages or ""),
        summary=first_sentence(body),
        pdf_url=record.pdf_url,
        method="local",
    )


def first_heading(body: str) -> str:
    """First heading that is not one of Olostep's ``# Page N`` markers."""
    for match in _HEADING.finditer(body):
        text = _clean(match.group(1))
        if not _PAGE_TITLE.match(text):
            return text
    return ""


def first_date(body: str) -> str:
    match = _DATE.search(body[:3000])
    return match.group(0) if match else ""


def first_sentence(body: str) -> str:
    """A one-line summary: the document's purpose sentence, if it has one.

    Government documents usually say what they are near the start ("This
    guidance sets out..."). If no such sentence exists, fall back to the first
    full-length prose sentence that is not part of a contents page.
    """
    text = _clean(PAGE_MARKER.sub(" ", body))[:_SUMMARY_SEARCH_CHARS]
    if match := _PURPOSE.search(text):
        return _truncate(match.group(0))
    for sentence in _SENTENCE.findall(text):
        words = sentence.split()
        if _MIN_SENTENCE_WORDS <= len(words) <= _MAX_SENTENCE_WORDS and not (
            _looks_like_toc(sentence)
            or _BOILERPLATE.search(sentence)
            or _mostly_capitals(sentence)
        ):
            return _truncate(sentence.strip())
    return ""


async def _extract_with_olostep(
    sample: list[tuple[PdfRecord, Path]], api_key: str
) -> list[DatasetRow]:
    """LLM extraction with ``scrapes.create(llm_extract=...)`` (re-scrapes)."""
    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)
    async with AsyncOlostep(api_key=api_key) as client:

        async def one(record: PdfRecord) -> DatasetRow:
            async with semaphore:
                try:
                    result = await client.scrapes.create(
                        url_to_scrape=record.pdf_url,
                        formats=["json"],
                        llm_extract={"schema": LLM_SCHEMA},
                    )
                    data = _as_dict(getattr(result, "json_content", None))
                except Exception as exc:  # one PDF must not stop the dataset
                    logger.warning("LLM extract failed for %s: %s", record.pdf_url, exc)
                    data = {}
            return DatasetRow(
                custom_id=record.custom_id,
                title=str(data.get("title") or ""),
                organisation=str(data.get("organisation") or ""),
                publication_date=str(data.get("publication_date") or ""),
                pages=str(data.get("page_count") or ""),
                summary=_truncate(str(data.get("summary") or "")),
                pdf_url=record.pdf_url,
                method="olostep",
            )

        return list(await asyncio.gather(*(one(r) for r, _ in sample)))


def _as_dict(value: Any) -> dict[str, Any]:
    """``json_content`` may arrive as a dict or a JSON string."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _record_from_manifest(row: dict[str, str]) -> PdfRecord:
    pages = row.get("pages", "")
    return PdfRecord(
        pdf_url=row["pdf_url"],
        title=row["title"],
        publication_url=row["publication_url"],
        organisation=row["organisation"],
        published=row["published"],
        pages=int(pages) if pages.isdigit() else None,
    )


def _clean(text: str) -> str:
    """Collapse whitespace and drop Markdown emphasis and links."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_`]+", "", text)
    return " ".join(text.split())


def _looks_like_toc(text: str) -> bool:
    """Contents pages: dot leaders, the word "Contents", or many page numbers."""
    numbers = re.findall(r"(?:^|\s)\d{1,3}(?=\s|$)", text)
    return "...." in text or "contents" in text.lower() or len(numbers) >= 3


def _mostly_capitals(text: str) -> bool:
    """Title pages and headings are often set in capitals."""
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) > len(letters) / 2


def _truncate(text: str) -> str:
    if len(text) <= SUMMARY_MAX_CHARS:
        return text
    return text[: SUMMARY_MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"
