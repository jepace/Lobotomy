#!/usr/bin/env python3
"""
Scan wiki/ for pages with a repeated heading — the same section title appearing more than
once at the same level.

update_file now refuses to *create* this (see agent.py's duplicate-heading guard), but that
only stops it going forward. Existing wiki pages can already have it from before the guard
existed: a whole-page rewrite lost track partway through and pasted an old version of a
section back in alongside the new one, e.g. two "## Background" blocks, one stale.

This is report-only. Merging duplicated content requires judgment about which version is
current and what to keep from each, so it is not something to do unattended.

Run from the repo root:
  python3 tools/find_duplicate_sections.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import WIKI_DIR, wiki_pages

HEAD_RE = re.compile(r"^(#{1,6})[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)

found = 0
for p in wiki_pages():
    text = p.read_text(encoding="utf-8", errors="replace")
    body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)
    seen = {}
    dupes = []
    for m in HEAD_RE.finditer(body):
        key = (len(m.group(1)), m.group(2).strip().lower())
        if key in seen and m.group(2).strip() not in dupes:
            dupes.append(m.group(2).strip())
        seen[key] = True
    if dupes:
        found += 1
        rel = p.relative_to(WIKI_DIR)
        print(f"{rel}: {', '.join(dupes)}")

if not found:
    print("No duplicated headings found.")
else:
    print(f"\n{found} page(s) affected. Open each, decide which copy of the section is "
          f"current, merge the two by hand, and save — there is no safe automatic fix.")
