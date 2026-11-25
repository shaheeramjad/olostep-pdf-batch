"""PDF sources: where the URL list comes from.

Every source returns :class:`PdfRecord` objects, which are saved to
``data/pdf_urls.csv``. The rest of the pipeline only reads that CSV, so adding
a new source means writing one function that returns records.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit

from ..utils import custom_id_for_url, read_csv, write_csv


@dataclass(frozen=True)
class PdfRecord:
    """One PDF to scrape, plus what the source knows about it."""

    pdf_url: str
    title: str = ""
    publication_url: str = ""
    publication_title: str = ""
    organisation: str = ""
    published: str = ""  # ISO date (YYYY-MM-DD) when known
    pages: int | None = None  # page count reported by the source
    file_size_bytes: int | None = None

    @property
    def custom_id(self) -> str:
        """Deterministic batch id, also used as the Markdown file name."""
        return custom_id_for_url(self.pdf_url)


CSV_FIELDS = ("custom_id", *(f.name for f in fields(PdfRecord)))


def is_http_url(url: str) -> bool:
    """True for absolute http(s) URLs."""
    parts = urlsplit(url.strip())
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def is_pdf_url(url: str) -> bool:
    """True for http(s) URLs whose path ends in ``.pdf`` (any case)."""
    return is_http_url(url) and urlsplit(url).path.lower().endswith(".pdf")


def finalize(records: Iterable[PdfRecord], *, limit: int, seed: int) -> list[PdfRecord]:
    """De-duplicate by URL, shuffle with a fixed seed, and cap at ``limit``.

    Shuffling mixes organisations and years, so a small ``--limit`` is still a
    representative sample. The fixed seed makes runs reproducible.
    """
    unique: dict[str, PdfRecord] = {}
    for record in records:
        unique.setdefault(record.pdf_url.strip(), record)
    ordered = sorted(unique.values(), key=lambda r: r.pdf_url)
    random.Random(seed).shuffle(ordered)
    return ordered[:limit]


def save_records(path: Path, records: list[PdfRecord]) -> None:
    """Write records to CSV (with their ``custom_id``)."""
    rows = [[r.custom_id, *asdict(r).values()] for r in records]
    write_csv(path, CSV_FIELDS, rows)


def load_records(path: Path) -> list[PdfRecord]:
    """Read records written by :func:`save_records`."""
    return [_record_from_row(row) for row in read_csv(path)]


def _record_from_row(row: dict[str, str]) -> PdfRecord:
    return PdfRecord(
        pdf_url=row["pdf_url"],
        title=row.get("title", ""),
        publication_url=row.get("publication_url", ""),
        publication_title=row.get("publication_title", ""),
        organisation=row.get("organisation", ""),
        published=row.get("published", ""),
        pages=_int_or_none(row.get("pages")),
        file_size_bytes=_int_or_none(row.get("file_size_bytes")),
    )


def _int_or_none(value: str | None) -> int | None:
    return int(value) if value and value.strip().isdigit() else None
