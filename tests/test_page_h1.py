"""Every wiki page opens with `# {title}` — its own heading, in the file.

The wiki is plain markdown and the project promises it is viewer-agnostic: readable in
glow, mdcat, GitHub, a text editor. None of those know about `title:` frontmatter, so a
page without an H1 has no name on it anywhere outside the web UI. The web UI renders the
title in its top bar and strips this line, so it is not shown twice there — the file is
canonical, the web view is presentation.

It is filled in rather than demanded. create_file already knows the title, so requiring
the H1 in `body` would be a rule to remember and a refusal to spend when it is forgotten,
for a line that is entirely derivable. update_file restores it for the same reason, and
heal_pages backfills it across the wiki — which, because heal_pages runs at startup and
after every ingest, means pages written before this rule acquire one on their own.

The regex is the part that bit: `#` followed by `[ \\t]*` also matches `## Overview`, so an
early version REPLACED a page's first section heading with the title instead of inserting
an H1 above it. That is what `test_body_opening_with_a_section_keeps_that_section` is for.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class EnsureH1UnitTest(unittest.TestCase):

    def test_inserted_above_an_existing_first_section(self):
        self.assertEqual(agent.ensure_h1("## Overview\n\nX.\n", "Duke Hartman"),
                         "# Duke Hartman\n\n## Overview\n\nX.\n")

    def test_a_matching_h1_is_left_exactly_as_is(self):
        body = "# Duke Hartman\n\n## Overview\n\nX.\n"
        self.assertEqual(agent.ensure_h1(body, "Duke Hartman"), body)

    def test_a_mismatched_h1_is_rewritten_to_the_frontmatter_title(self):
        # title: is authoritative — rename_page.py moves it and the H1 together.
        self.assertEqual(agent.ensure_h1("# Duke H.\n\n## Overview\n\nX.\n", "Duke Hartman"),
                         "# Duke Hartman\n\n## Overview\n\nX.\n")

    def test_an_h1_further_down_the_page_is_not_touched(self):
        body = "## Overview\n\nX.\n\n# Not The Title\n\nY.\n"
        self.assertEqual(agent.ensure_h1(body, "Duke Hartman"),
                         "# Duke Hartman\n\n" + body)

    def test_no_title_means_no_change(self):
        self.assertEqual(agent.ensure_h1("## Overview\n\nX.\n", ""), "## Overview\n\nX.\n")


class WritePathTest(TempWikiTestCase):

    def test_create_file_adds_the_h1(self):
        agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/duke-hartman.md", "title": "Duke Hartman",
            "type": "entity", "body": "## Overview\n\nA former executive.\n"})
        self.assertIn("# Duke Hartman\n", self.w.disk("entities/duke-hartman.md"))

    def test_body_opening_with_a_section_keeps_that_section(self):
        # The regex bug: "## Overview" matched as an H1 and was replaced by the title,
        # silently deleting the opener that create_file had just required.
        agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/duke-hartman.md", "title": "Duke Hartman",
            "type": "entity", "body": "## Overview\n\nA former executive.\n"})
        body = self.w.disk("entities/duke-hartman.md")
        self.assertIn("## Overview", body, "the page's first section heading was eaten")
        self.assertIn("A former executive.", body)

    def test_update_file_restores_an_h1_the_rewrite_dropped(self):
        self.w.page("entities/acme.md", title="Acme Corp", type="entity",
                    body="# Acme Corp\n\n## Overview\n\nA firm.\n")
        self.w.read("wiki/entities/acme.md")
        agent._update_file("wiki/entities/acme.md", "## Overview\n\nA firm, expanded.\n")
        self.assertIn("# Acme Corp\n", self.w.disk("entities/acme.md"))

    def test_the_h1_is_not_a_section_the_tools_can_touch(self):
        # It must not show up as an editable section, or the agent will try to write to it.
        agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/duke-hartman.md", "title": "Duke Hartman",
            "type": "entity", "body": "## Overview\n\nA former executive.\n"})
        r = agent.TOOL_FNS["read_section"]({"path": "wiki/entities/duke-hartman.md",
                                            "section": "Nope"})
        self.assertNotIn("Duke Hartman  (", r)

    def test_the_h1_does_not_trip_the_page_title_heading_rule(self):
        # _bad_headings refuses a SECTION named after the page; level 1 is exempt.
        r = agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/duke-hartman.md", "title": "Duke Hartman",
            "type": "entity", "body": "## Overview\n\nA former executive.\n"})
        self.assertFalse(r.startswith("Error:"), r)


class HealTest(TempWikiTestCase):

    def test_heal_pages_backfills_a_missing_h1(self):
        self.w.page("entities/duke-hartman.md", title="Duke Hartman", type="entity",
                    body="## Overview\n\nA former executive.\n")
        agent.heal_pages()
        body = self.w.disk("entities/duke-hartman.md")
        self.assertIn("# Duke Hartman\n", body)
        self.assertIn("## Overview", body)

    def test_heal_pages_leaves_a_correct_page_alone(self):
        p = self.w.page("entities/acme.md", title="Acme Corp", type="entity",
                        body="# Acme Corp\n\n## Overview\n\nA firm.\n")
        before = p.read_text(encoding="utf-8")
        agent.heal_pages()
        self.assertEqual(p.read_text(encoding="utf-8"), before)

    def test_heal_pages_dry_run_writes_nothing(self):
        p = self.w.page("entities/duke-hartman.md", title="Duke Hartman", type="entity",
                        body="## Overview\n\nX.\n")
        before = p.read_text(encoding="utf-8")
        agent.heal_pages(dry_run=True)
        self.assertEqual(p.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
