#!/usr/bin/env python3
"""Rebuild the ## Sources section for one page or all entity/concept pages.

Usage:
  python3 tools/rebuild_sources.py                        # all entities + concepts
  python3 tools/rebuild_sources.py wiki/entities/foo.md   # one page
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agent import WIKI_DIR, _inject_sources_section, _atomic_write


def rebuild(path: Path) -> bool:
    content = path.read_text(encoding="utf-8", errors="replace")
    new = _inject_sources_section(content, path)
    if new != content:
        _atomic_write(path, new)
        print(f"updated {path.relative_to(WIKI_DIR.parent)}")
        return True
    return False


if len(sys.argv) > 1:
    targets = [Path(sys.argv[1])]
else:
    targets = [
        p for d in ("entities", "concepts")
        for p in (WIKI_DIR / d).glob("*.md")
    ]

changed = sum(rebuild(p) for p in targets)
print(f"{changed} page(s) updated.")
