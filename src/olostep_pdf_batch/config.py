"""All tunable constants and runtime settings live here."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from . import __version__

API_KEY_ENV = "OLOSTEP_API_KEY"
API_KEY_PLACEHOLDER = "your_key_here"

# --- Paths -------------------------------------------------------------------
DATA_DIR = Path("data")
OUTPUT_DIR = Path("output")
URLS_CSV = DATA_DIR / "pdf_urls.csv"
COLLECT_META = DATA_DIR / "collect.json"  # source, count and collect time

# --- Collect (GOV.UK) --------------------------------------------------------
DEFAULT_LIMIT = 5_000
SHUFFLE_SEED = 42  # fixed so the same --limit always picks the same PDFs
USER_AGENT = (
    f"olostep-pdf-batch/{__version__} "
    "(+https://github.com/olostep-api/olostep-pdf-batch)"
)
GOVUK_BASE = "https://www.gov.uk"
# Publication types that usually carry PDF reports. Searched together (OR).
GOVUK_DOCUMENT_TYPES = (
    "policy_paper",
    "research",
    "corporate_report",
    "independent_report",
    "guidance",
)
GOVUK_PAGE_SIZE = 1_000  # max results per Search API request
GOVUK_CONCURRENCY = 4  # parallel Content API requests (be polite)
GOVUK_REQUEST_DELAY = 0.1  # seconds each worker waits between requests
GOVUK_TIMEOUT = 30.0
MAX_PDFS_PER_PUBLICATION = 2  # keeps the dataset diverse

# --- Run (Olostep) -----------------------------------------------------------
DEFAULT_CONCURRENCY = 50  # parallel Markdown downloads
# The SDK sends every request over one HTTP/2 connection, and the API allows
# 128 streams on it ("Max outbound streams is 128"). Half of that leaves room
# for polling, listing and slow streams.
MAX_CONCURRENCY = 64
POLL_INTERVAL = 2.0  # seconds between batch status checks
FEED_INTERVAL = 5.0  # seconds between "which PDFs are ready?" listings
ITEMS_PAGE_SIZE = 10_000  # the maximum the batch items endpoint allows
# No timeout of our own around SDK calls: the SDK has timeouts and retries,
# and cancelling an HTTP/2 request from outside can leave its stream open.
LISTING_RETRIES = 3  # for the final "which PDFs are done?" listing
LISTING_BACKOFF_SECONDS = 5.0
DOWNLOAD_RETRIES = 2  # retries after the first attempt
BACKOFF_SECONDS = 1.0  # doubled on each retry: 1s, 2s
MILESTONES = (50, 90, 99, 100)  # percent of PDFs scraped
RESCRAPE_CONCURRENCY = 10  # parallel /scrapes calls for --rescrape

# --- Verify ------------------------------------------------------------------
NEAR_EMPTY_CHARS = 200  # less text than this is treated as empty
# Text PDFs give ~1,500+ characters per page. Far less usually means the pages
# are images (a scanned document) and little text could be read from them.
SCANNED_CHARS_PER_PAGE = 100

# --- Extract -----------------------------------------------------------------
DEFAULT_SAMPLE = 50
SUMMARY_MAX_CHARS = 240
LLM_EXTRACT_CREDITS = 10  # measured: 30 credits for 3 PDFs (scrape included)


class ConfigError(Exception):
    """Raised when required configuration (like the API key) is missing."""


@dataclass(frozen=True)
class Paths:
    """Every file the pipeline reads or writes, under one output folder."""

    out_dir: Path = OUTPUT_DIR

    @property
    def markdown_dir(self) -> Path:
        """One Markdown file per PDF, named ``<custom_id>.md``."""
        return self.out_dir / "markdown"

    @property
    def state_file(self) -> Path:
        """Batch id and timings, written as soon as the batch exists."""
        return self.out_dir / "batch.json"

    @property
    def submitted_csv(self) -> Path:
        """Exactly what was sent to Olostep (used by resume and report)."""
        return self.out_dir / "submitted.csv"

    @property
    def manifest_csv(self) -> Path:
        return self.out_dir / "manifest.csv"

    @property
    def failures_csv(self) -> Path:
        return self.out_dir / "failures.csv"

    @property
    def summary_json(self) -> Path:
        return self.out_dir / "summary.json"

    @property
    def dataset_csv(self) -> Path:
        return self.out_dir / "dataset.csv"


@dataclass(frozen=True)
class RunSettings:
    """Options for one batch run."""

    api_key: str = field(repr=False)  # never show the key in logs or reprs
    paths: Paths = field(default_factory=Paths)
    concurrency: int = DEFAULT_CONCURRENCY
    rescrape: bool = False  # re-scrape batch failures one by one (1 credit each)


def api_key_is_set() -> bool:
    """True when an API key is configured (the key itself is never returned)."""
    try:
        load_api_key()
    except ConfigError:
        return False
    return True


def load_api_key() -> str:
    """Read ``OLOSTEP_API_KEY`` from the environment or a local ``.env`` file.

    Raises:
        ConfigError: If the key is not set.
    """
    load_dotenv(Path.cwd() / ".env")
    key = os.getenv(API_KEY_ENV, "").strip()
    if not key or key == API_KEY_PLACEHOLDER:
        raise ConfigError(
            f"{API_KEY_ENV} is not set. Copy .env.example to .env and add "
            "your key from https://www.olostep.com/dashboard/api-keys"
        )
    return key
