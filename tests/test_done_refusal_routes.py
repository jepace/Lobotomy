"""done()'s refusal names the route to each page, and knows what was already read.

Observed in a 20-round ingest. `lookup_titles` marks each page with how to reach it, and
the agent followed that for its first two pages. Then it
called done() early, the refusal landed, and it read_section'd all four remaining pages —
because the refusal said, for every page at any size:

    These already have a page you did not update — read_section the section you are
    changing, then update_section with this source merged in

It did exactly what it was told. Two costs:

  * One of those four was `nathan-fielder.md`, which it had already read IN FULL nine
    rounds earlier. Coverage was credited, so update_section would have gone straight
    through; the round was spent fetching what it was already holding.
  * For the others, read_section is the worst of the three routes at equal cost — 2 rounds
    either way, but it sees one section rather than the whole page, so it cannot tell that
    the material belongs somewhere else.

done()'s refusal is the moment the agent re-plans, so it is the moment the routing has to
be right. Same staleness as the lookup_titles header before it: advice that was true when
written and was never revisited when the tools underneath it changed.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class DoneRefusalRouteTest(TempWikiTestCase):

    def setUp(self):
        super().setUp()
        self.w.page("sources/s.md", title="S", type="source",
                    body="# S\n\n## Summary\n\nx\n\n## Claims\n\n- A claim.\n\n"
                         "## Entities\n\n- Nathan Fielder\n\n"
                         "## Concepts\n\n- Legislation\n- U.S. Senate\n")
        self.w.page("entities/nathan-fielder.md", title="Nathan Fielder", type="entity",
                    body="# Nathan Fielder\n\n## Overview\n\nA comedian.\n")
        self.w.page("concepts/u-s-senate.md", title="U.S. Senate", type="concept",
                    body="# U.S. Senate\n\n## Definition\n\nThe upper chamber.\n")
        big = "# Legislation\n\n" + "".join(
            f"## Part {i}\n\n" + ("word " * 900) + "\n\n" for i in range(8))
        self.w.page("concepts/legislation.md", title="Legislation", type="concept", body=big)
        # A page written this session, so the "no entity/concept pages" refusal is not the
        # one that fires.
        self.w.page("entities/germanwings.md", title="Germanwings", type="entity",
                    body="# Germanwings\n\n## Overview\n\nAn airline.\n")
        agent._title_map_cache = None
        ctx = agent._ctx()
        ctx._current_inbox_path = "raw/a.md"
        ctx._current_source_page = "sources/s.md"
        ctx._session_entity_pages.append("entities/germanwings.md")

    def _refusal(self):
        r = agent._done({"summary": "x"})
        self.assertIn("refused", r, r[:200])
        return r

    def _row(self, r, slug):
        return next(l for l in r.splitlines() if slug in l)

    def test_a_short_unread_page_is_sent_to_read_file(self):
        r = self._refusal()
        self.assertIn("reads whole — read_file first",
                      self._row(r, "concepts/u-s-senate.md"))

    def test_a_long_page_is_sent_to_read_section(self):
        r = self._refusal()
        self.assertIn("too large to read whole — read_section",
                      self._row(r, "concepts/legislation.md"))

    def test_a_page_already_read_in_full_is_sent_straight_to_update_section(self):
        """The wasted round. Coverage is credited, so the read buys nothing."""
        agent._read_file("wiki/entities/nathan-fielder.md")
        row = self._row(self._refusal(), "entities/nathan-fielder.md")
        self.assertIn("already read in full this session", row)
        self.assertIn("do NOT read it again", row)

    def test_the_same_page_unread_is_not_claimed_to_be_read(self):
        row = self._row(self._refusal(), "entities/nathan-fielder.md")
        self.assertNotIn("already read", row)
        self.assertIn("read_file first", row)

    def test_it_no_longer_tells_every_page_to_read_section(self):
        """The stale blanket instruction that produced the observed behaviour."""
        r = self._refusal()
        self.assertNotIn("read_section the section you are changing", r)

    def test_it_gives_the_same_route_lookup_titles_gives(self):
        """The consolidation. These two replies both answer "how do I reach this page", and
        they used to answer it from separate copies of the reasoning — which drifted, so a
        large page was told "go straight to update_section" by one and "read_section" by the
        other. An agent told two things does one of them at random."""
        r = self._refusal()
        for slug, name in (("concepts/u-s-senate.md", "U.S. Senate"),
                           ("concepts/legislation.md", "Legislation"),
                           ("entities/nathan-fielder.md", "Nathan Fielder")):
            lookup = agent._lookup_titles({"names": [name]})
            lrow = next(l for l in lookup.splitlines() if slug in l)
            drow = self._row(r, slug)
            phrase = agent._write_route(slug)[1]
            self.assertIn(phrase, lrow, slug)
            self.assertIn(phrase, drow, slug)

    def test_the_legend_explains_the_routes_it_uses(self):
        """A marker nothing explains is a marker the agent has to guess at."""
        r = self._refusal()
        for phrase in ("read_file on it FIRST", "read_section for the section you mean",
                       "you are holding the page"):
            self.assertIn(phrase, r)

    def test_every_route_the_helper_can_return_is_in_the_legend(self):
        """Generated from one set of keys, so a route cannot be named per-page and left out
        of the legend — which is how a reader meets a marker with no explanation."""
        for key, phrase in agent._ROUTE_PHRASES.items():
            head = phrase.split("—")[0].strip()
            self.assertIn(head, agent.ROUTE_LEGEND, key)

    def test_the_schema_spells_out_the_same_three_routes(self):
        """LOBOTOMY.md is the sixth place this advice appeared, and the one the model reads
        BEFORE any reply arrives — so discovering a route by meeting it in a refusal costs a
        round that reading the schema costs nothing. It cannot be generated from the code
        (it is prose, and the system prompt only appends the tool table), so this is the tie
        that keeps the two in step: every phrase the code can emit is written there verbatim.
        """
        schema = (Path(__file__).resolve().parent.parent / "LOBOTOMY.md").read_text(
            encoding="utf-8")
        for key, phrase in agent._ROUTE_PHRASES.items():
            self.assertIn(phrase, schema, f"{key} is not in LOBOTOMY.md")

    def test_the_schema_does_not_say_the_old_contradictory_thing(self):
        """It told the model to go straight to update_section on a large page while done()
        told it to read_section. Both copies of the step 5/6 procedure said so."""
        schema = (Path(__file__).resolve().parent.parent / "LOBOTOMY.md").read_text(
            encoding="utf-8")
        self.assertNotIn("outline only", schema)

    def test_every_unhandled_name_still_appears(self):
        r = self._refusal()
        for slug in ("entities/nathan-fielder.md", "concepts/u-s-senate.md",
                     "concepts/legislation.md"):
            self.assertIn(slug, r)

    def test_the_claim_matches_what_read_file_would_do(self):
        """The row is a promise about another tool; if they disagree it is worse than
        saying nothing."""
        r = self._refusal()
        for slug, page in (("concepts/u-s-senate.md", "wiki/concepts/u-s-senate.md"),
                           ("concepts/legislation.md", "wiki/concepts/legislation.md")):
            said_whole = "reads whole" in self._row(r, slug)
            agent.init_session()
            agent._ctx()._current_inbox_path = "raw/a.md"
            agent._ctx()._current_source_page = "sources/s.md"
            agent._ctx()._session_entity_pages.append("entities/germanwings.md")
            got_whole = "[OUTLINE" not in agent._read_file(page)
            self.assertEqual(said_whole, got_whole, slug)

    def test_the_route_advice_is_followable(self):
        """Principle 4: follow the refusal and the edit goes through. The short page is
        read whole, and update_section then needs no second attempt."""
        agent._read_file("wiki/concepts/u-s-senate.md")
        out = agent._update_section({"path": "wiki/concepts/u-s-senate.md",
                                     "section": "Definition",
                                     "content": "The upper chamber. It acted in 2026."})
        self.assertNotIn("Error", out, out[:200])


if __name__ == "__main__":
    unittest.main()
