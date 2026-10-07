#!/usr/bin/env python3
"""Fold one or more wiki pages into another: repoint every link, carry the names, sources
and section content over, then delete the merged-away pages.

`find_duplicate_pages.py` finds these — one subject under two slugs, `gdp.md` and
`gross-domestic-product.md` — and is report-only, because deciding two pages are the same
thing needs judgment. Once you have decided, the rest is mechanical, and doing it by hand
means grepping for every link and getting the `../` count right from each directory.

    python3 tools/merge_page.py wiki/concepts/gdp.md \\
        --into wiki/concepts/gross-domestic-product.md --carry --dry-run
    python3 tools/merge_page.py wiki/concepts/gdp.md \\
        --into wiki/concepts/gross-domestic-product.md --carry
    python3 tools/relink.py      # then re-link bare prose under the new alias

**Several losers in one command**, because `find_duplicate_pages.py` reports groups and
some of them are not pairs — Pacific Gas and Electric had five pages. Each is merged into
the survivor in turn, and a refusal on one does not stop the rest; the exit code is
non-zero if any was refused.

**A `--dry-run` over several losers OVERSTATES the carry**, and it has to. Each loser is
compared against the survivor as it is on disk, and a dry run writes nothing — so two
losers that both say a thing the survivor does not are each reported as carrying it. In a
real run the first merge's carry is on disk before the second is compared, so the second
sees it and carries nothing. Read a multi-loser dry run as the upper bound.

    python3 tools/merge_page.py wiki/entities/pge.md wiki/entities/pacific-gas-electric.md \\
        wiki/entities/pacific-gas-and-electric.md \\
        --into wiki/entities/pacific-gas-and-electric-company.md --carry

**`--carry` is what makes a merge not be hand work.** Without it this refuses while the
merged-away page still says anything the survivor does not, and prints those lines for you
to move yourself — which was the one manual step left, and across 35 duplicate groups it
is the whole cost of the cleanup. With it, every outstanding line is appended to the
survivor's section of the same name, or to a new section if it has none — and a section it
had to create is reported, because the two pages naming one section differently
("Positions" against "Political Stances") is a judgement no string comparison can make.

**Except a summary.** An `## Overview` or `## Definition` delta is never appended, because
appending a sentence to a summary is exactly the accretion `_accreted_dated_sentences`
refuses and `overview_drift.py` reports on 11,000 pages. A tool that did it in the name of
convenience would be manufacturing the wiki's worst existing defect one merge at a time.
Those lines come back with the `update_section` call that resolves them, and there are
usually one or two — so `--carry` turns "move every delta by hand" into "rewrite one
paragraph", and the paragraph is the judgement half.

`--force` skips the outstanding check entirely, for when you have read the remainder and
decided it is redundant. It is the only mode that can lose text.

What is carried over regardless, because losing it would be silent damage:

- **every link** that pointed at the old page, repointed and re-relativized per page
- **`sources:`**, unioned into the survivor — provenance outlives the page
- **the old title and its aliases**, added as aliases of the survivor, so prose that said
  "GDP" still resolves after the page called GDP is gone. `--alias` adds more.

The old page's history under `wiki/.history/` is left in place: it is the only remaining
copy of what that page said. The survivor's own pre-merge content is snapshotted by
`_atomic_write`, so a merge is itself revertable from the survivor's history view.

Run from the repo root.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import merge_page, _rebuild_index

args = sys.argv[1:]
DRY_RUN = "--dry-run" in args
FORCE = "--force" in args
CARRY = "--carry" in args
ALL = "--all" in args
args = [a for a in args if a not in ("--dry-run", "--force", "--carry", "--all")]


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
if len(into) != 1 or not args:
    print(__doc__.strip())
    sys.exit(2)

tag = "[dry-run] " if DRY_RUN else ""
refused = 0

for n, loser in enumerate(args):
    if len(args) > 1:
        print(f"{'=' * 4} {loser}")
    # Aliases are passed on the first merge only. They are the operator's extra names for
    # the SURVIVOR, not for each loser, and adding them once per loser would be harmless
    # but would report them repeatedly as though something new had happened each time.
    r = merge_page(loser, into[0], extra_aliases=(aliases if n == 0 else ()),
                   force=FORCE, dry_run=DRY_RUN, carry=CARRY)

    made = set(r.get("new_sections") or ())
    for section, lines in (r.get("carried") or {}).items():
        note = "  (NEW section — check it is not the survivor's own under another name)" \
            if section in made else ""
        print(f"  {tag}carried {len(lines)} line(s) into ## {section}{note}")
        for line in lines[:4]:
            print(f"      {line[:104]}")
        if len(lines) > 4:
            print(f"      … and {len(lines) - 4} more")

    if r["error"]:
        refused += 1
        print(f"Refused: {r['error']}")
        if r["outstanding"]:
            print(f"\nStill only on {loser} ({len(r['outstanding'])}):")
            for c in (r.get("outstanding_detail") or
                      [{"raw": x, "section": ""} for x in r["outstanding"]])[:25]:
                # `new` is the sentences the survivor lacks; `raw` is the paragraph that
                # held them. Printing the paragraph meant diffing by eye.
                where = f"## {c['section']}  " if c.get("section") else ""
                print(f"  - {where}{(c.get('new') or c['raw'])[:200]}")
            if len(r["outstanding"]) > 25:
                print(f"  … and {len(r['outstanding']) - 25} more")
        print()
        continue

    if r["aliases"]:
        print(f"  {tag}aliases added to survivor: {', '.join(r['aliases'])}")
    if r["sources_added"]:
        print(f"  {tag}sources carried over: {len(r['sources_added'])}")
    for page in (r["repointed"] if ALL else r["repointed"][:25]):
        print(f"  {tag}repointed links in {page}")
    if len(r["repointed"]) > 25 and not ALL:
        print(f"      … and {len(r['repointed']) - 25} more (--all to list them)")
    print(f"{tag}Merged {r['deleted']} into {into[0]} — "
          f"{len(r['repointed'])} page(s) repointed.\n")

if not DRY_RUN and refused < len(args):
    _rebuild_index({})
    print("Index rebuilt. Run `python3 tools/relink.py` to re-link bare prose "
          "under the survivor's new aliases.")
if DRY_RUN and len(args) > 1:
    print("A multi-loser dry run is an upper bound: nothing was written, so each loser was\n"
          "compared against the unchanged survivor. In a real run the first carry is on\n"
          "disk before the second loser is compared.")
if refused:
    print(f"{refused} of {len(args)} refused." if len(args) > 1 else "", end="")
    if not CARRY:
        print("\n--carry places every non-summary delta for you; only a summary rewrite "
              "is left by hand.")
sys.exit(1 if refused else 0)
