"""Progress events, so the CLI (rich) and the UI (Streamlit) share one pipeline.

The pipeline only calls these methods; it never prints or renders anything
itself. The CLI implements them with rich progress bars, the Streamlit app
with a thread-safe state object that the page polls.
"""

from __future__ import annotations

from typing import Protocol


class RunListener(Protocol):
    """Receives progress from a running batch."""

    def batch_created(self, batch_id: str, total: int) -> None:
        """The batch exists (its id is already saved to ``batch.json``)."""

    def scrape_progress(self, completed: int, total: int, elapsed: float) -> None:
        """Olostep has finished ``completed`` of ``total`` PDFs."""

    def download_progress(self, finished: int, failed: int) -> None:
        """``finished`` downloads are done (``failed`` of them failed)."""

    def milestone(self, line: str) -> None:
        """A headline such as ``5,000 PDFs: 99% done in 7m 12s``."""
