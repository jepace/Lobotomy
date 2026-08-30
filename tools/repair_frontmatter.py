#!/usr/bin/env python3
"""
Backfill missing system-managed frontmatter fields on existing wiki pages.

Why this exists: agent.py:_update_file now restores/backfills created:, updated: and
raw_source: on every write, so no page can lose them again. But that only fires when a
page is actually written — a page damaged before the fix keeps reporting as broken in
/wiki/lint and in the post-ingest validation report forever, because nothing touches it.
This is the one-time repair for that backlog.

Fills, from the file's own mtime (the best evidence available — the true creation date is
unknowable after the fact, and mtime is exactly what updated: means):
    created:   missing -> file mtime
    updated:   missing -> file mtime
    tags:      missing -> []
    sources:   missing -> []

title: and type: are NOT synthesized — they carry real meaning that cannot be guessed
safely, so pages missing those are reported for manual attention and left untouched.

Body content is never modified; only the frontmatter block is edited, and only by adding
fields that are absent. A field that is already present is left exactly as-is.

Run from the repo root:
  python3 tools/repair_frontmatter.py [--dry-run]
"""
import datetime
import re
import sys
from pathlib import Path

WIKI_DIR = Path(__file__).resolve().parent.parent / "wiki"
DRY_RUN  = "--dry-run" in sys.argv

# Operational files at the wiki root that legitimately have no article frontmatter.
SKIP_NAMES = {"index.md", "log.md", "overview.md", "reading-list.md",
              "tasks.md", "tasks-archive.md"}
SUBDIRS = ("sources", "entities", "concepts", "synthesis")

# Fields this tool can safely synthesize, in the order they should appear.
DATE_FIELDS = ("created", "updated")
LIST_FIELDS = ("tags", "sources")


def _insert_before_close(content: str, line: str) -> str:
    """Insert a frontmatter line just before the closing `---`.

    Anchored on the frontmatter block itself rather than a sibling field — the bug this
    whole exercise came from was an insert anchored on `updated:` that silently did
    nothing when `updated:` was also missing.
    """
    m = re.match(r"^---\s*\n.*?\n(---\s*\n)", content, re.DOTALL)
    if not m:
        return content
    at = m.start(1)
    return content[:at] + line + "\n" + content[at:]


fixed_files = fixed_fields = 0
needs_attention = []

pages = []
for sub in SUBDIRS:
    d = WIKI_DIR / sub
    if d.is_dir():
        pages.extend(sorted(d.glob("*.md")))

for f in pages:
    if f.name in SKIP_NAMES:
        continue
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue

    rel = str(f.relative_to(WIKI_DIR))
    fm_match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not fm_match:
        needs_attention.append(f"{rel}: no frontmatter block at all")
        continue

    keys = {l.split(":", 1)[0].strip() for l in fm_match.group(1).splitlines() if ":" in l}

    missing_core = [k for k in ("title", "type") if k not in keys]
    if missing_core:
        needs_attention.append(f"{rel}: missing {', '.join(missing_core)} (not auto-fillable)")

    mtime = datetime.date.fromtimestamp(f.stat().st_mtime).isoformat()
    added = []
    new_text = text
    for field in DATE_FIELDS:
        if field not in keys:
            new_text = _insert_before_close(new_text, f"{field}: {mtime}")
            added.append(field)
    for field in LIST_FIELDS:
        if field not in keys:
            new_text = _insert_before_close(new_text, f"{field}: []")
            added.append(field)

    if not added or new_text == text:
        continue

    fixed_files += 1
    fixed_fields += len(added)
    label = f"{rel}: {'would add' if DRY_RUN else 'added'} {', '.join(added)}"
    if DRY_RUN:
        print(f"  [dry-run] {label}")
    else:
        f.write_text(new_text, encoding="utf-8")
        print(f"  {label}")

print(f"\n{'[dry-run] ' if DRY_RUN else ''}Backfilled {fixed_fields} field(s) across {fixed_files} page(s).")
if needs_attention:
    print(f"\nNeeds manual attention ({len(needs_attention)}):")
    for n in needs_attention:
        print(f"  - {n}")
