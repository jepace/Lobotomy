#!/usr/bin/env python3
"""
Which page titles are common words, measured from the wiki itself.

A page titled "Lost" links the word *lost* in "the hikers were lost for three days". So does
"Agency", "Power", "Mission", "United". The autolinker matches case-insensitively, so a
title that is also an ordinary English word bleeds into every page that happens to use it —
and the once-per-section rule makes it worse than a stray link, because the wrong mention
SPENDS the section's one link and the genuine mention then gets nothing.

No dictionary is needed to find these, and none is used: the wiki says which of its own
titles are common words. A proper noun is written capitalised wherever it appears; a common
noun is written lowercase. So count both, per title, across the whole wiki.

Two numbers matter and they are different questions:

  * ALREADY LINKED, lowercase display text — links on disk right now that are almost
    certainly wrong. This is damage done.
  * BARE lowercase — occurrences not yet linked, which the next relink sweep will link.
    This is damage pending.

Capitalised occurrences are counted too, as the honest denominator. Read them with one
caveat: a sentence starting "Lost aired in 2010" and one starting "Lost in the woods, they
turned back" both look capitalised, so the capitalised count is an over-estimate of genuine
proper-noun use, which makes this report conservative — it under-reports bleeding rather
than over-reporting it.

    python3 tools/bleeding_titles.py                 # the worst offenders
    python3 tools/bleeding_titles.py --all           # every title with any lowercase use
    python3 tools/bleeding_titles.py --min-lower 25  # raise the reporting floor
    python3 tools/bleeding_titles.py --words 2       # also check two-word titles (slower)

Read-only: it writes nothing, makes no API call, and costs nothing to run. For each page it
flags, it prints the rename command to fix it.

Headings are excluded from the counts because the autolinker skips heading lines, so an
occurrence there is not linkable either way. A page's own title on its own page is excluded
for the same reason it is not interesting: every page says its own name.
"""
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent
from agent import _MD_LINK_RE, is_generated_page

# A word, as the autolinker's own boundaries see one. Apostrophes are kept inside a token so
# "Noah's" is one word rather than two.
_WORD = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?")


def _slug_of(rel: str) -> str:
    return Path(rel).name


def _candidates(max_words: int):
    """{lowercased title: [(title, wiki_rel), ...]} for titles short enough to bleed.

    Aliases are in the title map too and bleed exactly the same way, so they are included;
    the report says which is which by looking the title up again.
    """
    out = defaultdict(list)
    for title, rel in agent._build_title_map():
        words = _WORD.findall(title)
        if not words or len(words) > max_words:
            continue
        out[" ".join(w.lower() for w in words)].append((title, rel))
    return out


def scan(max_words: int = 1):
    """Count lowercase and capitalised uses of every candidate title across the wiki."""
    cands = _candidates(max_words)
    stats = {k: {"lower": 0, "cap": 0, "linked_lower": 0, "linked_cap": 0}
             for k in cands}

    for p in agent.wiki_pages():
        rel = str(p.relative_to(agent.WIKI_DIR))
        if is_generated_page(p):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.match(r"^---\s*\n.*?\n---\s*\n", text, re.DOTALL)
        body = text[m.end():] if m else text

        # Existing links first, then remove them so the bare scan cannot count them twice.
        for disp, target in _MD_LINK_RE.findall(body):
            key = " ".join(w.lower() for w in _WORD.findall(disp))
            if key not in stats:
                continue
            # Only links pointing at a page this title actually names.
            if not any(_slug_of(r) == _slug_of(target.split("#")[0])
                       for _t, r in cands[key]):
                continue
            first = next((c for c in disp if c.isalpha()), "")
            stats[key]["linked_lower" if first.islower() else "linked_cap"] += 1
        bare = _MD_LINK_RE.sub(" ", body)

        for line in bare.splitlines():
            if re.match(r"^\s*#{1,6}\s", line):
                continue                        # the autolinker skips headings
            toks = [(mm.group(0), mm.start()) for mm in _WORD.finditer(line)]
            for n in range(1, max_words + 1):
                for i in range(len(toks) - n + 1):
                    span = [t for t, _ in toks[i:i + n]]
                    key = " ".join(w.lower() for w in span)
                    st = stats.get(key)
                    if st is None:
                        continue
                    # This title names this very page — every page says its own name.
                    if any(r == rel for _t, r in cands[key]):
                        continue
                    st["lower" if span[0][0].islower() else "cap"] += 1

    rows = []
    for key, st in stats.items():
        lower = st["lower"] + st["linked_lower"]
        cap = st["cap"] + st["linked_cap"]
        if not lower:
            continue
        rows.append({
            "key": key,
            "titles": cands[key],
            "lower": lower, "cap": cap,
            "linked_lower": st["linked_lower"], "bare_lower": st["lower"],
            "share": lower / (lower + cap) if (lower + cap) else 1.0,
        })
    rows.sort(key=lambda r: (-r["linked_lower"], -r["bare_lower"]))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true",
                    help="every title with any lowercase use, not just the worst")
    ap.add_argument("--min-lower", type=int, default=5,
                    help="ignore titles with fewer lowercase uses than this (default 5)")
    ap.add_argument("--words", type=int, default=1,
                    help="longest title to check, in words (default 1)")
    args = ap.parse_args()

    rows = scan(max_words=max(1, args.words))
    shown = [r for r in rows
             if args.all or (r["lower"] >= args.min_lower and r["share"] >= 0.5)]

    if not shown:
        print("No bleeding titles found.")
        return 0

    print(f"{len(shown)} title(s) used mostly as ordinary words. "
          f"'linked' are links on disk now; 'bare' is what the next relink would link.\n")
    print(f"{'title':<24} {'linked':>7} {'bare':>7} {'CAPS':>7}  {'lower':>6}  page")
    print("-" * 88)
    for r in shown:
        title, rel = r["titles"][0]
        print(f"{title[:23]:<24} {r['linked_lower']:>7} {r['bare_lower']:>7} "
              f"{r['cap']:>7}  {r['share']*100:>5.0f}%  {rel}")
        for extra_t, extra_rel in r["titles"][1:]:
            print(f"{'  also ' + extra_t[:15]:<24} {'':>7} {'':>7} {'':>7}  {'':>6}  {extra_rel}")

    print("\nTo disambiguate one, Wikipedia-style — the parenthetical never appears in\n"
          "prose, so the bleeding stops immediately and the wrong links are stripped:\n")
    for r in shown[:5]:
        title, rel = r["titles"][0]
        stem = Path(rel).stem
        print(f"  python3 tools/rename_page.py wiki/{rel} wiki/{Path(rel).parent}/"
              f"{stem}-disambiguated.md \\\n"
              f'      --title "{title} (…)"')
    print("\n  python3 tools/relink.py        # once, after the renames")
    print("\nNote that renaming stops the bleeding but wins no links back: prose says\n"
          f'"{shown[0]["titles"][0][0]}", never "{shown[0]["titles"][0][0]} (…)". An '
          'aliases: entry would restore\nlinking AND the bleeding, because matching is '
          "case-insensitive — so the alias\ntakes the lowercase occurrence first and the "
          "once-per-section rule then leaves\nthe real mention with nothing. Until alias "
          "matching can be case-sensitive,\n`no_autolink: true` is the honest setting for "
          "a page like this.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
