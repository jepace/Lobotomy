"""done()'s refusal names the route to each page, and knows what was already read.

Observed in a 20-round ingest. `lookup_titles` marks each page "reads whole — read_file
first" or "outline only", and the agent followed that for its first two pages. Then it
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
