"""Source parsing, filtering and de-duplication (no network)."""

from olostep_pdf_batch.sources import (
    PdfRecord,
    finalize,
    is_pdf_url,
    load_records,
    save_records,
)
from olostep_pdf_batch.sources.file import parse_text
from olostep_pdf_batch.sources.govuk import pdfs_from_content

ASSETS = "https://assets.publishing.service.gov.uk/media"


def _content(attachments: list[dict]) -> dict:
    """A trimmed Content API response, shaped like the real one."""
    return {
        "base_path": "/government/publications/example",
        "title": "Example publication",
        "first_published_at": "2025-07-03T00:00:00+01:00",
        "links": {"organisations": [{"title": "Department for Example"}]},
        "details": {"attachments": attachments},
    }


def test_is_pdf_url() -> None:
    assert is_pdf_url(f"{ASSETS}/abc/report.pdf")
    assert is_pdf_url(f"{ASSETS}/abc/REPORT.PDF")
    assert not is_pdf_url(f"{ASSETS}/abc/data.csv")
    assert not is_pdf_url("/government/publications/x/report.pdf")  # relative
    assert not is_pdf_url("ftp://example.com/report.pdf")


def test_pdfs_from_content_keeps_only_pdf_files() -> None:
    content = _content(
        [
            {"attachment_type": "html", "url": "/government/publications/x/html"},
            {"url": f"{ASSETS}/1/data.csv", "content_type": "text/csv"},
            {
                "url": f"{ASSETS}/2/report.pdf",
                "title": "The report",
                "number_of_pages": 12,
                "file_size": 2048,
            },
        ]
    )
    [record] = pdfs_from_content(content)
    assert record.pdf_url == f"{ASSETS}/2/report.pdf"
    assert record.title == "The report"
    assert record.pages == 12
    assert record.file_size_bytes == 2048
    assert record.organisation == "Department for Example"
    assert record.published == "2025-07-03"
    assert (
        record.publication_url == "https://www.gov.uk/government/publications/example"
    )


def test_pdfs_from_content_caps_per_publication() -> None:
    attachments = [{"url": f"{ASSETS}/{i}/part-{i}.pdf"} for i in range(5)]
    assert len(pdfs_from_content(_content(attachments))) == 2


def test_pdfs_from_content_handles_missing_fields() -> None:
    assert pdfs_from_content({}) == []
    [record] = pdfs_from_content(_content([{"url": f"{ASSETS}/1/a.pdf"}]))
    assert record.pages is None
    assert record.title == "Example publication"  # falls back to publication


def test_finalize_dedupes_and_caps() -> None:
    records = [PdfRecord(f"{ASSETS}/{i % 3}/a.pdf") for i in range(10)]
    result = finalize(records, limit=2, seed=1)
    assert len(result) == 2
    assert len({r.pdf_url for r in result}) == 2


def test_finalize_is_deterministic_for_a_seed() -> None:
    records = [PdfRecord(f"{ASSETS}/{i}/a.pdf") for i in range(50)]
    first = finalize(records, limit=10, seed=42)
    again = finalize(list(reversed(records)), limit=10, seed=42)
    assert first == again


def test_parse_text_csv_with_url_column() -> None:
    text = "Title,PDF_URL\nAnnual report,https://example.com/a.pdf\nBad,not-a-url\n"
    [record] = parse_text(text)
    assert record.pdf_url == "https://example.com/a.pdf"
    assert record.title == "Annual report"


def test_parse_text_plain_lines_with_comments() -> None:
    text = "# my PDFs\nhttps://example.com/a.pdf\n\nhttps://arxiv.org/pdf/2401.00001\n"
    urls = [r.pdf_url for r in parse_text(text)]
    assert urls == ["https://example.com/a.pdf", "https://arxiv.org/pdf/2401.00001"]


def test_records_round_trip_through_csv(tmp_path) -> None:
    records = [PdfRecord(f"{ASSETS}/1/a.pdf", title='Says "hi", ok', pages=3)]
    path = tmp_path / "urls.csv"
    save_records(path, records)
    assert load_records(path) == records


def test_saved_list_round_trips_through_file_source(tmp_path) -> None:
    """A list saved by `collect` keeps its metadata when used as --input."""
    record = PdfRecord(
        f"{ASSETS}/1/a.pdf",
        title="Annual report",
        organisation="Department for Example",
        published="2025-07-03",
        pages=12,
        file_size_bytes=2048,
    )
    path = tmp_path / "pdf_urls.csv"
    save_records(path, [record])
    assert parse_text(path.read_text()) == [record]
