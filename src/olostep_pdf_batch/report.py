"""Step 5: write manifest.csv, failures.csv and summary.json; print a table."""

from __future__ import annotations

import json
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .config import Paths
from .utils import atomic_write_text, format_duration, read_csv, write_csv
from .verify import Verified

QUALITY_LABELS = {
    "ok": "Good text",
    "likely_scanned": "Likely scanned (needs OCR)",
    "near_empty": "Near-empty",
    "empty": "Empty",
}
MANIFEST_FIELDS = (
    "custom_id", "pdf_url", "title", "organisation", "published", "pages",
    "file_size_bytes", "status", "quality", "chars", "file", "error",
    "publication_url",
)  # fmt: skip
FAILURE_FIELDS = (
    "custom_id", "pdf_url", "reason", "pages", "file_size_mb", "publication_url",
)  # fmt: skip


@dataclass(frozen=True)
class Timings:
    """Seconds spent in each phase."""

    collect: float
    batch_create: float
    scrape: float  # from batch submission until Olostep finished every PDF
    download_tail: float  # downloading left after the batch finished
    rescrape: float = 0.0  # optional single-scrape fallback for failures
    milestones: dict[str, float] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return (
            self.collect
            + self.batch_create
            + self.scrape
            + self.download_tail
            + self.rescrape
        )


@dataclass(frozen=True)
class Summary:
    """Verified numbers for one run."""

    batch_id: str
    submitted: int
    succeeded: int
    failed: int
    quality: dict[str, int]
    total_chars: int
    rescraped: int  # PDFs recovered with single /scrapes calls
    timings: Timings
    timeline: list[list[float]] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return self.succeeded / self.submitted if self.submitted else 0.0

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly representation for ``summary.json``."""
        data = asdict(self)
        data["success_rate"] = round(self.success_rate, 4)
        data["timings"]["total"] = round(self.timings.total, 2)
        data["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        return data


def summarize(
    batch_id: str,
    rows: list[Verified],
    timings: Timings,
    timeline: list[list[float]],
    rescraped: list[str],
) -> Summary:
    """Count successes, failures and quality classes."""
    good = [r for r in rows if r.succeeded]
    quality = Counter(r.quality for r in good)
    return Summary(
        batch_id=batch_id,
        submitted=len(rows),
        succeeded=len(good),
        failed=len(rows) - len(good),
        quality={key: quality.get(key, 0) for key in QUALITY_LABELS},
        total_chars=sum(r.chars for r in good),
        rescraped=len(set(rescraped) & {r.record.custom_id for r in good}),
        timings=timings,
        timeline=timeline,
    )


def write_reports(paths: Paths, rows: list[Verified], summary: Summary) -> None:
    """Write ``manifest.csv``, ``failures.csv`` and ``summary.json``."""
    write_csv(paths.manifest_csv, MANIFEST_FIELDS, [_manifest_row(r) for r in rows])
    failures = [_failure_row(r) for r in rows if not r.succeeded]
    write_csv(paths.failures_csv, FAILURE_FIELDS, failures)
    text = json.dumps(summary.to_dict(), indent=2) + "\n"
    atomic_write_text(paths.summary_json, text)


def load_known_errors(paths: Paths) -> dict[str, str]:
    """Failure reasons from a previous report, by ``custom_id``."""
    if not paths.failures_csv.exists():
        return {}
    return {row["custom_id"]: row["reason"] for row in read_csv(paths.failures_csv)}


def load_summary(paths: Paths) -> dict[str, object] | None:
    """Read ``summary.json`` if a run has finished."""
    if not paths.summary_json.exists():
        return None
    return json.loads(paths.summary_json.read_text(encoding="utf-8"))


def zip_markdown(paths: Paths) -> Path:
    """Bundle every Markdown file into ``markdown.zip`` and return its path."""
    target = paths.out_dir / "markdown.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(paths.markdown_dir.glob("*.md")):
            archive.write(file, arcname=f"markdown/{file.name}")
    return target


def print_summary(console: Console, summary: Summary, paths: Paths) -> None:
    """Render the final summary table."""
    t = summary.timings
    table = Table(title="Olostep PDF batch", show_header=False, min_width=46)
    table.add_column(style="bold")
    table.add_column(justify="right")
    table.add_row("PDFs submitted", f"{summary.submitted:,}")
    table.add_row("Succeeded", f"[green]{summary.succeeded:,}")
    table.add_row("Failed", f"[red]{summary.failed:,}" if summary.failed else "0")
    table.add_row("Success rate", f"{summary.success_rate:.1%}")
    if summary.rescraped:
        table.add_row("  Recovered by re-scrape", f"{summary.rescraped:,}")
    table.add_section()
    for key, label in QUALITY_LABELS.items():
        table.add_row(f"  {label}", f"{summary.quality[key]:,}")
    table.add_row("Characters of text", f"{summary.total_chars:,}")
    table.add_section()
    table.add_row("Collect URLs", format_duration(t.collect))
    table.add_row("Create batch", format_duration(t.batch_create))
    table.add_row("Scrape (Olostep)", format_duration(t.scrape))
    for percent, seconds in t.milestones.items():
        table.add_row(f"  {percent}% done at", format_duration(seconds))
    table.add_row("Download tail", format_duration(t.download_tail))
    if t.rescrape:
        table.add_row("Re-scrape failures", format_duration(t.rescrape))
    table.add_row("Total", f"[bold]{format_duration(t.total)}")
    console.print(table)
    console.print(f"Markdown: [cyan]{paths.markdown_dir}/")
    console.print(f"Reports:  [cyan]{paths.manifest_csv}, {paths.failures_csv.name}, "
                  f"{paths.summary_json.name}")  # fmt: skip


def _manifest_row(r: Verified) -> list[object]:
    rec = r.record
    return [
        rec.custom_id, rec.pdf_url, rec.title, rec.organisation, rec.published,
        rec.pages or "", rec.file_size_bytes or "", r.status, r.quality,
        r.chars, r.file, r.error, rec.publication_url,
    ]  # fmt: skip


def _failure_row(r: Verified) -> list[object]:
    rec = r.record
    size_mb = round(rec.file_size_bytes / 1e6, 1) if rec.file_size_bytes else ""
    return [rec.custom_id, rec.pdf_url, r.error, rec.pages or "", size_mb,
            rec.publication_url]  # fmt: skip
