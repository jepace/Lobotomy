"""The name shown for a page comes from its frontmatter, not from its filename.

Every wiki view — the page, the editor, history, the diff, a shared link — used to derive
its heading with `stem.replace("-", " ").title()`. That round-trips only for names that
happen to be plain words. "jd-vance" rendered "Jd Vance", "ai-safety" rendered "Ai
Safety", "u-s-senate" rendered "U S Senate", and "pg-e" rendered "Pg E" for a page whose
title is "Pacific Gas & Electric". The frontmatter title is what the rest of the system
already treats as authoritative: lookup_titles matches it and the autolinker links it.

The slug stays as a fallback, for a page whose frontmatter is missing or damaged — a view
should still render something rather than 500.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

FM = ('---\ntitle: "{t}"\ntype: entity\ntags: []\n'
      'created: 2026-01-01\nupdated: 2026-01-01\nsources: []\n---\n\n## Overview\n\nX.\n')


class DisplayTitleTest(unittest.TestCase):

    def test_frontmatter_title_wins_over_the_slug(self):
        for slug, title in (("jd-vance", "JD Vance"),
                            ("ai-safety", "AI Safety"),
                            ("u-s-senate", "U.S. Senate"),
                            ("pg-e", "Pacific Gas & Electric"),
                            ("lds-church", "LDS Church")):
            with self.subTest(slug=slug):
                self.assertEqual(agent.page_display_title(FM.format(t=title), slug), title)

    def test_slug_is_the_fallback_when_frontmatter_has_no_title(self):
        self.assertEqual(
            agent.page_display_title("---\ntype: entity\n---\n\n## Overview\n\nX.\n",
                                     "duke-hartman"),
            "Duke Hartman")

    def test_no_frontmatter_at_all_still_renders_something(self):
        self.assertEqual(agent.page_display_title("just some text\n", "duke-hartman"),
                         "Duke Hartman")

    def test_empty_everything_does_not_raise(self):
        self.assertEqual(agent.page_display_title("", ""), "")

    def test_quoted_and_unquoted_titles_both_read(self):
        self.assertEqual(
            agent.page_display_title('---\ntitle: JD Vance\n---\n\nX\n', "jd-vance"),
            "JD Vance")

    def test_a_title_in_the_body_is_not_mistaken_for_frontmatter(self):
        # _fm_title scans with MULTILINE, so a body line beginning "title:" could match.
        # The frontmatter one comes first, which is what must win.
        text = FM.format(t="Duke Hartman") + "\nA sentence.\ntitle: Not This One\n"
        self.assertEqual(agent.page_display_title(text, "duke-hartman"), "Duke Hartman")


if __name__ == "__main__":
    unittest.main()
