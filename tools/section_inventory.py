#!/usr/bin/env python3
"""
Report which section headings actually exist across the wiki, per page type.

LOBOTOMY.md gives a template per page type — entity pages get Overview, Background,
Key Works / Products, Claims & Positions, Contradictions, Sources — but nothing enforces
it except on source pages, so what the wiki really contains has only ever been guessable.
This counts it.

Use it to decide what, if anything, to constrain: a heading used on 400 pages is part of
the vocabulary whether or not the template names it, and one used twice is drift.

It also flags six specific smells:

  off-template   a heading the page type's template does not list
  = page title   a section named after the page it is on ("Cybersecurity" on
                 cybersecurity.md) — the body of a page is not a section of itself
  linked heading a heading containing a markdown link, e.g.
                 "## [Atheism](../sources/atheism-wikipedia-2026.md)". Section tools match
                 headings by text, so the link syntax makes the section hard to address
  dated          a heading naming a date or a single event ("Current Standing (May 2026)"),
                 which Step 5 forbids: it is a changelog entry wearing a heading, and the
                 next ingest adds another beside it
  no opener      an entity page with no Overview, or a concept with no Definition — the
                 one heading its template says every page of that type keeps
  stub event     an event page whose Timeline has exactly one entry, backed by one
                 source, untouched for 30 days. An event page is created from the first
                 story about it, before anyone can know whether a second will come; when
                 none does, what is left holds a single sentence while reading, to a
                 later query, like coverage. Any one of the three signals alone is
                 innocent — a new page is new, and a quiet month is not proof. Together
                 they mean no second story ever arrived.

Report-only.

Run from the repo root:
  python3 tools/section_inventory.py            # summary
  python3 tools/section_inventory.py --pages    # and name the pages behind each smell
"""
import re
import sys
import datetime as dt
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import WIKI_DIR, wiki_pages, _norm_heading, _TL_BULLET_RE

SHOW_PAGES = "--pages" in sys.argv
HEAD_RE = re.compile(r"^(#{1,6})[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)

# Exactly the headings LOBOTOMY.md section 3 lists, including the forms it writes with a
# slash — "Key Works / Products" is one template heading offering a choice, so both halves
# count as following it. Splitting them, as an earlier version did, reported 64 uses of a
# heading the schema itself specifies as off-template and inflated the count badly.
TEMPLATE = {
    # "Timeline" is the unfolding-event template's accumulator, maintained by
    # add_timeline_entry — on-template for an entity page, not drift.
    "entity":    ["Overview", "Background", "Key Works / Products", "Key Works", "Products",
                  "Claims & Positions", "Timeline", "Contradictions", "Sources"],
    "concept":   ["Definition", "How It Works", "Origins & History", "Applications",
                  "Variants & Related Concepts", "Contradictions / Debates",
                  "Contradictions", "Debates", "Sources"],
    "synthesis": ["Question / Thesis", "Question", "Thesis", "Evidence For",
                  "Evidence Against", "Open Questions", "Sources"],
    "source":    ["Summary", "Claims", "Entities", "Concepts", "Quotes", "Context", "Sources"],
}
OPENER = {"entity": "Overview", "concept": "Definition"}
STUB_QUIET_DAYS = 30
DATED_RE = re.compile(r"\b(?:19|20)\d{2}\b"
                      r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
                      r"(?:uary|ruary|ch|il|e|y|ust|tember|ober|ember)?\b", re.IGNORECASE)

counts = defaultdict(Counter)       # type -> Counter of normalized heading
display = {}                        # normalized heading -> a raw form to show
pages_of = defaultdict(list)        # (type, normalized heading) -> pages
smells = defaultdict(list)          # smell -> [(page, detail)]
totals = Counter()

for p in wiki_pages():
    rel = p.relative_to(WIKI_DIR).as_posix()
    if p.name == "index.md" or rel == "log.md":
        continue
    text = p.read_text(encoding="utf-8", errors="replace")
    fm = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    body = text[fm.end():] if fm else text
    meta = fm.group(1) if fm else ""
    tm = re.search(r"^type:\s*(\S+)", meta, re.MULTILINE)
    ptype = tm.group(1).strip() if tm else "untyped"
    title_m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', meta, re.MULTILINE)
    title = title_m.group(1).strip() if title_m else p.stem
    totals[ptype] += 1

    allowed = {_norm_heading(h) for h in TEMPLATE.get(ptype, [])}
    found = set()
    for m in HEAD_RE.finditer(body):
        name, key = m.group(2).strip(), _norm_heading(m.group(2))
        if key == _norm_heading(title):      # the page's own H1
            if len(m.group(1)) > 1:          # an H2+ repeating the title is the smell
                smells["= page title"].append((rel, name))
            continue
        counts[ptype][key] += 1
        display.setdefault(key, name)
        pages_of[(ptype, key)].append(rel)
        found.add(key)
        if allowed and key not in allowed:
            smells["off-template"].append((rel, f"{name}  [{ptype}]"))
        if DATED_RE.search(name):
            smells["dated"].append((rel, name))
        if "](" in name:
            smells["linked heading"].append((rel, name))

    want = OPENER.get(ptype)
    if want and _norm_heading(want) not in found:
        smells["no opener"].append((rel, f"no {want}"))

    # A page carrying a Timeline is an event page — that section is what the unfolding-event
    # template adds and add_timeline_entry maintains, so no separate page type is needed to
    # recognize one.
    tl = re.search(r"^(#{1,6})[ \t]*Timeline[ \t]*$", body, re.MULTILINE | re.IGNORECASE)
    if tl:
        nxt = re.search(r"^#{1," + str(len(tl.group(1))) + r"}[ \t]*\S",
                        body[tl.end():], re.MULTILINE)
        sec = body[tl.end():tl.end() + nxt.start()] if nxt else body[tl.end():]
        entries = [ln for ln in sec.split("\n") if _TL_BULLET_RE.match(ln.strip())]
        src_m = re.search(r"^sources:\s*\[(.*?)\]", meta, re.MULTILINE | re.DOTALL)
        n_src = len([x for x in re.findall(r'"([^"]+)"', src_m.group(1) if src_m else "")
                     if x.strip()])
        quiet = (dt.date.today() - dt.date.fromtimestamp(p.stat().st_mtime)).days
        if len(entries) == 1 and n_src <= 1 and quiet >= STUB_QUIET_DAYS:
            smells["stub event"].append(
                (rel, f"1 timeline entry, {n_src} source(s), untouched {quiet} days"))

for ptype in sorted(totals):
    c = counts[ptype]
    if not c:
        continue
    print(f"\n=== {ptype} ({totals[ptype]:,} pages, {len(c)} distinct headings) ===")
    for key, n in c.most_common(12):
        share = 100 * n / totals[ptype]
        intpl = "" if _norm_heading(display[key]) in {_norm_heading(h) for h in TEMPLATE.get(ptype, [])} else "   (off-template)"
        print(f"  {n:6,}  {share:5.1f}%  {display[key]}{intpl}")
    onceonly = sum(1 for k, n in c.items() if n == 1)
    if onceonly:
        print(f"  … and {onceonly} heading(s) used on exactly one page")

print("\n=== smells ===")
for smell in ("no opener", "stub event", "= page title", "linked heading",
              "dated", "off-template"):
    hits = smells[smell]
    if not hits:
        print(f"  {smell:14} none")
        continue
    print(f"  {smell:14} {len(hits):,}")
    if SHOW_PAGES:
        for rel, detail in hits[:25]:
            print(f"      {rel}: {detail}")
        if len(hits) > 25:
            print(f"      … and {len(hits) - 25} more")

if not SHOW_PAGES:
    print("\nRe-run with --pages to see which pages are behind each smell.")
