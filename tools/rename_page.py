#!/usr/bin/env python3
"""
Rename a wiki page — file, title, and every link pointing at it.

Pages are titled by the LLM at creation from whatever the source called the subject, and
it sometimes picks a fragment: an article about the airline mentioned "United", so the
page became entities/united.md titled "United". A title that is a common word or part of
a longer proper noun then does real damage, because the autolinker matches it everywhere —
that page turned "United Nations" into "[United](../entities/united.md) Nations" across
the wiki.

    python3 tools/rename_page.py wiki/entities/united.md wiki/entities/united-airlines.md \\
        --title "United Airlines"

What it does:
  * moves the page, and its version history along with it
  * sets title: in the frontmatter when --title is given
  * repoints every link that pointed at the old path

Links whose display text was the OLD title are stripped back to plain text rather than
repointed, because that text is what is now wrong: "[United](...) Nations" becomes
"United Nations", not a link relabelled to an airline. Run relink.py afterwards and the
prose that genuinely names the subject gets linked again under the new title, while the
prose that never meant it stays plain.

Add --dry-run to see the counts without writing. Everything it changes goes through the
normal write path, so each page keeps a history entry and is revertable.
"""
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import (WIKI_DIR, HISTORY_DIR, wiki_pages, _atomic_write,
                   begin_write_scope, is_generated_page, _rebuild_index)

args = [a for a in sys.argv[1:] if not a.startswith("-")]
DRY = "--dry-run" in sys.argv
NEW_TITLE = None
if "--title" in sys.argv:
    i = sys.argv.index("--title")
    if i + 1 >= len(sys.argv):
        sys.exit("--title needs a value")
    NEW_TITLE = sys.argv[i + 1]
    args = [a for a in args if a != NEW_TITLE]

if len(args) != 2:
    sys.exit(__doc__.strip().split("\n\n")[0] + "\n\n"
             "Usage: python3 tools/rename_page.py <old.md> <new.md> [--title \"New Title\"] [--dry-run]")

src, dst = (Path(a).resolve() for a in args)
for p, label in ((src, "source"), (dst, "destination")):
    try:
        p.relative_to(WIKI_DIR.resolve())
    except ValueError:
        sys.exit(f"{label} must be inside wiki/: {p}")
if not src.exists():
    sys.exit(f"Not found: {src}")
if dst.exists():
    sys.exit(f"Destination already exists: {dst}")
if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", dst.stem):
    sys.exit(f"Destination filename is not a valid slug: {dst.name}")

text = src.read_text(encoding="utf-8", errors="replace")
old_title_m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', text, re.MULTILINE)
old_title = old_title_m.group(1).strip() if old_title_m else src.stem
new_text = text
if NEW_TITLE:
    new_text = re.sub(r'^title:.*$', f'title: "{NEW_TITLE}"', new_text, count=1, flags=re.MULTILINE)
    # The H1 normally repeats the title; keep them in step.
    new_text = re.sub(r'^#\s+' + re.escape(old_title) + r'\s*$', f"# {NEW_TITLE}",
                      new_text, count=1, flags=re.MULTILINE)

old_rel = src.relative_to(WIKI_DIR.resolve())
new_rel = dst.relative_to(WIKI_DIR.resolve())
print(f"{old_rel}  ->  {new_rel}")
print(f'title: "{old_title}"' + (f'  ->  "{NEW_TITLE}"' if NEW_TITLE else "  (unchanged)"))

LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
repointed = stripped = pages_touched = 0
edits = []

for page in wiki_pages():
    # index.md is regenerated below; log.md is the audit trail, and repointing a link in
    # it would rewrite what the log says happened. See agent._GENERATED_PAGES.
    if page.resolve() == src or is_generated_page(page):
        continue
    body = page.read_text(encoding="utf-8", errors="replace")

    def _fix(m):
        global repointed, stripped
        label, target = m.group(1), m.group(2)
        if target.startswith(("http", "#", "mailto")):
            return m.group(0)
        try:
            if (page.parent / target).resolve() != src:
                return m.group(0)
        except OSError:
            return m.group(0)
        # Display text that was the old title is exactly what the rename declares wrong.
        if NEW_TITLE and label.strip().lower() == old_title.lower():
            stripped += 1
            return label
        repointed += 1
        prefix = "../" * len(page.parent.relative_to(WIKI_DIR).parts)
        return f"[{label}]({prefix}{new_rel.as_posix()})"

    fixed = LINK_RE.sub(_fix, body)
    if fixed != body:
        pages_touched += 1
        edits.append((page, fixed))

print(f"\nlinks pointing at it: {repointed} repointed, {stripped} stripped to plain text, "
      f"across {pages_touched} page(s)")

if DRY:
    print("\nDry run — nothing written.")
    sys.exit(0)

begin_write_scope()
for page, fixed in edits:
    _atomic_write(page, fixed)
_atomic_write(dst, new_text)
src.unlink()
hist_src, hist_dst = HISTORY_DIR / old_rel, HISTORY_DIR / new_rel
if hist_src.is_dir():
    hist_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(hist_src), str(hist_dst))
    print(f"history moved: .history/{old_rel} -> .history/{new_rel}")

# The index links to the page by its old path and is not repointed above — it is
# generated, so it is rebuilt rather than patched. Without this the rename would leave a
# dead link in index.md until the next ingest happened to rebuild it.
_rebuild_index({})
print("index rebuilt")

print(f"\nDone. Now run:  python3 tools/relink.py")
print("so prose naming the subject links to it again under the new title.")
