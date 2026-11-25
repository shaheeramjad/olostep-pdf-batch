"""Small helpers: ids, atomic writes, CSV, timing and logging setup."""

from __future__ import annotations

import csv
import hashlib
import logging
import os
import re
import tempfile
import time
import warnings
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from rich.console import Console
from rich.logging import RichHandler

MAX_SLUG_LENGTH = 60
_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_length: int = MAX_SLUG_LENGTH) -> str:
    """Turn arbitrary text into a lowercase, filesystem-safe slug.

    >>> slugify("Annual Report 2024.pdf")
    'annual-report-2024-pdf'
    """
    slug = _NON_SLUG_CHARS.sub("-", text.lower()).strip("-")
    return slug[:max_length].rstrip("-") or "pdf"


def custom_id_for_url(url: str) -> str:
    """Build a deterministic, unique and readable ``custom_id`` for a PDF URL.

    The PDF file name keeps the id readable; a short hash of the full URL keeps
    it unique. The id doubles as the Markdown file name (``<custom_id>.md``).
    """
    name = Path(unquote(urlsplit(url).path)).stem
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{slugify(name)}-{digest}"


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` via a temp file + rename, so no partial file is left.

    If the final file exists, it is complete. That makes downloads safe to
    resume: existing files are simply skipped.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def write_csv(
    path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]
) -> None:
    """Write a CSV with a header row, atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def read_csv(path: Path) -> list[dict[str, str]]:
    """Read a CSV with a header row into a list of dicts."""
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def format_duration(seconds: float) -> str:
    """Format seconds for humans: ``41.3s``, ``7m 12s`` or ``1h 02m 03s``."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s"


@dataclass
class Timer:
    """A monotonic stopwatch. Use as a context manager or call ``stop()``."""

    started: float = field(default_factory=time.perf_counter)
    ended: float | None = None

    @property
    def elapsed(self) -> float:
        """Seconds since start (frozen once stopped)."""
        end = self.ended if self.ended is not None else time.perf_counter()
        return end - self.started

    def stop(self) -> float:
        """Stop the timer and return the elapsed seconds."""
        self.ended = time.perf_counter()
        return self.elapsed

    def __enter__(self) -> Timer:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()


def setup_logging(console: Console, verbose: bool = False) -> None:
    """Route ``logging`` through rich so log lines print above progress bars."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, show_path=False)],
        force=True,
    )
    quiet = logging.DEBUG if verbose else logging.WARNING
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(quiet)
    if not verbose:
        silence_sdk_warnings()


def silence_sdk_warnings() -> None:
    """Hide harmless SDK noise.

    olostep 1.2.3 warns whenever the API returns fields newer than its models
    (e.g. ``cost_usd``). Useful for SDK developers, noise for this tool.
    """
    logging.getLogger("olostep").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore", message=r".*extra field")
