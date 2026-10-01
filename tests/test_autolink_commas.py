"""A comma in a title is optional in the text — but not the other way round.

Asked whether the comma was causing misses on a wiki holding "University of California"
alongside "University of California, Merced" and friends. Measured first, and the answer is
no: when the page title and the prose spell the name the same way, the comma is matched
literally and the longest-first ordering gives the campus its link. All three pages present
and spelled alike came out right.

What does fail is a SPELLING MISMATCH, and it fails worse than a missed link:

    page title: University of California, Merced
    prose:      ...at the University of California Merced...
    result:     ...at the [University of California](../entities/university-of-california.md) Merced...

The long title did not match, so the shorter parent did. The sentence is about the campus
and the link goes to the whole system — a wrong link that RESOLVES, so lint is silent and
the page reads fine. Same family as the apostrophe case `_esc_flex` already handles, where
"Noah’s" never matched a page spelled "Noah's".

**Only one direction is safe, and the other is the interesting half of this module.** A
comma in the title may be absent from the text. A space in the title must NOT match a comma
in the text, because "attended by Smith, Johnson and Lee" would then link "Smith, Johnson"
as one person. A title carries the punctuation of a formal name; prose carries the
punctuation of a sentence.

Two things this does not fix, stated so they are not mistaken for passing:

  * Prose that says "UC Merced" still does not link. That is what `aliases:` is for, and it
    is deliberately a human decision.
  * A campus with no page of its own — "University of California, Santa Cruz" where only
    the parent exists — still links the parent and stops mid-name. Declining it would need
    the linker to know that ", Santa Cruz" continues a proper name rather than beginning a
    new clause, and "Monterey County, California" is the same shape with the opposite right
    answer.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

UC = "../entities/university-of-california.md"
UCM = "../entities/university-of-california-merced.md"


class _Base(TempWikiTestCase):
    def _pages(self, *titles):
        for t in titles:
            slug = t.lower().replace(", ", "-").replace(" ", "-").replace(",", "")
            self.w.page(f"entities/{slug}.md", title=t, type="entity",
                        body=f"# {t}\n\n## Overview\n\nx\n")
        agent._title_map_cache = None

    def _link(self, text):
        self.w.page("entities/p.md", title="P", type="entity",
                    body=f"# P\n\n## Overview\n\n{text}\n")
        agent._autolink_now(agent.WIKI_DIR / "entities/p.md")
        body = (agent.WIKI_DIR / "entities/p.md").read_text(
            encoding="utf-8").split("---", 2)[2]
        return next(l for l in body.splitlines()
                    if l.strip() and not l.startswith("#"))


class CommaInTitleTest(_Base):

    def setUp(self):
        super().setUp()
        self._pages("University of California", "University of California, Merced")

    def test_prose_without_the_comma_reaches_the_campus(self):
        """The reported shape, and the one that produced a wrong link rather than none."""
        out = self._link("Researchers at the University of California Merced published it.")
        self.assertIn(UCM, out)
        self.assertNotIn(UC + ")", out)

    def test_prose_with_the_comma_still_works(self):
        out = self._link("Researchers at the University of California, Merced published it.")
        self.assertIn(UCM, out)

    def test_the_display_text_keeps_what_the_author_wrote(self):
        """Rewriting an author's punctuation is not the linker's job — the same rule the
        typographic variants follow."""
        out = self._link("Researchers at the University of California Merced published it.")
        self.assertIn("[University of California Merced]", out)

    def test_the_parent_still_links_when_the_sentence_is_about_it(self):
        out = self._link("The University of California, which runs ten campuses, grew.")
        self.assertIn(UC, out)
        self.assertIn("[University of California]", out)

    def test_two_campuses_in_one_sentence(self):
        self._pages("University of California, Irvine")
        out = self._link("Both University of California Merced and University of "
                         "California, Irvine took part.")
        self.assertIn(UCM, out)
        self.assertIn("university-of-california-irvine.md", out)


class TheUnsafeDirectionTest(_Base):
    """The half that must NOT happen."""

    def test_a_space_in_a_title_does_not_match_a_comma_in_prose(self):
        """"attended by Smith, Johnson and Lee" is two people, and linking it as one is a
        confident lie about who was there."""
        self._pages("Smith Johnson")
        out = self._link("Attended by Smith, Johnson and Lee.")
        self.assertNotIn("](", out)

    def test_a_list_of_places_is_not_welded_into_one_name(self):
        self._pages("Monterey Salinas")
        self.assertNotIn("](", self._link("Served Monterey, Salinas and Gilroy."))

    def test_an_exact_title_with_a_space_still_matches(self):
        self._pages("Smith Johnson")
        self.assertIn("](", self._link("Attended by Smith Johnson and Lee."))


class StillLimitedTest(_Base):
    """Stated rather than left to be discovered as a surprise."""

    def test_an_abbreviation_does_not_link_without_an_alias(self):
        self._pages("University of California", "University of California, Merced")
        self.assertNotIn("](", self._link("Researchers at UC Merced published it."))

    def test_an_alias_is_what_makes_it_link(self):
        """The documented route — a human decision, not something the linker guesses."""
        self.w.page("entities/university-of-california-merced.md",
                    title="University of California, Merced", type="entity",
                    aliases=["UC Merced"], body="# x\n\n## Overview\n\nx\n")
        agent._title_map_cache = None
        self.assertIn(UCM, self._link("Researchers at UC Merced published it."))

    def test_a_campus_with_no_page_links_the_parent(self):
        """Current behaviour, recorded honestly: the link resolves and stops mid-name.
        Declining it needs the linker to know ", Santa Cruz" continues a proper name, and
        "Monterey County, California" is the same shape with the opposite right answer."""
        self._pages("University of California")
        out = self._link("A grant went to the University of California, Santa Cruz.")
        self.assertIn(UC, out)
        self.assertIn(", Santa Cruz", out)


class OtherPunctuationTest(_Base):

    def test_a_title_with_no_comma_is_unaffected(self):
        self._pages("Monterey County")
        self.assertIn("](", self._link("Filed in Monterey County court."))

    def test_the_apostrophe_rule_still_holds(self):
        self.w.page("entities/noahs-ark-scans.md", title="Noah's Ark Scans", type="entity",
                    body="# x\n\n## Overview\n\nx\n")
        agent._title_map_cache = None
        self.assertIn("](", self._link("The group Noah’s Ark Scans went looking."))

    def test_a_trailing_comma_in_prose_is_not_eaten_into_the_link(self):
        self._pages("Monterey County")
        out = self._link("In Monterey County, the case was heard.")
        self.assertIn("[Monterey County]", out)
        self.assertIn("), the case", out)


if __name__ == "__main__":
    unittest.main()
