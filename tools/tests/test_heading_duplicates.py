"""No page carries the same heading twice — enforced in the write paths, not just known.

test_heading_rules.py covers `_heading_dupes` as a unit: given a body, which headings
repeat. That is necessary and not sufficient. Deleting the `if _dupes:` branch from
create_file left the helper working, every unit test green, and the guard gone — so these
tests drive the five write tools instead of the helper.

The delta rule matters as much as the rule. An earlier version checked the whole resulting
page rather than what the edit introduced, and deadlocked 176 real pages: update_section
rewrites one section and cannot merge a duplicate pair elsewhere, so every edit to a
damaged page was refused, including the edits that would have repaired it.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent

CLEAN = "## Overview\n\nA politician from Ohio.\n\n## Background\n\nRaised in Ohio.\n"
DUPED = ("## Overview\n\nA politician from Ohio.\n\n## Background\n\nEarly life.\n"
         "\n## Background\n\nLater career.\n")


class CreateFileTest(TempWikiTestCase):

    def test_duplicate_heading_refused(self):
        r = agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/jd-vance.md", "title": "JD Vance", "type": "entity",
            "body": DUPED})
        self.assertTrue(r.startswith("Error:"), r)
        self.assertIn("Background", r)
        self.assertFalse(self.w.exists("entities/jd-vance.md"),
                         "refused create_file still wrote the page")

    def test_same_heading_at_different_levels_allowed(self):
        # '## Foo' and '### Foo' are a section and a genuine subsection.
        r = agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/jd-vance.md", "title": "JD Vance", "type": "entity",
            "body": "## Overview\n\nX.\n\n## Background\n\nY.\n\n### Background\n\nZ.\n"})
        self.assertFalse(r.startswith("Error:"), r)

    def test_headings_differing_only_by_trailing_punctuation_collide(self):
        r = agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/jd-vance.md", "title": "JD Vance", "type": "entity",
            "body": "## Overview\n\nX.\n\n## Key Policies\n\nA.\n\n## Key Policies:\n\nB.\n"})
        self.assertTrue(r.startswith("Error:"), r)


class UpdateFileTest(TempWikiTestCase):

    def test_duplicate_introduced_by_rewrite_refused(self):
        p = self.w.page("entities/jd-vance.md", title="JD Vance", type="entity", body=CLEAN)
        before = p.read_text(encoding="utf-8")
        self.w.read("wiki/entities/jd-vance.md")
        r = agent._update_file("wiki/entities/jd-vance.md", DUPED)
        self.assertTrue(r.startswith("Error:"), r)
        self.assertEqual(p.read_text(encoding="utf-8"), before, "refused write changed the file")


class SectionToolsTest(TempWikiTestCase):
    """The delta rule, on a page that already carries a duplicate pair."""

    def setUp(self):
        super().setUp()
        self.p = self.w.page("entities/jd-vance.md", title="JD Vance", type="entity",
                             body=DUPED)
        self.w.read("wiki/entities/jd-vance.md")

    def test_update_section_still_works_on_an_already_damaged_page(self):
        self.w.read_section("wiki/entities/jd-vance.md", "Overview")
        r = agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/jd-vance.md", "section": "Overview",
            "content": "A politician from Ohio, and the sitting Vice President."})
        self.assertFalse(r.startswith("Error:"),
                         f"a pre-existing duplicate elsewhere blocked an unrelated "
                         f"section edit — this is the 176-page deadlock: {r}")

    def test_update_section_content_echoing_its_own_heading_is_absorbed(self):
        # The content is placed UNDER the heading that is already there, so a model that
        # helpfully repeats the heading would produce two. update_section strips the echo
        # instead of refusing — the unambiguous-mistake rule — and the page ends with one.
        self.w.read_section("wiki/entities/jd-vance.md", "Overview")
        r = agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/jd-vance.md", "section": "Overview",
            "content": "## Overview\n\nThe heading is echoed back into the body."})
        self.assertFalse(r.startswith("Error:"), r)
        body = self.w.disk("entities/jd-vance.md")
        self.assertEqual(body.count("## Overview"), 1,
                         "the echoed heading was kept, giving the page two Overviews")
        self.assertIn("The heading is echoed back into the body.", body)

    def test_update_section_content_adding_a_different_existing_heading_refused(self):
        # Echoing its own heading is absorbed; smuggling in a heading the page already has
        # somewhere else is a real duplicate and must be refused.
        self.w.read_section("wiki/entities/jd-vance.md", "Overview")
        before = self.w.disk("entities/jd-vance.md")
        r = agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/jd-vance.md", "section": "Overview",
            "content": "Text.\n\n## Overview\n\nA second Overview smuggled in below."})
        self.assertTrue(r.startswith("Error:"), r)
        self.assertEqual(self.w.disk("entities/jd-vance.md"), before)

    def test_append_section_creating_an_existing_heading_does_not_duplicate_it(self):
        r = agent.TOOL_FNS["append_section"]({
            "path": "wiki/entities/jd-vance.md", "section": "Overview",
            "text": "An additional sentence."})
        self.assertFalse(r.startswith("Error:"), r)
        body = self.w.disk("entities/jd-vance.md")
        self.assertEqual(body.count("\n## Overview"), 1,
                         "append_section created a second Overview instead of appending")

    def test_replace_text_introducing_a_duplicate_refused(self):
        before = self.w.disk("entities/jd-vance.md")
        r = agent.TOOL_FNS["replace_text"]({
            "path": "wiki/entities/jd-vance.md",
            "old_text": "A politician from Ohio.",
            "new_text": "A politician from Ohio.\n\n## Overview\n\nSecond copy."})
        self.assertTrue(r.startswith("Error:"), r)
        self.assertEqual(self.w.disk("entities/jd-vance.md"), before)


if __name__ == "__main__":
    unittest.main()
