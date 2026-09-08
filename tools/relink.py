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


def _progress(done, total, path, changed):
    # Only for the whole-wiki run, where this takes minutes and silence looks like a hang.
    # "at <page>" deliberately: this names where the scan has got to, not a page that was
    # changed. Those are listed at the end, and confusing the two makes a sweep that
    # changed one page look like it rewrote hundreds.
    if pages is None and (time.time() - _last[0] > 5 or done == total):
        _last[0] = time.time()
        print(f"  scanned {done}/{total} ({100 * done // total}%) · "
              f"{changed} changed so far · at {path}", flush=True)


res = relink_all(progress=_progress, pages=pages, dry_run=DRY)

verb = "would be changed" if DRY else "changed"
print(f"\n{res['changed']} of {res['scanned']} page(s) {verb} in {res['elapsed']:.1f}s.")

if res["changed"]:
    shown = res["changed_pages"][:40]
    header = "Pages that would change:" if DRY else "Pages updated:"
    print(f"\n{header}")
    print("\n".join(f"  {p}" for p in shown))
    if res["changed"] > len(shown):
        print(f"  … and {res['changed'] - len(shown)} more")
else:
    print("\nNothing to do — every page already links everything it can.")

# A page is linked against the titles that exist when it is reached, so a title created
# mid-sweep is missed by everything already passed — and the next run picks those up. That
# reads as the tool failing to settle, so say plainly when it happened.
if res["titles_end"] != res["titles_start"]:
    delta = res["titles_end"] - res["titles_start"]
    print(f"\nNote: the title count changed during this run "
          f"({res['titles_start']} -> {res['titles_end']}, {delta:+d}). Something was "
          f"writing pages while this ran — an ingest, most likely. Pages already passed "
          f"could not see those titles, so another run will find more to do. Run it with "
          f"the queue idle for a result that settles.")

if DRY and res["changed"]:
    print("\nDry run — nothing was written. Re-run without --dry-run to apply.")
