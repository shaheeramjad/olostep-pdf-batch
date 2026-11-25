"""Quality checks: empty, near-empty, scanned and placeholder results."""

import asyncio
from types import SimpleNamespace

import pytest

from olostep_pdf_batch.download import (
    NoContentError,
    markdown_from_result,
    with_front_matter,
)
from olostep_pdf_batch.sources import PdfRecord
from olostep_pdf_batch.verify import (
    assess,
    count_page_markers,
    readable_chars,
    strip_front_matter,
    verify,
)

PROSE = "The department published its annual report on public services. " * 40


def test_assess_classes() -> None:
    assert assess("", pages=None) == ("empty", 0)
    assert assess("   \n\n ![](image.png) ", pages=None)[0] == "empty"
    assert assess("# Title\n\nShort note.", pages=1)[0] == "near_empty"
    assert assess(PROSE, pages=200)[0] == "likely_scanned"
    assert assess(PROSE, pages=2)[0] == "ok"
    assert assess(PROSE, pages=None)[0] == "ok"  # page count unknown


def test_image_only_pdf_is_likely_scanned() -> None:
    # Real output for a scanned 4-page GOV.UK PDF: page headings, no text.
    body = "# Page 1\n\n# Page 2\n\n# Page 3\n\n# Page 4"
    assert count_page_markers(body) == 4
    assert assess(body, pages=None) == ("likely_scanned", 0)


def test_one_page_poster_is_near_empty_not_scanned() -> None:
    assert assess("# Page 1\nAppendix E: Failsafe process", pages=1)[0] == "near_empty"


def test_readable_chars_ignores_markup() -> None:
    assert readable_chars("| a | b |\n|---|---|") == 2
    assert readable_chars("## Hello\n\n![chart](x.png)") == 5


def test_strip_front_matter() -> None:
    record = PdfRecord("https://example.com/a.pdf", title='A: "quoted" title')
    text = with_front_matter(record, "# Body\n\nText.")
    assert text.startswith('---\nsource_pdf: "https://example.com/a.pdf"\n')
    assert strip_front_matter(text) == "# Body\n\nText."


def test_verify_uses_files_on_disk(tmp_path) -> None:
    ok = PdfRecord("https://example.com/ok.pdf", pages=1)
    missing = PdfRecord("https://example.com/missing.pdf")
    batch_failed = PdfRecord("https://example.com/failed.pdf")
    (tmp_path / f"{ok.custom_id}.md").write_text(with_front_matter(ok, PROSE))

    rows = verify(
        [ok, missing, batch_failed],
        tmp_path,
        errors={batch_failed.custom_id: "scrape failed in batch"},
    )
    assert [r.status for r in rows] == ["succeeded", "failed", "failed"]
    assert rows[0].quality == "ok"
    assert rows[1].error == "no Markdown file was saved"
    assert rows[2].error == "scrape failed in batch"


def test_placeholder_content_is_not_markdown() -> None:
    result = SimpleNamespace(markdown_content="not-implemented")
    with pytest.raises(NoContentError):
        asyncio.run(markdown_from_result(result))


def test_missing_markdown_attribute_means_empty() -> None:
    # The SDK leaves null fields unset, so the attribute may not exist at all.
    assert asyncio.run(markdown_from_result(SimpleNamespace())) == ""
    real = SimpleNamespace(markdown_content="# Title\n\nText")
    assert asyncio.run(markdown_from_result(real)) == "# Title\n\nText"
