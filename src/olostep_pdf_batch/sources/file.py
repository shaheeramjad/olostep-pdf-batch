"""Bring your own PDFs: read URLs from a CSV file or pasted text.

Accepted input:

* a CSV with a ``pdf_url`` or ``url`` column (``title`` and
  ``publication_url`` columns are used when present), or
* plain text with one URL per line (``#`` starts a comment).

URLs don't have to end in ``.pdf`` (many PDF links don't), but they must be
absolute http(s) URLs.
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

from . import PdfRecord, is_http_url

logger = logging.getLogger(__name__)

URL_COLUMNS = ("pdf_url", "url")


def load_file(path: Path) -> list[PdfRecord]:
    """Read PDF records from a CSV or text file."""
    return parse_text(path.read_text(encoding="utf-8-sig"))


def parse_text(text: str) -> list[PdfRecord]:
    """Parse CSV text (with a URL column) or one URL per line."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header = [cell.strip().lower() for cell in next(csv.reader([lines[0]]))]
    url_column = next((c for c in URL_COLUMNS if c in header), None)
    if url_column is None:
        return _from_plain_lines(lines)
    return _from_csv(text, url_column)


def _from_csv(text: str, url_column: str) -> list[PdfRecord]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    reader.fieldnames = [name.strip().lower() for name in reader.fieldnames or []]
    records = []
    for row in reader:
        url = (row.get(url_column) or "").strip()
        if not is_http_url(url):
            _warn_skipped(url)
            continue
        records.append(
            PdfRecord(
                pdf_url=url,
                title=(row.get("title") or "").strip(),
                publication_url=(row.get("publication_url") or "").strip(),
            )
        )
    return records


def _from_plain_lines(lines: list[str]) -> list[PdfRecord]:
    records = []
    for line in lines:
        url = line.split("#", 1)[0].strip().strip(",")
        if not url:
            continue
        if is_http_url(url):
            records.append(PdfRecord(pdf_url=url))
        else:
            _warn_skipped(url)
    return records


def _warn_skipped(value: str) -> None:
    logger.warning("Skipping %r: not an absolute http(s) URL", value[:80])
