"""Search got faster, and this proves it still returns the same thing.

Reported as *"search is pretty slow; are there cheap optimizations we can be doing?"*
Measured at 2,000 pages / 17.8 MB before touching anything:

    'nonexistentterm'   423 ms      0/2000 hits
    'ingte'             575 ms    320/2000 hits
    'state'             632 ms   1999/2000 hits

A query matching NOTHING cost 423ms because every page paid two full-text regex
substitutions — `_SYS_FIELDS`, then the link-URL rewrite — **before a single keyword was
tested**. Two fresh copies of every page in the wiki, per query, to establish that none of
them matched. The autolinker learned this exact lesson twice (the per-title token
prefilter, then the per-line probe); search never did.

After:

    'nonexistentterm'   117 ms   3.6×
    'ingte'             216 ms   2.7×
    'state'             445 ms   1.4×

Two changes, and the second is the one that mattered. A `.lower()` and a plain `in` gate
the substitutions, which is safe because both substitutions only DELETE text — a keyword
absent from the raw bytes is absent from the rewritten text too. That alone was 3.9× on a
miss and **10% SLOWER** on a query matching every page, because the allocation was pure
overhead there. So the matching moved onto the lowered copy as well: the substitutions run
on it, the exact check is `in`, and the score is `str.count`. That removes every
`re.IGNORECASE` match and the `findall` list-building, and one allocation now pays for
three jobs instead of being charged on top of them.

**The gate is the interesting part.** The link rewrite turns `](URL)` into `]()`, so
deleting text can forge an adjacency that was not in the original: a keyword containing
`]`, `(` or `)` could match the rewritten text while being absent from the raw bytes, and a
naive prefilter would skip a page that really matches. Dropping a `sources:` line joins two
lines the same way, but a keyword cannot contain a newline because `query.split()` built
it. So the fast path is used only when every keyword is free of bracket characters — every
ordinary query — and anything else takes the old route and is merely slow.

`ReferenceEquivalenceTest` is the point of this module: it reimplements the ORIGINAL
algorithm as an oracle and asserts the two agree on paths, scores and order across every
query shape. A faster search that returns something subtly different is not a faster
search.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


def reference_search(query: str, wiki_dir: Path) -> list:
    """`search_wiki_core`'s keyword path exactly as it was before the prefilter.

    Deliberately a transcription rather than a tidy-up: an oracle rewritten in better style
    is an oracle that can disagree with the original for reasons of its own. Only the
    keyword/scoring half is reproduced, because that is the half that changed; the tag and
    date filters are asserted against the real implementation separately.
    """
    _META_STEMS = {"log", "index", "lint"}
    _SYS_FIELDS = re.compile(r'^(sources|created|raw_source):[ \t].*', re.MULTILINE)
    _VALID = {"sources", "entities", "concepts", "synthesis"}

    scope, kw_tokens = None, []
    for t in query.split():
        if t.lower().startswith("in:"):
            scope = t.lower()[3:].strip("/")
        elif t.lower().startswith(("tag:", "after:", "before:")):
            pass
        else:
            kw_tokens.append(t)

    or_groups, prev_or = [], False
    for t in kw_tokens:
        if t.upper() == "OR":
            prev_or = True
            continue
        p = re.compile(re.escape(t), re.IGNORECASE)
        if prev_or and or_groups:
            or_groups[-1].append(p)
        else:
            or_groups.append([p])
        prev_or = False
    patterns = [p for g in or_groups for p in g]

    if scope and scope in _VALID:
        root, exclude_sources = wiki_dir / scope, False
    else:
        root, exclude_sources = wiki_dir, True

    out = []
    for f in agent.wiki_pages(root):
        if exclude_sources and f.is_relative_to(wiki_dir / "sources"):
            continue
        if f.stem in _META_STEMS:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        searchable = _SYS_FIELDS.sub('', text)
        searchable = re.sub(r'\]\([^)]*\)', ']()', searchable)
        if or_groups and not all(any(p.search(searchable) for p in g) for g in or_groups):
            continue
        score = sum(len(p.findall(searchable)) for p in patterns) if patterns else 1
        if not score:
            continue
        out.append((score, "wiki/" + str(f.relative_to(wiki_dir))))
    out.sort(key=lambda x: -x[0])
    return out


def _actual(query, wiki_dir):
    res = agent.search_wiki_core(query, wiki_dir)
    return [(r["score"], r["rel"]) for r in res["results"]]


def _corpus(w):
    """Pages carrying the shapes the substitutions exist for: links whose URL holds words
    the prose does not, a `sources:` line naming pages the body never mentions, mixed case,
    and bracket characters in the prose."""
    w.page("entities/alpha.md", title="Alpha Corporation", tags=["politics"],
           sources=["sources/zzz-2026-hidden.md"],
           body="## Overview\n\nAlpha makes [widgets](../concepts/gizmo-hidden.md) in "
                "Ohio. ALPHA is also a letter. alpha appears thrice.\n")
    w.page("entities/beta.md", title="Beta Industries", tags=["economy"],
           sources=["sources/zzz-2026-hidden.md"],
           body="## Overview\n\nBeta trades with [Alpha Corporation](alpha.md) and with "
                "Gamma. See also (parenthetical) remarks here.\n")
    w.page("concepts/gamma.md", title="Gamma", type="concept", tags=["politics"],
           body="## Definition\n\nGamma is a concept involving brackets [like this] and "
                "a URL http://example.com/alpha/page in prose.\n")
    w.page("entities/delta.md", title="Delta", tags=["politics"],
           body="## Overview\n\nNothing here matches the others at all.\n")
    w.page("sources/zzz-2026-hidden.md", title="A Hidden Source", type="source",
           body="## Summary\n\nThis source page mentions alpha and gizmo.\n")


class ReferenceEquivalenceTest(unittest.TestCase):
    """The fast path and the original must agree exactly.

    Every query shape that exercises a different branch: a miss, a hit, multi-keyword AND,
    OR groups, case variation, a keyword that only exists inside a link URL, one that only
    exists in a `sources:` line, a scoped search, and the bracket keywords that are
    supposed to fall off the fast path.
    """

    QUERIES = [
        "nonexistentterm",
        "alpha",
        "ALPHA",
        "Alpha",
        "alpha ohio",
        "alpha OR gamma",
        "alpha OR gamma ohio",
        "widgets",
        "gizmo",            # only inside a link URL — must NOT match
        "hidden",           # only in a sources: line — must NOT match
        "in:concepts gamma",
        "in:sources alpha",
        "brackets",
        "[like",            # bracket keyword: falls off the fast path
        "this]",
        "(parenthetical)",
        "example.com",
        "letter",
    ]

    def test_the_fast_path_returns_exactly_what_the_old_code_returned(self):
        with TempWiki() as w:
            _corpus(w)
            for q in self.QUERIES:
                with self.subTest(query=q):
                    self.assertEqual(_actual(q, w.wiki),
                                     reference_search(q, w.wiki),
                                     f"search diverged from the reference for {q!r}")

    def test_scores_match_not_just_the_page_set(self):
        """Score decides the ORDER of results, so matching the set is not enough — a
        changed count would quietly reshuffle what the user sees first."""
        with TempWiki() as w:
            _corpus(w)
            got = dict((rel, sc) for sc, rel in _actual("alpha", w.wiki))
            want = dict((rel, sc) for sc, rel in reference_search("alpha", w.wiki))
            self.assertEqual(got, want)
            self.assertTrue(any(v > 1 for v in got.values()),
                            "the fixture must contain a page matching more than once, or "
                            "this proves nothing about counting")


class SubstitutionsStillApplyTest(unittest.TestCase):
    """The prefilter must not become a way of admitting what the substitutions reject."""

    def test_a_keyword_only_inside_a_link_url_does_not_match(self):
        """The reason the link rewrite exists. The word is in the raw bytes, so the
        prefilter admits the page — and the exact check on the rewritten text must still
        throw it out."""
        with TempWiki() as w:
            _corpus(w)
            self.assertEqual(_actual("gizmo", w.wiki), [])

    def test_a_keyword_only_in_a_sources_line_does_not_match(self):
        with TempWiki() as w:
            _corpus(w)
            self.assertEqual(_actual("hidden", w.wiki), [])

    def test_a_bracket_keyword_takes_the_slow_path_and_still_works(self):
        """`]`, `(` and `)` are excluded from the fast path because deleting a link URL
        joins `](` to `)`, which can forge a match that was not in the raw text. The slow
        path has to keep working, or the gate is a silent loss of function."""
        with TempWiki() as w:
            _corpus(w)
            hits = [rel for _s, rel in _actual("(parenthetical)", w.wiki)]
            self.assertEqual(hits, ["wiki/entities/beta.md"])

    def test_matching_stays_case_insensitive(self):
        with TempWiki() as w:
            _corpus(w)
            for q in ("alpha", "ALPHA", "Alpha", "aLpHa"):
                with self.subTest(q=q):
                    self.assertTrue(_actual(q, w.wiki), f"{q!r} found nothing")
            self.assertEqual(_actual("alpha", w.wiki), _actual("ALPHA", w.wiki))


class FiltersUnaffectedTest(unittest.TestCase):
    """The prefilter sits in front of the tag and date filters, so both have to still work
    — including on a query with no keywords at all, where `lit_groups` is empty and the
    prefilter must be a no-op rather than a page-rejecting one."""

    def test_a_tag_only_query_still_returns_pages(self):
        with TempWiki() as w:
            _corpus(w)
            hits = {rel for _s, rel in _actual("tag:politics", w.wiki)}
            self.assertEqual(hits, {"wiki/entities/alpha.md", "wiki/concepts/gamma.md",
                                    "wiki/entities/delta.md"})

    def test_a_tag_and_keyword_query_applies_both(self):
        with TempWiki() as w:
            _corpus(w)
            hits = {rel for _s, rel in _actual("tag:economy alpha", w.wiki)}
            self.assertEqual(hits, {"wiki/entities/beta.md"})

    def test_a_date_filter_still_excludes(self):
        with TempWiki() as w:
            w.page("entities/old.md", title="Old Page", created="2026-01-01",
                   body="## Overview\n\nThe subject is tariffs and trade.\n")
            w.page("entities/new.md", title="New Page", created="2026-09-01",
                   body="## Overview\n\nThe subject is tariffs and trade.\n")
            hits = {rel for _s, rel in _actual("tariffs after:2026-06-01", w.wiki)}
            self.assertEqual(hits, {"wiki/entities/new.md"})

    def test_an_empty_query_is_still_an_error(self):
        with TempWiki() as w:
            _corpus(w)
            self.assertIsNotNone(
                agent.search_wiki_core("", w.wiki)["error"])


class ServeSharesTheImplementationTest(unittest.TestCase):
    def test_serve_uses_the_same_function_object(self):
        """Both of serve.py's search routes go through this, so a change here is a change
        to the browser's search. `from agent import …` binds the object, and the one way
        this silently drifts is a second copy."""
        import serve
        self.assertIs(serve.search_wiki_core, agent.search_wiki_core)


if __name__ == "__main__":
    unittest.main()
