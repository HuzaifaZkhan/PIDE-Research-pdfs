#!/usr/bin/env python3
"""Stage 3 - turn the raw harvest into a working research index.

Emits:
  data/research_index.csv   one row per publication, with resolved taxonomy names
  data/fulltext/<slug>.txt  plain-text article body, so the corpus is greppable
                            without opening a single PDF
"""

import csv
import html
import json
import re
import sys
from urllib.parse import urlparse
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INDEX = DATA / "research_index.jsonl"
TAXFILE = DATA / "taxonomies.json"
CSV_OUT = DATA / "research_index.csv"
TEXT_DIR = DATA / "fulltext"
REPORT = DATA / "download_report.csv"

BLOCK_TAGS = re.compile(r"</(p|div|li|h[1-6]|tr|br)\s*>|<br\s*/?>", re.I)


def to_text(markup: str) -> str:
    if not markup:
        return ""
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", markup)
    s = BLOCK_TAGS.sub("\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return "\n".join(line.strip() for line in s.split("\n")).strip()


def load_jsonl(path: Path):
    """Last record for a given slug wins, so re-runs supersede earlier ones."""
    out = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[r.get("slug")] = r
    return list(out.values())


def main():
    if not INDEX.exists():
        sys.exit(f"missing {INDEX} - run tools/harvest.mjs first")
    recs = load_jsonl(INDEX)
    tax = json.loads(TAXFILE.read_text(encoding="utf-8")) if TAXFILE.exists() else {}

    def names(kind, ids):
        m = tax.get(f"research-{kind}", {})
        return "; ".join(m.get(str(i), f"#{i}") for i in (ids or []))

    # Where each PDF actually landed, from the download report.
    landed = {}
    if REPORT.exists():
        with REPORT.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["status"] in ("ok", "already_present"):
                    landed[row["url"]] = row["path"]

    # Safety net: catch any site-wide boilerplate PDF the denylist missed.
    url_freq = Counter(u for r in recs for u in (r.get("pdf_urls") or [])
                   if urlparse(u).netloc.endswith("pide.org.pk"))
    suspicious = [(u, c) for u, c in url_freq.items() if c > len(recs) * 0.10]

    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in recs:
        body = to_text(r.get("content_html", ""))
        slug = r.get("slug") or str(r.get("id"))
        if body:
            (TEXT_DIR / f"{slug}.txt").write_text(body, encoding="utf-8")
        all_pdfs = r.get("pdf_urls") or []
        # PIDE's own publication files vs PDFs merely cited in the article body
        pdfs = [u for u in all_pdfs if urlparse(u).netloc.endswith("pide.org.pk")]
        cited = [u for u in all_pdfs if u not in pdfs]
        t = r.get("tax_ids", {})
        rows.append({
            "slug": slug,
            "title": html.unescape(re.sub(r"<[^>]+>", "", r.get("title", ""))).strip(),
            "date": (r.get("date") or "")[:10],
            "year": names("year", t.get("year")),
            "category": names("category", t.get("category")),
            "sub_category": names("sub-category", t.get("sub_category")),
            "authors": names("author", t.get("author")),
            "keywords": names("keywords", t.get("keywords")),
            "jel_codes": names("jel", t.get("jel")),
            "page_url": r.get("link", ""),
            "pdf_url": pdfs[0] if pdfs else "",
            "extra_pdf_urls": "; ".join(pdfs[1:]),
            "cited_external_pdfs": "; ".join(cited),
            "pdf_path": landed.get(pdfs[0], "") if pdfs else "",
            "fulltext_path": f"data/fulltext/{slug}.txt" if body else "",
            "word_count": len(body.split()),
        })

    rows.sort(key=lambda x: (x["date"], x["slug"]), reverse=True)
    with CSV_OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    with_pdf = sum(1 for r in rows if r["pdf_url"])
    with_text = sum(1 for r in rows if r["word_count"] > 50)
    print(f"items:            {len(rows)}")
    print(f"with a PDF link:  {with_pdf}")
    print(f"with body text:   {with_text}")
    print(f"total words:      {sum(r['word_count'] for r in rows):,}")
    by_year = Counter(r["year"] or "(none)" for r in rows)
    print("by publication year: " + ", ".join(f"{k}={v}" for k, v in sorted(by_year.items())))
    if suspicious:
        print("\nWARNING - PDFs linked from >10% of items, likely site boilerplate:")
        for u, c in sorted(suspicious, key=lambda x: -x[1]):
            print(f"   {c:5d}x  {u}")
    print(f"\nwrote {CSV_OUT.relative_to(ROOT)} and {TEXT_DIR.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
