#!/usr/bin/env python3
"""Stage 2 - download every harvested PDF.

file.pide.org.pk has no Cloudflare protection (plain curl with no User-Agent
gets HTTP 200), so this runs fully parallel with no browser involved.

Re-runnable: anything already on disk anywhere under the FYP folder is skipped,
which preserves the 19 files already sitting in 2022/.
"""

import csv
import json
import os
import re
import sys
import threading
import time
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INDEX = DATA / "research_index.jsonl"
PDF_DIR = ROOT / "pdfs"
REPORT = DATA / "download_report.csv"

WORKERS = 3
TIMEOUT = 180
RETRIES = 4

print_lock = threading.Lock()


def log(*a):
    with print_lock:
        print(*a, flush=True)


def existing_files() -> dict:
    """Every PDF already on disk under the project, by lowercase filename."""
    found = {}
    for p in ROOT.rglob("*.pdf"):
        if ".chrome-profile" in p.parts or "node_modules" in p.parts:
            continue
        found.setdefault(p.name.lower(), p)
    return found


def load_records():
    if not INDEX.exists():
        sys.exit(f"missing {INDEX} - run tools/harvest.mjs first")
    recs, seen = [], set()
    with INDEX.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("slug") in seen:  # later run wins
                recs = [x for x in recs if x.get("slug") != r["slug"]]
            seen.add(r.get("slug"))
            recs.append(r)
    return recs


def year_of(rec) -> str:
    m = re.match(r"(\d{4})", rec.get("date") or "")
    return m.group(1) if m else "undated"


def fetch(session, url, dest: Path):
    """Download url to dest. Returns (status, bytes, note)."""
    for attempt in range(1, RETRIES + 2):
        try:
            with session.get(url, timeout=TIMEOUT, stream=True, allow_redirects=True) as r:
                if r.status_code != 200:
                    if attempt > RETRIES:
                        return (f"http_{r.status_code}", 0, "")
                    time.sleep(min(2 ** attempt, 30))
                    continue
                expected = int(r.headers.get("content-length") or 0)
                tmp = dest.with_suffix(dest.suffix + ".part")
                tmp.parent.mkdir(parents=True, exist_ok=True)
                n = 0
                with tmp.open("wb") as fh:
                    for chunk in r.iter_content(65536):
                        fh.write(chunk)
                        n += len(chunk)

                with tmp.open("rb") as fh:
                    magic = fh.read(5)
                if not magic.startswith(b"%PDF"):
                    tmp.unlink(missing_ok=True)
                    if attempt > RETRIES:
                        return ("not_a_pdf", n, magic[:5].decode("latin-1", "replace"))
                    continue
                if expected and n != expected:
                    tmp.unlink(missing_ok=True)
                    if attempt > RETRIES:
                        return ("size_mismatch", n, f"expected {expected}")
                    continue

                tmp.replace(dest)
                return ("ok", n, "")
        except Exception as e:  # noqa: BLE001 - report, don't crash the pool
            if attempt > RETRIES:
                return ("error", 0, type(e).__name__)
            time.sleep(min(2 ** attempt, 30))
    return ("failed", 0, "")


def main():
    recs = load_records()
    on_disk = existing_files()
    log(f"{len(recs)} harvested items; {len(on_disk)} PDFs already on disk")

    jobs, rows, no_pdf = [], [], []
    seen_urls = set()
    for rec in recs:
        urls = rec.get("pdf_urls") or []
        if not urls:
            no_pdf.append(rec)
            continue
        for url in urls:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            # Article bodies cite PDFs on other government sites; those are
            # references, not PIDE publications. Record and skip them.
            if not urlparse(url).netloc.endswith("pide.org.pk"):
                rows.append(
                    dict(slug=rec["slug"], url=url, status="external_citation",
                         bytes=0, path="")
                )
                continue
            name = url.split("?")[0].rsplit("/", 1)[-1]
            if not name.lower().endswith(".pdf"):
                name += ".pdf"
            prior = on_disk.get(name.lower())
            if prior is not None:
                rows.append(
                    dict(slug=rec["slug"], url=url, status="already_present",
                         bytes=prior.stat().st_size, path=str(prior.relative_to(ROOT)))
                )
                continue
            dest = PDF_DIR / year_of(rec) / name
            jobs.append((rec, url, dest))

    # Bandwidth is the bottleneck, so order matters: fetch the scholarly output
    # first and leave the newspaper clippings for last. Nothing is dropped.
    CORE = ("Working Paper", "Policy Viewpoint", "Knowledge Brief",
            "Research Reports", "PDR", "Monograph Series", "BASICS Notes",
            "Policy and Research")
    tax_path = DATA / "taxonomies.json"
    cat_names = json.loads(tax_path.read_text(encoding="utf-8")).get(
        "research-category", {}) if tax_path.exists() else {}

    def priority(rec):
        cats = [cat_names.get(str(i), "") for i in
                rec.get("tax_ids", {}).get("category", [])]
        if any(c == "PIDE in Press" for c in cats):
            return 2
        return 0 if any(c in CORE for c in cats) else 1

    jobs.sort(key=lambda j: priority(j[0]))
    from collections import Counter as _C
    order = _C(priority(j[0]) for j in jobs)
    log(f"{len(jobs)} to download, {len(rows)} already present, "
        f"{len(no_pdf)} items have no PDF link")
    log(f"  order: {order[0]} core research, {order[1]} other, {order[2]} press (last)")

    if jobs:
        session = requests.Session()
        session.headers["User-Agent"] = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        done = 0
        total_bytes = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futs = {pool.submit(fetch, session, u, d): (rec, u, d)
                    for rec, u, d in jobs}
            for fut in as_completed(futs):
                rec, url, dest = futs[fut]
                status, n, note = fut.result()
                total_bytes += n
                done += 1
                rows.append(
                    dict(slug=rec["slug"], url=url, status=status, bytes=n,
                         path=str(dest.relative_to(ROOT)) if status == "ok" else note)
                )
                if status != "ok":
                    log(f"  ! {status} {note} {url}")
                if done % 50 == 0 or done == len(jobs):
                    log(f"  {done}/{len(jobs)}  ({total_bytes/1e9:.2f} GB)")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["slug", "url", "status", "bytes", "path"])
        w.writeheader()
        w.writerows(rows)

    if no_pdf:
        p = DATA / "items_without_pdf.csv"
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["slug", "title", "date", "link", "detail_status"])
            for r in no_pdf:
                w.writerow([r.get("slug"), re.sub(r"<[^>]+>", "", r.get("title", "")),
                            r.get("date"), r.get("link"), r.get("detail_status")])
        log(f"wrote {p.relative_to(ROOT)} ({len(no_pdf)} items)")

    from collections import Counter
    tally = Counter(r["status"] for r in rows)
    log("\nsummary: " + ", ".join(f"{k}={v}" for k, v in tally.most_common()))
    log(f"report: {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
