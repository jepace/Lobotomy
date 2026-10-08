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

**Read the page TYPE first, because it decides whether a lowercase use is wrong at all.**

  * A `concept` page is *supposed* to catch the common noun. "inflation" in prose is about
    inflation, and `[inflation](../concepts/inflation.md)` is the link a concept wiki
    exists to make. A high lowercase share on a concept page is normal, and renaming it
    would be a mistake. They are reported under --concepts, for review, not for action.
  * An `entity` page is a proper noun, so a lowercase use of its name is a DIFFERENT WORD.
    "Succession" the series against succession the process; "Visa" the company against a
    visa in a passport; "Block", "Notion", "Coach", "Vanguard", "Girls", "Survivor". These
    are the real bleeds and they are what the default report shows.

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


def _page_type(rel: str) -> str:
    """entity / concept / source / synthesis, from the page's own frontmatter.

    The single most important column, and it was missing from the first version of this
    report: without it the tool flagged 383 titles and told you to rename them, when most
    were concept pages doing exactly what a concept page is for.
    """
    try:
        text = (agent.WIKI_DIR / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "?"
    m = re.search(r"^type:[ \t]*(\S+)", text, re.MULTILINE)
    return m.group(1).strip().lower() if m else "?"


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
    stats = {k: {"lower": 0, "cap": 0, "cap_mid": 0, "linked_lower": 0, "linked_cap": 0}
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
                    if span[0][0].islower():
                        st["lower"] += 1
                    else:
                        st["cap"] += 1
                        # **A capital in the MIDDLE of a sentence is a proper noun**, and
                        # that is the signal the `type:` field used to stand in for. A page
                        # titled "Tariffs" is written capitalised only at the start of a
                        # sentence; one titled "Lost" (the TV series) is written "the
                        # finale of Lost aired", mid-sentence. Counting the two separately
                        # answers "is this title a name?" from the wiki itself, instead of
                        # trusting a field the wiki is known to have got wrong.
                        _pre = line[:toks[i][1]].rstrip()
                        if _pre and not re.search(r"[.!?:;]$|^\s*[-*+>]$", _pre):
                            st["cap_mid"] += 1

    rows = []
    for key, st in stats.items():
        lower = st["lower"] + st["linked_lower"]
        cap = st["cap"] + st["linked_cap"]
        if not lower:
            continue
        rows.append({
            "key": key,
            "titles": cands[key],
            "type": _page_type(cands[key][0][1]),
            "lower": lower, "cap": cap, "cap_mid": st["cap_mid"],
            "linked_lower": st["linked_lower"], "bare_lower": st["lower"],
            "share": lower / (lower + cap) if (lower + cap) else 1.0,
        })
    rows.sort(key=lambda r: (-r["linked_lower"], -r["bare_lower"]))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--concepts", "--expected", action="store_true", dest="concepts",
                    help="also list the titles that read as ordinary words (review only)")
    ap.add_argument("--min-cap-mid", type=int, default=1,
                    help="mid-sentence capitalised uses before a title counts as a name "
                         "(default 1)")
    ap.add_argument("--all", action="store_true",
                    help="every title with any lowercase use, at any ratio")
    ap.add_argument("--min-lower", type=int, default=5,
                    help="ignore titles with fewer lowercase uses than this (default 5)")
    ap.add_argument("--words", type=int, default=1,
                    help="longest title to check, in words (default 1)")
    args = ap.parse_args()

    rows = scan(max_words=max(1, args.words))
    keep = [r for r in rows
            if args.all or (r["lower"] >= args.min_lower and r["share"] >= 0.5)]
    # **Textual evidence first, the `type:` field only as a tiebreak.** The split used to
    # be `type != "concept"` alone, on the sound reasoning that a concept page titled
    # "Tariffs" is SUPPOSED to be linked from the word `tariffs`. The reasoning is right;
    # the field carrying it is not reliable — the live wiki has people and organisations
    # filed under `concepts/`, and `rename_page.py` moved pages between directories for
    # months without carrying the type along.
    #
    # So the question "is this title a NAME?" is asked of the text wherever the text can
    # answer it. A title written capitalised in the MIDDLE of a sentence is a name,
    # whatever directory its page sits in: "the finale of Lost aired" is proof, and
    # "Tariffs are a tax" at the start of a sentence is not. Measured on a fixture, that
    # catches a proper noun misfiled as a concept — which the old filter hid completely.
    #
    # **Where `cap_mid` is zero there is no textual evidence either way**, and the field is
    # the only signal there is. A first version treated zero as proof of a common noun,
    # which dropped three existing cases: a page titled "Succession" whose name the wiki
    # only ever writes lowercase gets no mid-sentence capital, and the lowercase links to
    # it are still wrong. A weak signal beats none, so the old rule stands as the
    # fallback — demoted from the decision to a tiebreak, which is the most the field has
    # earned.
    def _is_bleed(r):
        if r["cap_mid"] >= args.min_cap_mid:
            return True                      # the text says it is a name
        return r["type"] not in ("concept", "?")   # no evidence: fall back to the field

    bleeds = [r for r in keep if _is_bleed(r)]
    expected = [r for r in keep if not _is_bleed(r)]

    def table(rs):
        print(f"{'title':<24} {'type':<8} {'linked':>7} {'bare':>7} {'CAPS':>6} "
              f"{'mid':>5} {'lower':>6}  page")
        print("-" * 99)
        for r in rs:
            title, rel = r["titles"][0]
            print(f"{title[:23]:<24} {r['type'][:7]:<8} {r['linked_lower']:>7} "
                  f"{r['bare_lower']:>7} {r['cap']:>6} {r['cap_mid']:>5} "
                  f"{r['share']*100:>5.0f}%  {rel}")

    if bleeds:
        print(f"{len(bleeds)} PROPER NOUN(S) COLLIDING WITH AN ORDINARY WORD.\n"
              f"A lowercase use of an entity's name is a different word, so these links are "
              f"wrong.\n'linked' is wrong links on disk now; 'bare' is what the next relink "
              f"would add.\n")
        table(bleeds)
    else:
        print("No entity page's name collides with an ordinary word.")

    if expected:
        if args.concepts:
            print(f"\n\n{len(expected)} CONCEPT PAGE(S) — REVIEW ONLY, NOT A BUG LIST.\n"
                  f"A concept page is meant to catch the common noun: 'inflation' in prose "
                  f"IS about\ninflation, and that link is what a concept wiki is for. Do not "
                  f"rename these because\nthey appear here. Worth a look only where the word "
                  f"is too generic to be a subject\n(a modal verb, a preposition, 'spread', "
                  f"'floor') — that is a page that should not\nexist, not a page that needs "
                  f"disambiguating.\n")
            table(expected)
        else:
            print(f"\n{len(expected)} concept page(s) also use their name as a common noun. "
                  f"That is what a\nconcept page is FOR, so they are not listed — pass "
                  f"--concepts to review them anyway.")

    if bleeds:
        print("\n\nTo disambiguate one, Wikipedia-style. The parenthetical never appears in "
              "prose,\nso the bleeding stops at once and rename_page.py strips the wrong "
              "links back to\nplain text:\n")
        for r in bleeds[:5]:
            title, rel = r["titles"][0]
            stem, parent = Path(rel).stem, Path(rel).parent
            print(f"  python3 tools/rename_page.py wiki/{rel} wiki/{parent}/{stem}-x.md \\\n"
                  f'      --title "{title} (…)"        # (…) = what it actually is')
        print("\n  python3 tools/relink.py        # once, after the renames")
        print("\nThe rename stops the bleeding but wins no links back: prose says the bare\n"
              "name, never the parenthetical. An aliases: entry would restore linking AND "
              "the\nbleeding, because matching is case-insensitive — the alias takes the "
              "lowercase\noccurrence first, and the once-per-section rule then leaves the "
              "real mention\nwith nothing. So pair the rename with `no_autolink: true` "
              "until alias matching\ncan be case-sensitive.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
