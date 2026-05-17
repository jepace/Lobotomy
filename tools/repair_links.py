#!/usr/bin/env python3
"""
Repair broken internal markdown links in the wiki.

Fixes two classes of problems:

1. Nested/double-linked patterns (old autolink bug):
       [Text](../entities/[text](../entities/text.md))
   →   [Text](../entities/text.md)

2. Wrong relative path prefixes written by the LLM:
       ../../sources/foo.md   (from concepts/ — one too many ../)
       concepts/sources/foo.md  (absolute-style prefix instead of ../)
       entities/sources/foo.md  (same)
   All resolved by computing the correct relative path from the page's
   actual location to the target file.

Run from the repo root:
  python3 tools/repair_links.py [--dry-run]
"""
import os
import re
import sys
from pathlib import Path

WIKI_DIR = Path(__file__).resolve().parent.parent / "wiki"
DRY_RUN  = "--dry-run" in sys.argv

# --- Fix 1: nested/double-linked patterns -----------------------------------

nested_re = re.compile(r'\[([^\]]+)\]\(([^)]*\[[^\]]*\][^)]*)\)')

def _repair_nested(m):
    link_text = m.group(1)
    bad_url   = m.group(2)
    inner = re.search(r'\]\(([^)]+)\)', bad_url)
    if inner:
        return f"[{link_text}]({inner.group(1)})"
    clean = bad_url[:bad_url.index('[')].rstrip('/')
    return f"[{link_text}]({clean})"


# --- Fix 2: wrong relative paths --------------------------------------------
# Matches any markdown link whose target doesn't start with http/# and
# resolves to a non-existent file.

link_re = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')

def _repair_path(page: Path, link_path: str) -> str | None:
    """
    Given a link path that doesn't resolve from `page`, try to find the
    correct relative path by locating the filename anywhere in wiki/.
    Returns corrected path string, or None if target can't be found.
    """
    if link_path.startswith("http") or link_path.startswith("#") or link_path.startswith("mailto"):
        return None

    # Strip fragment
    fragment = ""
    if "#" in link_path:
        link_path, fragment = link_path.split("#", 1)
        fragment = "#" + fragment

    target = (page.parent / link_path).resolve()
    if target.exists():
        return None  # already valid

    # Extract just the filename and search wiki/ for it
    filename = Path(link_path).name
    if not filename.endswith(".md"):
        return None

    matches = list(WIKI_DIR.rglob(filename))
    if len(matches) == 1:
        correct_rel = Path(os.path.relpath(matches[0], page.parent))
        return str(correct_rel) + fragment
    elif len(matches) > 1:
        # Prefer match whose parent dir name appears in link_path
        for m in matches:
            if m.parent.name in link_path:
                correct_rel = Path(os.path.relpath(m, page.parent))
                return str(correct_rel) + fragment

    return None


fixed_files = fixed_links = 0

for f in sorted(WIKI_DIR.rglob("*.md")):
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue

    # Pass 1: nested links
    new_text, n1 = nested_re.subn(_repair_nested, text)

    # Pass 2: wrong relative paths
    count = [0]
    def _path_replacer(m, _page=f, _count=count):
        display   = m.group(1)
        link_path = m.group(2)
        fixed     = _repair_path(_page, link_path)
        if fixed:
            _count[0] += 1
            return f"[{display}]({fixed})"
        return m.group(0)

    new_text = link_re.sub(_path_replacer, new_text)
    n2 = count[0]

    total = n1 + n2
    if total:
        fixed_links += total
        fixed_files += 1
        rel = f.relative_to(WIKI_DIR)
        if DRY_RUN:
            print(f"  [dry-run] {rel}: would fix {total} ({n1} nested, {n2} bad-path)")
        else:
            f.write_text(new_text, encoding="utf-8")
            print(f"  {rel}: fixed {total} ({n1} nested, {n2} bad-path)")

print(f"\n{'[dry-run] ' if DRY_RUN else ''}Repaired {fixed_links} links across {fixed_files} files.")
