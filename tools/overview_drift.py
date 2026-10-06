#!/usr/bin/env python3
"""
Which summary sections have stopped being summaries, measured from the wiki itself.

An entity page's `## Overview` and a concept page's `## Definition` are supposed to say
what the subject IS. They are also the section the model reaches for when its material
fits nothing else on the page — so on a broad standing page they accumulate one unrelated
dated sentence per ingest until the summary is a pile of headlines. Reported from a live
page:

    Florida is a U.S. state located in the southeastern region. In August 2026, housing
    market data showed typical home values at $375,470… In 2026, amid nationwide
    redistricting battles… Within the Democratic Party in the state, the 2026 primary
    season saw… As of October 2026, the state is also battling a significant dengue
    outbreak… In October 2026, state officials announced that Florida would discontinue
    the use of Flock Safety…

One paragraph, seven sentences, 1,723 characters, 18 links — and only the FIRST sentence
is about Florida. The other six are six different articles.

`_accreted_dated_sentences` in agent.py now refuses the write that does this, so the wiki
stops acquiring it. That does nothing for the pages already written, and there is no way
to know how many without counting — hence this. It reads; it never writes.

Three signals, reported separately because they call for different work:

  DATED    sentences in the summary that OPEN by placing themselves in time. Each one is
           a news item that belongs in a named section, a Timeline, or on its own page.
           This is the signal that matters — it says the section changed job.
  WALL     the summary is one paragraph over --wall characters. A reader cannot find
           anything in it. Fixing this is reflowing, which is cheap and purely cosmetic.
  LONG     more than --sentences sentences. A summary that runs long is usually long
           because it accumulated, so this mostly agrees with DATED — where it does not,
           it is a genuinely detailed summary and fine.

A page is only listed when it trips DATED, or when it trips WALL and LONG together.
A single long paragraph that is genuinely about its subject is not drift, and the whole
value of a report like this is in not crying wolf.

**Nothing here can be repaired mechanically.** Splitting a pile into sections is a
judgement about what the page is for, and where to break a wall into paragraphs is a
judgement about what belongs together — so this prints what to look at and stops. For a
page that trips DATED, the repair is the Regenerate Workflow or a human; for one that
trips only WALL, it is a paragraph break.

Sample runs:

    python3 tools/overview_drift.py                  # the worst 40, by dated sentences
    python3 tools/overview_drift.py --limit 200
    python3 tools/overview_drift.py --show florida   # the full text of one page's summary
    python3 tools/overview_drift.py --wall 1200 --sentences 8
    python3 tools/overview_drift.py --csv > drift.csv
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent
from agent import (_DATED_OPENER_RE, _MD_LINK_RE, _SUMMARY_SECTIONS, _find_section,
                   _norm_heading, _sentences, is_generated_page, wiki_pages)


def _summary_of(text: str):
    """(section name, body) for the page's summary section, or (None, None).

    Both names are tried because the two page types use different ones for the same job,
    and `_find_section` is the same resolver the write paths use — so what this reports on
    is exactly what `update_section` would be editing.
    """
    for name in _SUMMARY_SECTIONS:
        found = _find_section(text, name)
        if found:
            _head, start, end = found
            return name.title(), text[start:end].strip()
    return None, None


def _measure(body: str) -> dict:
    flat = _MD_LINK_RE.sub(r"\1", body)
    paras = [p for p in body.split("\n\n") if p.strip()]
    sents = _sentences(body)
    dated = [s for s in sents if _DATED_OPENER_RE.match(s)]
    return {
        "chars": len(flat),
        "paras": len(paras),
        "sentences": len(sents),
        "links": len(_MD_LINK_RE.findall(body)),
        "dated": dated,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=40,
                    help="how many pages to print (default 40)")
    ap.add_argument("--wall", type=int, default=900,
                    help="one paragraph longer than this is a WALL (default 900 chars)")
    ap.add_argument("--sentences", type=int, default=6,
                    help="more sentences than this is LONG (default 6)")
    ap.add_argument("--show", metavar="SLUG",
                    help="print one page's summary section in full and stop")
    ap.add_argument("--csv", action="store_true",
                    help="machine-readable, every hit, no cap")
    args = ap.parse_args()

    rows, scanned, with_summary = [], 0, 0
    for f in wiki_pages():
        if is_generated_page(f):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        name, body = _summary_of(text)
        if not body:
            continue
        with_summary += 1
        rel = f.relative_to(agent.WIKI_DIR).as_posix()
        m = _measure(body)
        m.update(rel=rel, section=name, body=body)
        wall = m["paras"] == 1 and m["chars"] > args.wall
        long_ = m["sentences"] > args.sentences
        if m["dated"] or (wall and long_):
            m["flags"] = ([f"DATED×{len(m['dated'])}"] if m["dated"] else []) + \
                         (["WALL"] if wall else []) + (["LONG"] if long_ else [])
            rows.append(m)

    if args.show:
        want = args.show.lower().removesuffix(".md")
        hit = [r for r in rows if Path(r["rel"]).stem == want] or \
              [r for r in rows if want in r["rel"]]
        if not hit:
            print(f"No drifting summary matching {args.show!r}. "
                  f"(It may be fine, or have no summary section.)")
            return 1
        r = hit[0]
        print(f"{r['rel']}  —  ## {r['section']}\n")
        print(r["body"])
        print(f"\n{'-' * 72}")
        print(f"{r['chars']} chars, {r['sentences']} sentences, {r['paras']} paragraph(s), "
              f"{r['links']} links, {len(r['dated'])} dated opener(s)")
        for s in r["dated"]:
            print(f"  DATED  {s[:100]}{'…' if len(s) > 100 else ''}")
        return 0

    # Worst first: dated sentences are the signal that the section changed job, so they
    # order the report; size breaks ties.
    rows.sort(key=lambda r: (-len(r["dated"]), -r["chars"]))

    if args.csv:
        w = csv.writer(sys.stdout)
        w.writerow(["page", "section", "dated", "sentences", "paragraphs", "chars", "links"])
        for r in rows:
            w.writerow([r["rel"], r["section"], len(r["dated"]), r["sentences"],
                        r["paras"], r["chars"], r["links"]])
        return 0

    print(f"Scanned {scanned} pages, {with_summary} with a summary section.")
    print(f"{len(rows)} have drifted "
          f"(a dated sentence, or one paragraph over {args.wall} chars AND over "
          f"{args.sentences} sentences).\n")
    if not rows:
        print("Nothing to look at.")
        return 0

    for r in rows[:args.limit]:
        print(f"{r['rel']}")
        print(f"    ## {r['section']}  —  {', '.join(r['flags'])}  "
              f"({r['chars']} chars, {r['sentences']} sentences, {r['paras']} para, "
              f"{r['links']} links)")
        for s in r["dated"][:3]:
            print(f"      • {s[:96]}{'…' if len(s) > 96 else ''}")
        if len(r["dated"]) > 3:
            print(f"      … and {len(r['dated']) - 3} more")
    if len(rows) > args.limit:
        print(f"\n… and {len(rows) - args.limit} more. --limit to see them, --csv for all.")

    print(f"\n  python3 tools/overview_drift.py --show <slug>   # read one in full")
    print("\nNo repair is offered, deliberately: where each dated sentence belongs is a\n"
          "judgement about what the page is for, and splitting a wall into paragraphs is\n"
          "a judgement about what belongs together. A DATED page wants the Regenerate\n"
          "Workflow or a human; a WALL-only page wants a paragraph break.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
