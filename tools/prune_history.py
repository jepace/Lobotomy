#!/usr/bin/env python3
"""
What `wiki/.history/` is costing, and how to get some of it back.

Saving an article stopped working, and the cause was nginx:

    pwritev() "/var/tmp/nginx/client_body_temp/0000000398" failed
              (28: No space left on device)

A request body larger than `client_body_buffer_size` is spooled to disk, so **a full
filesystem fails exactly the long pastes and none of the short ones** — which is why it
looked like a content bug for days. Nothing in the application log, because nginx never
forwarded the request.

The history store is the most likely thing to have filled it. `_snapshot_version` keeps
`_HISTORY_KEEP` (50) revisions **per page**, pruned per page on write, with no aggregate
cap — and at ~11,000 pages that is up to half a million full copies, not diffs. Nothing
reported its size, so it grew unwatched.

    python3 tools/prune_history.py                 # report only; touches nothing
    python3 tools/prune_history.py --keep 10       # what pruning to 10 would reclaim
    python3 tools/prune_history.py --keep 10 --apply
    python3 tools/prune_history.py --orphans       # pages that no longer exist

**A dry run is the default and `--apply` is required**, because this deletes history that
cannot come back: a revision is the only copy of what a page said before a write. Keeping
fewer is a real loss of recoverable past, so the report leads with what you would give up.

Deleting the OLDEST revisions of each page is the right order — they are the ones
`_snapshot_version` would evict next anyway, and the recent ones are what a revert reaches
for. Orphans are separate and safer: `wiki/.history/entities/foo.md/` for a page that no
longer exists is history for something you cannot navigate to.
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:,.0f}{unit}" if unit == "B" else f"{n:,.1f}{unit}"
        n /= 1024.0
    return f"{n:,.1f}TB"


def page_dirs():
    """Every per-page revision directory under the history store.

    Resolved at call time, never captured at import — the rule CLAUDE.md states for all
    four of agent's path globals, and the one tools/add_story.py broke.
    """
    root = agent.HISTORY_DIR
    if not root.is_dir():
        return []
    return [d for d in root.rglob("*") if d.is_dir()
            and any(f.is_file() for f in d.iterdir())]


def survey(keep: int):
    """(rows, totals). A row per page directory, newest-first revisions already ordered.

    Revision filenames begin with a fixed-width microsecond timestamp precisely so lexical
    sort is chronological — the history view and `_snapshot_version`'s own pruning both
    depend on that, and so does this.
    """
    rows, total_bytes, total_files, reclaim_bytes, reclaim_files = [], 0, 0, 0, 0
    for d in page_dirs():
        revs = sorted(f for f in d.iterdir() if f.is_file())
        sizes = [f.stat().st_size for f in revs]
        page_bytes = sum(sizes)
        doomed = revs[:-keep] if keep > 0 else list(revs)
        d_bytes = sum(f.stat().st_size for f in doomed)
        total_bytes += page_bytes
        total_files += len(revs)
        reclaim_bytes += d_bytes
        reclaim_files += len(doomed)
        rows.append({"dir": d, "revs": revs, "bytes": page_bytes,
                     "doomed": doomed, "doomed_bytes": d_bytes,
                     "orphan": not (agent.WIKI_DIR / d.relative_to(agent.HISTORY_DIR)).exists()})
    rows.sort(key=lambda r: -r["bytes"])
    return rows, {"bytes": total_bytes, "files": total_files,
                  "reclaim_bytes": reclaim_bytes, "reclaim_files": reclaim_files}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", type=int, default=agent._HISTORY_KEEP,
                    help=f"revisions to keep per page (default {agent._HISTORY_KEEP})")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete; without it nothing is touched")
    ap.add_argument("--orphans", action="store_true",
                    help="only pages that no longer exist in the wiki")
    ap.add_argument("--top", type=int, default=15, help="pages to list (default 15)")
    args = ap.parse_args()

    if args.keep < 0:
        ap.error("--keep cannot be negative")

    rows, tot = survey(args.keep)
    if not rows:
        print(f"No history under {agent.HISTORY_DIR}.")
        return 0

    free = shutil.disk_usage(agent.HISTORY_DIR).free
    print(f"history store: {agent.HISTORY_DIR}")
    print(f"  {tot['files']:,} revisions across {len(rows):,} pages, {_human(tot['bytes'])}")
    print(f"  free on this filesystem: {_human(free)}")

    if args.orphans:
        rows = [r for r in rows if r["orphan"]]
        # A deleted page's whole directory goes, not just its older revisions.
        for r in rows:
            r["doomed"], r["doomed_bytes"] = r["revs"], r["bytes"]
        tot["reclaim_files"] = sum(len(r["doomed"]) for r in rows)
        tot["reclaim_bytes"] = sum(r["doomed_bytes"] for r in rows)
        print(f"\n{len(rows):,} page(s) have history but no page in the wiki.")

    print(f"\nlargest:")
    for r in rows[:args.top]:
        rel = r["dir"].relative_to(agent.HISTORY_DIR)
        mark = "  (orphan)" if r["orphan"] else ""
        print(f"  {_human(r['bytes']):>9}  {len(r['revs']):>3} revs  {rel}{mark}")

    if not tot["reclaim_files"]:
        print(f"\nNothing to prune at --keep {args.keep}.")
        return 0

    print(f"\nPruning to {args.keep} revision(s) per page would delete "
          f"{tot['reclaim_files']:,} revision(s) and reclaim {_human(tot['reclaim_bytes'])}.")
    print("That history is the only copy of what those pages said before each write; "
          "it cannot be recovered.")

    if not args.apply:
        print("\nDry run — nothing was touched. Add --apply to delete.")
        return 0

    deleted = freed = 0
    for r in rows:
        for f in r["doomed"]:
            try:
                n = f.stat().st_size
                f.unlink()
                deleted += 1
                freed += n
            except OSError as e:
                print(f"  could not delete {f}: {e}", file=sys.stderr)
        # An emptied directory is noise in a tree every maintenance pass walks.
        try:
            if r["dir"].is_dir() and not any(r["dir"].iterdir()):
                r["dir"].rmdir()
        except OSError:
            pass
    print(f"\nDeleted {deleted:,} revision(s), reclaimed {_human(freed)}.")
    print(f"free now: {_human(shutil.disk_usage(agent.HISTORY_DIR).free)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
