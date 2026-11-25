"""Helpers: ids, atomic writes, durations and local extraction."""

from olostep_pdf_batch.extract import first_date, first_heading, first_sentence
from olostep_pdf_batch.utils import (
    atomic_write_text,
    custom_id_for_url,
    format_duration,
    slugify,
)


def test_slugify() -> None:
    assert slugify("Annual Report 2024.pdf") == "annual-report-2024-pdf"
    assert slugify("???") == "pdf"


def test_custom_id_is_deterministic_readable_and_unique() -> None:
    url = "https://assets.example.gov.uk/media/1/Annual%20Report_2024.pdf"
    other = "https://assets.example.gov.uk/media/2/Annual%20Report_2024.pdf"
    assert custom_id_for_url(url) == custom_id_for_url(url)
    assert custom_id_for_url(url).startswith("annual-report-2024-")
    assert custom_id_for_url(url) != custom_id_for_url(other)


def test_atomic_write_leaves_no_temp_files(tmp_path) -> None:
    path = tmp_path / "out" / "a.md"
    atomic_write_text(path, "hello")
    atomic_write_text(path, "world")
    assert path.read_text() == "world"
    assert [p.name for p in path.parent.iterdir()] == ["a.md"]


def test_format_duration() -> None:
    assert format_duration(41.26) == "41.3s"
    assert format_duration(432) == "7m 12s"
    assert format_duration(3723) == "1h 02m 03s"


def test_first_sentence_skips_headings_lists_and_contents() -> None:
    body = (
        "# Report title\n\n"
        "Contents 3\n\n"
        "- a bullet point that is long enough to count as words\n\n"
        "Introduction .......... 4\n\n"
        "This report sets out how the service will improve over the next "
        "five years. It also covers funding."
    )
    assert first_sentence(body) == (
        "This report sets out how the service will improve over the next five years."
    )


def test_first_date() -> None:
    assert first_date("Published 12 March 2024 by the department") == "12 March 2024"
    assert first_date("No dates here") == ""


def test_first_heading_skips_page_markers() -> None:
    assert first_heading("# Page 1\n\n## Annual report 2025\n\nText") == (
        "Annual report 2025"
    )
