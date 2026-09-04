#!/usr/bin/env python3
"""Monthly log of the PIDE corpus.

The research-year taxonomy only carries a year, so month granularity comes from
the WordPress publish date. Those two agree closely (verified at harvest time:
the 300 most recently posted items were all tagged 2025/2026), but the month is
"when PIDE published it on the site", which is the best the source offers.

Writes data/monthly_log.csv and prints a readable table.
"""

import csv
import collections
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "research_index.csv"
OUT = ROOT / "data" / "monthly_log.csv"

CORE = ("Working Paper", "Policy Viewpoint", "Knowledge Brief", "Research Reports",
        "PDR", "Monograph Series", "BASICS Notes", "Policy and Research",
        "Book", "Book Chapter", "Webinars Brief")


def main():
    if not SRC.exists():
        sys.exit(f"missing {SRC} - run tools/build_index.py first")
    rows = list(csv.DictReader(SRC.open(encoding="utf-8")))

    months = collections.defaultdict(lambda: collections.Counter())
    cats_seen = collections.Counter()
    for r in rows:
        ym = (r["date"] or "")[:7]
        if not ym:
            ym = "(undated)"
        first_cat = (r["category"] or "(none)").split(";")[0].strip()
        cats_seen[first_cat] += 1
        m = months[ym]
        m["items"] += 1
        if r["pdf_url"]:
            m["with_pdf"] += 1
        if r["pdf_path"]:
            m["downloaded"] += 1
        m["words"] += int(r["word_count"] or 0)
        if first_cat == "PIDE in Press":
            m["press"] += 1
        elif first_cat in CORE:
            m["research"] += 1
        else:
            m["other"] += 1

    fields = ["month", "items", "research", "press", "other",
              "with_pdf", "downloaded", "words"]
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(fields)
        for ym in sorted(months):
            m = months[ym]
            w.writerow([ym, m["items"], m["research"], m["press"], m["other"],
                        m["with_pdf"], m["downloaded"], m["words"]])

    hdr = f"{'month':10} {'items':>6} {'research':>9} {'press':>6} {'other':>6} {'pdfs':>6} {'words':>9}"
    print(hdr)
    print("-" * len(hdr))
    year = None
    for ym in sorted(months):
        if ym[:4] != year and year is not None:
            print()
        year = ym[:4]
        m = months[ym]
        print(f"{ym:10} {m['items']:6d} {m['research']:9d} {m['press']:6d} "
              f"{m['other']:6d} {m['downloaded']:6d} {m['words']:9,d}")

    tot = collections.Counter()
    for m in months.values():
        tot.update(m)
    print("-" * len(hdr))
    print(f"{'TOTAL':10} {tot['items']:6d} {tot['research']:9d} {tot['press']:6d} "
          f"{tot['other']:6d} {tot['downloaded']:6d} {tot['words']:9,d}")
    print(f"\nmonths covered: {len([k for k in months if k != '(undated)'])}")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
