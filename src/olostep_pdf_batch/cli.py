"""Command-line interface: collect, run, resume, report, extract."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from olostep import Olostep_BaseError
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from . import __version__
from .config import (
    DEFAULT_CONCURRENCY,
    DEFAULT_LIMIT,
    DEFAULT_SAMPLE,
    LLM_EXTRACT_CREDITS,
    OUTPUT_DIR,
    ConfigError,
    Paths,
    RunSettings,
    load_api_key,
)
from .extract import METHODS, extract_dataset, pick_sample
from .pipeline import SOURCES, collect, report_from_disk, run_batch
from .report import print_summary
from .utils import setup_logging

logger = logging.getLogger(__name__)
console = Console()


class RichListener:
    """Shows the run as two live progress bars plus milestone lines."""

    def __init__(self, progress: Progress) -> None:
        self._progress = progress
        self._scrape = progress.add_task("Scraping", total=None)
        self._download = progress.add_task("Downloading", total=None)

    def batch_created(self, batch_id: str, total: int) -> None:
        self._progress.update(self._scrape, total=total)
        self._progress.update(self._download, total=total)

    def scrape_progress(self, completed: int, total: int, elapsed: float) -> None:
        self._progress.update(self._scrape, completed=completed)

    def download_progress(self, finished: int, failed: int) -> None:
        self._progress.update(self._download, completed=finished)

    def milestone(self, line: str) -> None:
        self._progress.console.print(f"[bold green]{line}")


def build_progress() -> Progress:
    """One live display with a bar for scraping and one for downloading."""
    return Progress(
        TextColumn("[bold]{task.description:<12}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TextColumn("elapsed"),
        TimeElapsedColumn(),
        console=console,
    )


# --- Commands ----------------------------------------------------------------


def cmd_collect(args: argparse.Namespace) -> int:
    """Collect PDF URLs into data/pdf_urls.csv."""
    if args.source == "file" and not args.input:
        raise SystemExit("error: --source file needs --input my_pdfs.csv")
    with build_progress() as progress:
        task = progress.add_task("Collecting", total=args.limit)
        records = asyncio.run(
            collect(
                args.source,
                limit=args.limit,
                input_path=args.input,
                on_progress=lambda done, total: progress.update(task, completed=done),
            )
        )
        progress.update(task, completed=len(records), total=len(records))
    console.print(f"[bold green]{len(records):,} PDF URLs collected")
    for record in records[:5]:
        console.print(f"  {record.pdf_url}")
    return 0 if records else 1


def cmd_run(args: argparse.Namespace, *, resume: bool) -> int:
    """Submit (or resume) the batch and download every PDF's Markdown."""
    settings = RunSettings(
        api_key=load_api_key(),
        paths=Paths(args.out),
        concurrency=args.concurrency,
        rescrape=args.rescrape,
    )
    with build_progress() as progress:
        summary = asyncio.run(
            run_batch(settings, RichListener(progress), resume=resume)
        )
    print_summary(console, summary, settings.paths)
    return 0 if summary.succeeded else 1


def cmd_report(args: argparse.Namespace) -> int:
    """Re-verify the last run from disk and print the summary."""
    paths = Paths(args.out)
    print_summary(console, report_from_disk(paths), paths)
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """Build dataset.csv from a sample of the Markdown files."""
    paths = Paths(args.out)
    sample = pick_sample(paths, args.sample)
    api_key = None
    if args.method == "olostep":
        api_key = load_api_key()
        credits = len(sample) * LLM_EXTRACT_CREDITS
        console.print(
            f"LLM extraction re-scrapes {len(sample)} PDFs: ~{credits:,} credits."
        )
        if not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
            return 1
    rows = asyncio.run(
        extract_dataset(sample, paths, method=args.method, api_key=api_key)
    )
    _print_dataset_preview(rows)
    console.print(f"[bold green]{len(rows):,} rows -> {paths.dataset_csv}")
    return 0


def _print_dataset_preview(rows: list, limit: int = 5) -> None:
    table = Table("Title", "Organisation", "Date", "Pages", "Summary", width=120)
    for row in rows[:limit]:
        table.add_row(row.title, row.organisation, row.publication_date, row.pages,
                      row.summary)  # fmt: skip
    console.print(table)


# --- Parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Define the sub-commands and their flags."""
    parser = argparse.ArgumentParser(
        prog="olostep-pdf-batch",
        description="Scrape thousands of PDFs into an AI-ready dataset with Olostep.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logs")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("collect", help="collect PDF URLs into data/pdf_urls.csv")
    p.add_argument("--source", choices=SOURCES, default="govuk")
    p.add_argument("--limit", type=_positive, default=DEFAULT_LIMIT)
    p.add_argument("--input", type=Path, help="CSV/text file for --source file")

    run_help = {
        "run": "submit the batch and download Markdown",
        "resume": "resume the batch saved in output/batch.json",
    }
    for name, text in run_help.items():
        p = sub.add_parser(name, help=text)
        p.add_argument("--concurrency", type=_positive, default=DEFAULT_CONCURRENCY)
        p.add_argument(
            "--rescrape",
            action="store_true",
            help="retry failed PDFs one by one with /scrapes (1 credit each)",
        )
        _add_out(p)

    _add_out(sub.add_parser("report", help="re-verify the last run from disk"))

    p = sub.add_parser("extract", help="build dataset.csv from a sample")
    p.add_argument("--sample", type=_positive, default=DEFAULT_SAMPLE)
    p.add_argument("--method", choices=METHODS, default="local")
    p.add_argument("--yes", action="store_true", help="skip the credit prompt")
    _add_out(p)
    return parser


def _add_out(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR, help="output folder")


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive number")
    return number


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    setup_logging(console, args.verbose)
    commands = {
        "collect": cmd_collect,
        "run": lambda a: cmd_run(a, resume=False),
        "resume": lambda a: cmd_run(a, resume=True),
        "report": cmd_report,
        "extract": cmd_extract,
    }
    try:
        return commands[args.command](args)
    except KeyboardInterrupt:
        console.print(
            "\n[yellow]Interrupted.[/] Continue later with the `resume` command."
        )
        return 130
    except (ConfigError, FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error:[/] {exc}")
        return 2
    except Olostep_BaseError as exc:
        logger.error("Olostep API error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
