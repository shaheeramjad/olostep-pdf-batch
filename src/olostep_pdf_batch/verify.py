"""Step 4: check every submitted PDF and judge the quality of its Markdown.

Each PDF gets exactly one row: did it fail (and why), or how good is its text?
Quality is judged from the text, plus the page count (from the source, or
from the ``# Page N`` headings Olostep writes for each PDF page):

* ``likely_scanned`` pages but (almost) no text: the pages are images.
                     Olostep does not OCR them, so they need an OCR step.
* ``empty``          no text and no page information
* ``near_empty``     a few words only (posters, cover sheets, slides)
* ``ok``             everything else
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import NEAR_EMPTY_CHARS, SCANNED_CHARS_PER_PAGE
from .sources import PdfRecord

_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
# Olostep starts every PDF page with a "# Page N" heading.
PAGE_MARKER = re.compile(r"^#\s+Page\s+(\d+)\s*$", re.M)
# Markdown syntax that adds characters but no readable text.
_MARKUP = re.compile(r"!\[[^\]]*\]\([^)]*\)|[#*_|>`\-]+|\s+")


@dataclass(frozen=True)
class Verified:
    """The final, verified outcome for one submitted PDF."""

    record: PdfRecord
    status: str  # "succeeded" or "failed"
    quality: str = ""  # see module docstring; empty when failed
    chars: int = 0  # readable characters (whitespace and markup removed)
    file: str = ""
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


def strip_front_matter(markdown: str) -> str:
    """Return the Markdown body without the leading ``---`` header."""
    return _FRONT_MATTER.sub("", markdown, count=1).strip()


def readable_chars(markdown: str) -> int:
    """Count characters of real text: no page markers, markup or whitespace."""
    return len(_MARKUP.sub("", PAGE_MARKER.sub("", markdown)))


def count_page_markers(markdown: str) -> int:
    """Number of ``# Page N`` headings, i.e. PDF pages Olostep saw."""
    return len(PAGE_MARKER.findall(markdown))


def assess(body: str, pages: int | None) -> tuple[str, int]:
    """Judge one Markdown body. Returns ``(quality, readable_chars)``.

    Args:
        body: Markdown without front matter.
        pages: Page count from the source, if known.
    """
    chars = readable_chars(body)
    pages = pages or count_page_markers(body)
    if pages and chars == 0:
        return "likely_scanned", 0
    if chars == 0:
        return "empty", 0
    # A single page with little text is usually a poster or cover sheet,
    # so the per-page check needs at least two pages to mean "scanned".
    if pages >= 2 and chars / pages < SCANNED_CHARS_PER_PAGE:
        return "likely_scanned", chars
    if chars < NEAR_EMPTY_CHARS:
        return "near_empty", chars
    return "ok", chars


def verify(
    records: list[PdfRecord],
    markdown_dir: Path,
    errors: dict[str, str],
) -> list[Verified]:
    """Produce one verified row per submitted PDF.

    Success is decided by the file on disk, not by what the run believed, so
    the report is correct even after crashes, resumes or manual edits.

    Args:
        records: Everything that was submitted.
        markdown_dir: Folder with ``<custom_id>.md`` files.
        errors: Known failure reasons by ``custom_id``.
    """
    return [_verify_one(r, markdown_dir, errors) for r in records]


def _verify_one(
    record: PdfRecord, markdown_dir: Path, errors: dict[str, str]
) -> Verified:
    path = markdown_dir / f"{record.custom_id}.md"
    if not path.is_file():
        reason = errors.get(record.custom_id, "no Markdown file was saved")
        return Verified(record, "failed", error=reason)
    body = strip_front_matter(path.read_text(encoding="utf-8"))
    quality, chars = assess(body, record.pages)
    return Verified(record, "succeeded", quality=quality, chars=chars, file=str(path))
