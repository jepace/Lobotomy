"""A tag is one string, however the model wrapped it.

Observed on a real page, after a hand repair:

    tags: ["justice-department", `law-enforcement`, `federal-agency`, "drug-policy"]

Backticks are not YAML quoting — the model renders a tag name as code and writes it that
way. The single bad line is not the bug. The bug is that it SPREADS, and the loop needs
all four of these steps, which is why the fix touches all four:

  1. update_file passed the model's tags: line through verbatim. (type: is sanitized
     three lines away, from the "concept}EX_HEAT_CP" episode; tags: never was.)
  2. _collect_tags stripped `"` and `'` but not backticks, so `law-enforcement` entered
     the wiki's canonical tag list as its own tag.
  3. orientation_message() hands that list to every later ingest: "Prefer tags from this
     list where appropriate."
  4. The model copies it verbatim onto the next page.

So one page's markdown habit became the wiki's vocabulary, and repairing a page by hand
could not stop it — every other page carrying the tag fed it straight back on the next
round. That is what these tests pin: not just that a write is cleaned, but that a page
already on disk cannot teach the broken spelling to the next ingest.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class NormTagTest(unittest.TestCase):

    def test_the_wrappers_a_tag_arrives_in_are_stripped(self):
        for raw in ('"law-enforcement"', "`law-enforcement`", "'law-enforcement'",
                    " law-enforcement ", "“law-enforcement”",
                    "‘law-enforcement’", "`law-enforcement`  "):
            self.assertEqual(agent.norm_tag(raw), "law-enforcement", raw)

    def test_case_is_folded(self):
        self.assertEqual(agent.norm_tag("Justice-Department"), "justice-department")

    def test_the_observed_line_parses_to_four_clean_tags(self):
        line = ('tags: ["justice-department", `law-enforcement`, `federal-agency`, '
                '"drug-policy"]')
        self.assertEqual(agent.parse_tags_line(line),
                         ["justice-department", "law-enforcement", "federal-agency",
                          "drug-policy"])

    def test_a_value_without_the_field_name_parses_too(self):
        # serve.py's tag views pass the value alone.
        self.assertEqual(agent.parse_tags_line('["a", `b`]'), ["a", "b"])

    def test_a_colon_inside_a_tag_is_not_a_field_separator(self):
        # Only a leading "tags:" is stripped. Splitting on the first colon would cut this.
        self.assertEqual(agent.parse_tags_line('tags: ["ns:thing"]'), ["ns:thing"])

    def test_duplicates_that_differ_only_in_wrapping_collapse(self):
        self.assertEqual(
            agent.parse_tags_line('tags: ["measles", `measles`, "Measles"]'), ["measles"])

    def test_empty_and_missing_render_as_an_empty_list(self):
        self.assertEqual(agent.render_tags_line([]), "tags: []")
        self.assertEqual(agent.parse_tags_line("tags: []"), [])

    def test_rendering_is_idempotent(self):
        once = agent.render_tags_line(["a", "`b`"])
        self.assertEqual(once, 'tags: ["a", "b"]')
        self.assertEqual(agent.render_tags_line(agent.parse_tags_line(once)), once)


class WritePathTest(TempWikiTestCase):

    def test_create_file_cannot_store_a_backticked_tag(self):
        out = agent._create_file({
            "path": "wiki/entities/dea.md",
            "title": "Drug Enforcement Administration", "type": "entity",
            "tags": ["justice-department", "`law-enforcement`"],
            "body": "## Overview\n\nAn agency.\n"})
        self.assertNotIn("Error", out, out)
        disk = self.w.disk("entities/dea.md")
        self.assertIn('tags: ["justice-department", "law-enforcement"]', disk)
        self.assertNotIn("`", disk.split("---")[1])

    def test_update_file_normalizes_the_tags_line_it_is_given(self):
        """The passthrough that let it reach disk. type: was sanitized right beside it."""
        self.w.page("entities/dea.md", title="DEA", type="entity",
                    body="# DEA\n\n## Overview\n\nAn agency.\n")
        self.w.read("wiki/entities/dea.md")
        out = agent._update_file(
            "wiki/entities/dea.md",
            '---\ntitle: "DEA"\ntype: entity\n'
            'tags: ["justice-department", `law-enforcement`, `federal-agency`]\n'
            'updated: 2026-09-26\nsources: []\n---\n\n# DEA\n\n## Overview\n\nAn agency.\n')
        self.assertNotIn("Error", out, out)
        disk = self.w.disk("entities/dea.md")
        self.assertIn(
            'tags: ["justice-department", "law-enforcement", "federal-agency"]', disk)
        self.assertNotIn("`", disk.split("---")[1])

    def test_a_clean_tags_line_is_left_exactly_alone(self):
        self.w.page("entities/dea.md", title="DEA", type="entity",
                    body="# DEA\n\n## Overview\n\nAn agency.\n")
        self.w.read("wiki/entities/dea.md")
        agent._update_file(
            "wiki/entities/dea.md",
            '---\ntitle: "DEA"\ntype: entity\ntags: ["justice-department"]\n'
            'updated: 2026-09-26\nsources: []\n---\n\n# DEA\n\n## Overview\n\nA.\n')
        self.assertIn('tags: ["justice-department"]', self.w.disk("entities/dea.md"))


class ItCannotSpreadTest(TempWikiTestCase):
    """Step 2 and 3 of the loop: what a damaged page teaches the next ingest."""

    def _damaged(self):
        self.w.page("entities/dea.md", title="DEA", type="entity",
                    body="# DEA\n\n## Overview\n\nAn agency.\n")
        p = self.w.wiki / "entities" / "dea.md"
        p.write_text(p.read_text().replace(
            "tags: []",
            'tags: ["justice-department", `law-enforcement`, `federal-agency`]'))

    def test_a_damaged_page_on_disk_teaches_only_clean_tags(self):
        self._damaged()
        tags = agent._collect_tags()
        self.assertIn("law-enforcement", tags)
        self.assertNotIn("`law-enforcement`", tags)
        self.assertFalse([t for t in tags if "`" in t], tags)

    def test_the_prompt_the_model_is_given_carries_no_backticks(self):
        """The actual transmission step — a list item here becomes the next page's tag."""
        self._damaged()
        msg = agent.orientation_message()
        self.assertIn("law-enforcement", msg)
        self.assertNotIn("`law-enforcement`", msg)

    def test_the_tag_is_not_counted_twice(self):
        self.w.page("entities/a.md", title="A", type="entity", tags=["law-enforcement"],
                    body="# A\n\nx.\n")
        self._damaged()
        self.assertEqual([t for t in agent._collect_tags() if "law-enforcement" in t],
                         ["law-enforcement"])


class HealTest(TempWikiTestCase):
    """Pages already carrying it must converge on their own — repairing one page by hand
    leaves every other page still feeding the broken spelling back."""

    def _damaged(self, slug="dea"):
        self.w.page(f"entities/{slug}.md", title=slug.upper(), type="entity",
                    body=f"# {slug.upper()}\n\n## Overview\n\nAn agency.\n")
        p = self.w.wiki / "entities" / f"{slug}.md"
        p.write_text(p.read_text().replace(
            "tags: []", 'tags: ["justice-department", `law-enforcement`]'))
        return p

    def test_heal_pages_repairs_the_tags_line(self):
        p = self._damaged()
        agent.heal_pages()
        self.assertIn('tags: ["justice-department", "law-enforcement"]', p.read_text())

    def test_heal_pages_is_idempotent_on_tags(self):
        """heal_pages runs at startup and after every ingest; a normalizer that rewrote
        every pass would fill page history with empty revisions forever."""
        p = self._damaged()
        agent.heal_pages()
        once = p.read_text()
        agent.heal_pages()
        self.assertEqual(p.read_text(), once)

    def test_a_clean_page_is_not_rewritten(self):
        self.w.page("entities/b.md", title="B", type="entity", tags=["law-enforcement"],
                    body="# B\n\n## Overview\n\nx.\n")
        p = self.w.wiki / "entities" / "b.md"
        before = p.read_text()
        agent.heal_pages()
        self.assertEqual(p.read_text(), before)


if __name__ == "__main__":
    unittest.main()
