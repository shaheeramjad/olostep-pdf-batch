# PDF feedback for Olostep

Real friction points found while building this project with `olostep` 1.2.3
(2026-11-25). Every item comes from a real run, the installed SDK source or
the live docs. Each has a reproducible example.

Runs used:

* `batch_fbseya59a2ww`: 100 GOV.UK PDFs (28 organisations, 1 to 362 pages,
  up to 39 MB, 3,807 pages in total).
* `batch_a7t7m4srs3hl`: 2 GOV.UK FOI PDFs, one of them image-only (scanned).

---

## 1. Batch items "complete" with the text `not-implemented` instead of Markdown

**Priority: High**

**Issue.** 37 of 100 PDFs in one batch finished as completed. `/retrieve`
returned `success: true` with `markdown_content` and `html_content` set to the
literal string `not-implemented`. The hosted file
(`markdown_hosted_url`) contains the same string, and `json` returns nothing.
The batch was charged 1 credit for each of them.

**Example.**
`https://assets.publishing.service.gov.uk/media/694520572f02611393508914/Arranging_removal.pdf`
(48 pages, 0.5 MB) in `batch_fbseya59a2ww`: retrieve gives `not-implemented`.
The same URL through `client.scrapes.create(..., formats=["markdown"])`
returns 88,515 characters of good Markdown in one call.

Re-scraping the 37 one by one with `/scrapes` recovered 35 in 17 seconds
(37 credits). Two still returned `not-implemented` on `/scrapes`, including
`dhsc-annual-report-and-accounts-2025-2026-print-ready-corrected-23-november.pdf`
(362 pages, 25 MB).

Without a check for this string, a pipeline reports 100% success while 37%
of the "Markdown" is one word. Olostep's own `pdf-scraper` repo already
filters it (`PLACEHOLDER_VALUES = {"not-implemented", ...}`), so the behaviour
seems known internally, but it is not documented.

**Suggestion.**
1. Mark these items as `failed` (with a reason) instead of `completed`, and do
   not charge for them.
2. Or let batch items fall back to the same PDF path `/scrapes` uses, since
   that path works for 35 of the 37.
3. Until then, document the placeholder and the workaround.

## 2. SDK: downloads at scale die with "Max outbound streams is 128"

**Priority: High**

**Issue.** The SDK sends every request over one HTTP/2 connection
(`http2=True` whenever `h2` is installed; `max_connections=200`). The API
allows 128 concurrent streams on it. Above that, requests fail with
`LocalProtocolError: Max outbound streams is 128, 128 open`. The SDK retries
4 times and then raises.

Worse, it also happened at 100 concurrent downloads (about 102 requests in
flight). The likely cause: requests cancelled from outside (an
`asyncio.wait_for` timeout shorter than the SDK's own 150-second timeout) leave
their HTTP/2 streams counted as open, so the pool fills up over time.

**Example.** 5,000-PDF batch `batch_9dc11ru501ze`, downloading with
`client.retrieve.get` at concurrency 200, then again at 100: downloads
started fast, then slowed to about one PDF per second, with 3,327 stream
errors in the log. The final `batches.items` call then failed and stopped the
run.

**Suggestion.**
1. Cap concurrent requests in the SDK's transport below the server's stream
   limit (or open more connections), instead of failing.
2. Document a safe concurrency for `retrieve` and say not to cancel SDK calls
   from outside (or make cancellation release the stream).
3. Expose an option to turn HTTP/2 off.

## 3. No reason for failed batch items

**Priority: High**

**Issue.** `GET /v1/batches/{id}/items?status=failed` returns `custom_id`,
`url` and `retrieve_id` only. The API reference lists no error field, and the
SDK's `BatchItemsResponseListItem` has none. For PDFs the reason matters a lot
(too large? timed out? encrypted? not a PDF?), because it decides whether to
retry, split or OCR the file.

**Example.** This project's `failures.csv` can only say "scrape failed in
batch (the API gives no reason)". To give users something to go on, it adds
the page count and file size from the source.

**Suggestion.** Add `error: {code, message}` to failed items, and to items
that complete without content (see 1).

## 4. Scanned PDFs: no OCR, and no signal that OCR was needed

**Priority: Medium**

**Issue.** An image-only PDF comes back as page headings with no text. The
result has `success: true` and no flag saying the pages had no text layer.

**Example.**
`https://assets.publishing.service.gov.uk/media/5a78b83340f0b62b22cbc3f4/717785.pdf`
(4 pages, `pdffonts` shows no fonts, `pdftotext` yields 4 characters). The
Markdown is:

```
# Page 1

# Page 2

# Page 3

# Page 4
```

This project detects it locally ("likely scanned": pages but no text).

**Suggestion.** Either OCR image-only pages (an `ocr: true` option would do),
or return `page_metadata` like `{"pages": 4, "pages_without_text": 4}` so
users can route those files to OCR.

## 5. No PDF documentation page (the old link is a 404)

**Priority: Medium**

**Issue.** `https://docs.olostep.com/features/scraping/scrape-pdf`, linked from
the `olostep-api/pdf-scraper` README, returns 404. The current docs index
(`llms.txt`) has no PDF page. PDFs appear in one bullet on the Scrape page
("Handles dynamic content: js-rendered sites, login flows via actions, PDFs").

Questions a PDF user has, with no answer in the docs:

* Is there a page or size limit? (A 362-page, 25 MB PDF failed both ways.)
* Is there OCR?
* How are tables represented? (None of the 98 Markdown files here contains
  a Markdown table row, although annual reports and accounts were included.)
* Is the `# Page N` heading per page a stable format users can rely on? (It
  differs by endpoint: see item 6.)
* Do PDFs cost the same as web pages? (Measured: yes, 1 credit per PDF in a
  batch. `/retrieve` is free.)

**Suggestion.** A "Scrape PDFs" page covering limits, OCR, output format
(page markers, tables), cost and the batch-vs-scrape behaviour above.

## 6. Same PDF, different Markdown format from `/batches` and `/scrapes`

**Priority: Medium**

**Issue.** Batch results start every PDF page with a `# Page N` heading
(63 of 63 files). The same kind of PDFs scraped with `/scrapes` almost never
have them (1 of 35 files). Code that splits or counts pages by these headings
works for one endpoint and silently not for the other.

**Example.** `Arranging_removal.pdf` via `/scrapes` starts with
"General Instructions: Immigration Returns..." and has no page headings,
while every batch-scraped PDF in `batch_fbseya59a2ww` starts with `# Page 1`.

**Suggestion.** One PDF output format for both endpoints (page headings are
useful for chunking and citations, so keeping them everywhere is best).

## 7. Long tail on PDF batches

**Priority: Medium**

**Issue.** In the 100-PDF batch, 79 PDFs were done after 63 seconds. The 80th
finished at 325 seconds and the last at 664 seconds. Milestones: 50% at
34.9s, 90% at 7m 23s, 99% at 10m 43s, 100% at 11m 04s. More than 90% of the
wall time was spent on the last 21 PDFs. (Observation only; the items API has
no per-item timestamps to tell which PDFs were slow or why.)

**Suggestion.** Per-item `started_at` / `finished_at` in the items list would
let users see whether large PDFs or placeholders (item 1) cause the tail.

## 8. SDK: null result fields are missing attributes, not `None`

**Priority: Medium**

**Issue.** `ScrapeResult.__init__` only `setattr`s fields that are not null.
So `result.markdown_content` raises `AttributeError` when a PDF has no
Markdown, instead of returning `None`. The docstring lists `json_content` and
`markdown_content` as attributes, so code that follows it crashes on the
first empty result.

**Example.** `olostep/frontend/client_state.py`, `ScrapeResult.__init__`:
"Dynamically set attributes for all fields in the results model that are not
null". In the probe scrape of a text PDF, `json_content` was not an
attribute at all. This project uses `getattr(result, "markdown_content", None)`.

**Suggestion.** Always set every documented field (to `None` when empty).

## 9. SDK: API fields not exposed (`credits_consumed`, item `metadata`, `raw_pdf`)

**Priority: Low**

**Issue.**
* The batch API returns `credits_consumed` and `cost_usd`, but `BatchInfo`
  only copies `id, status, created, total_urls, completed_urls, country`, so
  cost per batch cannot be read through the SDK.
* The API accepts item-level `metadata` on batch items, but the SDK's
  `BatchItem` model has only `url` and `custom_id`.
* The docs list a `raw_pdf` format; the SDK `Format` enum has no such value.
* Every scrape logs a `UserWarning` about extra fields (`cache`, `cost_usd`,
  `from_cache`). This project filters it.

**Suggestion.** Expose these fields and update the models; turn the
extra-field warning into a debug log.

## 10. Cost clarity: `llm_extract` on PDFs

**Priority: Low**

**Issue.** The Scrape docs say "If you use LLM extract, it costs 10 credits",
and the pricing section says "Scrape costs 1 credit by default". It is not
clear whether that means 10 or 11 per call.

**Example.** Measured: 3 PDFs with `llm_extract` cost 30 credits, so 10 per
PDF including the scrape.

**Suggestion.** Say "10 credits in total (includes the scrape)".

## 11. No structured extraction over content you already have

**Priority: Low**

**Issue.** After a batch, the Markdown is already downloaded. To get
structured fields (title, date, summary) the SDK offers:
* `answers.create(task, json_format)`: takes only a task and searches the web
  (20 credits). It cannot take our content.
* `scrapes.create(..., llm_extract=...)`: re-scrapes the PDF (10 credits).

**Suggestion.** Allow `llm_extract` on a batch (one schema for all items), or
an endpoint that extracts from a `retrieve_id`.

---

## What worked well

* **One batch, streamed downloads.** Listing `status="completed"` items while
  the batch runs and retrieving them right away works for PDFs exactly as it
  does for web pages. The download tail after the batch finished was 0.9s.
* **Text PDFs convert well.** Headings, lists and reading order are clean
  (e.g. the NHS 10 Year Health Plan executive summary). In batch output the
  `# Page N` headings make page-level chunking and page counts easy.
* **Cost is simple once measured:** 1 credit per PDF in a batch, `/retrieve`
  free, and failed or placeholder items still visible for a retry.
* **`/scrapes` is a reliable fallback.** It fixed 35 of 37 placeholder results
  in 17 seconds with 10 parallel calls.
* **`llm_extract` works on PDFs.** On a 286-page annual report it returned the
  right title, organisation and page count, and a good one-sentence summary.
* **`custom_id` round-trips**, so deterministic file names and resume are easy.
