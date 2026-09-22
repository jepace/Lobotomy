#!/usr/bin/env python3
"""Give entity and concept pages the opener their template requires.

Every entity page is supposed to open with `## Overview` and every concept page with
`## Definition`. That is enforced at `create_file` — and nowhere else, so pages written
before the check will never acquire one. Every ingest that touches such a page pays for
it: the agent asks for the section the schema promises, misses, and spends a round
recovering.

Most of those pages are not missing the prose, only the heading:

    # Lee Jae Myung                          # Lee Jae Myung

    Lee Jae Myung is the President of   ->   ## Overview
    South Korea, elected in 2025...
                                             Lee Jae Myung is the President of
    ## Political Career                      South Korea, elected in 2025...

                                             ## Political Career

The lead paragraph already says what Overview is for. Untitled, no tool can address it and
it does not appear in a page outline. This inserts the heading. It moves nothing, rewrites
nothing, and invents nothing.

A page with no lead paragraph is left alone and listed. Its first section is something
like "Background & Leadership", which is not an overview — renaming it would misdescribe
what it holds, and writing an overview from scratch is authorship, not repair.

Every page changed goes through the normal write path, so it keeps a version-history entry
and is revertable from that page's History view.

Run from the repo root, as the user the server runs as:
  python3 tools/promote_openers.py --dry-run
  python3 tools/promote_openers.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import promote_lead_to_opener

DRY_RUN = "--dry-run" in sys.argv
VERBOSE = "--all" in sys.argv

r = promote_lead_to_opener(dry_run=DRY_RUN)
tag = "[dry-run] " if DRY_RUN else ""

shown = r["promoted"] if VERBOSE else r["promoted"][:25]
for line in shown:
    print(f"  {tag}{line}")
if len(r["promoted"]) > len(shown):
    print(f"      … and {len(r['promoted']) - len(shown)} more (--all to list them)")

if r["needs_text"]:
    print(f"\nNeeds a human or a regenerate — no lead paragraph to promote "
          f"({len(r['needs_text'])}):")
    for line in (r["needs_text"] if VERBOSE else r["needs_text"][:25]):
        print(f"      {line}")
    if not VERBOSE and len(r["needs_text"]) > 25:
        print(f"      … and {len(r['needs_text']) - 25} more (--all to list them)")

print(f"\n{tag}{len(r['promoted'])} page(s) {'would get' if DRY_RUN else 'got'} an opener; "
      f"{len(r['needs_text'])} still need one written. "
      f"{r['pages']} page(s) lacked one in total.")
