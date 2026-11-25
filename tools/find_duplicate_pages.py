#!/usr/bin/env python3
"""
Scan wiki/ for pages that probably describe the same thing under different titles.

The exact-match lookup that decided create-vs-update during an ingest saw "Pacific Gas and
Electric Company", "Pacific Gas & Electric Co." and "Pacific Gas & Electric" as three
unrelated names, so each got its own page. lookup_titles now reports these as SIMILAR at
ingest time, but that only stops new ones — this finds the pages already split.

Report-only. Merging needs judgment this cannot have: a parent holding company and its
operating subsidiary normalize to the same key ("PG&E Corporation" and "PG&E") and are
correctly two pages, while five spellings of one utility are correctly one.

Run from the repo root:
  python3 tools/find_duplicate_pages.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import duplicate_page_groups

# A thin CLI over agent.duplicate_page_groups, deliberately. This used to BE the scan --
# 54 lines of top-level script, the same unreachable shape rename_page.py had -- so a
# route and a test could not ask it anything. One answer about what a collision is.
groups = duplicate_page_groups()
if not groups:
    print("No duplicate-looking page titles found.")
    sys.exit(0)

pages = sum(len(g["pages"]) for g in groups)
print(f"{len(groups)} group(s), {pages} pages:\n")
for g in groups:
    print(f"  {g['key']}")
    for pg in sorted(g["pages"], key=lambda x: -x["chars"]):
        print(f"      {pg['chars']:>7,}  {pg['path']}   \"{pg['title']}\"")
    print()
print("Report only -- merging needs judgement this cannot have: a parent holding company")
print("and its subsidiary normalize to the same key and are correctly two pages.")
print("\n  python3 tools/merge_page.py <loser> <survivor> --carry --dry-run")
