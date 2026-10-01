"""lookup_titles names each existing page's sections, so placement is decided in time.

Observed across a 58-page ingest, once per page: update_section('Overview') -> refused for
not having read it -> update_section('Overview') again with the same destination. Every
page. The agent was told only that wiki/entities/gavin-newsom.md EXISTS, so it composed
Overview content and went; it learned the page had twenty other sections from the refusal,
which arrives AFTER the material is written and where the cheapest move is to resend what
it already has.

The section list was already in that refusal (852e5cb). It is not enough, and the reason is
structural rather than a wording problem: a list that arrives after the decision cannot
change the decision. So the page's shape moves to lookup_titles, which is where the agent is
still deciding what to write and where.

Two constraints on that:

  * NAMES ONLY, never content. Anything here that credited read coverage would stop the
    read-before-write guard firing, and that guard is what protects a page's existing text
    from a blind rewrite. There is a test for exactly that.
  * `## Sources` is excluded — it is auto-generated from frontmatter and is never a
    destination for anything. That exclusion lives in `_page_section_names` and nowhere
    else: a second copy here was dead code, and mutate.py said so by reporting it MISSED.
    The behaviour is still asserted below; only the duplicate rule is gone.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class PageShapeTest(TempWikiTestCase):

    def _newsom(self):
        self.w.page("entities/gavin-newsom.md", title="Gavin Newsom", type="entity",
                    body="# Gavin Newsom\n\n## Overview\n\nx\n\n## Political Career\n\ny\n\n"
                         "## Presidential Ambitions\n\nz\n\n## Sources\n\n- a\n")

    def test_the_sections_are_named_beside_the_page(self):
        self._newsom()
        out = agent._lookup_titles({"names": ["Gavin Newsom"]})
        self.assertIn("sections: Overview, Political Career, Presidential Ambitions", out)

    def test_the_sources_section_is_never_offered(self):
        self._newsom()
        self.assertNotIn("Sources", agent._lookup_titles({"names": ["Gavin Newsom"]})
                         .split("CREATE")[0].split("sections:")[1])

    def test_an_overview_only_page_says_so(self):
        """Useful in its own right: it tells the agent Overview IS the only choice."""
        self.w.page("entities/janice-hahn.md", title="Janice Hahn", type="entity",
                    body="# Janice Hahn\n\n## Overview\n\nx\n")
        out = agent._lookup_titles({"names": ["Janice Hahn"]})
        self.assertIn("sections: Overview", out)

    def test_a_page_with_no_sections_adds_nothing(self):
        self.w.page("entities/bare.md", title="Bare", type="entity", body="# Bare\n\nx\n")
        out = agent._lookup_titles({"names": ["Bare"]})
        self.assertIn("wiki/entities/bare.md", out)
        self.assertNotIn("sections:", out)

    def test_a_missing_page_gets_no_section_list(self):
        out = agent._lookup_titles({"names": ["Nobody At All"]})
        self.assertIn("Nobody At All", out)
        self.assertNotIn("sections:", out)

    def test_a_long_section_list_is_capped_and_says_so(self):
        names = [f"Section {i}" for i in range(20)]
        body = "# Big\n\n" + "".join(f"## {n}\n\nx\n\n" for n in names)
        self.w.page("entities/big.md", title="Big", type="entity", body=body)
        out = agent._lookup_titles({"names": ["Big"]})
        self.assertIn("+6 more", out)
        self.assertIn("Section 0", out)

    def test_each_page_says_which_read_route_it_supports(self):
        """The datum the agent cannot otherwise have, and the one that decides the route.

        Short page: read_file first is free and STRICTLY better — 2 rounds either way, but
        it composes once with every section's text in front of it. Straight to
        update_section means composing blind and then re-deciding while holding a draft
        aimed at the guessed section; an observed 58-page ingest did exactly that and
        resent Overview after a refusal that had named the alternatives.
        Long page: read_file returns an outline and costs a round, so the route is a direct
        read_section for the section named in this very reply — also 2 rounds, and it
        composes once for the same reason the short-page case does. This reply used to say
        "go straight to update_section" here while done()'s refusal said read_section; both
        now come from `_write_route`, which resolved it this way.
        """
        self._newsom()
        big = "# Big\n\n" + "".join(f"## S{i}\n\n" + ("word " * 900) + "\n\n"
                                     for i in range(8))
        self.w.page("entities/big.md", title="Big", type="entity", body=big)
        agent._title_map_cache = None
        out = agent._lookup_titles({"names": ["Gavin Newsom", "Big"]})
        newsom = next(l for l in out.splitlines() if "gavin-newsom" in l)
        big_l = next(l for l in out.splitlines() if "entities/big.md" in l)
        self.assertIn("reads whole — read_file first", newsom)
        self.assertIn("too large to read whole — read_section", big_l)
        # And both routes are explained, not just marked.
        self.assertIn("costs no extra round", out)
        self.assertIn("read_file would return only an outline", out)

    def test_the_route_is_the_one_done_would_give_for_the_same_page(self):
        """The consolidation itself. Two replies giving one page two different routes means
        the agent does one of them at random, and the round it spends is real either way."""
        self._newsom()
        rel = "entities/gavin-newsom.md"
        out = agent._lookup_titles({"names": ["Gavin Newsom"]})
        row = next(l for l in out.splitlines() if rel in l)
        self.assertIn(agent._write_route(rel)[1], row)

    def test_a_page_already_read_in_full_is_not_sent_back_to_read_it(self):
        """_page_shape had no coverage case, so lookup_titles told the agent to read_file a
        page it was already holding. done()'s copy of the advice did have it."""
        self._newsom()
        rel = "entities/gavin-newsom.md"
        agent._read_file(f"wiki/{rel}")
        out = agent._lookup_titles({"names": ["Gavin Newsom"]})
        row = next(l for l in out.splitlines() if rel in l)
        self.assertIn("already read in full this session", row)
        self.assertNotIn("read_file first", row)

    def test_the_marker_agrees_with_what_read_file_actually_does(self):
        """The marker is a promise about another tool; if they disagree it is worse than
        saying nothing."""
        self._newsom()
        out = agent._lookup_titles({"names": ["Gavin Newsom"]})
        said_whole = "reads whole" in out
        agent.init_session()
        got_whole = "[OUTLINE" not in agent._read_file("wiki/entities/gavin-newsom.md")
        self.assertEqual(said_whole, got_whole)

    def test_the_instruction_names_the_mistake(self):
        """Overview is a summary of the page, not the place everything goes."""
        self._newsom()
        out = agent._lookup_titles({"names": ["Gavin Newsom"]})
        self.assertIn("belongs to", out)
        self.assertIn("Overview is a summary of the page", out)


class ItMustNotCreditReadCoverageTest(TempWikiTestCase):
    """The load-bearing constraint. lookup_titles names sections; it does not show text, so
    it cannot satisfy read-before-write. If it ever did, a blind rewrite would go straight
    through and discard the page's existing content."""

    def setUp(self):
        super().setUp()
        self.w.page("entities/x.md", title="X", type="entity",
                    body="# X\n\n## Overview\n\nimportant existing text\n\n## Career\n\ny\n")

    def test_update_section_is_still_refused_after_a_lookup(self):
        agent._lookup_titles({"names": ["X"]})
        r = agent._update_section({"path": "wiki/entities/x.md", "section": "Overview",
                                   "content": "replacement"})
        self.assertIn("had not read", r)
        self.assertIn("important existing text", self.w.disk("entities/x.md"))

    def test_update_file_is_still_refused_after_a_lookup(self):
        agent._lookup_titles({"names": ["X"]})
        r = agent._update_file("wiki/entities/x.md",
                               '---\ntitle: "X"\ntype: entity\ntags: []\n'
                               'updated: 2026-09-27\nsources: []\n---\n\n# X\n\ngutted\n')
        self.assertIn("not read", r)
        self.assertIn("important existing text", self.w.disk("entities/x.md"))

    def test_no_page_content_leaks_into_the_reply(self):
        out = agent._lookup_titles({"names": ["X"]})
        self.assertNotIn("important existing text", out)


class TheRefusalStillCarriesTheListTest(TempWikiTestCase):
    """Belt and braces: the earlier fix stays. A page whose shape the agent did not look up
    still gets told what else is there when the guard fires."""

    def test_the_refusal_still_reveals_the_other_sections(self):
        """However it does it. On a short page the whole page comes back, so the other
        sections arrive with their CONTENT; over _WIKI_READ_LIMIT they arrive as names.
        Either way the agent cannot come away believing Overview is all there is."""
        self.w.page("entities/x.md", title="X", type="entity",
                    body="# X\n\n## Overview\n\nx\n\n## Sanctions and the Oil Sector\n\ny\n")
        r = agent._update_section({"path": "wiki/entities/x.md", "section": "Overview",
                                   "content": "new"})
        self.assertIn("Sanctions and the Oil Sector", r)

    def test_a_long_page_still_gets_the_names_list(self):
        big = "# B\n\n## Overview\n\n" + ("word " * 4000) + \
              "\n\n## Sanctions and the Oil Sector\n\n" + ("word " * 2000) + "\n"
        self.w.page("entities/big.md", title="B", type="entity", body=big)
        r = agent._update_section({"path": "wiki/entities/big.md", "section": "Overview",
                                   "content": "new"})
        self.assertIn("other sections", r)
        self.assertIn("Sanctions and the Oil Sector", r)


if __name__ == "__main__":
    unittest.main()
