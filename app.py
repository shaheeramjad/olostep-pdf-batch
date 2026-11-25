"""Streamlit UI in the style of olostep.com tool pages.

Launch with ``streamlit run app.py``. All work is done by the same package
functions the CLI uses; this file only lays out the page:

nav -> hero -> tool window (collect + run) -> results window -> dataset
window -> "how it works" steps.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from olostep_pdf_batch.batch import BatchState
from olostep_pdf_batch.config import (
    DEFAULT_CONCURRENCY,
    DEFAULT_LIMIT,
    DEFAULT_SAMPLE,
    LLM_EXTRACT_CREDITS,
    MAX_CONCURRENCY,
    URLS_CSV,
    ConfigError,
    Paths,
    RunSettings,
    api_key_is_set,
    load_api_key,
)
from olostep_pdf_batch.download import PLACEHOLDER_REASON
from olostep_pdf_batch.extract import extract_dataset, pick_sample
from olostep_pdf_batch.pipeline import BATCH_FAILED_REASON, collect
from olostep_pdf_batch.report import QUALITY_LABELS, load_summary, zip_markdown
from olostep_pdf_batch.utils import silence_sdk_warnings
from olostep_pdf_batch.verify import PAGE_MARKER, strip_front_matter
from ui import components as ui
from ui import theme
from ui.format import compact_number, gigabytes, short_duration
from ui.runner import RunState

PREVIEW_MAX_CHARS = 30_000  # rendering huge documents slows the page down
SOURCES = {
    "govuk": "GOV.UK publications",
    "upload": "Upload a CSV",
    "paste": "Paste URLs",
}
GOOD_SUCCESS_RATE = 0.95  # below this the success tile is not shown in green
# Short reason labels that fit a table column; the full text is in failures.csv.
REASON_LABELS = {
    PLACEHOLDER_REASON: "No Markdown ('not-implemented')",
    BATCH_FAILED_REASON: "Scrape failed (no reason given)",
}
HOW_IT_WORKS = [
    ("list", "Collect", "List public PDFs from GOV.UK with its Search and Content "
     "APIs, or bring your own CSV. Each gets a stable custom_id."),
    ("layers", "Batch", "Every PDF goes into one Olostep batch. The batch id is "
     "saved at once, so a run can always be resumed."),
    ("download", "Stream", "Markdown is downloaded while the batch runs, as soon "
     "as each PDF is done, with retries and atomic writes."),
    ("check", "Verify", "Each PDF is checked on disk: good text, near-empty, "
     "needs OCR or failed with a reason. Then extract a dataset."),
]  # fmt: skip


@dataclass(frozen=True)
class Settings:
    """Choices made in the tool window."""

    source: str
    limit: int
    concurrency: int
    rescrape: bool
    text: str | None  # uploaded or pasted URLs, for the file source


@st.cache_resource
def run_state() -> RunState:
    """One run state per server, so a run survives page reloads."""
    return RunState()


def main() -> None:
    st.set_page_config(page_title="Bulk PDF to Markdown | Olostep", layout="wide")
    silence_sdk_warnings()
    ui.apply_theme()
    paths = Paths()
    state = run_state()

    ui.nav()
    ui.hero(
        "Bulk PDF to Markdown",
        "Turn thousands of PDFs into an AI-ready Markdown dataset with one "
        "Olostep batch. Collect, scrape, verify and extract.",
        [("Start a run", "#run", "primary"), ("See how it works", "#how", "secondary")],
    )
    tool_window(paths, state)
    if not state.running and paths.manifest_csv.exists():
        results_window(paths)
        dataset_window(paths)
    how_it_works()


# --- Tool window: collect + run ----------------------------------------------


def tool_window(paths: Paths, state: RunState) -> None:
    st.markdown('<div id="run"></div>', unsafe_allow_html=True)
    body, footer = ui.window("tool", "PDF Batch", footer=not state.running)
    with body:
        ui.label("Where are the PDFs?")
        source = (
            st.segmented_control(
                "Source",
                list(SOURCES),
                format_func=SOURCES.get,
                default="govuk",
                label_visibility="collapsed",
            )
            or "govuk"
        )
        text = _source_text(source)
        urls = _load_csv(URLS_CSV)
        ui.help_line(_url_facts(urls) if not urls.empty else ["No PDF URLs yet."])
        if not urls.empty:
            with st.expander(f"Preview {len(urls):,} URLs"):
                _urls_table(urls)
        live = st.fragment(run_every=1.0 if state.running else None)(_live_progress)
        live(state)
    if footer is not None:
        with footer:
            _tool_footer(paths, source, text, urls)


def _source_text(source: str) -> str | None:
    if source == "upload":
        upload = st.file_uploader(
            "CSV with a pdf_url or url column",
            type=["csv", "txt"],
            label_visibility="collapsed",
        )
        return upload.getvalue().decode("utf-8-sig") if upload else None
    if source == "paste":
        return (
            st.text_area(
                "PDF URLs",
                placeholder="https://example.com/report.pdf\nOne URL per line",
                height=120,
                label_visibility="collapsed",
            )
            or None
        )
    return None


def _tool_footer(
    paths: Paths, source: str, text: str | None, urls: pd.DataFrame
) -> None:
    """Settings on the left, actions on the right, like the site's tools."""
    gear, _, resume_col, collect_col, run_col = st.columns(
        [1.5, 0.3, 1.7, 1.3, 1.3], vertical_alignment="center"
    )
    settings = _settings_popover(gear, source, text)
    if paths.state_file.exists():
        batch_id = BatchState.load(paths.state_file).batch_id
        if resume_col.button(
            "Resume last run", help=batch_id, use_container_width=True
        ):
            _start(paths, settings, resume=True)
    has_urls = not urls.empty
    if collect_col.button(
        "Collect URLs",
        type="secondary" if has_urls else "primary",
        disabled=source != "govuk" and not text,
        use_container_width=True,
    ):
        _collect(settings)
    if run_col.button(
        "Start batch",
        type="primary",
        disabled=not (has_urls and api_key_is_set()),
        use_container_width=True,
    ):
        _confirm_start(paths, settings, len(urls))
    if not api_key_is_set():
        ui.note("OLOSTEP_API_KEY is missing: add it to .env to run a batch.", "error")


def _settings_popover(
    column: DeltaGenerator, source: str, text: str | None
) -> Settings:
    with column.popover("Settings", icon=":material/tune:"):
        limit = st.number_input("Max PDFs to collect", 10, DEFAULT_LIMIT, 100, step=100)
        concurrency = st.slider(
            "Parallel downloads",
            8,
            MAX_CONCURRENCY,
            DEFAULT_CONCURRENCY,
            help=f"Capped at {MAX_CONCURRENCY}: the API limits open requests.",
        )
        rescrape = st.checkbox("Re-scrape PDFs the batch can't convert (1 credit each)")
    return Settings(source, int(limit), concurrency, rescrape, text)


def _collect(settings: Settings) -> None:
    bar = st.progress(0.0, text="Collecting PDF URLs...")
    records = asyncio.run(
        collect(
            "govuk" if settings.source == "govuk" else "file",
            limit=settings.limit,
            text=settings.text,
            on_progress=lambda done, total: bar.progress(
                done / total, text=f"Collecting PDF URLs... {done:,} of {total:,}"
            ),
        )
    )
    bar.empty()
    st.toast(f"{len(records):,} PDF URLs collected")
    st.rerun()


def _url_facts(urls: pd.DataFrame) -> list[str]:
    items = [f"{len(urls):,} PDFs ready"]
    organisations = urls["organisation"].replace("", pd.NA).nunique()
    if organisations:
        items.append(f"{organisations:,} organisations")
    if urls["pages"].notna().any():
        items.append(f"{int(urls['pages'].sum()):,} pages")
    if urls["file_size_bytes"].notna().any():
        items.append(gigabytes(urls["file_size_bytes"].sum()))
    items.append(f"about {len(urls):,} credits")
    return items


def _urls_table(urls: pd.DataFrame) -> None:
    st.dataframe(
        urls[["title", "organisation", "pages", "pdf_url"]].head(200),
        hide_index=True,
        use_container_width=True,
        height=260,
        column_config={
            "title": st.column_config.TextColumn("Title", width="large"),
            "organisation": st.column_config.TextColumn("Organisation", width="medium"),
            "pages": st.column_config.NumberColumn("Pages", width="small"),
            "pdf_url": st.column_config.LinkColumn(
                "PDF", display_text="Open PDF", width="small"
            ),
        },
    )


@st.dialog("Start the batch?")
def _confirm_start(paths: Paths, settings: Settings, count: int) -> None:
    st.write(
        f"This submits **{count:,} PDFs** as one Olostep batch and uses about "
        f"**{count:,} credits**."
    )
    if settings.rescrape:
        st.write("Failed PDFs will then be re-scraped one by one (1 credit each).")
    if paths.state_file.exists():
        ui.note("A new batch replaces the saved run in output/batch.json.")
    cancel, confirm = st.columns(2)
    if cancel.button("Cancel", use_container_width=True):
        st.rerun()
    if confirm.button("Start batch", type="primary", use_container_width=True):
        _start(paths, settings, resume=False)


def _start(paths: Paths, settings: Settings, *, resume: bool) -> None:
    try:
        run_settings = RunSettings(
            api_key=load_api_key(),
            paths=paths,
            concurrency=settings.concurrency,
            rescrape=settings.rescrape,
        )
    except ConfigError as exc:
        ui.note(str(exc), "error")
        return
    run_state().start(run_settings, resume=resume)
    st.rerun()


def _live_progress(state: RunState) -> None:
    snap = state.snapshot()
    if snap.status == "idle":
        return
    total = snap.submitted or 1
    ui.tiles(
        [
            ui.Tile("Submitted", f"{snap.submitted:,}", snap.batch_id),
            ui.Tile(
                "Scraped",
                f"{snap.completed:,}",
                f"{snap.completed / total:.0%}",
                "primary",
            ),
            ui.Tile("Downloaded", f"{snap.downloaded:,}", "Markdown files", "success"),
            ui.Tile("Failed", f"{snap.failed:,}", "", "error" if snap.failed else ""),
            ui.Tile("Elapsed", short_duration(snap.elapsed), "since submit"),
        ]
    )
    st.progress(min(snap.completed / total, 1.0), text="Scraping")
    st.progress(min((snap.downloaded + snap.failed) / total, 1.0), text="Downloading")
    for line in snap.milestones:
        ui.milestone(line)
    if snap.status == "error":
        ui.note(f"Run stopped: {snap.error}. Use Resume to continue.", "error")
    elif snap.status == "done" and st.session_state.get("shown_run") != snap.batch_id:
        st.session_state["shown_run"] = snap.batch_id
        st.rerun()  # full rerun, so the results window appears


# --- Results window ------------------------------------------------------------


def results_window(paths: Paths) -> None:
    summary = load_summary(paths)
    manifest = _load_csv(paths.manifest_csv)
    title = f"Results · {summary['batch_id']}" if summary else "Results"
    body, footer = ui.window("results", title, footer=True)
    with body:
        if summary:
            _summary_tiles(summary)
        good = manifest[manifest["status"] == "succeeded"]
        failed = manifest[manifest["status"] == "failed"]
        docs_tab, fail_tab, time_tab = st.tabs(
            [f"Documents ({len(good):,})", f"Failures ({len(failed):,})", "Timeline"]
        )
        with docs_tab:
            selected = _documents_table(good)
            if selected is not None:
                _preview(selected)
        with fail_tab:
            _failures(failed)
        with time_tab:
            _timeline(summary)
    with footer:
        _downloads(paths)


def _summary_tiles(summary: dict) -> None:
    timings, quality = summary["timings"], summary["quality"]
    rate = summary["success_rate"]
    low_text = quality["empty"] + quality["near_empty"]
    ui.tiles(
        [
            ui.Tile(
                "Success rate",
                f"{rate:.1%}",
                f"{summary['succeeded']:,} of {summary['submitted']:,} PDFs",
                "success" if rate >= GOOD_SUCCESS_RATE else "",
            ),
            ui.Tile(
                "Total time",
                short_duration(timings["total"]),
                f"scrape {short_duration(timings['scrape'])}",
            ),
            ui.Tile(
                "Failed",
                f"{summary['failed']:,}",
                "see Failures",
                "error" if summary["failed"] else "",
            ),
            ui.Tile(
                "Needs OCR", f"{quality['likely_scanned']:,}", "pages without text"
            ),
            ui.Tile(
                "Text",
                compact_number(summary["total_chars"]),
                f"characters · {low_text:,} near-empty",
            ),
        ]
    )


def _documents_table(good: pd.DataFrame) -> pd.Series | None:
    """Searchable, filterable list of converted PDFs. Returns the selection."""
    search_col, filter_col = st.columns([2, 1])
    query = search_col.text_input(
        "Search",
        placeholder="Search titles, organisations and URLs",
        label_visibility="collapsed",
    )
    qualities = filter_col.multiselect(
        "Quality",
        list(QUALITY_LABELS),
        format_func=QUALITY_LABELS.get,
        placeholder="All quality",
        label_visibility="collapsed",
    )
    view = good
    if query:
        haystack = view["title"] + " " + view["organisation"] + " " + view["pdf_url"]
        view = view[haystack.str.contains(query, case=False, regex=False)]
    if qualities:
        view = view[view["quality"].isin(qualities)]
    shown = view.assign(quality=view["quality"].map(QUALITY_LABELS))
    columns = [
        "title",
        "organisation",
        "published",
        "pages",
        "quality",
        "chars",
        "pdf_url",
    ]
    event = st.dataframe(
        shown[columns],
        hide_index=True,
        use_container_width=True,
        height=340,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "title": st.column_config.TextColumn("Title", width="large"),
            "organisation": st.column_config.TextColumn("Organisation", width="medium"),
            "published": st.column_config.TextColumn("Published", width="small"),
            "pages": st.column_config.NumberColumn("Pages", width="small"),
            "quality": st.column_config.TextColumn("Quality", width="small"),
            "chars": st.column_config.NumberColumn(
                "Characters", format="localized", width="small"
            ),
            "pdf_url": st.column_config.LinkColumn(
                "PDF", display_text="Open PDF", width="small"
            ),
        },
    )
    ui.note(f"{len(view):,} documents. Select a row to preview its Markdown.")
    rows = event.selection.rows
    return view.iloc[rows[0]] if rows else None


def _preview(row: pd.Series) -> None:
    text_col, meta_col = st.columns([3, 1])
    with meta_col:
        pages = f"{int(row['pages']):,} pages" if pd.notna(row["pages"]) else ""
        quality = QUALITY_LABELS.get(row["quality"], "")
        st.markdown(
            f'<div class="op-preview-title">{row["title"]}</div>'
            f'<div class="op-preview-meta">{row["organisation"]}<br>'
            f"{row['published']} · {pages}<br>{quality}<br><br>"
            f'<a href="{row["pdf_url"]}" target="_blank">Open original PDF</a><br>'
            f'<a href="{row["publication_url"]}" target="_blank">GOV.UK page</a></div>',
            unsafe_allow_html=True,
        )
    with text_col, st.container(height=480, border=True, key="preview"):
        body = strip_front_matter(Path(row["file"]).read_text(encoding="utf-8"))
        st.markdown(_readable_pages(body[:PREVIEW_MAX_CHARS]) or "_(empty)_")
        if len(body) > PREVIEW_MAX_CHARS:
            ui.note(
                f"Showing the first {PREVIEW_MAX_CHARS:,} of {len(body):,} characters."
            )


def _readable_pages(markdown: str) -> str:
    """Show Olostep's ``# Page N`` headings as small dividers, not titles."""
    dividers = PAGE_MARKER.sub(r"\n---\n*Page \1*\n", markdown).strip()
    return dividers.removeprefix("---").lstrip()  # no rule above page 1


def _failures(failed: pd.DataFrame) -> None:
    if failed.empty:
        ui.empty("No failures. Every PDF was converted.")
        return
    counts = failed["error"].value_counts()
    ui.chips(
        [(f"{n:,}", REASON_LABELS.get(reason, reason)) for reason, n in counts.items()]
    )
    if PLACEHOLDER_REASON in counts:
        ui.note(
            "Tip: turn on re-scrape in Settings and resume the run to retry these "
            "one by one."
        )
    shown = failed.assign(error=failed["error"].map(lambda r: REASON_LABELS.get(r, r)))
    st.dataframe(
        shown[
            ["title", "organisation", "error", "pages", "file_size_bytes", "pdf_url"]
        ],
        hide_index=True,
        use_container_width=True,
        height=320,
        column_config={
            "title": st.column_config.TextColumn("Title", width="large"),
            "organisation": st.column_config.TextColumn("Organisation", width="medium"),
            "error": st.column_config.TextColumn("Reason"),
            "pages": st.column_config.NumberColumn("Pages", width="small"),
            "file_size_bytes": st.column_config.NumberColumn(
                "Size", format="bytes", width="small"
            ),
            "pdf_url": st.column_config.LinkColumn(
                "PDF", display_text="Open PDF", width="small"
            ),
        },
    )


def _timeline(summary: dict | None) -> None:
    if not summary or not summary.get("timeline"):
        ui.empty("No timeline recorded for this run.")
        return
    data = pd.DataFrame(summary["timeline"], columns=["seconds", "PDFs scraped"])
    data["Minutes since submit"] = data["seconds"] / 60
    st.line_chart(
        data,
        x="Minutes since submit",
        y="PDFs scraped",
        color=theme.PRIMARY,
        height=280,
    )
    submitted = summary["submitted"]
    for percent, seconds in summary["timings"]["milestones"].items():
        ui.milestone(
            f"{submitted:,} PDFs: {percent}% done in {short_duration(seconds)}"
        )


def _downloads(paths: Paths) -> None:
    files = [p for p in (paths.manifest_csv, paths.dataset_csv) if p.exists()]
    columns = st.columns(4)
    for column, path in zip(columns, files, strict=False):
        column.download_button(
            path.name,
            path.read_bytes(),
            path.name,
            "text/csv",
            use_container_width=True,
        )
    zip_col = columns[len(files)]
    zip_path = paths.out_dir / "markdown.zip"
    if _is_newer(zip_path, paths.manifest_csv):
        zip_col.download_button(
            "markdown.zip",
            zip_path.read_bytes(),
            zip_path.name,
            "application/zip",
            use_container_width=True,
        )
    elif zip_col.button("Build Markdown ZIP", use_container_width=True):
        with st.spinner("Zipping Markdown files..."):
            zip_markdown(paths)
        st.rerun()


# --- Dataset window --------------------------------------------------------------


def dataset_window(paths: Paths) -> None:
    body, _ = ui.window("dataset", "Dataset")
    with body:
        ui.label("Extract title, organisation, date, pages and a summary")
        method_col, sample_col, button_col = st.columns(
            [1.6, 1, 1], vertical_alignment="bottom"
        )
        method = (
            method_col.segmented_control(
                "Method",
                ["local", "olostep"],
                format_func={
                    "local": "Local (free)",
                    "olostep": "Olostep LLM extract",
                }.get,
                default="local",
            )
            or "local"
        )
        sample = int(sample_col.number_input("Sample size", 1, 500, DEFAULT_SAMPLE))
        confirmed = True
        if method == "olostep":
            credits = sample * LLM_EXTRACT_CREDITS
            confirmed = st.checkbox(
                f"Re-scrape {sample} PDFs for about {credits:,} credits"
            )
        if button_col.button(
            "Extract", type="primary", disabled=not confirmed, use_container_width=True
        ):
            api_key = load_api_key() if method == "olostep" else None
            with st.spinner(f"Extracting {sample} PDFs..."):
                rows = pick_sample(paths, sample)
                asyncio.run(
                    extract_dataset(rows, paths, method=method, api_key=api_key)
                )
        dataset = _load_csv(paths.dataset_csv)
        if dataset.empty:
            ui.empty("No dataset yet. Start with a small sample.")
            return
        st.dataframe(
            dataset[["title", "organisation", "publication_date", "pages", "summary"]],
            hide_index=True,
            use_container_width=True,
            height=300,
            column_config={
                "title": st.column_config.TextColumn("Title", width="medium"),
                "organisation": st.column_config.TextColumn("Organisation"),
                "publication_date": st.column_config.TextColumn("Date", width="small"),
                "pages": st.column_config.NumberColumn("Pages", width="small"),
                "summary": st.column_config.TextColumn("Summary", width="large"),
            },
        )


# --- How it works ------------------------------------------------------------------


def how_it_works() -> None:
    st.markdown('<div id="how"></div>', unsafe_allow_html=True)
    ui.section(
        "How to turn PDFs into an AI-ready dataset in 4 steps",
        "One batch for every PDF, streamed to Markdown and checked on disk.",
    )
    ui.steps(HOW_IT_WORKS)


# --- Data --------------------------------------------------------------------------


def _is_newer(path: Path, than: Path) -> bool:
    """True if ``path`` exists and was written after ``than``."""
    return path.exists() and path.stat().st_mtime >= than.stat().st_mtime


def _load_csv(path: Path) -> pd.DataFrame:
    """Read a CSV (cached until the file changes); empty if it doesn't exist."""
    if not path.exists():
        return pd.DataFrame()
    return _read_csv(str(path), path.stat().st_mtime)


@st.cache_data(show_spinner=False)
def _read_csv(path: str, mtime: float) -> pd.DataFrame:
    """``mtime`` is part of the cache key, so an updated file is re-read."""
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    for column in ("pages", "chars", "file_size_bytes"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


main()
