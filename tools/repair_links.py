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

3. Junk-scheme links the LLM wrote directly into body text — e.g.
       [previous ruling](about:reader?url=https%3A%2F%2F...)
   a browser-internal URL (Firefox Reader View, chrome://, moz-extension://,
   view-source:, javascript:) that got copied verbatim from the raw source
   into a quote or citation. agent.py's _strip_broken_wiki_links() already
   catches this going forward at write time, but wiki/sources/ pages are
   immutable after creation — a page written before that scheme was covered,
   or whose raw source carried an unusual scheme, keeps it forever with no
   normal write path to self-heal. This pass strips the link to plain text,
   matching what should have happened at creation time; a real http(s)/mailto
   link is left alone.

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

    # Extract just the filename and search wiki/ and raw/ for it
    filename = Path(link_path).name
    if not filename.endswith(".md") and not filename.endswith(".txt"):
        return None

    RAW_DIR = WIKI_DIR.parent / "raw"
    matches = list(WIKI_DIR.rglob(filename)) + list(RAW_DIR.glob(filename))
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


# --- Fix 3: junk-scheme links (about:, chrome:, moz-extension:, javascript:, ...) -------
# Any absolute URI (RFC 3986 scheme prefix) that isn't http(s)/mailto is not a wiki page
# and never will be — strip it to plain text rather than try to relocate it.

_scheme_re = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def _is_junk_scheme(link_path: str) -> bool:
    if link_path.startswith(("http", "mailto", "#")):
        return False
    return bool(_scheme_re.match(link_path))


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

    # Pass 3: junk-scheme links -> plain text. subn()'s own count is how many times the
    # pattern matched, not how many replacements actually changed anything (link_re
    # matches every link in the file) -- count explicitly, same as pass 2, or every
    # ordinary link would be misreported as a "fix".
    junk_count = [0]
    def _junk_replacer(m, _jc=junk_count):
        display, link_path = m.group(1), m.group(2)
        if _is_junk_scheme(link_path):
            _jc[0] += 1
            return display
        return m.group(0)

    new_text = link_re.sub(_junk_replacer, new_text)
    n3 = junk_count[0]

    total = n1 + n2 + n3
    if total:
        fixed_links += total
        fixed_files += 1
        rel = f.relative_to(WIKI_DIR)
        if DRY_RUN:
            print(f"  [dry-run] {rel}: would fix {total} ({n1} nested, {n2} bad-path, {n3} junk-scheme)")
        else:
            f.write_text(new_text, encoding="utf-8")
            print(f"  {rel}: fixed {total} ({n1} nested, {n2} bad-path, {n3} junk-scheme)")

print(f"\n{'[dry-run] ' if DRY_RUN else ''}Repaired {fixed_links} links across {fixed_files} files.")
