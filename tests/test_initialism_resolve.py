"""A name spelled out resolves to the page that initialises it, and vice versa.

The observed duplicate. An ingest listed "New York University Langone Health" in the
source page's ## Entities, then created the page as "NYU Langone Health" at
entities/nyu-langone-health.md — a perfectly reasonable title. Every check then said the
listed name had no page:

    done() refused (1/2): 1 need creating: New York University Langone Health
    lookup_titles → CREATE (1) — these have no page. Call create_file for each one.
                    "This answer is exact and covers aliases — do not call search_wiki
                     to double-check it."

So done() demanded the page, lookup_titles confirmed the demand and told the model not to
verify it, and the model created entities/new-york-university-langone-health.md: a second
page for the same hospital, in a wiki that already has find_duplicate_pages.py because
this keeps happening.

Nothing in that chain was wrong on its own. They share one resolver, so they were all
wrong the same way — which is what made the duplicate inevitable rather than unlucky.

_initialism_match closes it, and declares rather than suggests. Every word outside the
initialism has to match exactly and both names must be consumed completely, which is much
tighter than _norm_title_key (that one drops legal suffixes and so cannot tell a holding
company from its subsidiary, which is why it only ever offers a hint).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class InitialismMatchTest(unittest.TestCase):
    """The rule itself, with the cases that must NOT match."""

    def test_the_observed_pair(self):
        self.assertTrue(agent._initialism_match(
            "New York University Langone Health", "NYU Langone Health"))

    def test_it_works_in_both_directions(self):
        self.assertTrue(agent._initialism_match(
            "NYU Langone Health", "New York University Langone Health"))

    def test_the_initialism_can_be_the_whole_name(self):
        self.assertTrue(agent._initialism_match("UN", "United Nations"))
        self.assertTrue(agent._initialism_match("United Nations", "UN"))

    def test_it_handles_an_initialism_in_the_middle(self):
        self.assertTrue(agent._initialism_match(
            "United Nations Security Council", "UN Security Council"))

    def test_punctuated_initialisms_work(self):
        self.assertTrue(agent._initialism_match("U.N. Security Council",
                                                "United Nations Security Council"))

    def test_an_exact_match_is_not_this_functions_business(self):
        # It is the last resort; the exact paths ran first and would have returned.
        self.assertFalse(agent._initialism_match("NYU Langone Health",
                                                 "NYU Langone Health"))

    def test_an_ordinary_abbreviation_does_not_match(self):
        # M,S are not the initials of "Microsoft", "Word". Requiring a run of consecutive
        # words is what keeps this off shortenings.
        self.assertFalse(agent._initialism_match("MS Word", "Microsoft Word"))

    def test_a_different_entity_does_not_match(self):
        self.assertFalse(agent._initialism_match("NYU Langone Health",
                                                 "NYU School of Medicine"))
        self.assertFalse(agent._initialism_match("United Nations", "United States"))

    def test_extra_words_are_not_ignored(self):
        # Both names must be consumed completely — a parent and a subsidiary are pages of
        # their own, and this must not collapse them.
        self.assertFalse(agent._initialism_match("NYU Langone Health",
                                                 "New York University Langone Health System"))

    def test_a_single_letter_is_not_an_initialism(self):
        self.assertFalse(agent._initialism_match("A Corp", "Alpha Corp"))

    def test_it_does_not_match_on_first_letters_alone(self):
        self.assertFalse(agent._initialism_match("Sao Paulo", "SO Paulo"))


class ResolveTest(TempWikiTestCase):

    def _wiki(self):
        self.w.page("entities/nyu-langone-health.md", title="NYU Langone Health",
                    type="entity", body="# NYU Langone Health\n\n## Overview\n\nA hospital.\n")
        return {t.lower(): rel for t, rel in agent._build_title_map()}

    def test_the_spelled_out_name_resolves_to_the_initialised_page(self):
        self.assertEqual(
            agent._resolve_page("New York University Langone Health", self._wiki()),
            "entities/nyu-langone-health.md")

    def test_an_unrelated_name_still_resolves_to_nothing(self):
        self.assertEqual(agent._resolve_page("Mount Sinai Hospital", self._wiki()), "")

    def test_lookup_titles_says_update_not_create(self):
        self._wiki()
        agent.init_session()
        r = agent.TOOL_FNS["lookup_titles"]({"names": ["New York University Langone Health"]})
        self.assertIn("UPDATE (1)", r, r)
        self.assertIn("entities/nyu-langone-health.md", r)
        self.assertIn("do not make a second one", r,
                      "the reply does not warn against the duplicate this exists to prevent")

    def test_done_no_longer_demands_a_page_that_exists(self):
        """The refusal that drove the duplicate.

        The source page lists the spelled-out name; the session created the initialised
        page. done() must count that as handled, or its only available advice is the one
        that produces the second page.
        """
        self.w.page("entities/nyu-langone-health.md", title="NYU Langone Health",
                    type="entity", body="# NYU Langone Health\n\n## Overview\n\nA hospital.\n")
        agent.init_session(inbox_path="raw/glp1.md")
        ctx = agent._ctx()
        ctx._session_entity_pages.append("entities/nyu-langone-health.md")
        ctx._current_source_page = "sources/blum-2026-glp1.md"
        self.w.page("sources/blum-2026-glp1.md", title="Blum 2026 GLP-1", type="source",
                    body="# Blum 2026 GLP-1\n\n## Summary\n\nX.\n\n"
                         "## Entities\n\n- New York University Langone Health\n")
        to_update, to_create = agent._unhandled_listed_pages(ctx)
        self.assertEqual(to_create, [],
                         "done() would still demand a page the session already wrote")
        self.assertEqual(to_update, [],
                         "the page was written this session, so it needs no update")


if __name__ == "__main__":
    unittest.main()
