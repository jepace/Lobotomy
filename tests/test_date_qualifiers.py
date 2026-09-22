"""_absorb_date_qualifiers — docs/test-plan.md section 5.

Trailing date qualifiers ("Fiscal Challenges (2026)") are dropped so the standing heading
survives; a leading date ("2026 Outbreak") names the event itself and is refused instead,
pointed at add_timeline_entry.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class AbsorbUnit(unittest.TestCase):
    """Direct tests of _absorb_date_qualifiers for the exact strip shapes in the spec."""

    def _stripped(self, heading, title=""):
        body = f"{heading}\n\ntext\n"
        new_body, renamed, collisions = agent._absorb_date_qualifiers(body, "", title)
        return new_body.splitlines()[0], renamed, collisions

    def test_parenthetical_year_stripped(self):
        after, renamed, _ = self._stripped("## Fiscal Challenges (2026)")
        self.assertEqual(after, "## Fiscal Challenges")
        self.assertEqual(renamed, [("Fiscal Challenges (2026)", "Fiscal Challenges")])

    def test_in_year_stripped(self):
        after, _, _ = self._stripped("## Role and Context in 2026")
        self.assertEqual(after, "## Role and Context")

    def test_as_of_stripped(self):
        after, _, _ = self._stripped("## Status of the Primary Race (As of June 2026)")
        self.assertEqual(after, "## Status of the Primary Race")

    def test_en_dash_range_stripped(self):
        after, _, _ = self._stripped("## Political Discourse (2017–2026)")
        self.assertEqual(after, "## Political Discourse")

    def test_leading_dates_refused_nothing_to_strip(self):
        for heading in ("## 2026 Outbreak", "## 1976 Chowchilla Kidnapping",
                         "## 2026 Forecast"):
            with self.subTest(heading=heading):
                after, renamed, _ = self._stripped(heading)
                self.assertEqual(after, heading)
                self.assertEqual(renamed, [])

    def test_collision_with_existing_section_reports_merge_target(self):
        body = "## Context\n\nOld.\n\n## Context (2026)\n\nNew.\n"
        new_body, renamed, collisions = agent._absorb_date_qualifiers(body, "", "Some Page")
        self.assertEqual(renamed, [])
        self.assertEqual(collisions, [("Context (2026)", ("section", "Context"))])

    def test_collision_with_title_reports_title(self):
        body = "## Cybersecurity in 2026\n\ntext\n"
        _, renamed, collisions = agent._absorb_date_qualifiers(body, "", "Cybersecurity")
        self.assertEqual(renamed, [])
        self.assertEqual(collisions, [("Cybersecurity in 2026", ("title", "Cybersecurity"))])

    def test_non_colliding_heading_on_same_page_still_absorbed(self):
        # Proves the above is about the TITLE specifically, not the shape of the heading.
        after, renamed, collisions = self._stripped(
            "## Threat Landscape in 2026", title="Cybersecurity")
        self.assertEqual(after, "## Threat Landscape")
        self.assertEqual(collisions, [])


class AbsorptionAcrossWritePaths(TempWikiTestCase):

    def _base_page(self):
        return self.w.page(
            "entities/widgets.md", title="Widgets", type="entity",
            body="## Overview\n\nWidgets are things.\n\n"
                 "## Background\n\nSome background text about widgets.\n")

    def test_create_file_absorbs_trailing_date(self):
        result = agent._create_file({
            "path": "wiki/entities/foo.md", "title": "Foo", "type": "entity",
            "body": "## Overview\n\nX.\n\n## Fiscal Challenges (2026)\n\nY.\n",
        })
        self.assertFalse(result.startswith("Error:"), result)
        disk = self.w.disk("entities/foo.md")
        self.assertIn("## Fiscal Challenges\n", disk)
        self.assertNotIn("(2026)", disk)

    def test_update_file_absorbs_trailing_date(self):
        p = self._base_page()
        self.w.read("wiki/entities/widgets.md")
        before = p.read_text(encoding="utf-8")
        body_only = before.split("---\n", 2)[2]
        new_body = body_only + "\n## Fiscal Challenges (2026)\n\nNew material.\n"
        result = agent._update_file("wiki/entities/widgets.md", new_body)
        self.assertFalse(result.startswith("Error:"), result)
        disk = p.read_text(encoding="utf-8")
        self.assertIn("## Fiscal Challenges\n", disk)
        self.assertNotIn("(2026)", disk)

    def test_update_section_absorbs_trailing_date(self):
        self._base_page()
        self.w.read_section("wiki/entities/widgets.md", "Background")
        result = agent._update_section({
            "path": "wiki/entities/widgets.md", "section": "Background",
            "content": "Revised.\n\n## Fiscal Challenges (2026)\n\nExtra.\n",
        })
        self.assertFalse(result.startswith("Error:"), result)
        disk = self.w.disk("entities/widgets.md")
        self.assertIn("## Fiscal Challenges\n", disk)
        self.assertNotIn("(2026)", disk)

    def test_append_section_absorbs_trailing_date(self):
        self._base_page()
        result = agent._append_section({
            "path": "wiki/entities/widgets.md", "section": "Fiscal Challenges (2026)",
            "text": "New material about fiscal challenges.",
        })
        self.assertFalse(result.startswith("Error:"), result)
        disk = self.w.disk("entities/widgets.md")
        self.assertIn("## Fiscal Challenges\n", disk)
        self.assertNotIn("(2026)", disk)

    def test_replace_text_absorbs_trailing_date(self):
        p = self._base_page()
        result = agent._replace_text({
            "path": "wiki/entities/widgets.md",
            "old_text": "Some background text about widgets.",
            "new_text": "Some background text about widgets.\n\n"
                       "## Fiscal Challenges (2026)\n\nMore.\n",
        })
        self.assertFalse(result.startswith("Error:"), result)
        disk = p.read_text(encoding="utf-8")
        self.assertIn("## Fiscal Challenges\n", disk)
        self.assertNotIn("(2026)", disk)

    def test_preexisting_dated_heading_left_alone_by_unrelated_edit(self):
        p = self.w.page(
            "entities/widgets.md", title="Widgets", type="entity",
            body="## Overview\n\nWidgets are things.\n\n"
                 "## Legacy Notes (2025)\n\nOld notes.\n")
        self.w.read("wiki/entities/widgets.md")
        before = p.read_text(encoding="utf-8")
        body_only = before.split("---\n", 2)[2]
        new_body = body_only.replace("Widgets are things.",
                                      "Widgets are things people use daily.")
        result = agent._update_file("wiki/entities/widgets.md", new_body)
        self.assertFalse(result.startswith("Error:"), result)
        self.assertIn("## Legacy Notes (2025)", p.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
