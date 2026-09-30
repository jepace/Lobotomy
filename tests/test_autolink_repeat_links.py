"""A title links once per section's prose, and on every list or table row.

Wikipedia's MOS:REPEATLINK: link once per article, but relink in infoboxes, tables, image
captions, footnotes and lists — because a reader arrives at those out of order. Linking
every occurrence turned a paragraph mentioning measles six times into six identical links.

Two deliberate departures from the letter of that rule, both forced by the shape of this
wiki:

  * **Per section, not per page.** donald-trump.md is 136KB across twenty-odd sections. One
    link at the top of that leaves the whole rest of the page with no navigation. A section
    here is about what an article is on Wikipedia.
  * **Lists always link**, and a list row does not consume the section's first mention.
    A source page's ## Entities and ## Concepts are bullet lists of names, and a Timeline
    is a list of dated entries. Those are lookup tables; a row whose link was spent in a
    paragraph above it is a dead row. This is the exception the user asked for before it
    was built, and it is in the Wikipedia rule too.

The half that is easy to forget: the rule has to **unlink** repeats that are already there.
Applied only to newly written text it would be invisible, because ~9,000 pages were written
under the old rule. Unlinking is strictly scoped — same target page AND display text whose
words are exactly the title's — so a hand-written "[the disease](../concepts/measles.md)"
alias and every external link survive untouched.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class RepeatLinkTest(TempWikiTestCase):

    def _concepts(self, *names):
        for n in names:
            slug = n.lower().replace(" ", "-")
            self.w.page(f"concepts/{slug}.md", title=n, type="concept",
                        body=f"# {n}\n\nA thing.\n")

    def _page(self, body):
        self.w.page("entities/pa.md", title="PA Outbreak", type="entity",
                    body="# PA Outbreak\n\n" + body)
        agent._autolink({"path": "wiki/entities/pa.md"})
        return self.w.disk("entities/pa.md")

    def test_only_the_first_prose_mention_in_a_section_links(self):
        self._concepts("Measles")
        out = self._page("## Overview\n\nMeasles spread. More measles, and yet more "
                         "measles.\n")
        self.assertEqual(out.count("](../concepts/measles.md)"), 1, out)
        self.assertIn("[Measles](../concepts/measles.md) spread. More measles, and yet "
                      "more measles.", out)

    def test_each_section_gets_its_own_first_mention(self):
        self._concepts("Measles")
        out = self._page("## Overview\n\nMeasles spread, measles again.\n\n"
                         "## Background\n\nMeasles once more, and measles again.\n")
        self.assertEqual(out.count("](../concepts/measles.md)"), 2, out)

    def test_every_list_row_links(self):
        self._concepts("Measles", "Vaccination")
        out = self._page("## Entities\n\n- Measles\n- Vaccination\n- Measles again\n")
        self.assertEqual(out.count("](../concepts/measles.md)"), 2, out)
        self.assertEqual(out.count("](../concepts/vaccination.md)"), 1, out)

    def test_a_list_row_does_not_spend_the_sections_prose_mention(self):
        # The row is an index entry; the paragraph is prose. Neither should silence the
        # other.
        self._concepts("Measles")
        out = self._page("## Overview\n\n- Measles\n\nMeasles is the subject here.\n")
        self.assertEqual(out.count("](../concepts/measles.md)"), 2, out)

    def test_timeline_entries_all_link(self):
        self._concepts("Measles")
        out = self._page("## Timeline\n\n- **2026-08** — Measles kills two infants.\n"
                         "- **2026-09** — Measles deaths reach three.\n")
        self.assertEqual(out.count("](../concepts/measles.md)"), 2, out)

    def test_numbered_and_table_rows_count_as_lists(self):
        self._concepts("Measles")
        out = self._page("## Steps\n\n1. Measles is confirmed.\n2. Measles is reported.\n\n"
                         "## Table\n\n| Disease | Cases |\n| Measles | 676 |\n"
                         "| Measles | 700 |\n")
        self.assertEqual(out.count("](../concepts/measles.md)"), 4, out)


class UnlinkingRepeatsTest(TempWikiTestCase):
    """The retroactive half. Without it the rule only applies to new text."""

    def _setup(self, body):
        self.w.page("concepts/measles.md", title="Measles", type="concept",
                    body="# Measles\n\nA disease.\n")
        self.w.page("entities/pa.md", title="PA Outbreak", type="entity",
                    body="# PA Outbreak\n\n" + body)
        agent._autolink({"path": "wiki/entities/pa.md"})
        return self.w.disk("entities/pa.md")

    def test_a_page_linked_under_the_old_rule_is_cleaned_up(self):
        out = self._setup(
            "## Overview\n\nThe [measles](../concepts/measles.md) outbreak grew. More "
            "[measles](../concepts/measles.md) cases, and yet more "
            "[measles](../concepts/measles.md).\n")
        self.assertEqual(out.count("](../concepts/measles.md)"), 1, out)
        self.assertIn("More measles cases, and yet more measles.", out)

    def test_a_hand_written_alias_link_is_never_stripped(self):
        # Display text is not the title, so this is not a link the autolinker wrote.
        out = self._setup(
            "## Overview\n\n[Measles](../concepts/measles.md) spread. The "
            "[the disease](../concepts/measles.md) took hold.\n")
        self.assertIn("[the disease](../concepts/measles.md)", out, out)

    def test_an_external_link_is_never_stripped(self):
        out = self._setup(
            "## Overview\n\n[Measles](../concepts/measles.md) spread, per "
            "[the report](https://example.com/measles.md).\n")
        self.assertIn("[the report](https://example.com/measles.md)", out, out)

    def test_a_link_to_a_different_page_is_never_stripped(self):
        self.w.page("concepts/vaccination.md", title="Vaccination", type="concept",
                    body="# Vaccination\n\nA thing.\n")
        out = self._setup(
            "## Overview\n\n[Measles](../concepts/measles.md) spread; measles again. "
            "[Vaccination](../concepts/vaccination.md) helps.\n")
        self.assertIn("[Vaccination](../concepts/vaccination.md)", out, out)

    def test_a_repeat_in_a_list_keeps_its_link(self):
        out = self._setup(
            "## Overview\n\n[Measles](../concepts/measles.md) spread.\n\n"
            "## Entities\n\n- [Measles](../concepts/measles.md)\n")
        self.assertEqual(out.count("](../concepts/measles.md)"), 2, out)


class StabilityTest(TempWikiTestCase):
    """relink runs over the whole wiki and must converge — two consecutive runs reporting
    hundreds of changes is a bug this project has shipped before."""

    def _build(self):
        for n in ("Measles", "Vaccination", "Public Health"):
            slug = n.lower().replace(" ", "-")
            self.w.page(f"concepts/{slug}.md", title=n, type="concept",
                        body=f"# {n}\n\nA thing.\n")
        self.w.page("entities/pa.md", title="PA Outbreak", type="entity",
                    body="# PA Outbreak\n\n## Overview\n\nA public health crisis; measles "
                         "cases rose and public health officials warn measles spreads.\n\n"
                         "## Background\n\nSurging measles cases, falling vaccination "
                         "rates. Measles remains the concern.\n\n"
                         "## Entities\n\n- Measles\n- Vaccination\n- Public Health\n")

    def test_a_second_pass_changes_nothing(self):
        self._build()
        agent._autolink({"path": "wiki/entities/pa.md"})
        once = self.w.disk("entities/pa.md")
        agent._autolink({"path": "wiki/entities/pa.md"})
        self.assertEqual(self.w.disk("entities/pa.md"), once, "autolink does not converge")

    def test_a_third_pass_changes_nothing_either(self):
        # Unlinking exposes text that group 1 previously shielded, so convergence is not
        # obvious from one round.
        self._build()
        for _ in range(3):
            agent._autolink({"path": "wiki/entities/pa.md"})
        stable = self.w.disk("entities/pa.md")
        agent._autolink({"path": "wiki/entities/pa.md"})
        self.assertEqual(self.w.disk("entities/pa.md"), stable)

    def test_a_dry_run_still_writes_nothing(self):
        self._build()
        before = self.w.disk("entities/pa.md")
        agent._autolink({"path": "wiki/entities/pa.md", "dry_run": True})
        self.assertEqual(self.w.disk("entities/pa.md"), before)


class LookupRowVersusProseBulletTest(TempWikiTestCase):
    """Not every bullet is a lookup row.

    "## Entities" and "## Concepts" rows are names and a Timeline row is a dated entry —
    those are read out of order, so a row whose link was spent in a paragraph above it is
    a dead row. "## Claims" is a bulleted list of full SENTENCES: prose that happens to
    carry hyphens, read top to bottom, where linking California in all nine claims is
    exactly the repetition the once-per-section rule exists to stop.

    Length is the honest discriminator between a name and a sentence. Timeline rows are
    exempted explicitly, because a dated entry is a lookup row however long it runs.
    """

    def setUp(self):
        super().setUp()
        self.w.page("entities/california.md", title="California", type="entity",
                    body="# California\n\n## Overview\n\nA state.\n")

    def _src(self, body):
        self.w.page("sources/s.md", title="S", type="source", body="# S\n\n" + body)
        agent._autolink({"path": "wiki/sources/s.md"})
        return self.w.disk("sources/s.md")

    def test_a_claims_list_links_once_for_the_section(self):
        out = self._src(
            "## Claims\n\n"
            "- California has moved to protect reproductive health services after the "
            "federal ruling.\n"
            "- California officials said the clinics would remain open through the year.\n"
            "- Funding in California is drawn from a state reserve, not federal grants.\n")
        self.assertEqual(out.count("](../entities/california.md)"), 1, out)

    def test_a_name_row_still_always_links(self):
        out = self._src("## Overview\n\nCalifornia acted.\n\n"
                        "## Entities\n\n- California\n")
        self.assertEqual(out.count("](../entities/california.md)"), 2, out)

    def test_a_timeline_row_always_links_however_long(self):
        out = self._src(
            "## Timeline\n\n"
            "- **2026-09** — California announced the plan after weeks of sustained "
            "pressure from advocates and county health officials.\n"
            "- **2026-10** — California expanded it to cover three more counties, "
            "bringing the statewide total to eleven.\n")
        self.assertEqual(out.count("](../entities/california.md)"), 2, out)

    def test_a_table_row_always_links(self):
        out = self._src("## Funding\n\n| State | Amount |\n| California | 10 |\n"
                        "| California | 20 |\n")
        self.assertEqual(out.count("](../entities/california.md)"), 2, out)

    def test_a_short_bullet_is_a_name_even_outside_entities(self):
        out = self._src("## Affected\n\n- California\n- California\n")
        self.assertEqual(out.count("](../entities/california.md)"), 2, out)


class TheBudgetBelongsToThePageTest(TempWikiTestCase):
    """An alias is a different NAME, not a different subject.

    Observed on a real source page:

        President [Donald Trump](../entities/donald-trump.md)'s brand is deteriorating.
        Anderson contends that [Trump](../entities/donald-trump.md) and the party ...

    Two links to one page in one section's prose. `_seen` was a fresh cell per title, and
    every alias is its own entry in the title map, so donald-trump.md got one first mention
    as "Donald Trump" and another as "Trump". The reader does not care which of a page's
    names was used; the record is keyed by (target page, section) now.
    """

    def _wiki(self, aliases=("Trump",)):
        self.w.page("entities/donald-trump.md", title="Donald Trump", type="entity",
                    aliases=list(aliases),
                    body="# Donald Trump\n\n## Overview\n\nA politician.\n")

    def _src(self, body):
        self.w.page("sources/s.md", title="S", type="source", body=body)
        agent._autolink({"path": "wiki/sources/s.md"})
        return self.w.disk("sources/s.md")

    def test_a_title_and_its_alias_share_one_mention(self):
        self._wiki()
        out = self._src("# S\n\n## Summary\n\nPresident Donald Trump's brand is "
                        "deteriorating. Anderson contends that Trump faces headwinds.\n")
        self.assertEqual(out.count("](../entities/donald-trump.md)"), 1, out)

    def test_the_fuller_name_takes_it(self):
        """The title map is sorted longest-first, so the better display text wins."""
        self._wiki()
        out = self._src("# S\n\n## Summary\n\nPresident Donald Trump's brand. "
                        "Anderson contends that Trump faces headwinds.\n")
        self.assertIn("[Donald Trump](../entities/donald-trump.md)", out)
        self.assertNotIn("[Trump](", out)

    def test_a_repeat_already_on_disk_is_unlinked(self):
        """The retroactive half — the reported page was already in this state."""
        self._wiki()
        out = self._src("# S\n\n## Summary\n\nPresident "
                        "[Donald Trump](../entities/donald-trump.md)'s brand. Anderson "
                        "contends that [Trump](../entities/donald-trump.md) faces "
                        "headwinds.\n")
        self.assertEqual(out.count("](../entities/donald-trump.md)"), 1, out)
        self.assertIn("that Trump faces", out)

    def test_each_section_still_gets_its_own(self):
        self._wiki()
        out = self._src("# S\n\n## Summary\n\nDonald Trump spoke.\n\n"
                        "## Claims\n\nTrump said more.\n")
        self.assertEqual(out.count("](../entities/donald-trump.md)"), 2, out)

    def test_a_lookup_row_still_always_links(self):
        self._wiki()
        out = self._src("# S\n\n## Summary\n\nDonald Trump spoke.\n\n"
                        "## Entities\n\n- Trump\n")
        self.assertEqual(out.count("](../entities/donald-trump.md)"), 2, out)

    def test_two_different_pages_are_not_confused(self):
        """The record is keyed by target page; a second subject keeps its own mention."""
        self._wiki()
        self.w.page("entities/republican-party.md", title="Republican Party",
                    type="entity", body="# Republican Party\n\n## Overview\n\nA party.\n")
        out = self._src("# S\n\n## Summary\n\nDonald Trump and the Republican Party "
                        "face headwinds.\n")
        self.assertEqual(out.count("](../entities/donald-trump.md)"), 1, out)
        self.assertEqual(out.count("](../entities/republican-party.md)"), 1, out)

    def test_several_aliases_still_collapse_to_one(self):
        self._wiki(aliases=("Trump", "the president"))
        out = self._src("# S\n\n## Summary\n\nDonald Trump spoke. Trump repeated it. "
                        "the president insisted.\n")
        self.assertEqual(out.count("](../entities/donald-trump.md)"), 1, out)

    def test_it_converges(self):
        self._wiki()
        self._src("# S\n\n## Summary\n\nDonald Trump spoke, and Trump spoke again.\n")
        once = self.w.disk("sources/s.md")
        for _ in range(3):
            agent._autolink({"path": "wiki/sources/s.md"})
        self.assertEqual(self.w.disk("sources/s.md"), once)


if __name__ == "__main__":
    unittest.main()
