#!/usr/bin/env python3
"""
Re-run the autolinker over wiki pages — turn bare mentions into wiki links.

A page is normally autolinked only while an ingest is touching it, against the titles that
existed at that moment. Nothing revisits it afterwards, so a page written before its
subjects had pages keeps those mentions as plain text forever. Since most titles get
created after most pages, this accumulates. This is the sweep that catches up.

The web UI has the same thing on /wiki/lint ("Relink all pages"), which runs in the
background with a progress bar. Use this when you want a single page, a subset, or a
dry run first.

Run from the repo root:
  python3 tools/relink.py                              # every page (minutes on a big wiki)
  python3 tools/relink.py wiki/entities/pg-e.md        # one page
  python3 tools/relink.py wiki/concepts/*.md           # a subset
  python3 tools/relink.py --dry-run                    # report, change nothing
  python3 tools/relink.py --dry-run wiki/entities/pg-e.md

Every page it changes gets a version-history entry first, so anything it gets wrong is
revertable from that page's History view.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import WIKI_DIR, relink_all, wiki_pages

args = [a for a in sys.argv[1:] if not a.startswith("-")]
flags = {a for a in sys.argv[1:] if a.startswith("-")}
DRY = "--dry-run" in flags or "-n" in flags

if flags - {"--dry-run", "-n"}:
    sys.exit(f"Unknown option: {', '.join(sorted(flags - {'--dry-run', '-n'}))}\n"
             f"Usage: python3 tools/relink.py [--dry-run] [page.md ...]")

pages = None
if args:
    pages = []
    for a in args:
        p = Path(a).resolve()
        if not p.exists():
            sys.exit(f"Not found: {a}")
        try:
            p.relative_to(WIKI_DIR.resolve())
        except ValueError:
            sys.exit(f"Not a wiki page (must be under wiki/): {a}")
        pages.append(p)

label = f"{len(pages)} page(s)" if pages else "the whole wiki"
print(f"{'Checking' if DRY else 'Relinking'} {label}…")

_last = [time.time()]


def _progress(done, total, path):
    # Only for the whole-wiki run, where this takes minutes and silence looks like a hang.
    if pages is None and (time.time() - _last[0] > 5 or done == total):
        _last[0] = time.time()
        print(f"  {done}/{total} ({100 * done // total}%) — {path}", flush=True)


res = relink_all(progress=_progress, pages=pages, dry_run=DRY)

verb = "would be changed" if DRY else "changed"
print(f"\n{res['changed']} of {res['scanned']} page(s) {verb} in {res['elapsed']:.1f}s.")
if DRY and res["changed"]:
    print("Dry run — nothing was written. Re-run without --dry-run to apply.")
