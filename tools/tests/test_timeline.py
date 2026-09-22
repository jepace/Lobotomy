"""add_timeline_entry — docs/test-plan.md section 5."""
import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent


class TimelineTest(TempWikiTestCase):

    def _event_page(self, body="## Overview\n\nAn unfolding event.\n"):
        return self.w.page("entities/outbreak.md", title="Some Outbreak", type="entity",
                            body=body)

    def _add(self, date, text):
        return agent._add_timeline_entry({
            "path": "wiki/entities/outbreak.md", "date": date, "text": text,
        })

    def _timeline_lines(self):
        disk = self.w.disk("entities/outbreak.md")
        section = disk.split("## Timeline", 1)[1].split("## Sources")[0]
        return [l for l in section.splitlines() if l.strip().startswith(("-", "*"))]

    def test_five_entries_in_scrambled_order_read_chronologically(self):
        self._event_page()
        for date, text in [("2026-03-14", "Fourth event."), ("2026-01-01", "First event."),
                            ("2026-06-01", "Fifth event."), ("2026-02-01", "Second event."),
                            ("2026-02-15", "Third event.")]:
            self._add(date, text)
        lines = self._timeline_lines()
        dates = [l.split("**")[1] for l in lines]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(lines), 5)

    def test_two_entries_same_date_keep_insertion_order(self):
        self._event_page()
        self._add("2026-03-14", "First on this date.")
        self._add("2026-03-14", "Second on this date.")
        lines = self._timeline_lines()
        self.assertIn("First on this date.", lines[0])
        self.assertIn("Second on this date.", lines[1])

    def test_partial_dates_sort_before_precise_ones_they_contain(self):
        self._event_page()
        self._add("2026-03-14", "A specific day.")
        self._add("2026-03", "The whole month.")
        self._add("2026", "The whole year.")
        lines = self._timeline_lines()
        texts = [l.split("—", 1)[1].strip() if "—" in l else l.split("-", 3)[-1]
                 for l in lines]
        self.assertIn("The whole year.", lines[0])
        self.assertIn("The whole month.", lines[1])
        self.assertIn("A specific day.", lines[2])

    def test_duplicate_entry_refused_unchanged(self):
        self._event_page()
        self._add("2026-03-14", "Twelve cases confirmed.")
        before = self.w.disk("entities/outbreak.md")
        result = self._add("2026-03-14", "Twelve cases confirmed.")
        self.assertNotIn("Error:", result)
        self.assertIn("already", result.lower())
        self.assertEqual(self.w.disk("entities/outbreak.md"), before)

    def test_duplicate_detected_even_when_existing_entry_was_autolinked(self):
        # A second page whose title matches a word in the entry, so the autolinker links
        # it — the duplicate check must compare with links stripped, not raw text.
        self.w.page("entities/lancaster-county.md", title="Lancaster County", type="entity",
                     body="## Overview\n\nA county.\n")
        self._event_page()
        self._add("2026-03-14", "Cases confirmed in Lancaster County.")
        disk = self.w.disk("entities/outbreak.md")
        self.assertIn("](../entities/lancaster-county.md)", disk)
        result = self._add("2026-03-14", "Cases confirmed in Lancaster County.")
        self.assertIn("already", result.lower())

    def test_future_date_refused(self):
        self._event_page()
        future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        result = self._add(future, "Something scheduled.")
        self.assertTrue(result.startswith("Error:"), result)
        self.assertNotIn("## Timeline", self.w.disk("entities/outbreak.md"))

    def test_unparseable_date_refused_names_accepted_formats(self):
        self._event_page()
        result = self._add("last Tuesday", "Something happened.")
        self.assertTrue(result.startswith("Error:"), result)
        self.assertIn("YYYY-MM-DD", result)

    def test_whole_bullet_sent_as_text_renders_date_once(self):
        self._event_page()
        self._add("2026-03-14", "- **2026-03-14** — Officials confirm 12 cases.")
        lines = self._timeline_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].count("2026-03-14"), 1)

    def test_out_of_order_timeline_resorts_on_next_insert(self):
        # Hand-write an out-of-order timeline (as if written before this tool existed).
        self._event_page(
            body="## Overview\n\nAn event.\n\n## Timeline\n\n"
                 "- **2026-03-01** — Third.\n- **2026-01-01** — First.\n")
        self._add("2026-02-01", "Second.")
        lines = self._timeline_lines()
        self.assertIn("First.", lines[0])
        self.assertIn("Second.", lines[1])
        self.assertIn("Third.", lines[2])

    def test_prose_above_bullets_preserved(self):
        self._event_page(
            body="## Overview\n\nAn event.\n\n## Timeline\n\n"
                 "This outbreak has been unfolding since early 2026.\n\n"
                 "- **2026-01-01** — First.\n")
        self._add("2026-02-01", "Second.")
        disk = self.w.disk("entities/outbreak.md")
        section = disk.split("## Timeline", 1)[1].split("## Sources")[0]
        self.assertIn("This outbreak has been unfolding since early 2026.", section)
        # Prose stays above the bullets.
        self.assertLess(section.index("unfolding"), section.index("First."))

    def test_target_source_page_refused_immutable(self):
        self.w.page("sources/article.md", title="Article", type="source",
                     body="## Summary\n\nS.\n")
        result = agent._add_timeline_entry({
            "path": "wiki/sources/article.md", "date": "2026-01-01", "text": "Something.",
        })
        self.assertTrue(result.startswith("Error:"), result)
        self.assertIn("immutable", result)

    def test_no_timeline_section_created_before_sources(self):
        self.w.page("entities/outbreak.md", title="Some Outbreak", type="entity",
                     sources=["sources/a.md"],
                     body="## Overview\n\nAn event.\n")
        self._add("2026-01-01", "First reported case.")
        disk = self.w.disk("entities/outbreak.md")
        self.assertIn("## Timeline", disk)
        self.assertLess(disk.index("## Timeline"), disk.index("## Sources"))


if __name__ == "__main__":
    unittest.main()
