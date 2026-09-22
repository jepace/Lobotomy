#!/usr/bin/env python3
"""Rename a section heading across the whole wiki, merging its synonyms into one name.

The wiki's section names are its vocabulary, and vocabulary drifts. One idea ends up under
three names — "Claims & Positions" on 466 pages, "Positions" on 110, "Key Positions" on 25
— and then neither a reader nor a tool can find all of it. This collapses a cluster.

    # what the "Claims & Positions" rename was:
    python3 tools/rename_section.py "Positions" \\
        --from "Claims & Positions" --from "Key Positions" --dry-run
    python3 tools/rename_section.py "Positions" \\
        --from "Claims & Positions" --from "Key Positions"

The first argument is the name you want. Every `--from` is a name that should become it.
Matching ignores case, `&` vs `and`, and trailing punctuation, so "Claims and Positions:"
is caught without being listed.

**Renaming into a name the page already has is a merge**, and merges need judgment. This
applies exactly the ladder `find_duplicate_sections.py` uses — one side empty, the two
identical, or one containing the other — and refuses to go further. Two sections with
genuinely different content under one idea are listed for you instead. A tool that guessed
there would quietly lose text.

Heading level is preserved. Changes go through the normal write path, so every page is
revertable from its History view.

Run from the repo root. Afterwards, update the template in `LOBOTOMY.md` to match, or the
next ingest will write the old name back.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import rename_section

args = sys.argv[1:]
DRY_RUN = "--dry-run" in args
VERBOSE = "--all" in args
args = [a for a in args if a not in ("--dry-run", "--all")]

olds = [args[i + 1] for i, a in enumerate(args) if a == "--from" and i + 1 < len(args)]
positional = [a for i, a in enumerate(args)
              if a != "--from" and (i == 0 or args[i - 1] != "--from")]

if not positional or not olds:
    print(__doc__.strip())
    sys.exit(2)

new_name = positional[0]
r = rename_section(olds, new_name, dry_run=DRY_RUN)
tag = "[dry-run] " if DRY_RUN else ""


def _dump(label, rows):
    if not rows:
        return
    print(f"\n{label} ({len(rows)}):")
    for line in (rows if VERBOSE else rows[:25]):
        print(f"  {tag}{line}")
    if not VERBOSE and len(rows) > 25:
        print(f"      … and {len(rows) - 25} more (--all to list them)")


_dump("Renamed", r["renamed"])
_dump("Merged", r["merged"])
_dump("Needs a human — both sections have real content", r["needs_human"])

print(f"\n{tag}{len(r['renamed'])} renamed, {len(r['merged'])} merged, "
      f"{len(r['needs_human'])} left for you, out of {r['pages']} page(s) carrying "
      f"one of those headings.")
if r["needs_human"]:
    print("Merge those by hand (the page's Edit button), then run this again.")
