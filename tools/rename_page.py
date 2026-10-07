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
import pathlib
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import (_fm_title, WIKI_DIR, HISTORY_DIR, wiki_pages, _atomic_write,
                   begin_write_scope, is_generated_page, _rebuild_index,
                   _mkdir_inheriting)

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
dst_arg = args[1]          # as typed, for copy-pasteable suggestions
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
    # Name the call that works, not just the rule that was broken. The correction is
    # mechanical — lowercase, and any run of non-alphanumerics becomes one hyphen — so
    # there is no reason to make the caller guess it. Observed: an underscored name was
    # refused with nothing but "not a valid slug", and underscores are the obvious thing
    # to try when the convention is not in front of you.
    _fix = re.sub(r"[^a-z0-9]+", "-", dst.stem.lower()).strip("-")
    # Built from the argument AS TYPED, not from the resolved path, or the suggestion
    # comes back as an absolute path and is not copy-pasteable.
    _typed = pathlib.PurePosixPath(dst_arg)
    _hint = (f"\n\nDid you mean:\n"
             f"    python3 {sys.argv[0]} {args[0]} {_typed.parent}/{_fix}.md "
             f"--title \"...\"\n"
             if _fix and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", _fix) else "")
    sys.exit(f"Destination filename is not a valid slug: {dst.name}\n"
             f"Page files are lowercase-hyphenated: letters, digits and single hyphens "
             f"only.{_hint}")

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

# **A move between directories has to carry `type:` with it.** This script happily moved
# concepts/ebola.md to entities/ebola.md and left `type: concept` in the frontmatter — and
# `_OPENER`, `section_inventory.py` and `bleeding_titles.py` all read the FIELD, not the
# directory. So the half-move is worse than the misfile it was correcting: the page is
# where an entity lives while claiming to be a concept, and every tool that asks reads the
# stale answer. The directory is the one unambiguous signal here, so it wins.
_DIR_TYPE = {"entities": "entity", "concepts": "concept", "synthesis": "synthesis"}
_old_dir, _new_dir = old_rel.parts[0] if old_rel.parts[:-1] else "", \
                     new_rel.parts[0] if new_rel.parts[:-1] else ""
_new_type = _DIR_TYPE.get(_new_dir)
_type_changed = None
if _new_type and _old_dir != _new_dir:
    _tm = re.search(r"^type:[ \t]*(\S+)[ \t]*$", new_text, re.MULTILINE)
    if _tm and _tm.group(1) != _new_type:
        _type_changed = (_tm.group(1), _new_type)
        new_text = re.sub(r"^type:[ \t]*\S+[ \t]*$", f"type: {_new_type}",
                          new_text, count=1, flags=re.MULTILINE)

print(f"{old_rel}  ->  {new_rel}")
if _type_changed:
    print(f"type: {_type_changed[0]}  ->  {_type_changed[1]}   "
          f"(the destination directory decides)")
    _opener = {"entity": "Overview", "concept": "Definition"}.get(_new_type)
    _wrong = {"entity": "Definition", "concept": "Overview"}.get(_new_type)
    if _opener and _wrong and re.search(r"^#{2,6}[ \t]*" + _wrong + r"[ \t]*$",
                                        new_text, re.MULTILINE):
        print(f"  NOTE: the page still opens with '## {_wrong}'; a {_new_type} page wants "
              f"'## {_opener}'.\n"
              f"        python3 tools/rename_section.py '{_wrong}' '{_opener}' "
              f"   # or fix it in the editor")
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
    # NOT mkdir(parents=True): CLAUDE.md's rule, and this file was the one place still
    # breaking it. Run as root beside a server running as another user — which is how this
    # tool is normally run — a root-owned level inside wiki/.history/ means the server can
    # no longer write revisions for that page, and `_snapshot_version` deliberately never
    # raises, so the page simply stops accumulating history and nothing says so.
    _mkdir_inheriting(hist_dst.parent)
    shutil.move(str(hist_src), str(hist_dst))
    print(f"history moved: .history/{old_rel} -> .history/{new_rel}")

# The index links to the page by its old path and is not repointed above — it is
# generated, so it is rebuilt rather than patched. Without this the rename would leave a
# dead link in index.md until the next ingest happened to rebuild it.
_rebuild_index({})
print("index rebuilt")

print(f"\nDone. Now run:  python3 tools/relink.py")
print("so prose naming the subject links to it again under the new title.")

# The autolinker matches title:, never the filename. Renaming the FILE and leaving the
# title alone therefore changes nothing about what gets linked — relink simply recreates
# the same links against the new path. That is worth saying out loud, because the reason
# to rename one of these pages is almost always that its title is a common word, and the
# filename is the part that looks like the name.
if not NEW_TITLE and not DRY:
    _t = _fm_title(dst.read_text(encoding="utf-8", errors="replace"))
    print(f'\nNOTE: title: is unchanged — still "{_t}". The autolinker matches the TITLE,\n'
          f"      not the filename, so relink will link exactly what it linked before,\n"
          f"      only to the new path. If you renamed this to stop a common word being\n"
          f"      linked, pass --title too:\n"
          f'          python3 {sys.argv[0]} {args[0]} {dst_arg} --title "{_t} (…)"')
