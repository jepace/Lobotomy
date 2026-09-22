"""_bad_headings / _heading_dupes across all five write paths.

Three structural rules, shared by create_file, update_file, update_section,
append_section and replace_text so they cannot disagree about what is legal:
  - no heading may repeat the page's own title
  - no heading may name a date
  - no heading may contain a markdown link
  - no heading may appear twice on a page

See CLAUDE.md "Write-path guards" for why these are checked as a DELTA on update paths:
only violations the edit introduces are refused, never the whole resulting page.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent


class BadHeadingsUnit(unittest.TestCase):
    """Direct tests of _bad_headings — the matrix from docs/test-plan.md section 5."""

    def test_title_repeat_refused(self):
        bad = agent._bad_headings("## Cybersecurity\n\ntext\n", "Cybersecurity")
        self.assertEqual([r for _, r in bad], ["repeats the page's own title"])

    def test_own_h1_accepted(self):
        # Level 1 is exempt — that is the page's own H1, not a section.
        bad = agent._bad_headings("# Cybersecurity\n\ntext\n", "Cybersecurity")
        self.assertEqual(bad, [])

    def test_leading_date_refused(self):
        bad = agent._bad_headings("## 2026 Outbreak\n\ntext\n", "Some Page")
        self.assertEqual([r for _, r in bad], ["names a date"])

    def test_heading_link_refused(self):
        bad = agent._bad_headings(
            "## [Atheism](../sources/a.md)\n\ntext\n", "Some Page")
        self.assertEqual([r for _, r in bad], ["contains a link"])

    def test_month_name_false_positives_accepted(self):
        for heading in ("## What It May Mean", "## March of the Penguins",
                         "## Origins & History", "## Key Works / Products"):
            with self.subTest(heading=heading):
                bad = agent._bad_headings(f"{heading}\n\ntext\n", "Some Page")
                self.assertEqual(bad, [], f"{heading!r} should not be flagged")

    def test_duplicate_headings_detected(self):
        body = "## Key Policies\n\nA\n\n## Key Policies:\n\nB\n"
        dupes = agent._heading_dupes(body)
        self.assertEqual(dupes, ["Key Policies"])

    def test_subheading_not_a_duplicate(self):
        # '## Foo' and '### Foo' are different levels — a heading and a genuine
        # subheading, not a collision.
        body = "## Foo\n\nA\n\n### Foo\n\nB\n"
        self.assertEqual(agent._heading_dupes(body), [])


# Bad headings to try to introduce, and a substring of the refusal that identifies why.
_BAD_HEADINGS = [
    ("repeats title", "## Widgets", "repeats the page title"),
    ("names a date", "## 2026 Widget Recall", "names a date"),
    ("contains a link", "## [Widgets](../entities/widgets.md)", "contains a markdown link"),
]


class HeadingRuleAcrossWritePaths(TempWikiTestCase):
    """'Each rule, once per write path | refused in all five' from the test plan."""

    def _base_page(self):
        return self.w.page(
            "entities/widgets.md", title="Widgets", type="entity",
            body="## Overview\n\nWidgets are things that exist.\n\n"
                 "## Background\n\nSome background text about widgets.\n")

    def test_create_file_refuses_each_bad_heading(self):
        for name, heading, reason in _BAD_HEADINGS:
            with self.subTest(rule=name):
                path = f"wiki/entities/create-{name.split()[0]}.md"
                result = agent._create_file({
                    "path": path, "title": "Widgets", "type": "entity",
                    "body": f"## Overview\n\nStuff.\n\n{heading}\n\nMore stuff.\n",
                })
                self.assertTrue(result.startswith("Error:"), result)
                self.assertIn(reason, result)
                self.assertFalse((self.w.root / path).exists())

    def test_update_file_refuses_each_bad_heading(self):
        for name, heading, reason in _BAD_HEADINGS:
            with self.subTest(rule=name):
                p = self._base_page()
                self.w.read("wiki/entities/widgets.md")
                before = p.read_text(encoding="utf-8")
                body_only = before.split("---\n", 2)[2]
                new_body = body_only + f"\n{heading}\n\nExtra material.\n"
                result = agent._update_file("wiki/entities/widgets.md", new_body)
                self.assertTrue(result.startswith("Error:"), result)
                self.assertIn(reason, result)
                self.assertEqual(p.read_text(encoding="utf-8"), before)

    def test_update_section_refuses_each_bad_heading(self):
        # "contains a link" is excluded here: update_section runs the content through
        # _strip_broken_wiki_links (the LLM never writes markdown links by hand; the
        # autolinker adds them back) BEFORE the heading check, so a hand-written link
        # inside the new heading is already gone by the time _bad_headings looks at it —
        # see test_link_in_new_heading_is_stripped_not_refused below for that behavior.
        for name, heading, reason in _BAD_HEADINGS:
            if name == "contains a link":
                continue
            with self.subTest(rule=name):
                p = self._base_page()
                self.w.read_section("wiki/entities/widgets.md", "Background")
                before = p.read_text(encoding="utf-8")
                result = agent._update_section({
                    "path": "wiki/entities/widgets.md", "section": "Background",
                    "content": f"Revised background.\n\n{heading}\n\nExtra.\n",
                })
                self.assertTrue(result.startswith("Error:"), result)
                self.assertIn(reason, result)
                self.assertEqual(p.read_text(encoding="utf-8"), before)

    def test_update_section_link_in_new_heading_is_stripped_not_refused(self):
        # A link that does not collide with the title or a date, once stripped to its
        # display text, is just an ordinary new heading — accepted.
        self._base_page()
        self.w.read_section("wiki/entities/widgets.md", "Background")
        result = agent._update_section({
            "path": "wiki/entities/widgets.md", "section": "Background",
            "content": "Revised background.\n\n"
                       "## [Notable Recalls](../entities/widgets.md)\n\nExtra.\n",
        })
        self.assertFalse(result.startswith("Error:"), result)
        disk = self.w.disk("entities/widgets.md")
        self.assertIn("## Notable Recalls", disk)
        self.assertNotIn("](../entities/widgets.md)", disk.split("## Sources")[0])

    def test_append_section_refuses_each_bad_heading(self):
        for name, heading, reason in _BAD_HEADINGS:
            with self.subTest(rule=name):
                p = self._base_page()
                section_name = heading.lstrip("#").strip()
                # Strip markdown-link syntax so the *section name itself* is the thing
                # under test, matching the "contains a link" case too.
                result = agent._append_section({
                    "path": "wiki/entities/widgets.md", "section": section_name,
                    "text": "Some new material for this section.",
                })
                self.assertTrue(result.startswith("Error:"), result)
                self.assertIn(reason, result)

    def test_replace_text_refuses_each_bad_heading(self):
        for name, heading, reason in _BAD_HEADINGS:
            with self.subTest(rule=name):
                p = self._base_page()
                before = p.read_text(encoding="utf-8")
                result = agent._replace_text({
                    "path": "wiki/entities/widgets.md",
                    "old_text": "Some background text about widgets.",
                    "new_text": f"Some background text about widgets.\n\n{heading}\n\nMore.\n",
                })
                self.assertTrue(result.startswith("Error:"), result)
                self.assertIn(reason, result)
                self.assertEqual(p.read_text(encoding="utf-8"), before)


class HeadingRuleDelta(TempWikiTestCase):
    """update paths refuse only what the edit INTRODUCES — the scar from the 176-page
    deadlock: an earlier guard checked the whole resulting page and refused every edit
    that could have fixed a page already carrying a violation."""

    def _page_with_preexisting_bad_heading(self):
        return self.w.page(
            "entities/widgets.md", title="Widgets", type="entity",
            body="## Overview\n\nWidgets are things.\n\n"
                 "## Legacy Notes (2025)\n\nOld notes from last year.\n")

    def test_preexisting_dated_heading_untouched_by_unrelated_edit(self):
        p = self._page_with_preexisting_bad_heading()
        self.w.read("wiki/entities/widgets.md")
        before = p.read_text(encoding="utf-8")
        body_only = before.split("---\n", 2)[2]
        new_body = body_only.replace(
            "Widgets are things.", "Widgets are things that people use every day.")
        result = agent._update_file("wiki/entities/widgets.md", new_body)
        self.assertFalse(result.startswith("Error:"), result)
        self.assertIn("## Legacy Notes (2025)", p.read_text(encoding="utf-8"))

    def test_edit_removing_bad_heading_accepted(self):
        p = self._page_with_preexisting_bad_heading()
        self.w.read("wiki/entities/widgets.md")
        new_body = ("## Overview\n\nWidgets are things. Old notes from last year "
                    "have been folded in here.\n")
        result = agent._update_file("wiki/entities/widgets.md", new_body)
        self.assertFalse(result.startswith("Error:"), result)
        self.assertNotIn("Legacy Notes", p.read_text(encoding="utf-8"))

    def test_edit_introducing_second_bad_heading_names_only_the_new_one(self):
        p = self._page_with_preexisting_bad_heading()
        self.w.read("wiki/entities/widgets.md")
        before = p.read_text(encoding="utf-8")
        body_only = before.split("---\n", 2)[2]
        new_body = body_only + "\n## 2026 Widget Recall\n\nA new recall happened.\n"
        result = agent._update_file("wiki/entities/widgets.md", new_body)
        self.assertTrue(result.startswith("Error:"), result)
        self.assertIn("2026 Widget Recall", result)
        self.assertNotIn("Legacy Notes", result)
        self.assertEqual(p.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
