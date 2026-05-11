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
        combined = re.compile(
            r'(\[[^\]]*\]\([^)]*\))'
            r'|(?<!\w)(' + re.escape(title) + r')(?!\w)',
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
            return f"[{m.group(2)}]({p})"

        new_body = combined.sub(_replacer, body)
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
