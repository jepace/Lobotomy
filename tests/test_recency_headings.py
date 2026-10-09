"""A heading that names recency instead of a subject is refused.

Measured from a live ingest of one article: **82 tool calls, 36 refused**, and eleven NEW
sections created on existing pages — among them "Recent Developments", "Recent
Controversies" and "2026 Midterms Context". The last was already refused (a dated
heading); the first two were not, and they are the same mistake with the date left
implicit.

**And implicit is worse.** "## 2026 Senate Campaign" at least records which year it froze
at. "## Recent Developments" claims to be current forever: nothing revises it, the next
ingest adds its own news underneath, and a year later the heading is a lie about the
paragraph under it. The dated-heading rule exists because a section is revised in place
and a dated one never is — that argument applies to this heading word for word.

**A recency qualifier is required, and that is the whole design.** `## Controversies` is a
legitimate standing section on a person's page and `## Events` on a festival's; refusing
those would be the crying-wolf failure that every report in this project is built to
avoid. It is the qualifier in front of the noun that turns a subject into a changelog, so
the bare nouns are deliberately left alone — asserted below, because a regex loosened by
one character would start refusing them and the suite has to notice.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class PatternTest(unittest.TestCase):
    REFUSED = [
        "Recent Developments",
        "Recent Development",
        "Recent Controversies",
        "Recent News",
        "Recent Events",
        "Recent Updates",
        "Latest Developments",
        "Latest News",
        "Current Events",
        "Current Developments",
        "Ongoing Developments",
        "New Developments",
        "Most Recent Developments",
        "Newest Updates",
        "Upcoming Events",
        "recent developments",
        "RECENT DEVELOPMENTS",
        "Recent   Developments",
    ]

    ALLOWED = [
        # Bare nouns: legitimate standing sections, and the reason the qualifier is
        # required rather than the noun alone.
        "Controversies",
        "Events",
        "Developments",
        "News",
        "Updates",
        # Standing headings that merely contain one of the qualifier words.
        "Current Standing",
        "Current Position",
        "Recent History",          # a subject, not a changelog
        "Latest Album",
        "New Zealand Operations",
        "Ongoing Litigation",      # names what, not when
        "Overview",
        "Background",
        "Positions",
        "Timeline",
        "Electoral Record",
        "Recent and Historical Comparisons",
    ]

    def test_the_changelog_names_are_matched(self):
        for name in self.REFUSED:
            with self.subTest(heading=name):
                self.assertTrue(agent._RECENCY_HEADING_RE.match(name),
                                f"{name!r} is a changelog heading and was not matched")

    def test_the_standing_names_are_not(self):
        for name in self.ALLOWED:
            with self.subTest(heading=name):
                self.assertFalse(agent._RECENCY_HEADING_RE.match(name),
                                 f"{name!r} is a legitimate heading and was refused")


class BadHeadingsTest(unittest.TestCase):
    def test_it_is_reported_with_its_own_reason(self):
        bad = agent._bad_headings("## Recent Developments\n\nx\n", "Pete Ricketts")
        self.assertEqual(bad, [("Recent Developments", "names recency")])

    def test_a_dated_recency_heading_reports_as_dated(self):
        """Both rules match "Recent 2026 Developments". The date is the more specific
        finding and its refusal is the one that explains the rename, so it wins — the
        order in `_bad_headings` is what decides that and a reorder would change the
        message the model acts on."""
        bad = agent._bad_headings("## Recent 2026 Developments\n\nx\n", "X")
        self.assertEqual([r for _n, r in bad], ["names a date"])

    def test_the_h1_is_not_exempted_from_this_rule_by_accident(self):
        """Level 1 is exempt from the title rule only. A page whose H1 somehow reads
        "Recent Developments" is a page titled that, which is a different problem; what
        this pins is that the recency check still sees level-2 headings when an H1 is
        present above them."""
        bad = agent._bad_headings("# Pete Ricketts\n\n## Recent News\n\nx\n",
                                  "Pete Ricketts")
        self.assertEqual([n for n, _r in bad], ["Recent News"])

    def test_a_clean_page_reports_nothing(self):
        self.assertEqual(
            agent._bad_headings("# X\n\n## Overview\n\nx\n\n## Controversies\n\ny\n", "X"),
            [])


class WritePathsTest(unittest.TestCase):
    """Every write path shares `_bad_headings`, so they cannot disagree about what counts
    — and each reports it as a DELTA, because a page already carrying one has to stay
    editable (principle 2; an earlier whole-page heading guard deadlocked 176 real
    pages)."""

    def setUp(self):
        self.tmp = TempWiki().__enter__()
        self.addCleanup(self.tmp.__exit__, None, None, None)

    def _page(self, body="## Overview\n\nA senator from Nebraska.\n"):
        self.tmp.page("entities/pete-ricketts.md", title="Pete Ricketts", type="entity",
                      body=body)
        agent.init_session()
        agent._read_file("wiki/entities/pete-ricketts.md")
        return "wiki/entities/pete-ricketts.md"

    def test_append_section_refuses_it(self):
        p = self._page()
        r = agent.TOOL_FNS["append_section"](
            {"path": p, "section": "Recent Developments", "text": "Osborn runs."})
        self.assertTrue(r.startswith("Error:"), r)
        self.assertIn("names recency", r)

    def test_the_refusal_names_a_move_that_works(self):
        """Principle 4, and the only version of this assertion that proves anything:
        follow the refusal's instruction and assert it succeeds."""
        p = self._page()
        r = agent.TOOL_FNS["append_section"](
            {"path": p, "section": "Recent Developments", "text": "Osborn runs."})
        self.assertIn("Name the SUBJECT", r)
        ok = agent.TOOL_FNS["append_section"](
            {"path": p, "section": "Senate Campaign", "text": "Osborn runs."})
        self.assertFalse(ok.startswith("Error:"), ok)

    def test_the_refusal_does_not_steer_into_the_dated_heading_rule(self):
        """The trap this project keeps rediscovering: a refusal that tells the model to
        write something new has to respect the rules the OTHER guards will apply to it."""
        p = self._page()
        r = agent.TOOL_FNS["append_section"](
            {"path": p, "section": "Latest News", "text": "Osborn runs."})
        self.assertIn("no date", r)

    def test_update_section_refuses_a_heading_it_introduces(self):
        p = self._page(body="## Overview\n\nA senator.\n\n## Career\n\nHe served.\n")
        r = agent.TOOL_FNS["update_section"](
            {"path": p, "section": "Career",
             "content": "He served.\n\n## Recent Developments\n\nOsborn runs."})
        self.assertTrue(r.startswith("Error:"), r)

    def test_create_file_refuses_one(self):
        agent.init_session()
        r = agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/dan-osborn.md", "title": "Dan Osborn",
            "type": "entity",
            "body": "## Overview\n\nAn independent candidate.\n\n"
                    "## Recent Developments\n\nHe is running.\n",
        })
        self.assertTrue(r.startswith("Error:"), r)
        self.assertFalse((self.tmp.wiki / "entities" / "dan-osborn.md").exists())

    def test_a_page_that_already_has_one_stays_editable(self):
        """Principle 2. There are such sections on the live wiki already, and the only
        edits that could fix them are the ones a whole-page check would refuse."""
        p = self._page(body="## Overview\n\nA senator.\n\n"
                            "## Recent Developments\n\nOld news.\n")
        r = agent.TOOL_FNS["update_section"](
            {"path": p, "section": "Recent Developments",
             "content": "Old news, now rewritten with more detail about the campaign."})
        self.assertFalse(r.startswith("Error:"), r)

    def test_renaming_it_away_is_not_blocked(self):
        """The repair has to be reachable: dropping the heading in favour of a named one
        must go through, or the guard deadlocks the pages it is complaining about."""
        p = self._page(body="## Overview\n\nA senator.\n\n"
                            "## Recent Developments\n\nOld news.\n")
        r = agent.TOOL_FNS["update_file"](
            {"path": p,
             "content": "# Pete Ricketts\n\n## Overview\n\nA senator.\n\n"
                        "## Senate Campaign\n\nOld news.\n"})
        self.assertFalse(r.startswith("Error:"), r)


class SchemaTest(unittest.TestCase):
    def test_lobotomy_md_states_the_rule(self):
        """A guard added in code needs a matching line in the schema — discovering a rule
        by refusal costs a round, reading it costs nothing."""
        text = (Path(__file__).resolve().parent.parent / "LOBOTOMY.md").read_text()
        self.assertIn("Recent Developments", text)


if __name__ == "__main__":
    unittest.main()
