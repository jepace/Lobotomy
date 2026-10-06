#!/usr/bin/env python3
"""
Everything mechanically wrong with ONE wiki page, measured rather than read.

Written for `donald-trump.md` — 223,659 bytes across fourteen sections, 53.8s to autolink
against 13,195 titles, and described by its owner as "pretty much a complete mess". The
mess turned out not to be one problem. Reading the page text found nine distinct defects,
most of them counting problems a human cannot do by eye on a page that size, and all of
them cheap for a machine. So this counts them, on the real file, so the count can be
re-run after each repair pass and watched going down.

It reads. It never writes, and it offers no `--apply`, for the same reason
`overview_drift.py` does not: every repair here is a judgement about what the page is for.

Nine checks, each reported separately because each calls for different work:

  EMPTY         a section heading with no body under it. Benign on a new page; on an old
                one with sources for that heading's subject in its frontmatter, it means
                ingested material is not where the page's own map says it is. Both forms
                are reported, the second marked, because the second is data loss and the
                first is housekeeping.
  HEADINGS      what the write-path guards would refuse: a heading naming a date, one
                repeating the page title, one containing a link (`_bad_headings`), and
                duplicates (`_heading_dupes`) — plus the trap those two make together.
                "Key Policies & Actions (2025)" and "(2026)" are each illegal alone, and
                `_absorb_date_qualifiers` strips the date from both, so the mechanical fix
                COLLIDES them into a duplicate. A page can be in a state where neither
                guard's remedy is available, and nothing said so before this.
  DRIFT         the summary section measured the way `overview_drift.py` measures it. Kept
                here rather than deferred to that tool so one command answers the whole
                page, and the numbers are the same numbers.
  PILE          a section that has become a dumping ground: far larger than its siblings
                AND carrying many dated-opener sentences. Size alone is not a defect — a
                page's main section is supposed to be its largest — so both signals are
                required. This is what distinguishes "Second Presidential Term", holding
                some thirty-five unrelated topics, from a long section about one thing.
  REPEATS       near-identical paragraphs and list items, wherever they sit on the page.
                The expensive finding: a page with two parallel sections for one pile (one
                prose, one bullets) says everything twice, and the duplicates DRIFT — two
                copies of the press-ban paragraph on the page this was written for name
                different networks, so one of them is now wrong. Reported as pairs with
                their sections, loudest first.
  ECHOES        single sentences appearing verbatim in more than one place. A stricter,
                cheaper signal than REPEATS and never a false positive: nobody writes the
                same 80-character sentence twice on purpose.
  LINKS         dead targets; a proper name split by a link ("White [House](…)"); a body
                link pointing into `wiki/sources/`. See `_LINK_NOTE` for the one thing
                deliberately NOT judged here.
  TARGETS       distinct link targets that are probably one subject — `war-with-iran`,
                `war-in-iran` and `iran-war`; `republican`, `republicans` and
                `republican-party`. This is the expensive one to leave alone, because it
                is not about this page: the autolinker is splitting one subject's inbound
                links across competing pages everywhere, and merging them shrinks this
                page for free.
  SOURCES       among the page's own `sources:`, source pages with the same title (one
                article captured twice, ~40 minutes of ingest each), and slugs that are
                truncated or carry no author/year.

Sample runs:

    python3 tools/page_report.py entities/donald-trump.md
    python3 tools/page_report.py donald-trump            # slug is enough
    python3 tools/page_report.py donald-trump --only repeats,targets
    python3 tools/page_report.py donald-trump --repeats 40 --overlap 0.4
    python3 tools/page_report.py donald-trump --subsets  # the noisier half of TARGETS
    python3 tools/page_report.py donald-trump --links    # every link, for the bleed half
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent
from agent import (_DATED_OPENER_RE, _MD_LINK_RE, _SUMMARY_SECTIONS, _TRAILING_DATE_RE,
                   _WIKI_READ_LIMIT, _bad_headings, _fm_title, _heading_dupes,
                   _norm_heading, _norm_prose, _sentences, wiki_pages)

HEAD_RE = re.compile(r"^(#{1,6})[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)

# A link after one of these is a person being introduced by their office, so a capitalised
# single word before a capitalised single-word link is the normal shape and not a split
# name. Without this list, "President [Trump](…)" and "Senator [Collins](…)" drown the two
# real hits. The whole value of a report is in not crying wolf — the bleeding_titles
# lesson, which cost four mutations to learn.
_HONORIFICS = {
    "president", "vice", "senator", "sen", "representative", "rep", "governor", "gov",
    "mayor", "secretary", "judge", "justice", "chief", "deputy", "admiral", "general",
    "gen", "colonel", "col", "captain", "capt", "sergeant", "sgt", "lieutenant", "lt",
    "ambassador", "chancellor", "premier", "prime", "minister", "king", "queen", "prince",
    "pope", "dr", "doctor", "professor", "prof", "mr", "mrs", "ms", "sir", "dame", "lord",
    "reverend", "rev", "father", "rabbi", "imam", "director", "chairman", "chairwoman",
    "chair", "commissioner", "sheriff", "attorney", "counsel", "columnist", "biographer",
    "journalist", "pollster", "economist", "analyst", "candidate", "nominee", "leader",
    "spokesman", "spokeswoman", "author", "founder", "ceo", "cfo", "cto", "treasurer",
    "comptroller", "and", "or", "the", "a", "an", "of", "by", "with", "for", "like",
    "including", "such", "as", "at", "in", "on", "to", "from", "former", "ex", "late",
}

# Slug words carrying no subject: dropping them is what lets `war-with-iran`,
# `war-in-iran` and `iran-war` collapse to one key.
_SLUG_STOP = {"of", "in", "with", "for", "and", "on", "to", "a", "an", "the", "at", "by",
              "from", "s", "us"}

_LINK_NOTE = (
    "Common-word bleeding ([notes], [standing], [power]) is NOT judged here. Deciding\n"
    "  whether a lowercase link is a bleed needs evidence this page does not hold: a page\n"
    "  titled \"Tariffs\" is SUPPOSED to be linked from the word `tariffs`, and one titled\n"
    "  \"Notes\" is not, and nothing on one page distinguishes them. `bleeding_titles.py`\n"
    "  answers it from the whole wiki, by counting capitalised against lowercase use of\n"
    "  each title. Run that; --links here just lists what this page actually linked."
)

_UNIT_MIN_CHARS = 120     # below this, a repeated fragment is a shared phrase, not a copy
_SHINGLE = 6              # words per shingle for the near-duplicate comparison
_ECHO_MIN_CHARS = 80


# -- the page ----------------------------------------------------------------------------

def resolve_page(target: str) -> "Path | None":
    """A wiki-relative path, an absolute path, or a bare slug — to one page on disk.

    Matched against the live tree rather than guessed, so a typo reports "no such page"
    instead of a report full of zeroes about a file that was never read.
    """
    t = target.strip()
    p = Path(t)
    if p.is_absolute() and p.is_file():
        return p
    for cand in (agent.WIKI_DIR / t, agent.WIKI_DIR / (t + ".md"), Path(t)):
        if cand.is_file():
            return cand
    want = t.lower().removesuffix(".md")
    hits = [f for f in wiki_pages() if f.stem == want]
    return hits[0] if len(hits) == 1 else None


def split_frontmatter(text: str) -> "tuple[str, str]":
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    return (m.group(1), text[m.end():]) if m else ("", text)


def sections(body: str, title: str) -> list:
    """Every level-2+ heading with its body, in page order.

    `## Sources` is kept rather than filtered the way `_page_section_names` filters it:
    this report counts the page's bytes and that section is a real share of them, and the
    SOURCES check reads it. It is marked `generated` so the other checks can skip it —
    it is rendered from frontmatter, so a duplicate inside it is a duplicate source page,
    which is a different finding than a duplicate paragraph.
    """
    marks = [m for m in HEAD_RE.finditer(body) if len(m.group(1)) > 1]
    out = []
    for i, m in enumerate(marks):
        name = m.group(2).strip()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        sec_body = body[m.end():end].strip()
        out.append({
            "name": name,
            "level": len(m.group(1)),
            "body": sec_body,
            "chars": len(_MD_LINK_RE.sub(r"\1", sec_body)),
            "generated": _norm_heading(name) == "sources",
            "is_title": bool(title) and _norm_heading(name) == _norm_heading(title),
        })
    return out


def fm_sources(meta: str) -> list:
    m = re.search(r"^sources:\s*\[(.*?)\]", meta, re.MULTILINE | re.DOTALL)
    if not m:
        return []
    return [s.strip() for s in re.findall(r'"([^"]*)"', m.group(1)) if s.strip()]


# -- EMPTY ------------------------------------------------------------------------------

def empty_sections(secs: list, source_slugs: list) -> list:
    """Headings with nothing under them, each marked with whether the page holds sources
    for its subject.

    The marking is the finding. An empty `## Legacy` is a placeholder nobody filled in.
    An empty `## First Trump Impeachment` on a page whose frontmatter lists
    `wikipedia-first-impeachment-of-donald-trump-2026.md` means that source was ingested,
    this heading was created for it, and the text is somewhere else — which is the one
    shape here that is lost work rather than untidiness.

    Matched on the heading's content words appearing in a source slug, all of them, so
    "Legacy" does not match `...trump-legacy-of-...` by accident on one short word. Two
    words minimum for the same reason: a single-word heading matches far too much.
    """
    out = []
    for s in secs:
        if s["body"] or s["generated"]:
            continue
        words = [w for w in re.findall(r"[a-z0-9]+", s["name"].lower())
                 if w not in _SLUG_STOP and len(w) > 2]
        backing = []
        if len(words) >= 2:
            backing = [sl for sl in source_slugs
                       if all(w in sl.lower() for w in words)]
        out.append({"name": s["name"], "sources": backing})
    return out


# -- HEADINGS ---------------------------------------------------------------------------

def date_strip_collisions(secs: list) -> list:
    """Groups of headings that `_absorb_date_qualifiers` would merge into one.

    The guards cannot see this and neither can a reader. `_bad_headings` refuses a dated
    heading; `_absorb_date_qualifiers` absorbs a trailing date silently because that is
    "one heading with a date bolted on" and the body stays correct. Both are right about
    one heading at a time. Two headings differing ONLY by their trailing date are a third
    thing: the absorption is still mechanically correct and its result is a duplicate
    heading, which `_heading_dupes` then refuses — so the obvious fix for one guard
    manufactures a violation of another. Principle 4's documented worst case, found on
    disk rather than in a refusal.

    Reported only where the stripped forms collide AND the raw headings differ, so an
    ordinary dated heading with no twin stays a plain HEADINGS finding and is not counted
    twice.
    """
    groups = defaultdict(list)
    for s in secs:
        if s["generated"]:
            continue
        stripped = _TRAILING_DATE_RE.sub("", s["name"]).strip(" -–—:,")
        if stripped and stripped != s["name"]:
            groups[_norm_heading(stripped)].append((s["name"], stripped))
    return [{"merged": v[0][1], "from": [n for n, _ in v]}
            for v in groups.values() if len({n for n, _ in v}) > 1]


# -- DRIFT ------------------------------------------------------------------------------

def summary_drift(secs: list, wall: int = 900, max_sentences: int = 6) -> "dict | None":
    """The page's summary section measured, or None if it has none.

    Same three signals and the same defaults as `overview_drift.py`, reading the same
    `_SUMMARY_SECTIONS` and the same `_DATED_OPENER_RE`, so the two tools cannot report
    different numbers for one page.
    """
    for want in _SUMMARY_SECTIONS:
        for s in secs:
            if _norm_heading(s["name"]) != want:
                continue
            sents = _sentences(s["body"])
            dated = [x for x in sents if _DATED_OPENER_RE.match(x)]
            paras = [p for p in s["body"].split("\n\n") if p.strip()]
            return {
                "name": s["name"], "chars": s["chars"], "sentences": len(sents),
                "paras": len(paras), "links": len(_MD_LINK_RE.findall(s["body"])),
                "dated": dated,
                "wall": len(paras) == 1 and s["chars"] > wall,
                "long": len(sents) > max_sentences,
            }
    return None


# -- PILE -------------------------------------------------------------------------------

def piles(secs: list, share: float = 0.2, min_dated: int = 5) -> list:
    """Sections that have become dumping grounds.

    BOTH signals are required and that is the whole design. A page's principal section is
    supposed to be its biggest, so size alone reports the section that is working. Dated
    openers alone report a section that is legitimately a chronicle. Together — one
    section holding a fifth of the page AND a pile of news items that each place
    themselves in time — they describe a section that stopped having a subject, which is
    what one reading of the page this was written for found twice over.
    """
    live = [s for s in secs if not s["generated"] and not s["is_title"]]
    total = sum(s["chars"] for s in live) or 1
    out = []
    for s in live:
        dated = [x for x in _sentences(s["body"]) if _DATED_OPENER_RE.match(x)]
        if s["chars"] >= total * share and len(dated) >= min_dated:
            out.append({"name": s["name"], "chars": s["chars"],
                        "share": s["chars"] / total, "dated": len(dated)})
    out.sort(key=lambda r: -r["dated"])
    return out


# -- REPEATS and ECHOES -----------------------------------------------------------------

# A bullet's leading bold label: "- **Greenland Security Agreement** (September 2026): …".
# Stripped for COMPARISON only. It is the bullet's heading, not its prose, and leaving it
# on defeats both duplication checks on the commonest duplicate shape there is: the same
# sentence written once as a paragraph and once as a labelled bullet. Verified on the
# fixture — the Rattner sentence appears verbatim twice and went unreported until this.
_BULLET_LABEL_RE = re.compile(r"^\*\*[^*]{1,120}\*\*\s*(?:\([^)]{1,60}\))?\s*:?\s*")


def _units(secs: list) -> list:
    """The page's comparable blocks, as {section, text, cmp}.

    A bullet is its own unit because the duplication this is looking for is between a
    prose paragraph in one section and a bullet in another — the two-parallel-piles shape.
    Treating a 40-bullet list as one unit compares it against nothing.

    `text` is what the report prints and `cmp` is what the checks read, because the two
    want different things: a finding has to be quotable as it appears on the page, and a
    comparison has to see through the notation one copy happens to carry.
    """
    out = []
    for s in secs:
        if s["generated"]:
            continue
        for block in s["body"].split("\n\n"):
            if not block.strip():
                continue
            lines = block.split("\n")
            items = []
            if any(re.match(r"^\s*[-*+]\s", ln) for ln in lines):
                item = ""
                for ln in lines:
                    if re.match(r"^\s*[-*+]\s", ln):
                        if item.strip():
                            items.append(item.strip())
                        item = re.sub(r"^\s*[-*+]\s", "", ln)
                    else:
                        item += " " + ln.strip()
                if item.strip():
                    items.append(item.strip())
            else:
                items.append(block.strip())
            for it in items:
                out.append({"section": s["name"], "text": it,
                            "cmp": _BULLET_LABEL_RE.sub("", it)})
    return out


def _norm_unit(text: str) -> str:
    """A unit reduced to its words, for comparison only.

    Links flattened by `_norm_prose` because the page on disk is autolinked and two copies
    of one paragraph are reliably linked DIFFERENTLY — the once-per-section budget is keyed
    on (target page, section ordinal), so the second copy of a paragraph in another section
    carries links the first does not. Comparing raw bytes would miss every pair this check
    exists to find.
    """
    flat = _norm_prose(text).lower()
    flat = re.sub(r"[*_`]", "", flat)
    return " ".join(re.findall(r"[a-z0-9$%.]+", flat))


def _shingles(norm: str, n: int = _SHINGLE) -> set:
    w = norm.split()
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def repeated_units(secs: list, overlap: float = 0.5, min_chars: int = _UNIT_MIN_CHARS) -> list:
    """Pairs of blocks saying substantially the same thing, loudest first.

    Containment rather than Jaccard — `|A∩B| / min(|A|,|B|)` — because the common shape is
    a short bullet wholly absorbed into a long paragraph, and Jaccard scores that pair low
    precisely when it matters most. The short one being fully contained IS the finding.
    """
    units = []
    for u in _units(secs):
        norm = _norm_unit(u["cmp"])
        if len(norm) < min_chars:
            continue
        sh = _shingles(norm)
        if len(sh) >= 5:
            units.append({**u, "norm": norm, "sh": sh})
    out = []
    for i in range(len(units)):
        for j in range(i + 1, len(units)):
            a, b = units[i], units[j]
            shared = a["sh"] & b["sh"]
            if not shared:
                continue
            score = len(shared) / min(len(a["sh"]), len(b["sh"]))
            if score >= overlap:
                out.append({"score": score,
                            "a_section": a["section"], "b_section": b["section"],
                            "a": a["text"], "b": b["text"],
                            "identical": a["norm"] == b["norm"]})
    out.sort(key=lambda r: (-r["score"], -len(r["a"])))
    return out


def repeated_sentences(secs: list, min_chars: int = _ECHO_MIN_CHARS) -> list:
    """Sentences appearing verbatim in more than one place on the page.

    Normalized through `_norm_prose` for the autolinking reason above, then compared whole.
    No threshold to tune and no false positives worth the name: a sentence this long
    occurring twice was pasted, not written.
    """
    where = defaultdict(list)
    for u in _units(secs):
        for s in _sentences(u["cmp"]):
            norm = _norm_unit(s)
            if len(norm) >= min_chars:
                where[norm].append((u["section"], s))
    out = [{"sentence": v[0][1], "sections": [sec for sec, _ in v], "count": len(v)}
           for v in where.values() if len(v) > 1]
    out.sort(key=lambda r: (-r["count"], -len(r["sentence"])))
    return out


# -- LINKS ------------------------------------------------------------------------------

def _body_links(secs: list) -> list:
    """(section, display, target) for every link in the page's own prose.

    Headings are excluded — a link in a heading is already a HEADINGS finding, and
    reporting it twice makes the longer report look like the worse page. `## Sources` is
    excluded because those links are generated from frontmatter: a dead one there is a
    deleted source page, which belongs under SOURCES.
    """
    out = []
    for s in secs:
        if s["generated"]:
            continue
        for line in s["body"].split("\n"):
            if re.match(r"^\s*#{1,6}\s", line):
                continue
            for m in _MD_LINK_RE.finditer(line):
                out.append((s["name"], m.group(1), m.group(2), line))
    return out


def dead_links(page: Path, links: list) -> list:
    """Body links whose target is not a file on disk.

    Resolved relative to the page's own directory, which is how the wiki writes them and
    how a browser follows them. External URLs and bare fragments are skipped: neither is
    this tool's business and guessing about them would be noise.
    """
    seen, out = set(), []
    for sec, display, target, _line in links:
        t = target.split("#", 1)[0].strip()
        if not t or "://" in t or t.startswith(("mailto:", "#")):
            continue
        if (page.parent / t).is_file():
            continue
        key = (display, t)
        if key in seen:
            continue
        seen.add(key)
        out.append({"section": sec, "display": display, "target": t})
    return out


def known_titles() -> dict:
    """Every page title and alias in the wiki, by normalized key → relpath.

    Read through `_build_title_map`, which is the autolinker's own map, so what this
    report calls a known name is exactly what the autolinker would have matched.
    """
    out = {}
    try:
        for title, rel in agent._build_title_map():
            out.setdefault(agent._norm_name_key(title), rel)
    except Exception:
        return {}
    return out


def split_names(links: list, titles: "dict | None" = None) -> list:
    """A proper name with a link starting partway through it: `White [House](…)`.

    This renders as an ordinary capitalised name with a plausible link, so the page reads
    correctly and nothing reports it — but the link asserts the sentence is about the
    chamber of Congress when it is about the building. Four of these on the page this was
    written for, plus `Donald Trump [Jr](…)`, which needs a page called "Jr" to exist.

    Several preceding words are tried, longest first, because the real hits are not all
    two words: "Trump Jr" is not a name and "Donald Trump Jr" is, so a one-word lookbehind
    finds the `White [House](…)` case and misses the `[Jr](…)` one.

    **The decisive test is that the FULLER name is a page the autolinker knows.** Three
    weaker tests were tried first and each cried wolf: a capitalised word before a
    capitalised one-word link flags `Trump [Republicans](…)`, which is two words and two
    subjects; adding `_HONORIFICS` fixes `President [Trump](…)` and not that. Asking
    whether "White House" is itself a title settles it, and settles it the autolinker's
    own way — the title map is sorted longest-first precisely so the longer name wins,
    so a shorter title linked inside a longer known one is the linker having lost a race
    it is designed to win. `_title_upgrade_re` exists to repair exactly this shape, which
    makes every hit here something a `relink.py` sweep should be able to fix.

    **Stated gap:** where the fuller name has no page, the link is still pointing at the
    wrong subject and this cannot say so. There is nothing to compare against, and
    guessing would report every `Word [Word](…)` on the page.
    """
    titles = titles if titles is not None else {}
    out = []
    for sec, display, target, line in links:
        if not re.fullmatch(r"[A-Z][\w.'’-]*", display.strip()):
            continue
        for m in re.finditer(re.escape(f"[{display}]({target})"), line):
            # Exactly one space before the `[`: that is what makes the two words one name
            # rather than two. Anything else — punctuation, a bracket, start of line — is
            # a new clause and not a split. An earlier version rstripped first and then
            # asked whether the result ended in a space, which it never can, so the whole
            # check was dead and both real hits on the page went unreported.
            before = line[:m.start()]
            if not before.endswith(" ") or before.endswith("  "):
                continue
            # The run of capitalised words immediately before the link, nearest first. It
            # stops at the first word that is lowercase or an honorific, which is what
            # keeps "Trump attacked [Republicans](…)" out: "attacked" ends the run, so
            # there is no candidate name at all.
            run = []
            rest = before.rstrip()
            while len(run) < 4:
                pm = re.search(r"([A-Za-z][\w.'’-]*)[ \t]*$", rest)
                if not pm:
                    break
                w = pm.group(1)
                if not re.fullmatch(r"[A-Z][\w.'’-]*", w) or w.lower() in _HONORIFICS:
                    break
                # A word ending in a dot is a sentence end unless the dot belongs to the
                # word. "It happened at the White. [House](…) members objected" was
                # reported as the name "White House", because `.` is in the word pattern
                # (it has to be, for "U.S." and "Jr.") and `_norm_name_key` then strips it
                # back out. `_ABBREV_TAIL_RE` is agent.py's own answer to "does this dot
                # end an abbreviation", shared so the two cannot disagree.
                if w.endswith(".") and not (
                        agent._ABBREV_TAIL_RE.search(w)
                        or re.fullmatch(r"(?:[A-Z]\.)+", w)):
                    break
                run.insert(0, w)
                rest = rest[:pm.start(1)].rstrip()
                if rest.endswith((",", ";", ":", "(", "\"", "'")):
                    break
            hit = None
            for take in range(len(run), 0, -1):
                prefix = " ".join(run[-take:])
                full = f"{prefix} {display}".strip(" .,;:")
                page = titles.get(agent._norm_name_key(full))
                if page:
                    hit = (prefix, full, page)
                    break
            if not hit:
                continue
            prefix, full, page = hit
            out.append({"section": sec, "text": f"{prefix} [{display}]({target})",
                        "name": full, "should_be": page})
    uniq = {}
    for r in out:
        uniq.setdefault(r["text"], r)
    return list(uniq.values())


def source_links_in_body(links: list) -> list:
    """Body prose linking into `wiki/sources/`.

    Two different defects wear this shape and the report cannot tell them apart, so it
    names both. Either the prose is citing an article inline, which `## Sources` already
    does from frontmatter — or a source page's `title:` is a subject rather than a
    headline, so the autolinker matched that subject's name and linked it to the article.
    The page this was written for had `[Wildlife Acoustics](../sources/ren-2026-tariffs-…)`:
    a company's name pointing at a news story, because the source page is titled "Wildlife
    Acoustics". That one is only fixable on the source page, which is immutable to the
    LLM — so `heal_pages` or a human, and no relink will ever repair it.
    """
    seen, out = set(), []
    for sec, display, target, _line in links:
        if "/sources/" not in target and not target.startswith("sources/"):
            continue
        key = (display, target)
        if key in seen:
            continue
        seen.add(key)
        out.append({"section": sec, "display": display, "target": target})
    return out


def lowercase_links(links: list) -> list:
    """Every distinct body link whose display text is entirely lowercase.

    Informational, and deliberately not a finding — see `_LINK_NOTE`. A page titled
    "Tariffs" SHOULD be linked from the word `tariffs`, so this list contains the correct
    links and the bleeds mixed together, and nothing on one page separates them.
    """
    c = Counter()
    for _sec, display, target, _line in links:
        d = display.strip()
        if d and re.fullmatch(r"[a-z][a-z0-9 '’.&-]*", d):
            c[(d, target)] += 1
    return [{"display": d, "target": t, "count": n}
            for (d, t), n in c.most_common()]


# -- TARGETS ----------------------------------------------------------------------------

def _target_key(target: str) -> frozenset:
    """A link target reduced to its subject words.

    Stopwords out and a crude singular, so `war-with-iran`, `war-in-iran` and `iran-war`
    all reduce to {war, iran} and `tariff`/`tariffs` to {tariff}. Crude on purpose: a real
    stemmer would fold `press` into `pres` and merge subjects that are not one, and this
    answer only has to be good enough to put two slugs in front of a human.
    """
    stem = Path(target.split("#", 1)[0]).stem.lower()
    words = []
    for w in re.split(r"[^a-z0-9]+", stem):
        if not w or w in _SLUG_STOP:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
            w = w[:-1]
        words.append(w)
    return frozenset(words)


def competing_targets(links: list, include_subsets: bool = False) -> dict:
    """Distinct targets that are probably one subject.

    Two tiers, because they differ in how often they are right:

      SAME    the subject words are identical — `entities/republican.md` and
              `entities/republicans.md`, `new-york-times` and `the-new-york-times`,
              the three Iran-war pages. Near-always a genuine duplicate subject.
      SUBSET  one target's words contain another's — `midterms` inside
              `2026-midterm-elections`. Often a duplicate and often perfectly
              legitimate (`oil` inside `oil-reserves` is two real concepts), so it is
              behind a flag. Reporting it by default would make the whole section easy
              to ignore, and then the SAME tier goes unread with it.

    The finding is not really about this page. The autolinker matches page titles, so two
    pages for one subject split every future inbound link between them — merging them with
    `merge_page.py` is a wiki-wide repair that happens to shrink this page for free.
    """
    by_key = defaultdict(set)
    for _sec, _display, target, _line in links:
        t = target.split("#", 1)[0]
        if "://" in t or not t.endswith(".md"):
            continue
        k = _target_key(t)
        if k:
            by_key[k].add(t)
    same = [sorted(v) for v in by_key.values() if len(v) > 1]
    subsets = []
    if include_subsets:
        keys = sorted(by_key, key=len)
        for i, small in enumerate(keys):
            for big in keys[i + 1:]:
                if len(small) < len(big) and small < big:
                    subsets.append({"inner": sorted(by_key[small]),
                                    "outer": sorted(by_key[big])})
    same.sort(key=lambda g: -len(g))
    return {"same": same, "subsets": subsets}


# -- SOURCES ----------------------------------------------------------------------------

def source_findings(page: Path, source_slugs: list) -> dict:
    """Duplicate and malformed source pages among the page's own `sources:`.

    Duplicate TITLES, not duplicate slugs: two captures of one article get different slugs
    by construction — `nytimes-2026-trump-name-removed-kennedy-center` and
    `nytimes-2026-nytimes-com-live-updates-trump-s-name-must-be-removed-from` — so the
    slugs can never collide and the titles are identical. Each duplicate cost a full ingest
    at `max_rpm: 1`, and both now feed the same pages.

    Slug smells are separate and milder: no author/year prefix, a raw URL used as a slug,
    or a slug that ends mid-word because something truncated it. None of these breaks
    anything today; they make a page's provenance unreadable, and a truncated one has
    already been observed defeating the history view's source link.
    """
    titles, missing, smells = defaultdict(list), [], []
    for slug in source_slugs:
        sp = page.parent.parent / "sources" / (slug if slug.endswith(".md") else slug + ".md")
        if not sp.is_file():
            sp = agent.WIKI_DIR / "sources" / Path(slug).name
            if not sp.name.endswith(".md"):
                sp = sp.with_suffix(".md")
        stem = Path(slug).stem
        if sp.is_file():
            t = _fm_title(sp.read_text(encoding="utf-8", errors="replace"))
            if t:
                titles[" ".join(t.lower().split())].append((stem, t))
        else:
            missing.append(stem)
        why = []
        if stem.startswith(("http-", "https-")):
            why.append("a URL, not a slug")
        elif not re.search(r"\b(?:19|20)\d{2}\b", stem):
            why.append("no year")
        if re.search(r"-[a-z]{1,2}$", stem) or re.search(r"\d-\d\d$", stem):
            why.append("looks truncated")
        if why:
            smells.append({"slug": stem, "why": ", ".join(why)})
    return {
        "dupes": [{"title": v[0][1], "slugs": [s for s, _ in v]}
                  for v in titles.values() if len({s for s, _ in v}) > 1],
        "missing": missing,
        "smells": smells,
        "total": len(source_slugs),
    }


# -- the report -------------------------------------------------------------------------

def analyze(page: Path, *, overlap: float = 0.5, subsets: bool = False) -> dict:
    """Every check, against one page on disk. Pure: reads the file and returns findings."""
    text = page.read_text(encoding="utf-8", errors="replace")
    meta, body = split_frontmatter(text)
    title = _fm_title(text)
    secs = sections(body, title)
    slugs = fm_sources(meta)
    links = _body_links(secs)
    live = [s for s in secs if not s["generated"]]
    return {
        "page": page,
        "rel": page.relative_to(agent.WIKI_DIR).as_posix()
               if str(page).startswith(str(agent.WIKI_DIR)) else page.name,
        "title": title,
        "bytes": len(text.encode("utf-8")),
        "unlinked": len(_MD_LINK_RE.sub(r"\1", body)),
        "sections": secs,
        "links": len(links),
        "empty": empty_sections(secs, slugs),
        "bad_headings": _bad_headings(body, title),
        "dupe_headings": _heading_dupes(body),
        "collisions": date_strip_collisions(secs),
        "drift": summary_drift(secs),
        "piles": piles(secs),
        "repeats": repeated_units(live, overlap=overlap),
        "echoes": repeated_sentences(live),
        "dead": dead_links(page, links),
        "split": split_names(links, known_titles()),
        "source_links": source_links_in_body(links),
        "lowercase": lowercase_links(links),
        "targets": competing_targets(links, include_subsets=subsets),
        "sources": source_findings(page, slugs),
    }


def _clip(s: str, n: int = 100) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1] + "…"


def print_report(r: dict, *, only: set, repeats: int, subsets: bool, show_links: bool) -> int:
    def want(name):
        return not only or name in only

    counted = 0
    print(f"{r['rel']}  —  \"{r['title']}\"")
    print(f"{r['bytes']:,} bytes, {r['unlinked']:,} chars of text, "
          f"{len([s for s in r['sections'] if not s['generated']])} sections, "
          f"{r['links']:,} body links")
    if r["unlinked"] > _WIKI_READ_LIMIT:
        print(f"  {r['unlinked'] / _WIKI_READ_LIMIT:.0f}× _WIKI_READ_LIMIT "
              f"({_WIKI_READ_LIMIT:,}) — read_file returns an outline, so an LLM repair "
              f"goes section by section\n  with allow_shrink=true (LOBOTOMY.md §6b). "
              f"A whole-page update_file is not reachable and should not be.")
    print()

    if want("sections"):
        live = [s for s in r["sections"] if not s["generated"]]
        total = sum(s["chars"] for s in live) or 1
        print("SECTIONS")
        for s in live:
            bar = "█" * int(round(28 * s["chars"] / total))
            flag = "  EMPTY" if not s["body"] else ""
            print(f"  {s['chars']:7,}  {100 * s['chars'] / total:5.1f}%  {bar:<28} "
                  f"{s['name']}{flag}")
        print()

    if want("empty") and r["empty"]:
        lost = [e for e in r["empty"] if e["sources"]]
        print(f"EMPTY  {len(r['empty'])} section(s) with no body, "
              f"{len(lost)} of them backed by sources")
        for e in r["empty"]:
            if e["sources"]:
                print(f"  ✗ ## {e['name']}  — the page has {len(e['sources'])} source(s) "
                      f"for this and holds none of the text:")
                for s in e["sources"][:3]:
                    print(f"        wiki/{s.removeprefix('wiki/')}")
            else:
                print(f"    ## {e['name']}")
        counted += len(r["empty"])
        if lost:
            print("  The marked ones are ingested material that is not where this page's "
                  "own map says\n  it is. Check those source pages still hold it before "
                  "anything else here.")
        print()

    if want("headings") and (r["bad_headings"] or r["dupe_headings"] or r["collisions"]):
        print(f"HEADINGS  {len(r['bad_headings'])} the write guards would refuse, "
              f"{len(r['dupe_headings'])} duplicated")
        for name, why in r["bad_headings"]:
            print(f"    ## {name}  — {why}")
        for name in r["dupe_headings"]:
            print(f"    ## {name}  — appears more than once")
        for c in r["collisions"]:
            print(f"  ✗ {' + '.join('## ' + n for n in c['from'])}")
            print(f"        both absorb to \"{c['merged']}\" — stripping the dates, which "
                  f"is the mechanical fix for\n        a dated heading, COLLIDES these "
                  f"into a duplicate heading. Merge or rename them; do\n        not strip "
                  f"the dates and leave it there.")
        counted += len(r["bad_headings"]) + len(r["dupe_headings"])
        print()

    d = r["drift"]
    if want("drift") and d and (d["dated"] or (d["wall"] and d["long"])):
        flags = ([f"DATED×{len(d['dated'])}"] if d["dated"] else []) + \
                (["WALL"] if d["wall"] else []) + (["LONG"] if d["long"] else [])
        print(f"DRIFT  ## {d['name']} — {', '.join(flags)}")
        print(f"  {d['chars']:,} chars, {d['sentences']} sentences, {d['paras']} "
              f"paragraph(s), {d['links']} links")
        for s in d["dated"][:5]:
            print(f"    • {_clip(s, 96)}")
        if len(d["dated"]) > 5:
            print(f"    … and {len(d['dated']) - 5} more dated opener(s)")
        counted += len(d["dated"])
        print()

    if want("pile") and r["piles"]:
        print(f"PILE  {len(r['piles'])} section(s) carrying a page's worth of "
              f"unrelated news")
        for p in r["piles"]:
            print(f"    ## {p['name']}  — {p['chars']:,} chars "
                  f"({100 * p['share']:.0f}% of the page), {p['dated']} dated openers")
        counted += len(r["piles"])
        print()

    if want("repeats") and r["repeats"]:
        ident = sum(1 for x in r["repeats"] if x["identical"])
        print(f"REPEATS  {len(r['repeats'])} near-duplicate pair(s), {ident} identical")
        for x in r["repeats"][:repeats]:
            tag = "IDENTICAL" if x["identical"] else f"{100 * x['score']:.0f}%"
            print(f"  [{tag}]  ## {x['a_section']}  ↔  ## {x['b_section']}")
            print(f"      A  {_clip(x['a'], 108)}")
            print(f"      B  {_clip(x['b'], 108)}")
        if len(r["repeats"]) > repeats:
            print(f"  … and {len(r['repeats']) - repeats} more. "
                  f"--repeats N for more, --overlap to tighten.")
        counted += len(r["repeats"])
        print()

    if want("echoes") and r["echoes"]:
        print(f"ECHOES  {len(r['echoes'])} sentence(s) appearing verbatim more than once")
        for x in r["echoes"][:12]:
            print(f"    ×{x['count']}  {' / '.join('## ' + s for s in x['sections'])}")
            print(f"         {_clip(x['sentence'], 104)}")
        if len(r["echoes"]) > 12:
            print(f"    … and {len(r['echoes']) - 12} more")
        counted += len(r["echoes"])
        print()

    if want("links") and (r["dead"] or r["split"] or r["source_links"]):
        print(f"LINKS  {len(r['dead'])} dead, {len(r['split'])} split name(s), "
              f"{len(r['source_links'])} into wiki/sources/")
        for x in r["dead"]:
            print(f"  ✗ [{_clip(x['display'], 40)}]({x['target']})  — no such file "
                  f"(## {x['section']})")
        for x in r["split"]:
            print(f"  ✗ {x['text']}  — \"{x['name']}\" is one name and has its own page "
                  f"({x['should_be']});\n        the link starts partway through it")
        for x in r["source_links"]:
            print(f"    [{_clip(x['display'], 40)}]({x['target']})  — a body link to an "
                  f"article (## {x['section']})")
        counted += len(r["dead"]) + len(r["split"]) + len(r["source_links"])
        print(f"  {_LINK_NOTE}")
        print()

    if want("targets"):
        t = r["targets"]
        if t["same"] or t["subsets"]:
            print(f"TARGETS  {len(t['same'])} subject(s) split across more than one page")
            for g in t["same"]:
                print(f"  ✗ {'  =  '.join(g)}")
            if subsets:
                for g in t["subsets"]:
                    print(f"    {' / '.join(g['inner'])}  ⊂  {' / '.join(g['outer'])}")
            elif t["same"]:
                print("    --subsets also lists targets where one's words contain "
                      "another's (noisier:\n    `oil` inside `oil-reserves` is two real "
                      "concepts).")
            counted += len(t["same"])
            print("  Not a defect of this page. The autolinker matches titles, so every "
                  "future inbound\n  link to that subject is split between these pages. "
                  "find_duplicate_pages.py, then\n  merge_page.py — a wiki-wide repair "
                  "that shrinks this page for free.")
            print()

    if want("sources"):
        s = r["sources"]
        if s["dupes"] or s["missing"] or s["smells"]:
            print(f"SOURCES  {s['total']} listed, {len(s['dupes'])} article(s) captured "
                  f"more than once, {len(s['missing'])} missing")
            for x in s["dupes"]:
                print(f"  ✗ \"{_clip(x['title'], 76)}\"")
                for sl in x["slugs"]:
                    print(f"        {sl}")
            for x in s["missing"]:
                print(f"  ✗ {x}  — listed in sources: and not on disk")
            for x in s["smells"][:15]:
                print(f"    {x['slug']}  — {x['why']}")
            if len(s["smells"]) > 15:
                print(f"    … and {len(s['smells']) - 15} more slug smell(s)")
            counted += len(s["dupes"]) + len(s["missing"])
            print()

    print(f"{counted} finding(s). Nothing was changed; this tool has no --apply.")
    print("Mechanical first and free: find_duplicate_pages.py + merge_page.py (TARGETS),\n"
          "bleeding_titles.py then relink.py (the link bleeds this cannot judge),\n"
          "find_duplicate_sections.py and overview_drift.py to cross-check.\n"
          "Then the editorial work, which needs a judgement about what the page is FOR:\n"
          "where each REPEAT's material belongs, how a PILE splits, and which of its "
          "events\ndeserve pages of their own. Re-run this after each pass.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("page", help="wiki-relative path or bare slug, e.g. donald-trump")
    ap.add_argument("--only", default="",
                    help="comma-separated checks: sections,empty,headings,drift,pile,"
                         "repeats,echoes,links,targets,sources")
    ap.add_argument("--repeats", type=int, default=15,
                    help="how many REPEATS pairs to print (default 15)")
    ap.add_argument("--overlap", type=float, default=0.5,
                    help="shingle containment for a REPEATS pair (default 0.5)")
    ap.add_argument("--subsets", action="store_true",
                    help="also list the noisier TARGETS tier")
    ap.add_argument("--links", action="store_true",
                    help="list every lowercase-display link (see bleeding_titles.py)")
    args = ap.parse_args(argv)

    page = resolve_page(args.page)
    if not page:
        print(f"No single wiki page matches {args.page!r}. Give a wiki-relative path "
              f"(entities/donald-trump.md) or an unambiguous slug.", file=sys.stderr)
        return 1

    r = analyze(page, overlap=args.overlap, subsets=args.subsets)
    only = {x.strip().lower() for x in args.only.split(",") if x.strip()}
    rc = print_report(r, only=only, repeats=args.repeats, subsets=args.subsets,
                      show_links=args.links)
    if args.links:
        print(f"\nlowercase-display links ({len(r['lowercase'])} distinct)")
        for x in r["lowercase"]:
            print(f"  ×{x['count']:<3} [{x['display']}]({x['target']})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
