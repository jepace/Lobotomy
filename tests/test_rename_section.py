"""rename_section: collapse a cluster of synonymous headings into one name.

The wiki's section names are its vocabulary and the vocabulary drifts — one idea ends up
under three names and then neither a reader nor a tool can find all of it. The first real
use was "Claims & Positions" (466 pages) + "Positions" (110) + "Key Positions" (25) all
becoming "Positions".

What the tool REFUSES to do is the part worth testing. Renaming into a name the page
already carries is a merge, and merges need judgment, so it applies exactly the ladder
find_duplicate_sections uses — empty, identical, contained — and lists anything else for a
human. Two sections with genuinely different content under one idea are a real merge, and
a tool that guessed would quietly lose text.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

OLD = ["Claims & Positions", "Key Positions"]
NEW = "Positions"


def heads(text):
    return [l for l in text.splitlines() if l.startswith("#")]


class RenameSectionTest(TempWikiTestCase):

    def test_plain_rename(self):
        self.w.page("entities/a.md", title="A", type="entity",
                    body="# A\n\n## Overview\n\nX.\n\n## Claims & Positions\n\nFavors tariffs.\n")
        agent.rename_section(OLD, NEW)
        body = self.w.disk("entities/a.md")
        self.assertIn("## Positions", body)
        self.assertNotIn("Claims & Positions", body)
        self.assertIn("Favors tariffs.", body, "the section's content was lost")

    def test_a_second_synonym_also_renames(self):
        self.w.page("entities/b.md", title="B", type="entity",
                    body="# B\n\n## Key Positions\n\nOpposes tariffs.\n")
        agent.rename_section(OLD, NEW)
        self.assertIn("## Positions", self.w.disk("entities/b.md"))

    def test_matching_ignores_case_ampersand_and_punctuation(self):
        # "Claims and Positions:" must be caught without being listed explicitly.
        self.w.page("entities/c.md", title="C", type="entity",
                    body="# C\n\n## claims and positions:\n\nSomething.\n")
        agent.rename_section(OLD, NEW)
        self.assertIn("## Positions", self.w.disk("entities/c.md"))

    def test_heading_level_is_preserved(self):
        self.w.page("entities/d.md", title="D", type="entity",
                    body="# D\n\n## Background\n\nB.\n\n### Key Positions\n\nNested.\n")
        agent.rename_section(OLD, NEW)
        self.assertIn("### Positions", self.w.disk("entities/d.md"))

    def test_page_already_using_the_new_name_is_untouched(self):
        p = self.w.page("entities/e.md", title="E", type="entity",
                        body="# E\n\n## Positions\n\nFine.\n")
        before = p.read_text(encoding="utf-8")
        r = agent.rename_section(OLD, NEW)
        self.assertEqual(p.read_text(encoding="utf-8"), before)
        self.assertEqual(r["renamed"], [])

    def test_collision_with_an_empty_section_merges(self):
        self.w.page("entities/f.md", title="F", type="entity",
                    body="# F\n\n## Positions\n\nReal content.\n\n## Claims & Positions\n\n")
        r = agent.rename_section(OLD, NEW)
        body = self.w.disk("entities/f.md")
        self.assertEqual(heads(body).count("## Positions"), 1)
        self.assertIn("Real content.", body)
        self.assertTrue(r["merged"])

    def test_collision_with_both_populated_is_left_for_a_human(self):
        p = self.w.page("entities/g.md", title="G", type="entity",
                        body="# G\n\n## Positions\n\nSays A.\n\n"
                             "## Claims & Positions\n\nSays B, differently.\n")
        before = p.read_text(encoding="utf-8")
        r = agent.rename_section(OLD, NEW)
        self.assertEqual(p.read_text(encoding="utf-8"), before,
                         "a real merge was performed without judgment — text can be lost")
        self.assertTrue(any("g.md" in x for x in r["needs_human"]), r)
        self.assertEqual(r["merged"], [])

    def test_dry_run_writes_nothing_but_still_reports(self):
        p = self.w.page("entities/h.md", title="H", type="entity",
                        body="# H\n\n## Claims & Positions\n\nX.\n")
        before = p.read_text(encoding="utf-8")
        r = agent.rename_section(OLD, NEW, dry_run=True)
        self.assertEqual(p.read_text(encoding="utf-8"), before)
        self.assertEqual(len(r["renamed"]), 1)

    def test_other_sections_are_never_touched(self):
        self.w.page("entities/i.md", title="I", type="entity",
                    body="# I\n\n## Overview\n\nO.\n\n## Claims & Positions\n\nP.\n"
                         "\n## Contradictions\n\nC.\n")
        agent.rename_section(OLD, NEW)
        body = self.w.disk("entities/i.md")
        for probe in ("## Overview", "## Contradictions", "O.", "C."):
            self.assertIn(probe, body)

    def test_the_renamed_section_is_reachable_by_the_section_tools(self):
        # The point of the rename: one name the tools and a reader can both find.
        self.w.page("entities/j.md", title="J", type="entity",
                    body="# J\n\n## Overview\n\nO.\n\n## Claims & Positions\n\nFavors tariffs.\n")
        agent.rename_section(OLD, NEW)
        r = agent.TOOL_FNS["read_section"]({"path": "wiki/entities/j.md",
                                            "section": "Positions"})
        self.assertFalse(r.startswith("Error:"), r)
        self.assertIn("Favors tariffs.", r)

    def test_history_entry_recorded(self):
        self.w.page("entities/k.md", title="K", type="entity",
                    body="# K\n\n## Claims & Positions\n\nX.\n")
        agent.rename_section(OLD, NEW)
        revs = [x for x in agent.HISTORY_DIR.rglob("*.md") if x.is_file()]
        self.assertTrue(revs, "the rename is not revertable — no history entry")


if __name__ == "__main__":
    unittest.main()
