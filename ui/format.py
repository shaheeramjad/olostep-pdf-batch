"""Short, screen-friendly number and time formats for cards and captions."""

from __future__ import annotations

from olostep_pdf_batch.utils import format_duration

_COMPACT = ((1e9, "B"), (1e6, "M"))


def compact_number(value: float) -> str:
    """Exact up to a million (``5,000``), then short: ``304.7M``."""
    for size, suffix in _COMPACT:
        if value >= size:
            return f"{value / size:.1f}{suffix}"
    return f"{value:,.0f}"


def short_duration(seconds: float) -> str:
    """Like ``format_duration`` but drops seconds past an hour: ``1h 08m``."""
    if seconds < 3600:
        return format_duration(seconds)
    hours, minutes = divmod(round(seconds / 60), 60)
    return f"{hours}h {minutes:02d}m"


def gigabytes(total_bytes: float) -> str:
    return f"{total_bytes / 1e9:.1f} GB"
