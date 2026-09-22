#!/usr/bin/env python3
"""
Strip markdown links out of section headings, keeping the text.

    ## [Atheism](../sources/atheism-wikipedia-2026.md)
    ## Atheism

Every section tool — read_section, update_section, append_section — finds a section by
its heading text, so a heading carrying link syntax is effectively unaddressable: asked
for "Atheism", they do not find it, and append_section then creates a second section
beside it. The autolinker never causes this (it skips heading lines outright), so these
are hand-written; the write paths refuse them now, and this repairs the pages damaged
before that.

Nothing is lost. The link target is still reachable from the prose under the heading, and
`relink.py` re-links the subject there anyway.

Purely mechanical — the display text is exactly the heading the section should have had —
and every page changed goes through the normal write path, so it keeps a history entry
and is revertable from that page's History view.

Run from the repo root, as the user the server runs as:
  python3 tools/unlink_headings.py --dry-run
  python3 tools/unlink_headings.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import unlink_headings

DRY_RUN = "--dry-run" in sys.argv

r = unlink_headings(dry_run=DRY_RUN)
for line in r["detail"]:
    print(f"  {'[dry-run] ' if DRY_RUN else ''}{line}")
print(f"\n{'[dry-run] Would unlink' if DRY_RUN else 'Unlinked'} "
      f"{r['headings']} heading(s) across {r['pages']} page(s).")
