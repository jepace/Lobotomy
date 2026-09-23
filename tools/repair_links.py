#!/usr/bin/env python3
"""
Repair broken internal markdown links in the wiki.

Fixes four classes of problems:

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

4. Links to a page that no longer exists anywhere:
       [United](../entities/united.md)   with entities/united.md gone
   →   United
   Fix 2 only answers "is this the wrong route to a page that still exists?" — when
   the page is simply gone it gives up, so one deleted page leaves a dead link on
   every page that ever mentioned it. That was 135 of them for a single entities/
   united.md, which the autolinker had been matching inside "United Nations" and
   "united in opposition" across the whole wiki.

   The link is unwrapped to its display text, not repointed: there is no target to
   point at, and guessing one would be worse than the dead link — those 135 span the
   airline, the UN, and the ordinary English word. The text is prose and reads
   correctly on its own. Same reasoning as rename_page.py, which strips links whose
   display text was the old title. Run relink.py afterwards and whatever genuinely
   names a real page is linked again.

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


def _file_index(wiki_dir: Path, raw_dir: Path, history_dir: Path) -> dict:
    """filename -> every real file with that name, built once per run.

    Replaces an rglob per broken link, and fixes two bugs that walk had:

      * It included `wiki/.history/`. A page deleted from the wiki leaves its history
        behind at `wiki/.history/entities/united.md/`, and rglob matches DIRECTORIES —
        so that leftover directory was the single "match" for every dead link to the
        page, and each one got helpfully repaired to `../.history/entities/united.md`.
        Measured on a synthetic tree: all of them, silently.
      * At ~9,000 pages `.history` holds up to 50 revisions each, so each of those walks
        crossed a few hundred thousand entries. One deleted page means one broken link
        per page that mentioned it, so the cost is quadratic in exactly the case that
        made this pass necessary — which is why a dry run sat silent for minutes.
    """
    index: dict = {}
    for p in wiki_dir.rglob("*"):
        if not p.is_file():
            continue
        try:
            p.relative_to(history_dir)
            continue          # a stored revision is a record, never a link target
        except ValueError:
            pass
        index.setdefault(p.name, []).append(p)
    if raw_dir.is_dir():
        for p in raw_dir.glob("*"):
            if p.is_file():
                index.setdefault(p.name, []).append(p)
    return index


def _repair_path(page: Path, link_path: str, index: dict) -> "str | None":
    """
    Given a link path that doesn't resolve from `page`, try to find the
    correct relative path by locating the filename in the file index.
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
    if target.exists() and target.is_file():
        return None  # already valid

    # Extract just the filename and look it up
    filename = Path(link_path).name
    if not filename.endswith(".md") and not filename.endswith(".txt"):
        return None

    matches = index.get(filename, [])
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


# --- Fix 4: unwrap links to a page that no longer exists --------------------------------

def _is_dangling(page: Path, link_path: str, index: dict) -> bool:
    """True when this link points at a wiki page that exists nowhere — not at the path
    given, and not under any other path either.

    _repair_path answers a narrower question: "is this the wrong route to a page that
    exists?" When the page is simply gone it returns None and the link is left as it was,
    so a deleted or renamed-around page leaves a dead link on every page that mentioned
    it — 135 of them, in the case this was written for, all pointing at an entities/united.md
    that no longer existed.
    """
    if re.match(r"^(https?:|mailto:|#|/)", link_path, re.IGNORECASE):
        return False
    link_path = link_path.split("#", 1)[0]
    if not link_path or not link_path.endswith((".md", ".txt")):
        return False
    _target = (page.parent / link_path).resolve()
    # is_file(), not exists(): the leftover `wiki/.history/entities/united.md/` is a
    # directory with a page's name, and a link resolving to one points at nothing.
    if _target.is_file():
        return False
    # Existing anywhere else in the tree makes it _repair_path's job, not this one.
    return Path(link_path).name not in index


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


# Generated and append-only files. Every other write path already refuses these —
# _update_file by name, the three section tools via their own `_reserved` tuple — and
# _snapshot_version skips them too, so a write here is the one edit in this whole
# directory that CANNOT be reverted. That alone settles it, and each has its own reason
# on top:
#
#   index.md  (the root one and every subdirectory one) is regenerated by
#             _rebuild_index at the end of every ingest, so a repair is erased within
#             the hour. It carried 5,900 "bad-path" links on the real wiki, all of them
#             genuinely broken and all of them put there by the generator — see
#             agent.py:first_desc_line. Repairing the output of a generator that will
#             rewrite it is churn; the generator is the thing to fix.
#   log.md    is the append-only audit trail. Unwrapping 136 dead links in it rewrites
#             the record of what happened, which is the one thing a log must not do.
#
# reading-list.md and tasks.md are deliberately NOT here: they are hand-maintained, they
# get history like any other page, and a dead link in them is a dead link worth fixing.
_GENERATED = ("index.md", "log.md")


def _wiki_pages(wiki_dir: Path, history_dir: Path):
    """The live wiki pages this pass may rewrite.

    Skips wiki/.history/ — those are saved revisions, not live pages, and rewriting one
    would defeat the point of keeping it as a record — and the generated/append-only
    files listed above.
    """
    for f in sorted(wiki_dir.rglob("*.md")):
        if f.name in _GENERATED:
            continue
        try:
            f.relative_to(history_dir)
        except ValueError:
            yield f


def repair_links(dry_run: bool = False, progress=None) -> dict:
    """Run all four repair passes over the wiki (and raw/ for the reader-URL unwrap).

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
    if progress:
        progress("indexing wiki/ and raw/…")
    index = _file_index(wiki_dir, raw_dir, history_dir)
    pages = list(_wiki_pages(wiki_dir, history_dir))
    if progress:
        progress(f"{len(index)} filenames indexed; scanning {len(pages)} pages")

    fixed_files = 0
    fixed_links = 0
    detail = []

    for _i, f in enumerate(pages, start=1):
        if progress and _i % 1000 == 0:
            progress(f"  {_i}/{len(pages)} pages, {fixed_links} fixes so far")
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Pass 1: nested links
        new_text, n1 = _NESTED_RE.subn(_repair_nested, text)

        # Pass 2: wrong relative paths, and pass 4: links to a page that is simply gone.
        # One walk over the links, so the precedence is explicit — repointing a link to a
        # page that still exists always beats unwrapping it.
        count, gone = [0], [0]

        def _path_replacer(m, _page=f, _count=count, _gone=gone):
            display   = m.group(1)
            link_path = m.group(2)
            fixed     = _repair_path(_page, link_path, index)
            if fixed:
                _count[0] += 1
                return f"[{display}]({fixed})"
            if display.strip() and _is_dangling(_page, link_path, index):
                # The target exists nowhere, so there is nothing to point at. Unwrap to
                # the display text rather than leaving a link that 404s: the text is
                # ordinary prose and reads correctly on its own. Same reasoning as
                # rename_page.py, which strips links whose text was the old title — and
                # for the same reason it is right here, since a deleted page's name was
                # usually a word the autolinker had no business matching. Running
                # relink.py afterwards re-links whatever genuinely names a real page.
                _gone[0] += 1
                return display
            return m.group(0)

        new_text = _LINK_RE.sub(_path_replacer, new_text)
        n2, n4 = count[0], gone[0]

        # Pass 3: unwrap about:reader?url= anywhere in the file (frontmatter url: and the
        # rendered ## Sources link alike). subn()'s count is safe here: _READER_RE only
        # matches actual wrappers, unlike _LINK_RE which matches every link.
        new_text, n3 = _READER_RE.subn(_unwrap_reader, new_text)

        total = n1 + n2 + n3 + n4
        if total:
            fixed_links += total
            fixed_files += 1
            rel = f.relative_to(wiki_dir)
            detail.append(f"{rel}: fixed {total} ({n1} nested, {n2} bad-path, "
                          f"{n3} reader-url, {n4} dangling)")
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
    # A silent multi-minute run is indistinguishable from a hung one — which is exactly
    # how the .history walk above was found. stderr, so piping stdout still gives a clean
    # report.
    def _progress(msg):
        print(msg, file=sys.stderr, flush=True)

    with write_reason("repair-links"):
        result = repair_links(dry_run=DRY_RUN, progress=_progress)
    for line in result["detail"]:
        print(f"  {'[dry-run] ' if DRY_RUN else ''}{line}")
    print(f"\n{'[dry-run] ' if DRY_RUN else ''}Repaired {result['fixed_links']} links "
          f"across {result['fixed_files']} files.")
