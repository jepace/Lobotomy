"""Two names differing only by a leading national qualifier are one page.

**Observed in a live ingest, as its very last call.** The run updated
`entities/commission-of-fine-arts.md` early on, and twenty-odd rounds later called
`create_file` for `entities/u-s-commission-of-fine-arts.md` — two pages for one federal
body, and nothing in `_resolve_page` could see it. `_initialism_match` cannot: `U.S.` is
not an initialism OF anything in the other name, it is a word added in front of it. So
every check answered NO PAGE, `create_file` obliged, and `find_duplicate_pages.py` will not
report the pair either, because the two titles have different keys.

**The ≥2-word remainder is the entire guard, and it is doing real work.** Strip the
qualifier from a one-word remainder and the matches are wrong far more often than right:
"US Steel" is not "Steel", "US Open" is not "Open", "US Bank" is not "Bank", "US Airways"
is not "Airways". At two words and up the shape is an institution's name, and the qualifier
is a formality prose adds and drops freely.

**"federal" and "national" are deliberately not in the list**, and the asymmetry is why: a
false match here does not create a duplicate page, it sends the write to the WRONG page.
"U.S." is a place and never part of what a body does, while "National" and "Federal" are
ordinary words inside formal names whose remainder is often a subject of its own — a
"National Gallery" is not a "Gallery". The observed duplicate was geographic, so the list
is geographic.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


def _resolve(name):
    """`_resolve_page` as its callers reach it — they all build the same title map, and
    building it here means these tests go through the real one rather than a fixture."""
    return agent._resolve_page(
        name, {t.lower(): rel for t, rel in agent._build_title_map()})


class MatcherTest(unittest.TestCase):
    def test_the_observed_pair_matches(self):
        self.assertTrue(agent._jurisdiction_match(
            "U.S. Commission of Fine Arts", "Commission of Fine Arts"))

    def test_it_matches_in_both_directions(self):
        """`_resolve_page` walks the title map, so either name can be the one on disk."""
        self.assertTrue(agent._jurisdiction_match(
            "Commission of Fine Arts", "U.S. Commission of Fine Arts"))

    def test_every_spelling_of_the_qualifier(self):
        for q in ("U.S.", "US", "U S", "United States",
                  "United States of America", "U.K.", "UK", "United Kingdom"):
            with self.subTest(qualifier=q):
                self.assertTrue(
                    agent._jurisdiction_match(f"{q} Postal Service", "Postal Service"),
                    f"{q!r} did not read as a leading national qualifier")

    def test_a_one_word_remainder_never_matches(self):
        """The guard. Each of these is a different company or event from the bare word,
        and folding them would write one subject's content onto another's page."""
        for pair in (("US Steel", "Steel"),
                     ("US Open", "Open"),
                     ("U.S. Bank", "Bank"),
                     ("US Airways", "Airways"),
                     ("United States Steel", "Steel")):
            with self.subTest(pair=pair):
                self.assertFalse(agent._jurisdiction_match(*pair),
                                 f"{pair[0]!r} folded into {pair[1]!r}")

    def test_federal_and_national_are_not_qualifiers_here(self):
        """Stated as behaviour so removing them from the list is a decision somebody
        makes on purpose rather than a regression nothing notices."""
        self.assertFalse(agent._jurisdiction_match(
            "Federal Commission of Fine Arts", "Commission of Fine Arts"))
        self.assertFalse(agent._jurisdiction_match(
            "National Portrait Gallery", "Portrait Gallery"))

    def test_identical_names_are_not_a_jurisdiction_match(self):
        """They are already handled by the exact-key check above it; answering True here
        would make the loop's result depend on which check ran first."""
        self.assertFalse(agent._jurisdiction_match(
            "Commission of Fine Arts", "Commission of Fine Arts"))

    def test_unrelated_names_do_not_match(self):
        for pair in (("U.S. Department of Justice", "Department of State"),
                     ("Commission of Fine Arts", "Fine Arts Museum"),
                     ("United States Postal Service", "Royal Mail"),
                     ("", "Postal Service"),
                     ("Postal Service", "")):
            with self.subTest(pair=pair):
                self.assertFalse(agent._jurisdiction_match(*pair))

    def test_the_qualifier_must_be_a_whole_leading_word(self):
        """"Usual Suspects" starts with the letters of "US" and must not be read as
        qualifying "Suspects" — which the one-word guard also catches, so this uses a
        two-word remainder to test the word boundary on its own."""
        self.assertFalse(agent._jurisdiction_match(
            "Usual Postal Service", "Postal Service"))

    def test_a_middle_qualifier_is_not_a_leading_one(self):
        self.assertFalse(agent._jurisdiction_match(
            "Bank of U.S. Commerce Holdings", "Commerce Holdings"))


class ResolvePageTest(unittest.TestCase):
    """The matcher is only worth anything through `_resolve_page`, which is the one place
    that decides create-vs-update — and which `lookup_titles`, `done()`'s listed-name
    check and the duplicate guards all share, so a gap in it makes every caller wrong the
    same way at once. That sharing is what made the observed duplicate inevitable:
    `lookup_titles` confirmed there was no page ("this answer is exact… do not
    double-check it") and the model obliged."""

    def test_the_qualified_name_resolves_to_the_bare_page(self):
        with TempWiki() as w:
            w.page("entities/commission-of-fine-arts.md", title="Commission of Fine Arts",
                   body="## Overview\n\nA federal design review body.\n")
            self.assertEqual(_resolve("U.S. Commission of Fine Arts"),
                             "entities/commission-of-fine-arts.md")

    def test_the_bare_name_resolves_to_the_qualified_page(self):
        with TempWiki() as w:
            w.page("entities/u-s-postal-service.md", title="U.S. Postal Service",
                   body="## Overview\n\nThe mail.\n")
            self.assertEqual(_resolve("Postal Service"),
                             "entities/u-s-postal-service.md")

    def test_lookup_titles_reports_it_as_an_existing_page(self):
        """The call that confirmed the wrong answer in the observed log. It shares
        `_resolve_page`, so this passes or fails with the resolver — asserted anyway,
        because that sharing is the feature and a later refactor could break it."""
        with TempWiki() as w:
            w.page("entities/commission-of-fine-arts.md", title="Commission of Fine Arts",
                   body="## Overview\n\nA federal design review body.\n")
            agent.init_session()
            out = agent._lookup_titles({"names": ["U.S. Commission of Fine Arts"]})
            self.assertIn("commission-of-fine-arts.md", out)
            self.assertNotIn("CREATE", out.split("UPDATE")[0] if "UPDATE" in out else out)

    def test_create_file_refuses_the_duplicate(self):
        """The call that actually made the second page. Refusing it is the point of the
        whole change."""
        with TempWiki() as w:
            w.page("entities/commission-of-fine-arts.md", title="Commission of Fine Arts",
                   body="## Overview\n\nA federal design review body.\n")
            agent.init_session()
            out = agent._create_file({
                "path": "wiki/entities/u-s-commission-of-fine-arts.md",
                "title": "U.S. Commission of Fine Arts",
                "type": "entity",
                "body": "## Overview\n\nA federal design review body.\n",
            })
            self.assertIn("Error", out, out)
            self.assertIn("commission-of-fine-arts.md", out)
            self.assertFalse(
                (w.wiki / "entities" / "u-s-commission-of-fine-arts.md").exists(),
                "the duplicate page was created anyway")

    def test_a_one_word_remainder_still_resolves_to_nothing(self):
        """The guard, through the resolver: "US Steel" must still be creatable beside a
        page about steel."""
        with TempWiki() as w:
            w.page("concepts/steel.md", title="Steel", type="concept",
                   body="## Definition\n\nAn alloy.\n")
            self.assertEqual(_resolve("US Steel"), "")

    def test_an_exact_title_still_wins(self):
        """Both pages exist — the qualified name must resolve to its OWN page, not get
        folded into the bare one by a loop that runs too eagerly."""
        with TempWiki() as w:
            w.page("entities/commission-of-fine-arts.md", title="Commission of Fine Arts",
                   body="## Overview\n\nOne body.\n")
            w.page("entities/u-s-commission-of-fine-arts.md",
                   title="U.S. Commission of Fine Arts",
                   body="## Overview\n\nThe other.\n")
            self.assertEqual(_resolve("U.S. Commission of Fine Arts"),
                             "entities/u-s-commission-of-fine-arts.md")
            self.assertEqual(_resolve("Commission of Fine Arts"),
                             "entities/commission-of-fine-arts.md")

    def test_an_initialism_match_is_unaffected(self):
        """The jurisdiction loop is separate from the initialism loop rather than folded
        into it, because that one is gated on a shared first letter — sound for an
        initialism and wrong here by construction, since the qualifier changes the first
        letter. Both must still work."""
        with TempWiki() as w:
            w.page("entities/nyu-langone-health.md", title="NYU Langone Health",
                   body="## Overview\n\nA hospital.\n")
            self.assertEqual(_resolve("New York University Langone Health"),
                             "entities/nyu-langone-health.md")


if __name__ == "__main__":
    unittest.main()
