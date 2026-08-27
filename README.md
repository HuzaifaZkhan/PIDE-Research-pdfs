# PIDE Research Corpus (2022 → present)

Bulk harvest of PIDE's Research Showcase for the FYP comparing published research
claims against actual PIDE data for Pakistan.

## The PDFs are not in this repo

GitHub can't hold them: the corpus is **5.9 GB across 1,623 PDFs**, and 7 files
exceed GitHub's 100 MB hard limit (the largest is 374 MB). Two ways to get them:

1. **Download them yourself** - clone this repo and run
   `python3 tools/download_pdfs.py`. It reads `data/research_index.jsonl`,
   pulls every PDF from `file.pide.org.pk`, and sorts them into `pdfs/<year>/`.
   Takes ~40 minutes and needs no browser or credentials.
2. **Ask Huzaifa for the shared `pdfs.zip`** (5.6 GB) on Google Drive.

Everything else - the index, all 1,514 full-text articles, and the pipeline - is
here and is what most analysis actually needs.

## What's here

```
data/research_index.csv     the working index - one row per publication
data/fulltext/<slug>.txt    plain-text article bodies (greppable, no PDF needed)
data/research_index.jsonl   raw harvest, resumable
data/taxonomies.json        taxonomy id -> name maps
data/download_report.csv    per-file download outcome
data/items_without_pdf.csv  publications with no PDF (web-only pieces)
pdfs/<year>/*.pdf           the PDFs
tools/                      the three pipeline scripts
2022/                       19 PDFs downloaded by hand before this pipeline existed
```

`research_index.csv` columns: `slug, title, date, year, category, sub_category,
authors, keywords, jel_codes, page_url, pdf_url, extra_pdf_urls, pdf_path,
fulltext_path, word_count`.

## How it works

Three stages. Only stage 1 touches Cloudflare.

```bash
node tools/harvest.mjs                              # ~1h, opens a real Chrome window
node tools/recheck.mjs                              # re-checks items that showed no PDF
/opt/anaconda3/bin/python3 tools/download_pdfs.py   # ~40 min, no browser
/opt/anaconda3/bin/python3 tools/build_index.py     # ~1 min
```

**Use the full Anaconda path.** Plain `python3` on this machine resolves to
`/usr/local/bin/python3`, which has no `requests` installed.

**Why it's built this way.** `pide.org.pk` sits behind a Cloudflare WAF rule
(Error 1020) that blocks `curl`, `python-requests` and headless Chrome alike —
every HTML and JSON path, verified. A real headed browser passes, so stage 1
drives the installed Google Chrome and issues each request as a same-origin
`fetch()` from a live page. It uses the site's undocumented WP REST API
(`/wp-json/wp/v2/research`) which returns the full article text, then visits each
detail page once because the PDF link lives in a JetEngine meta field that the
API does not expose.

`file.pide.org.pk`, where the PDFs actually live, has **no** protection at all —
so stage 2 needs no browser and runs 8-way parallel.

Stage 1 rate-limits itself to ~1 request/sec against `pide.org.pk` and backs off
if Cloudflare pushes back. Please keep it that way: this is public material and
`robots.txt` permits it, but one polite pass is the deal.

## Scope

"2022 onwards" has two definitions on this site that disagree slightly — the
WordPress publish date, and the `research-year` taxonomy (the real publication
year). The harvest takes the **union** of both, so nothing is missed either way.

Counts by publication year at harvest time: 2022: 468, 2023: 465, 2024: 390,
2025: 299, 2026: 258 — about **1,880 items**, not the ~1,068 implied by the
89-page showcase view.

## Re-running

All three stages are re-runnable and idempotent:

- `harvest.mjs` appends to the JSONL and skips slugs it already has, so an
  interrupted run resumes where it stopped.
- `download_pdfs.py` skips any PDF already on disk anywhere under this folder —
  including the hand-downloaded files in `2022/`.
- `build_index.py` rebuilds the CSV and text from scratch each time.

To refresh the corpus later, delete `data/research_index.jsonl` and re-run all
three; or just re-run as-is to pick up newly published items.
