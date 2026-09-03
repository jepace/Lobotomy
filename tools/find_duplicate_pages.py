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
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import WIKI_DIR, wiki_pages, _norm_title_key

groups: dict = {}
for p in wiki_pages():
    rel = p.relative_to(WIKI_DIR)
    # Source pages are one-per-ingest by design and share wording constantly; index and log
    # are machine-generated. Neither is a duplicate-entity problem.
    if rel.parts[0] == "sources" or p.name in ("index.md", "log.md"):
        continue
    text = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', text, re.MULTILINE)
    if not m:
        continue
    title = m.group(1).strip()
    key = _norm_title_key(title)
    if key:
        groups.setdefault(key, []).append((title, rel.as_posix(), len(text)))

dupes = {k: v for k, v in groups.items() if len(v) > 1}
for key in sorted(dupes):
    print(f"\n{key!r} — {len(dupes[key])} pages:")
    for title, rel, size in sorted(dupes[key], key=lambda x: -x[2]):
        print(f"  {size:>7,}b  {title}  ({rel})")

if not dupes:
    print("No pages found sharing a normalized title.")
else:
    total = sum(len(v) for v in dupes.values())
    print(f"\n{len(dupes)} group(s), {total} pages. Largest in each group is listed first "
          f"and is usually the one to keep: merge the others into it, then delete them and "
          f"fix any links that pointed at them (tools/repair_links.py finds those).")
    print("Check each group before merging — a parent company and its subsidiary land in "
          "the same group and should stay separate.")
