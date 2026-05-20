#!/usr/bin/env python3
"""Maintenance utilities — no LLM involved.

Usage:
  python3 tools/rebuild_sources.py                        # rebuild ## Sources on all entities + concepts
  python3 tools/rebuild_sources.py wiki/entities/foo.md   # rebuild ## Sources on one page
  python3 tools/rebuild_sources.py --index                # regenerate wiki/index.md
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agent import WIKI_DIR, _inject_sources_section, _atomic_write, _rebuild_index


def rebuild_sources(path: Path) -> bool:
    content = path.read_text(encoding="utf-8", errors="replace")
    new = _inject_sources_section(content, path)
    if new != content:
        _atomic_write(path, new)
        print(f"updated {path.relative_to(WIKI_DIR.parent)}")
        return True
    return False


if "--index" in sys.argv:
    print(_rebuild_index({}))
elif len(sys.argv) > 1:
    p = Path(sys.argv[1]).resolve()
    if not p.exists():
        p = (WIKI_DIR.parent / sys.argv[1]).resolve()
    changed = int(rebuild_sources(p))
    print(f"{changed} page(s) updated.")
else:
    targets = [
        p for d in ("entities", "concepts")
        for p in (WIKI_DIR / d).glob("*.md")
    ]
    changed = sum(rebuild_sources(p) for p in targets)
    print(f"{changed} page(s) updated.")
