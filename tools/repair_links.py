#!/usr/bin/env python3
"""
One-shot repair for nested/double-linked markdown links created by the old autolink bug.

Bad pattern:  [Text](../entities/[text](../entities/text.md))
Fixed to:     [Text](../entities/text.md)

Run from the repo root:
  python3 tools/repair_links.py [--dry-run]
"""
import re
import sys
from pathlib import Path

WIKI_DIR = Path(__file__).resolve().parent.parent / "wiki"
DRY_RUN  = "--dry-run" in sys.argv

nested_re = re.compile(r'\[([^\]]+)\]\(([^)]*\[[^\]]*\][^)]*)\)')

def repair(m):
    link_text = m.group(1)
    bad_url   = m.group(2)
    # URL like: ../entities/[anthropic](../entities/anthropic.md)
    # Extract the inner link's URL — that's the canonical target
    inner = re.search(r'\]\(([^)]+)\)', bad_url)
    if inner:
        return f"[{link_text}]({inner.group(1)})"
    # Fallback: strip from the [ onward
    clean = bad_url[:bad_url.index('[')].rstrip('/')
    return f"[{link_text}]({clean})"

fixed_files = fixed_links = 0

for f in sorted(WIKI_DIR.rglob("*.md")):
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    new_text, n = nested_re.subn(repair, text)
    if n:
        fixed_links += n
        fixed_files += 1
        rel = f.relative_to(WIKI_DIR)
        if DRY_RUN:
            print(f"  [dry-run] {rel}: would fix {n}")
        else:
            f.write_text(new_text, encoding="utf-8")
            print(f"  {rel}: fixed {n}")

print(f"\n{'[dry-run] ' if DRY_RUN else ''}Repaired {fixed_links} nested links across {fixed_files} files.")
