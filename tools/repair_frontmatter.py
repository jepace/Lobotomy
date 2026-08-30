#!/usr/bin/env python3
"""
Manually run the same page repairs the server performs automatically.

agent.heal_pages() runs on its own at server startup and after every ingest, so this is
normally unnecessary — it exists for repairing a wiki without starting the server, and
for previewing what would change with --dry-run.

Repairs (see agent.heal_pages for the full contract):
    missing created:/updated:  -> the file's own mtime
    missing tags:/sources:     -> []
    about:reader?url=...       -> the real article URL it wraps

Pages missing title: or type: are reported, never guessed.

Run from the repo root:
  python3 tools/repair_frontmatter.py [--dry-run]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import heal_pages

DRY_RUN = "--dry-run" in sys.argv

result = heal_pages(dry_run=DRY_RUN)
prefix = "[dry-run] " if DRY_RUN else ""
print(f"{prefix}{'Would repair' if DRY_RUN else 'Repaired'} {result['pages']} page(s): "
      f"{result['frontmatter']} frontmatter field(s), {result['reader_urls']} reader URL(s).")

if result["manual"]:
    print(f"\nNeeds manual attention ({len(result['manual'])}):")
    for item in result["manual"]:
        print(f"  - {item}")
