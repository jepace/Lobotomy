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


if __name__ == "__main__":
    unittest.main()
