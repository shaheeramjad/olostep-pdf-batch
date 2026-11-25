# olostep-pdf-batch

Scrape thousands of PDFs into an AI-ready Markdown dataset with Python and
[Olostep](https://www.olostep.com) Batches.

## Demo video

> Video coming soon: *Scraping 5,000 PDFs into an AI-Ready Dataset with Python
> and Olostep*.

## Features

- **Collect** public PDF URLs from GOV.UK publications (Search + Content APIs),
  or bring your own CSV. De-duplicated, shuffled with a fixed seed, capped.
- **One batch job** for every PDF, each with a deterministic `custom_id`.
- **Download while scraping.** Markdown is fetched as soon as Olostep finishes
  each PDF, with bounded concurrency, timeouts, retries and atomic writes.
- **Verify** every PDF: succeeded or failed (with a reason), empty,
  near-empty, likely scanned (needs OCR), total characters, timings.
- **Recover** PDFs the batch could not convert with `--rescrape` (single
  `/scrapes` calls, 1 credit each).
- **Extract** a structured `dataset.csv` (title, organisation, date, pages,
  summary) locally for free, or with Olostep LLM extraction.
- **Resumable.** The batch id is saved the moment the batch exists.
- **Streamlit UI** with live progress, results, Markdown preview and downloads.

## Requirements

- Python **3.11 to 3.13** (required by the `olostep` SDK)
- An Olostep API key from the
  [Olostep dashboard](https://www.olostep.com/dashboard/api-keys)

> New Olostep accounts can submit up to 100 URLs per batch. Ask Olostep
> support to raise the limit before you run thousands of PDFs.

## Quick Start

### 1) Install

```bash
git clone https://github.com/shaheeramjad/olostep-pdf-batch
cd olostep-pdf-batch
python3.12 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

### 2) Add your API key

```bash
cp .env.example .env    # then set OLOSTEP_API_KEY
```

### 3) Run

```bash
python -m olostep_pdf_batch collect --source govuk --limit 100
python -m olostep_pdf_batch run --rescrape
python -m olostep_pdf_batch extract --sample 50
```

## CLI Reference

| Command | What it does |
| --- | --- |
| `collect --source govuk --limit 5000` | Collect GOV.UK PDF URLs into `data/pdf_urls.csv` |
| `collect --source file --input my_pdfs.csv` | Use your own list (CSV or one URL per line) |
| `run --concurrency 64` | Submit one batch, stream Markdown downloads, verify |
| `resume` | Continue the batch saved in `output/batch.json` |
| `report [--no-timings]` | Re-verify the last run from disk (no API calls) |
| `extract --sample 50` | Build `dataset.csv` from a sample (local, free) |

| Option | Default | Description |
| --- | --- | --- |
| `--limit N` | `5000` | Maximum PDFs to collect |
| `--concurrency N` | `50` | Parallel Markdown downloads, max 64 (`run`, `resume`) |
| `--rescrape` | off | Retry failed PDFs one by one with `/scrapes` (1 credit each) |
| `--method local\|olostep` | `local` | Extraction method; `olostep` asks before spending credits |
| `--out DIR` | `output` | Output folder |
| `-v` | off | Debug logs |

## Costs

Measured on real runs:

| Step | Credits |
| --- | --- |
| Batch | 1 per PDF |
| Downloading results (`/retrieve`) | free |
| `--rescrape` fallback | 1 per failed PDF |
| `extract --method olostep` | 10 per PDF |
| `extract --method local` | free |

A 5,000-PDF run costs about 5,000 credits, plus 1 credit for each PDF you
re-scrape.

## UI

```bash
streamlit run app.py
```

![Olostep PDF Batch UI](docs/ui.png)
<!-- Screenshot placeholder: add docs/ui.png -->

The page follows Olostep's tool pages: the same nav, sky hero, and a
macOS-style tool window.

- **Tool window**: pick a source (GOV.UK, upload a CSV or paste URLs), see a
  one-line summary (PDFs, organisations, pages, size, credits) and preview the
  URLs. **Settings** holds the PDF limit, parallel downloads and re-scrape.
  **Collect URLs** and **Start batch** sit at the bottom right; Start asks
  for confirmation before spending credits. **Resume last run** appears when
  `output/batch.json` exists. While a batch runs, live tiles, two progress
  bars and milestone lines appear in the window.
- **Results window**: summary tiles, then tabs for *Documents* (search,
  filter by quality, click a row to read its Markdown next to links to the
  original PDF), *Failures* (counts by reason) and *Timeline*. Download
  `manifest.csv`, `dataset.csv` or a ZIP of all Markdown files from its footer.
- **Dataset window**: build `dataset.csv` from a sample.

Colours, fonts, radii and shadows are measured from olostep.com and live in
`ui/theme.py`. The logo and hero image are in `static/`. The app imports the
package once at start-up: after changing code under `src/`, restart
`streamlit run app.py`.

## How it works

```
collect  ->  batch  ->  stream download  ->  verify  ->  extract
(GOV.UK)    (Olostep)   (while it runs)     (reports)   (dataset.csv)
```

1. **Collect** (`sources/govuk.py`): the GOV.UK Search API lists
   publications (policy papers, research, corporate and independent reports,
   guidance). The Content API returns each one's attachments; only `.pdf`
   URLs are kept, with title, organisation, date, page count and file size.
   Requests are polite: 4 at a time, a short delay, `Retry-After` respected
   and a User-Agent naming this project.
2. **Batch** (`batch.py`): all PDFs go out in one `client.batches.create`
   call. The batch id is written to `output/batch.json` immediately, and
   `client.batches.info` is polled for progress and the 50/90/99/100%
   milestones.
3. **Stream download** (`download.py`): every few seconds,
   `client.batches.items(status="completed")` lists finished PDFs, and
   `client.retrieve.get(..., formats=["markdown"])` fetches them right away.
   Files are written atomically as `markdown/<custom_id>.md`, each with a
   front-matter block (source PDF, publication page, title). Existing files
   are skipped, so `resume` is safe.
4. **Verify** (`verify.py`): each submitted PDF is checked against the file on
   disk and classed as good text, near-empty, empty or likely scanned.
5. **Extract** (`extract.py`): see [Structured dataset](#structured-dataset).

### Things PDFs do that web pages don't

- Some batch items complete with the text `not-implemented` instead of
  Markdown. They are reported as failures, and `--rescrape` recovers most of
  them with a single `/scrapes` call.
- Olostep does not OCR scanned PDFs. An image-only PDF comes back as
  `# Page 1`, `# Page 2`, ... with no text, and is reported as *likely
  scanned* so you can send it to an OCR tool.


## Structured dataset

`extract` writes one row per PDF: `title`, `organisation`, `first_published`
(GOV.UK's first-published date), `document_date` (a date found in the PDF),
`pages`, `summary`.

- **`--method local`** (default, free): GOV.UK already gives the exact title,
  organisation, date and page count. The summary is the document's purpose
  sentence ("This guidance sets out ...") or its first real prose sentence.
- **`--method olostep`**: Olostep LLM extraction
  (`scrapes.create(llm_extract=...)`) with a JSON schema. Better summaries,
  but it re-scrapes each PDF (10 credits). The SDK has no way to run
  extraction over Markdown you already downloaded.

Always start with a sample (`--sample 50`).

## Output Layout

```
data/
├── pdf_urls.csv        # collected PDFs: custom_id, pdf_url, title, organisation, pages...
└── collect.json        # source, count and collect time
output/
├── markdown/<custom_id>.md   # one Markdown file per PDF, with front matter
├── submitted.csv       # exactly what was sent to Olostep
├── batch.json          # batch id, timings and milestones (used by resume)
├── manifest.csv        # one row per PDF: status, quality, chars, file, error
├── failures.csv        # failed PDFs with a reason, page count and size
├── summary.json        # totals, quality counts, timings, timeline
└── dataset.csv         # structured fields (from `extract`)
```

## Sample summary

A real run of 100 GOV.UK PDFs (28 organisations, 3,807 pages):

```
$ python -m olostep_pdf_batch run
100 PDFs: 50% done in 34.9s
100 PDFs: 90% done in 7m 23s
100 PDFs: 99% done in 10m 43s
100 PDFs: 100% done in 11m 04s
...  Succeeded 63, Failed 37 ('not-implemented' results)

$ python -m olostep_pdf_batch resume --rescrape
INFO     Re-scraping 37 failed PDFs one by one...
INFO     Recovered 35 of 37 with /scrapes

              Olostep PDF batch
┌────────────────────────────────┬───────────┐
│ PDFs submitted                 │       100 │
│ Succeeded                      │        98 │
│ Failed                         │         2 │
│ Success rate                   │     98.0% │
│   Recovered by re-scrape       │        35 │
├────────────────────────────────┼───────────┤
│   Good text                    │        94 │
│   Likely scanned (needs OCR)   │         0 │
│   Near-empty                   │         4 │
│   Empty                        │         0 │
│ Characters of text             │ 5,179,240 │
├────────────────────────────────┼───────────┤
│ Collect URLs                   │     17.3s │
│ Create batch                   │      2.1s │
│ Scrape (Olostep)               │   11m 04s │
│   50% done at                  │     34.9s │
│   90% done at                  │    7m 23s │
│   99% done at                  │   10m 43s │
│   100% done at                 │   11m 04s │
│ Download tail                  │      1.9s │
│ Re-scrape failures             │     17.0s │
│ Total                          │   11m 42s │
└────────────────────────────────┴───────────┘
```

The near-empty PDFs are one-page posters, a flowchart and a slide with no
text. `run --rescrape` does both steps in one command.

## Bring your own PDFs

Any CSV with a `pdf_url` (or `url`) column works; `title` and
`publication_url` columns are used when present. A plain text file with one
URL per line works too.

```bash
python -m olostep_pdf_batch collect --source file --input my_pdfs.csv
python -m olostep_pdf_batch run
```

To repeat the 100-PDF demo from the video exactly, use the list in
`examples/govuk-100.csv` (100 GOV.UK PDFs, Open Government Licence; fits the
100-URL batch limit of a new account):

```bash
python -m olostep_pdf_batch collect --source file --input examples/govuk-100.csv
python -m olostep_pdf_batch run --rescrape
```

In the UI, choose **Upload a CSV** or **Paste URLs** in the sidebar.

## Use cases

- **Research datasets.** Turn a public archive of reports into clean text.
- **RAG over reports.** Feed Markdown (with page headings and source links)
  into a vector store and cite the original PDF.
- **Compliance archives.** Keep searchable text copies of guidance, standards
  and notices, and spot scanned documents that need OCR.
- **Finance filings.** Convert annual reports and accounts into text for
  analysis.

## Project Structure

```text
.
├── app.py                     # Streamlit UI (layout only)
├── ui/                        # theme, styles, components, background runner
├── static/                    # Olostep logo and hero image (served by Streamlit)
├── src/olostep_pdf_batch/
│   ├── cli.py                 # collect, run, resume, report, extract
│   ├── pipeline.py            # the steps, shared by the CLI and the UI
│   ├── config.py              # every constant and setting
│   ├── sources/               # govuk.py, file.py (+ PdfRecord)
│   ├── batch.py               # create batch, save id, poll, milestones
│   ├── download.py            # streaming download, retries, re-scrape
│   ├── verify.py              # empty / near-empty / scanned checks
│   ├── extract.py             # dataset.csv
│   ├── report.py              # manifest, failures, summary, table
│   └── utils.py
└── tests/
```

## Development

```bash
pip install -e ".[dev]"
ruff format . && ruff check .
pytest
```

See [CONVENTIONS.md](CONVENTIONS.md) for the code conventions this project
follows.

## Data licence

The PDFs collected from GOV.UK are published under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/),
unless a document says otherwise. If you publish a dataset built from them,
attribute the source (for example: "Contains public sector information
licensed under the Open Government Licence v3.0").

## Olostep References

- [Olostep documentation](https://docs.olostep.com/)
- [Batch endpoint](https://docs.olostep.com/features/batches)
- [Scrape endpoint](https://docs.olostep.com/features/scrapes)
- [LLM extraction](https://docs.olostep.com/features/structured-content/llm-extraction)
- [Python SDK](https://docs.olostep.com/sdks/python)

## License

[MIT](LICENSE)
