#!/usr/bin/env python3
"""
autolink_wiki.py — one-shot wiki cross-linker

Scans every page in wiki/sources, entities, concepts, synthesis and inserts
a link on the first bare occurrence of every other wiki page title. Safe to
re-run: existing links are never touched, only bare text is linked.

Usage:
    python3 tools/autolink_wiki.py [--dry-run]
"""
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WIKI_DIR  = REPO_ROOT / "wiki"
SUBDIRS   = ("sources", "entities", "concepts", "synthesis")

DRY_RUN = "--dry-run" in sys.argv


def build_title_index() -> list[tuple[str, Path]]:
    pages = []
    for sd in SUBDIRS:
        d = WIKI_DIR / sd
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            if f.name == "index.md":
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
            if not m:
                continue
            for line in m.group(1).splitlines():
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"\'')
                    if title:
                        pages.append((title, f))
                    break
    return pages


def _title_alts(title: str) -> str:
    """
    Return a regex alternation string matching `title` bare OR with exactly one
    contiguous sub-span of words already wrapped in a markdown link.
    This lets the autolinker upgrade partial links like
      'CASA of [Monterey County](url)' → '[CASA of Monterey County](new_url)'.
    """
    words = title.split()
    n = len(words)

    def _esc(ws: list) -> str:
        return r"\s+".join(re.escape(w) for w in ws)

    alts = [_esc(words)]
    for s in range(n):
        for e in range(s + 1, n + 1):
            if s == 0 and e == n:
                continue
            pre, span, post = words[:s], words[s:e], words[e:]
            p = ""
            if pre:  p += _esc(pre) + r"\s+"
            p += r"\[" + _esc(span) + r"\]\([^)]*\)"
            if post: p += r"\s+" + _esc(post)
            alts.append(p)
    return "(?:" + "|".join(alts) + ")"


def autolink_page(target_p: Path, title_index: list[tuple[str, Path]]) -> int:
    up_parts = target_p.parent.relative_to(WIKI_DIR).parts
    up = "/".join([".." ] * len(up_parts))

    title_map = []
    for title, src_p in title_index:
        if src_p.resolve() == target_p.resolve():
            continue
        rel = src_p.relative_to(WIKI_DIR)
        link_path = f"{up}/{rel}" if up else str(rel)
        title_map.append((title, link_path))

    title_map.sort(key=lambda x: -len(x[0]))

    content  = target_p.read_text(encoding="utf-8", errors="replace")
    fm_match = re.match(r"^(---\s*\n.*?\n---\s*\n)", content, re.DOTALL)
    frontmatter, body = (
        (fm_match.group(1), content[len(fm_match.group(1)):])
        if fm_match else ("", content)
    )
    # Split off the H1 title line so we never linkify inside it.
    h1_match = re.match(r"^(# [^\n]*\n)", body)
    h1_line = h1_match.group(1) if h1_match else ""
    body = body[len(h1_line):]

    linked = 0
    for title, link_path in title_map:
        # Group 1: existing complete link — pass through unchanged (never nest links).
        # Group 2: title bare or with a sub-span already linked — replace with new link.
        combined = re.compile(
            r"(\[[^\]]*\]\([^)]*\))"
            r"|(?<!\w)(" + _title_alts(title) + r")(?!\w)",
            re.IGNORECASE,
        )
        replaced = False

        def _replacer(m, p=link_path):
            nonlocal replaced
            if m.group(1):
                return m.group(1)
            if replaced:
                return m.group(2)
            replaced = True
            display = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", m.group(2))
            return f"[{display}]({p})"

        new_lines = []
        for line in body.split("\n"):
            if re.match(r'^#{1,6}\s', line):
                new_lines.append(line)
            else:
                new_lines.append(combined.sub(_replacer, line))
        new_body = "\n".join(new_lines)
        if replaced:
            body = new_body
            linked += 1

    if linked and not DRY_RUN:
        target_p.write_text(frontmatter + h1_line + body, encoding="utf-8")
    return linked


def main():
    print(f"Building title index…")
    title_index = build_title_index()
    print(f"  {len(title_index)} wiki pages indexed.")
    if DRY_RUN:
        print("  (dry-run — no files will be written)")
    print()

    total_linked = 0
    pages_changed = 0

    for sd in SUBDIRS:
        d = WIKI_DIR / sd
        if not d.is_dir():
            continue
        pages = sorted(p for p in d.glob("*.md") if p.name != "index.md")
        for target_p in pages:
            n = autolink_page(target_p, title_index)
            if n:
                rel = target_p.relative_to(WIKI_DIR)
                print(f"  {rel}: +{n} link(s)")
                total_linked += n
                pages_changed += 1

    print()
    action = "Would add" if DRY_RUN else "Added"
    print(f"{action} {total_linked} link(s) across {pages_changed} page(s).")


if __name__ == "__main__":
    main()
