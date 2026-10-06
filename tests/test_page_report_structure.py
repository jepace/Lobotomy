"""`page_report.py`'s structural checks: sections, EMPTY, HEADINGS, DRIFT, PILE.

Written from `donald-trump.md` — 223,659 bytes, fourteen sections, and "pretty much a
complete mess". The mess was nine separate defects, and the three in this module are the
ones that are about the page's SHAPE rather than its text.

The finding worth keeping is the heading collision. The page carries
`## Key Policies & Actions (2025)` and `## Key Policies & Actions (2026)`. Each is
independently illegal — `_bad_headings` refuses a heading naming a date — and
`_absorb_date_qualifiers` silently strips a trailing date because that is "a standing
heading with a date bolted on" and the body stays correct. Both guards are right about
one heading at a time. Together they are a trap: absorbing the dates COLLIDES these two
into a duplicate heading, which `_heading_dupes` then refuses. The obvious repair for one
guard manufactures a violation of another, which is principle 4's documented worst case
found on disk instead of in a refusal — and nothing reported it before.

The EMPTY check's marking matters more than the check. An empty `## Legacy` is a
placeholder. An empty `## First Trump Impeachment` on a page whose `sources:` lists
`wikipedia-first-impeachment-of-donald-trump-2026.md` means that source was ingested, the
heading was made for it, and the text is not here — lost work rather than untidiness.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import page_report


def _report(w, body, **kw):
    w.page("entities/subject.md", title="Subject", body=body, **kw)
    return page_report.analyze(w.wiki / "entities" / "subject.md")


class SectionsTest(unittest.TestCase):
    def test_every_level_two_heading_is_a_section_with_its_size(self):
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nOne.\n\n## Background\n\nTwo two two.\n")
            names = [s["name"] for s in r["sections"]]
            self.assertEqual(names, ["Overview", "Background"])
            self.assertGreater(r["sections"][1]["chars"], r["sections"][0]["chars"])

    def test_the_sections_measure_text_not_link_syntax(self):
        """A page on disk is autolinked, so counting raw bytes measures the linker's
        output as much as the writer's — and the whole point of the size column is to say
        which section a human has to read."""
        with TempWiki() as w:
            plain = _report(w, "## Overview\n\nThe White House did it.\n")
            linked = _report(w, "## Overview\n\nThe [White House](../entities/wh.md) "
                                "did it.\n")
            self.assertEqual(plain["sections"][0]["chars"],
                             linked["sections"][0]["chars"])

    def test_the_generated_sources_section_is_marked_not_dropped(self):
        """It is a real share of the page's bytes, so it is counted; it is generated from
        frontmatter, so no other check may read it — a duplicate inside it is a duplicate
        SOURCE PAGE, which is a different finding with a different repair."""
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nx\n\n## Sources\n\n- [A](../sources/a.md)\n")
            gen = [s for s in r["sections"] if s["generated"]]
            self.assertEqual([s["name"] for s in gen], ["Sources"])


class EmptySectionTest(unittest.TestCase):
    def test_a_heading_with_no_body_is_reported(self):
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nx\n\n## Legacy\n\n## Background\n\ny\n")
            self.assertEqual([e["name"] for e in r["empty"]], ["Legacy"])

    def test_an_empty_section_whose_subject_the_page_has_sources_for_is_marked(self):
        """The whole value of the check. This is the shape that means ingested text is
        missing, and it reads identically to a placeholder unless the sources are read."""
        with TempWiki() as w:
            w.page("sources/wikipedia-first-impeachment-of-donald-trump-2026.md",
                   title="First Impeachment", type="source", body="## Summary\n\nx\n")
            r = _report(
                w, "## Overview\n\nx\n\n## First Impeachment\n\n## Legacy\n",
                sources=["sources/wikipedia-first-impeachment-of-donald-trump-2026.md"])
            by_name = {e["name"]: e for e in r["empty"]}
            self.assertEqual(sorted(by_name), ["First Impeachment", "Legacy"])
            self.assertTrue(by_name["First Impeachment"]["sources"],
                            "an empty section backed by a source must be marked")
            self.assertFalse(by_name["Legacy"]["sources"],
                             "a plain placeholder must not be dressed up as lost work")

    def test_a_one_word_heading_is_never_matched_against_a_source_slug(self):
        """A single short word matches far too much — `legacy` appears inside plenty of
        slugs — and a report that calls every placeholder lost work is one nobody reads."""
        with TempWiki() as w:
            w.page("sources/nytimes-2026-the-legacy-of-the-whole-thing.md",
                   title="Legacy", type="source", body="## Summary\n\nx\n")
            r = _report(w, "## Overview\n\nx\n\n## Legacy\n",
                        sources=["sources/nytimes-2026-the-legacy-of-the-whole-thing.md"])
            self.assertEqual([e["sources"] for e in r["empty"]], [[]])

    def test_all_of_a_headings_words_must_appear_in_the_slug(self):
        """Any-word matching would tie `## Second Trump Impeachment` to the FIRST
        impeachment's source and send the reader to the wrong page."""
        with TempWiki() as w:
            w.page("sources/wikipedia-first-impeachment-of-donald-trump-2026.md",
                   title="First Impeachment", type="source", body="## Summary\n\nx\n")
            r = _report(
                w, "## Overview\n\nx\n\n## Second Trump Impeachment\n",
                sources=["sources/wikipedia-first-impeachment-of-donald-trump-2026.md"])
            self.assertEqual([e["sources"] for e in r["empty"]], [[]])


class HeadingCollisionTest(unittest.TestCase):
    def test_two_headings_differing_only_by_their_date_are_reported_as_a_collision(self):
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nx\n\n## Key Policies (2025)\n\na\n\n"
                           "## Key Policies (2026)\n\nb\n")
            self.assertEqual(len(r["collisions"]), 1)
            c = r["collisions"][0]
            self.assertEqual(c["merged"], "Key Policies")
            self.assertEqual(sorted(c["from"]),
                             ["Key Policies (2025)", "Key Policies (2026)"])

    def test_the_collision_is_reported_on_top_of_the_plain_dated_heading_finding(self):
        """Both facts are true and they call for different work: the dated-heading rule
        says rename, and the collision says you cannot simply strip the date to do it."""
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nx\n\n## Key Policies (2025)\n\na\n\n"
                           "## Key Policies (2026)\n\nb\n")
            dated = [n for n, why in r["bad_headings"] if why == "names a date"]
            self.assertEqual(sorted(dated),
                             ["Key Policies (2025)", "Key Policies (2026)"])

    def test_one_dated_heading_with_no_twin_is_not_a_collision(self):
        """Absorption handles it cleanly, so saying otherwise would be crying wolf — and
        would double-count every dated heading in the wiki."""
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nx\n\n## Key Policies (2026)\n\na\n")
            self.assertEqual(r["collisions"], [])

    def test_a_heading_whose_date_is_its_subject_is_not_a_collision(self):
        """`_absorb_date_qualifiers` deliberately refuses to strip a LEADING date — in
        "2026 Outbreak" the date is the subject and stripping leaves "Outbreak", which
        names nothing. Two such headings do not collide because neither is absorbed."""
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nx\n\n## 2025 Outbreak\n\na\n\n"
                           "## 2026 Outbreak\n\nb\n")
            self.assertEqual(r["collisions"], [])

    def test_duplicate_headings_already_on_disk_are_reported(self):
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nx\n\n## Trade\n\na\n\n## Trade\n\nb\n")
            self.assertEqual(r["dupe_headings"], ["Trade"])


class DriftTest(unittest.TestCase):
    ACCRETED = ("## Overview\n\nSubject is a person. In August 2026, housing data showed "
                "typical home values at $375,470 in the region. As of October 2026, the "
                "state is battling a significant outbreak of dengue fever. In October "
                "2026, officials announced the end of the Flock Safety contract.\n")

    def test_the_summary_sections_dated_openers_are_counted_and_quoted(self):
        with TempWiki() as w:
            r = _report(w, self.ACCRETED)
            self.assertEqual(len(r["drift"]["dated"]), 3)
            self.assertTrue(r["drift"]["dated"][0].startswith("In August 2026"))

    def test_the_numbers_are_overview_drifts_numbers(self):
        """Two tools reporting different figures for one page is how a reader stops
        trusting both. They read the same `_SUMMARY_SECTIONS`, the same
        `_DATED_OPENER_RE` and the same `_sentences`, so this pins the agreement."""
        import agent
        with TempWiki() as w:
            r = _report(w, self.ACCRETED)
            body = (w.wiki / "entities" / "subject.md").read_text()
            section = body.split("## Overview", 1)[1].strip()
            sents = agent._sentences(section)
            self.assertEqual(r["drift"]["sentences"], len(sents))
            self.assertEqual(len(r["drift"]["dated"]),
                             len([s for s in sents
                                  if agent._DATED_OPENER_RE.match(s)]))

    def test_a_concept_pages_definition_is_the_summary_too(self):
        with TempWiki() as w:
            w.page("concepts/thing.md", title="Thing", type="concept",
                   body="## Definition\n\nA thing. In August 2026, a thing happened to "
                        "the thing and it was notable at the time.\n")
            r = page_report.analyze(w.wiki / "concepts" / "thing.md")
            self.assertEqual(r["drift"]["name"], "Definition")
            self.assertEqual(len(r["drift"]["dated"]), 1)

    def test_a_page_with_no_summary_section_reports_none_rather_than_zeroes(self):
        with TempWiki() as w:
            r = _report(w, "## Background\n\nx\n")
            self.assertIsNone(r["drift"])


class PileTest(unittest.TestCase):
    @staticmethod
    def _dated(n, word="thing"):
        return " ".join(
            f"In September 2026, the {word} number {i} happened and was reported widely "
            f"by several outlets at considerable length indeed."
            for i in range(n))

    def test_a_large_section_full_of_dated_news_is_a_pile(self):
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nShort.\n\n## Second Term\n\n"
                        + self._dated(12) + "\n")
            self.assertEqual([p["name"] for p in r["piles"]], ["Second Term"])
            self.assertGreaterEqual(r["piles"][0]["dated"], 12)

    def test_a_large_section_about_one_thing_is_not_a_pile(self):
        """Size alone would report the section that is working. A page's principal
        section is supposed to be its biggest one."""
        with TempWiki() as w:
            prose = ("The subject was born and then did many things over a long career "
                     "that are described here at length without reference to any "
                     "particular date. ") * 12
            r = _report(w, "## Overview\n\nShort.\n\n## Background\n\n" + prose + "\n")
            self.assertEqual(r["piles"], [])

    def test_a_small_section_of_dated_news_is_not_a_pile(self):
        """Dated openers alone would report a legitimate short chronicle, and would fire
        on the Timeline section of every event page in the wiki."""
        with TempWiki() as w:
            big = ("The subject did a great many things, none of them dated, described "
                   "here in considerable detail for the reader. ") * 40
            r = _report(w, "## Overview\n\n" + big + "\n\n## Recent\n\n"
                        + self._dated(6) + "\n")
            self.assertEqual([p["name"] for p in r["piles"]], [])


class SizeTest(unittest.TestCase):
    def test_the_report_measures_the_page_against_the_read_limit(self):
        """The number that decides how a repair has to be done: over
        `_WIKI_READ_LIMIT`, `read_file` returns an outline and a whole-page `update_file`
        is not reachable, so the work is section-by-section per LOBOTOMY.md §6b."""
        import agent
        with TempWiki() as w:
            r = _report(w, "## Overview\n\n" + ("word " * 12_000) + "\n")
            self.assertGreater(r["unlinked"], agent._WIKI_READ_LIMIT)


class ResolveTest(unittest.TestCase):
    def test_a_bare_slug_resolves_to_the_one_page_that_matches(self):
        with TempWiki() as w:
            w.page("entities/donald-trump.md", title="Donald Trump",
                   body="## Overview\n\nx\n")
            self.assertEqual(page_report.resolve_page("donald-trump").name,
                             "donald-trump.md")
            self.assertEqual(page_report.resolve_page("entities/donald-trump.md").name,
                             "donald-trump.md")

    def test_a_slug_that_matches_nothing_resolves_to_none(self):
        """So the CLI can say "no such page" instead of printing a report full of zeroes
        about a file it never read."""
        with TempWiki() as w:
            w.page("entities/a.md", title="A", body="## Overview\n\nx\n")
            self.assertIsNone(page_report.resolve_page("no-such-page"))

    def test_resolution_reads_the_live_tree_rather_than_a_captured_path(self):
        """`from agent import WIKI_DIR` binds the VALUE, so a module that captures it at
        import scans the real wiki from inside the harness and nothing fails. That has
        already happened twice in this codebase."""
        with TempWiki() as w:
            w.page("entities/only-here.md", title="Only Here", body="## Overview\n\nx\n")
            p = page_report.resolve_page("only-here")
            self.assertIsNotNone(p)
            self.assertTrue(str(p).startswith(str(w.wiki)),
                            f"resolved outside the temp wiki: {p}")


if __name__ == "__main__":
    unittest.main()
