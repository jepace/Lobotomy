#!/usr/bin/env python3
"""Fold one wiki page into another: repoint every link, carry the names and sources over,
then delete the merged-away page.

`find_duplicate_pages.py` finds these — one subject under two slugs, `gdp.md` and
`gross-domestic-product.md` — and is report-only, because deciding two pages are the same
thing needs judgment. Once you have decided, the rest is mechanical, and doing it by hand
means grepping for every link and getting the `../` count right from each directory.

    python3 tools/merge_page.py wiki/concepts/gdp.md \\
        --into wiki/concepts/gross-domestic-product.md --dry-run
    python3 tools/merge_page.py wiki/concepts/gdp.md \\
        --into wiki/concepts/gross-domestic-product.md
    python3 tools/relink.py      # then re-link bare prose under the new alias

**The bodies are not merged for you.** Combining two pages' prose is the judgment half,
and a tool that concatenated them would produce exactly the duplication the wiki is trying
to avoid. So this refuses while the merged-away page still says anything the survivor does
not, and prints those lines. Move them first — the page's Edit button, or ask the agent to
— then run this again. `--force` skips the check, for when you have read the remainder and
decided it is redundant.

What it does carry over, because losing it would be silent damage:

- **every link** that pointed at the old page, repointed and re-relativized per page
- **`sources:`**, unioned into the survivor — provenance outlives the page
- **the old title and its aliases**, added as aliases of the survivor, so prose that said
  "GDP" still resolves after the page called GDP is gone. `--alias` adds more.

The old page's history under `wiki/.history/` is left in place: it is the only remaining
copy of what that page said.

Run from the repo root.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import merge_page, _rebuild_index

args = sys.argv[1:]
DRY_RUN = "--dry-run" in args
FORCE = "--force" in args
args = [a for a in args if a not in ("--dry-run", "--force")]


def _take(flag):
    out = []
    while flag in args:
        i = args.index(flag)
        if i + 1 >= len(args):
            sys.exit(f"{flag} needs a value")
        out.append(args[i + 1])
        del args[i:i + 2]
    return out


into = _take("--into")
aliases = _take("--alias")
if len(into) != 1 or len(args) != 1:
    print(__doc__.strip())
    sys.exit(2)

r = merge_page(args[0], into[0], extra_aliases=aliases, force=FORCE, dry_run=DRY_RUN)
tag = "[dry-run] " if DRY_RUN else ""

if r["error"]:
    print(f"Refused: {r['error']}")
    if r["outstanding"]:
        print(f"\nStill only on {args[0]} ({len(r['outstanding'])}):")
        for line in r["outstanding"][:25]:
            print(f"  - {line[:110]}")
        if len(r["outstanding"]) > 25:
            print(f"  … and {len(r['outstanding']) - 25} more")
    sys.exit(1)

if r["aliases"]:
    print(f"  {tag}aliases added to survivor: {', '.join(r['aliases'])}")
if r["sources_added"]:
    print(f"  {tag}sources carried over: {len(r['sources_added'])}")
for page in (r["repointed"] if "--all" in sys.argv else r["repointed"][:25]):
    print(f"  {tag}repointed links in {page}")
if len(r["repointed"]) > 25 and "--all" not in sys.argv:
    print(f"      … and {len(r['repointed']) - 25} more (--all to list them)")
print(f"\n{tag}Merged {r['deleted']} into {into[0]} — "
      f"{len(r['repointed'])} page(s) repointed.")
if not DRY_RUN:
    _rebuild_index({})
    print("Index rebuilt. Run `python3 tools/relink.py` to re-link bare prose "
          "under the survivor's new aliases.")
