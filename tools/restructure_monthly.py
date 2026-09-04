#!/usr/bin/env python3
"""Reorganise pdfs/<year>/ into pdfs/<year>/<month>/.

Month comes from each publication's date in research_index.csv. The legacy
2022/ folder (hand-downloaded before this pipeline existed) is left untouched;
its files are *copied* into the tree so pdfs/ is a complete corpus.

Run with --apply to actually move files; default is a dry run.
"""
import csv, pathlib, shutil, sys, collections

ROOT = pathlib.Path(__file__).resolve().parent.parent
PDFS = ROOT / "pdfs"
LEGACY = ROOT / "2022"
APPLY = "--apply" in sys.argv

month_of = {}
for r in csv.DictReader((ROOT / "data/research_index.csv").open(encoding="utf-8")):
    ym = (r["date"] or "")[:7]
    if not ym:
        continue
    for u in [r["pdf_url"]] + (r["extra_pdf_urls"].split("; ") if r["extra_pdf_urls"] else []):
        if u.strip():
            name = u.split("/")[-1].lower()
            month_of[name] = ym
            # Some real filenames contain spaces (or %20) that URL handling
            # strips; match those too rather than orphaning the file.
            month_of[name.replace("%20", "").replace(" ", "")] = ym

moves, unknown = [], []
for p in sorted(PDFS.rglob("*.pdf")):
    n = p.name.lower()
    ym = month_of.get(n) or month_of.get(n.replace("%20", "").replace(" ", ""))
    if not ym:
        unknown.append(p); continue
    dest = PDFS / ym[:4] / ym[5:7] / p.name
    if p != dest:
        moves.append((p, dest))

copies = []
for p in sorted(LEGACY.glob("*.pdf")) if LEGACY.exists() else []:
    n = p.name.lower()
    ym = month_of.get(n) or month_of.get(n.replace("%20", "").replace(" ", ""))
    if not ym:
        unknown.append(p); continue
    dest = PDFS / ym[:4] / ym[5:7] / p.name
    if not dest.exists():
        copies.append((p, dest))

print(f"move  : {len(moves)} files already in pdfs/")
print(f"copy  : {len(copies)} files from legacy 2022/ (originals kept)")
print(f"unmapped (left alone): {len(unknown)}")
for p in unknown[:10]:
    print("   ?", p.relative_to(ROOT))

spread = collections.Counter(d.parent.relative_to(PDFS).as_posix() for _, d in moves + copies)
print(f"\nresulting month folders: {len(spread)}")
for k in sorted(spread)[:6]:
    print(f"   pdfs/{k}/ -> {spread[k]} files")
print("   ...")

if not APPLY:
    print("\nDRY RUN - pass --apply to perform the move")
    sys.exit(0)

EXT = ROOT / "cited_external_pdfs"
for p in unknown:
    dest = EXT / p.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.move(str(p), str(dest))
        print(f"  parked non-PIDE file -> cited_external_pdfs/{p.name[:60]}")

for src, dest in moves:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
for src, dest in copies:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dest))
for d in sorted(PDFS.rglob("*"), reverse=True):
    if d.is_dir() and not any(d.iterdir()):
        d.rmdir()
print(f"\nmoved {len(moves)}, copied {len(copies)}")
