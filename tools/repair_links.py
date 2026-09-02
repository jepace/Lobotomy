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

3. Firefox Reader View URLs captured instead of the article URL:
       url: "about:reader?url=https%3A%2F%2Fwww.nytimes.com%2F..."
   Saving an article from the bookmarklet or share sheet while Reader View is
   open records location.href, which is the about:reader wrapper rather than
   the article. That value lands in the raw file's and source page's `url:`
   frontmatter, and _inject_sources_section then renders it as a [U](U) link
   in ## Sources — which is where it showed up as a "broken link". Capture is
   normalized at the source now (agent.py:_normalize_capture_url, applied at
   every capture point in serve.py), but existing files keep the bad URL, and
   wiki/sources/ pages are immutable so nothing rewrites them. This pass
   unwraps every occurrence of the wrapper anywhere in a file — fixing the
   frontmatter and the rendered ## Sources link together — so the result is a
   working link to the real article rather than a deleted one.

   Scans raw/ as well as wiki/, since the raw file carries the same bad url:.

Run from the repo root:
  python3 tools/repair_links.py [--dry-run]
"""
import os
import re
import sys
import urllib.parse
from pathlib import Path

WIKI_DIR = Path(__file__).resolve().parent.parent / "wiki"
RAW_DIR  = Path(__file__).resolve().parent.parent / "raw"
HISTORY_DIR = WIKI_DIR / ".history"
DRY_RUN  = "--dry-run" in sys.argv


def _wiki_pages():
    """wiki/**/*.md, skipping wiki/.history/ — those are saved revisions, not live
    pages, and rewriting one would defeat the point of keeping it as a record."""
    for f in sorted(WIKI_DIR.rglob("*.md")):
        try:
            f.relative_to(HISTORY_DIR)
        except ValueError:
            yield f

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


# --- Fix 3: unwrap Firefox Reader View URLs --------------------------------------------
# Mirrors agent.py:_normalize_capture_url, kept standalone so this repair tool stays
# dependency-free (importing agent.py would pull in config.json loading).

_reader_re = re.compile(r"about:reader\?url=[^\s\"'<>)\]]+", re.IGNORECASE)


def _unwrap_reader(match: "re.Match") -> str:
    wrapper = match.group(0)
    qs = wrapper.split("?", 1)[1] if "?" in wrapper else ""
    inner = urllib.parse.parse_qs(qs).get("url", [""])[0].strip()
    return inner if inner.startswith(("http://", "https://")) else wrapper


fixed_files = fixed_links = 0

for f in _wiki_pages():
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

    # Pass 3: unwrap about:reader?url= anywhere in the file (frontmatter url: and the
    # rendered ## Sources link alike). subn()'s count is safe here: _reader_re only
    # matches actual wrappers, unlike link_re which matches every link.
    new_text, n3 = _reader_re.subn(_unwrap_reader, new_text)

    total = n1 + n2 + n3
    if total:
        fixed_links += total
        fixed_files += 1
        rel = f.relative_to(WIKI_DIR)
        if DRY_RUN:
            print(f"  [dry-run] {rel}: would fix {total} ({n1} nested, {n2} bad-path, {n3} reader-url)")
        else:
            f.write_text(new_text, encoding="utf-8")
            print(f"  {rel}: fixed {total} ({n1} nested, {n2} bad-path, {n3} reader-url)")

# raw/ carries the same bad url: frontmatter from capture — link repair does not apply
# there (raw files have no wiki links), only the reader-URL unwrap.
for f in sorted(RAW_DIR.glob("*.md")) if RAW_DIR.is_dir() else []:
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    new_text, n = _reader_re.subn(_unwrap_reader, text)
    if n:
        fixed_links += n
        fixed_files += 1
        if DRY_RUN:
            print(f"  [dry-run] raw/{f.name}: would fix {n} (reader-url)")
        else:
            f.write_text(new_text, encoding="utf-8")
            print(f"  raw/{f.name}: fixed {n} (reader-url)")

print(f"\n{'[dry-run] ' if DRY_RUN else ''}Repaired {fixed_links} links across {fixed_files} files.")
