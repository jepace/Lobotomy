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

Writes go through agent._atomic_write, like every other maintenance tool here: each page
changed gets a version-history entry first, so a bad repair is revertable from that page's
History view, and the file keeps its existing owner and mode rather than being re-owned to
whoever ran the tool. It used to call Path.write_text directly and had neither — the only
tool in this directory that did, while tools/README.md claimed all of them went through
the server's write path.

Run from the repo root:
  python3 tools/repair_links.py [--dry-run]
"""
import os
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent
from agent import _atomic_write, write_reason

# --- Fix 1: nested/double-linked patterns -----------------------------------

_NESTED_RE = re.compile(r'\[([^\]]+)\]\(([^)]*\[[^\]]*\][^)]*)\)')


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

_LINK_RE = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')


def _repair_path(page: Path, link_path: str, wiki_dir: Path, raw_dir: Path) -> "str | None":
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

    matches = list(wiki_dir.rglob(filename)) + list(raw_dir.glob(filename))
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
# Mirrors agent.py:_normalize_capture_url rather than importing it — the function there is
# entangled with capture-time concerns this pass does not want. (The module is imported
# either way now, for _atomic_write.)

_READER_RE = re.compile(r"about:reader\?url=[^\s\"'<>)\]]+", re.IGNORECASE)


def _unwrap_reader(match: "re.Match") -> str:
    wrapper = match.group(0)
    qs = wrapper.split("?", 1)[1] if "?" in wrapper else ""
    inner = urllib.parse.parse_qs(qs).get("url", [""])[0].strip()
    return inner if inner.startswith(("http://", "https://")) else wrapper


def _wiki_pages(wiki_dir: Path, history_dir: Path):
    """wiki/**/*.md, skipping wiki/.history/ — those are saved revisions, not live
    pages, and rewriting one would defeat the point of keeping it as a record."""
    for f in sorted(wiki_dir.rglob("*.md")):
        try:
            f.relative_to(history_dir)
        except ValueError:
            yield f


def repair_links(dry_run: bool = False) -> dict:
    """Run all three repair passes over the wiki (and raw/ for the reader-URL unwrap).

    Reads agent.WIKI_DIR / agent.RAW_DIR / agent.HISTORY_DIR at call time rather than
    capturing them as defaults — the same "resolve at call time, not at import" rule
    wiki_pages() learned the hard way (see CLAUDE.md "Module state"), so a test harness
    that rebinds those globals to a throwaway wiki is actually honored here instead of
    silently repairing the real one.

    Returns {"fixed_files": int, "fixed_links": int, "detail": [str, ...]}.
    """
    wiki_dir = agent.WIKI_DIR
    raw_dir = agent.RAW_DIR
    history_dir = agent.HISTORY_DIR

    fixed_files = 0
    fixed_links = 0
    detail = []

    for f in _wiki_pages(wiki_dir, history_dir):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Pass 1: nested links
        new_text, n1 = _NESTED_RE.subn(_repair_nested, text)

        # Pass 2: wrong relative paths
        count = [0]

        def _path_replacer(m, _page=f, _count=count):
            display   = m.group(1)
            link_path = m.group(2)
            fixed     = _repair_path(_page, link_path, wiki_dir, raw_dir)
            if fixed:
                _count[0] += 1
                return f"[{display}]({fixed})"
            return m.group(0)

        new_text = _LINK_RE.sub(_path_replacer, new_text)
        n2 = count[0]

        # Pass 3: unwrap about:reader?url= anywhere in the file (frontmatter url: and the
        # rendered ## Sources link alike). subn()'s count is safe here: _READER_RE only
        # matches actual wrappers, unlike _LINK_RE which matches every link.
        new_text, n3 = _READER_RE.subn(_unwrap_reader, new_text)

        total = n1 + n2 + n3
        if total:
            fixed_links += total
            fixed_files += 1
            rel = f.relative_to(wiki_dir)
            detail.append(f"{rel}: fixed {total} ({n1} nested, {n2} bad-path, {n3} reader-url)")
            if not dry_run:
                _atomic_write(f, new_text)

    # raw/ carries the same bad url: frontmatter from capture — link repair does not apply
    # there (raw files have no wiki links), only the reader-URL unwrap.
    for f in sorted(raw_dir.glob("*.md")) if raw_dir.is_dir() else []:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        new_text, n = _READER_RE.subn(_unwrap_reader, text)
        if n:
            fixed_links += n
            fixed_files += 1
            detail.append(f"raw/{f.name}: fixed {n} (reader-url)")
            if not dry_run:
                _atomic_write(f, new_text)

    return {"fixed_files": fixed_files, "fixed_links": fixed_links, "detail": detail}


if __name__ == "__main__":
    DRY_RUN = "--dry-run" in sys.argv
    # Scoped at the entry point rather than inside repair_links(), so it restores even if
    # the pass raises — and so a caller that wants its own label is not overridden.
    with write_reason("repair-links"):
        result = repair_links(dry_run=DRY_RUN)
    for line in result["detail"]:
        print(f"  {'[dry-run] ' if DRY_RUN else ''}{line}")
    print(f"\n{'[dry-run] ' if DRY_RUN else ''}Repaired {result['fixed_links']} links "
          f"across {result['fixed_files']} files.")
