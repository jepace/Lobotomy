"""A source page's ## Entities and ## Concepts are lookup tables, so they are sorted.

CLAUDE.md already calls them that, and `_is_lookup_row` gives them their own autolinking
rule for exactly this reason: a reader arrives at one of those rows out of order, hunting
for a name. An unordered lookup table makes them read the whole list, and these run to
dozens of rows.

What must NOT be sorted is the point of most of this module:

  * `## Claims` is prose in bullet form and reads top to bottom. Sorting it would scramble
    an argument into alphabetical order.
  * `## Timeline` is chronological and belongs to `normalize_timeline`.

Source pages are immutable to the LLM once written, so existing ones can only be ordered by
`heal_pages`; new ones are born sorted in `create_file`. Sorting is idempotent, which
matters because `heal_pages` runs at startup and after every ingest — a normalizer that
rewrote every pass would fill page history with empty revisions forever.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class SortUnitTest(unittest.TestCase):

    def _rows(self, out, heading):
        block = out.split(f"## {heading}")[1].split("\n##")[0]
        return [l.strip() for l in block.splitlines() if l.strip().startswith("- ")]

    def test_rows_are_alphabetized(self):
        out = agent.sort_lookup_lists(
            "# S\n\n## Entities\n\n- Xi Jinping\n- China\n- Donald Trump\n")
        self.assertEqual(self._rows(out, "Entities"),
                         ["- China", "- Donald Trump", "- Xi Jinping"])

    def test_it_sorts_by_the_visible_text_not_the_link(self):
        """On disk these rows are already autolinked, so sorting raw text would order by
        '[' and then by target path."""
        out = agent.sort_lookup_lists(
            "# S\n\n## Entities\n\n- [Zulu](../entities/aaa.md)\n"
            "- [Alpha](../entities/zzz.md)\n")
        self.assertEqual(self._rows(out, "Entities"),
                         ["- [Alpha](../entities/zzz.md)", "- [Zulu](../entities/aaa.md)"])

    def test_sorting_is_case_insensitive(self):
        out = agent.sort_lookup_lists("# S\n\n## Concepts\n\n- tariffs\n- Inflation\n")
        self.assertEqual(self._rows(out, "Concepts"), ["- Inflation", "- tariffs"])

    def test_concepts_are_sorted_too(self):
        out = agent.sort_lookup_lists("# S\n\n## Concepts\n\n- zoning\n- abortion\n")
        self.assertEqual(self._rows(out, "Concepts"), ["- abortion", "- zoning"])

    # -- what must not be touched ----------------------------------------------

    def test_claims_are_left_in_the_order_they_were_written(self):
        """Prose in bullet form. Sorting it scrambles an argument."""
        src = ("# S\n\n## Claims\n\n- Zebra populations fell sharply in 2026.\n"
               "- Apple harvests were unaffected.\n")
        self.assertEqual(agent.sort_lookup_lists(src), src)

    def test_a_timeline_is_left_alone(self):
        src = ("# S\n\n## Timeline\n\n- **2026-09** — Later event.\n"
               "- **2026-01** — Earlier event.\n")
        self.assertEqual(agent.sort_lookup_lists(src), src)

    def test_an_unrelated_section_is_left_alone(self):
        src = "# S\n\n## Summary\n\n- second\n- first\n"
        self.assertEqual(agent.sort_lookup_lists(src), src)

    # -- shape preservation ----------------------------------------------------

    def test_a_lead_in_paragraph_stays_above_the_list(self):
        out = agent.sort_lookup_lists(
            "# S\n\n## Entities\n\nThe article names these:\n\n- Beta\n- Alpha\n")
        self.assertIn("The article names these:", out)
        self.assertLess(out.index("The article names these:"), out.index("- Alpha"))

    def test_the_following_section_is_not_absorbed(self):
        out = agent.sort_lookup_lists(
            "# S\n\n## Entities\n\n- Beta\n- Alpha\n\n## Concepts\n\n- zeta\n- alpha\n")
        self.assertEqual(self._rows(out, "Entities"), ["- Alpha", "- Beta"])
        self.assertEqual(self._rows(out, "Concepts"), ["- alpha", "- zeta"])

    def test_a_single_row_is_untouched(self):
        src = "# S\n\n## Entities\n\n- Only One\n"
        self.assertEqual(agent.sort_lookup_lists(src), src)

    def test_a_file_ending_without_a_newline_keeps_that_shape(self):
        out = agent.sort_lookup_lists("# S\n\n## Entities\n\n- Beta\n- Alpha")
        self.assertTrue(out.endswith("- Beta"), repr(out[-20:]))
        self.assertFalse(out.endswith("\n"))

    def test_it_is_idempotent(self):
        src = "# S\n\n## Entities\n\n- Xi Jinping\n- China\n- Donald Trump\n"
        once = agent.sort_lookup_lists(src)
        self.assertEqual(agent.sort_lookup_lists(once), once)

    def test_duplicate_rows_are_kept_not_merged(self):
        """Sorting orders; it does not decide that two rows are the same thing."""
        out = agent.sort_lookup_lists("# S\n\n## Entities\n\n- Alpha\n- Beta\n- Alpha\n")
        self.assertEqual(self._rows(out, "Entities"), ["- Alpha", "- Alpha", "- Beta"])


class WritePathTest(TempWikiTestCase):

    def test_a_new_source_page_is_born_sorted(self):
        out = agent._create_file({
            "path": "wiki/sources/s.md", "title": "S", "type": "source", "tags": ["news"],
            "body": ("## Summary\n\nA summary.\n\n## Claims\n\n- Zebra first.\n"
                     "- Apple second.\n\n## Entities\n\n- Xi Jinping\n- China\n"
                     "- Donald Trump\n\n## Concepts\n\n- tariffs\n- Inflation\n")})
        self.assertNotIn("Error", out, out[:200])
        disk = self.w.disk("sources/s.md")
        ents = disk.split("## Entities")[1].split("\n##")[0]
        self.assertLess(ents.index("China"), ents.index("Xi Jinping"))
        # Claims keep the article's order.
        claims = disk.split("## Claims")[1].split("\n##")[0]
        self.assertLess(claims.index("Zebra"), claims.index("Apple"))

    def test_heal_pages_sorts_a_page_already_on_disk(self):
        """Source pages are immutable to the LLM, so this is the only route for one that
        was written before the rule existed."""
        self.w.page("sources/s.md", title="S", type="source",
                    body="# S\n\n## Summary\n\nx\n\n## Entities\n\n- Xi Jinping\n- China\n")
        agent.heal_pages()
        disk = self.w.disk("sources/s.md")
        self.assertLess(disk.index("China"), disk.index("Xi Jinping"))

    def test_heal_pages_is_idempotent_on_it(self):
        self.w.page("sources/s.md", title="S", type="source",
                    body="# S\n\n## Summary\n\nx\n\n## Entities\n\n- Xi Jinping\n- China\n")
        agent.heal_pages()
        once = self.w.disk("sources/s.md")
        agent.heal_pages()
        self.assertEqual(self.w.disk("sources/s.md"), once)

    def test_an_already_sorted_page_is_not_rewritten(self):
        self.w.page("sources/s.md", title="S", type="source",
                    body="# S\n\n## Summary\n\nx\n\n## Entities\n\n- Alpha\n- Beta\n")
        before = self.w.disk("sources/s.md")
        agent.heal_pages()
        self.assertEqual(self.w.disk("sources/s.md"), before)

    def test_sorting_survives_the_autolinker(self):
        """The rows get linked after they are sorted; order must not depend on that."""
        self.w.page("entities/china.md", title="China", type="entity",
                    body="# China\n\n## Overview\n\nA country.\n")
        self.w.page("entities/xi-jinping.md", title="Xi Jinping", type="entity",
                    body="# Xi Jinping\n\n## Overview\n\nA leader.\n")
        self.w.page("sources/s.md", title="S", type="source",
                    body="# S\n\n## Summary\n\nx\n\n## Entities\n\n- Xi Jinping\n- China\n")
        agent.heal_pages()
        agent._autolink({"path": "wiki/sources/s.md"})
        disk = self.w.disk("sources/s.md")
        ents = disk.split("## Entities")[1].split("\n##")[0]
        self.assertLess(ents.index("China"), ents.index("Xi Jinping"))


if __name__ == "__main__":
    unittest.main()
