"""The non-LLM maintenance passes — docs/test-plan.md section 5:
unlink_headings, heal_pages, update_file's type restore, repair_links.
"""
import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent
import repair_links


class UnlinkHeadingsTest(TempWikiTestCase):

    def test_link_stripped_display_text_kept(self):
        self.w.page("entities/atheism.md", title="Atheism", type="entity",
                     body="## [Atheism](../sources/a.md)\n\nBody text.\n")
        result = agent.unlink_headings()
        self.assertEqual(result["headings"], 1)
        disk = self.w.disk("entities/atheism.md")
        self.assertIn("## Atheism\n", disk)
        self.assertNotIn("](../sources/a.md)", disk.split("## Sources")[0])

    def test_prose_links_untouched(self):
        self.w.page("entities/foo.md", title="Foo", type="entity",
                     body="## Overview\n\nSee [Bar](../entities/bar.md) for more.\n")
        agent.unlink_headings()
        disk = self.w.disk("entities/foo.md")
        self.assertIn("[Bar](../entities/bar.md)", disk)

    def test_history_entry_recorded(self):
        p = self.w.page("entities/atheism.md", title="Atheism", type="entity",
                         body="## [Atheism](../sources/a.md)\n\nBody.\n")
        agent.unlink_headings()
        hist_dir = agent.HISTORY_DIR / "entities" / "atheism.md"
        self.assertTrue(hist_dir.is_dir())
        self.assertEqual(len(list(hist_dir.glob("*.md"))), 1)

    def test_dry_run_writes_nothing(self):
        p = self.w.page("entities/atheism.md", title="Atheism", type="entity",
                         body="## [Atheism](../sources/a.md)\n\nBody.\n")
        before = p.read_text(encoding="utf-8")
        result = agent.unlink_headings(dry_run=True)
        self.assertEqual(result["headings"], 1)
        self.assertEqual(p.read_text(encoding="utf-8"), before)


class HealPagesTest(TempWikiTestCase):

    def test_corrupted_type_repaired(self):
        p = self.w.page("entities/foo.md", title="Foo", type="entity",
                         body="## Overview\n\nX.\n")
        text = p.read_text(encoding="utf-8").replace("type: entity", "type: concept}EX_HEAT_CP")
        p.write_text(text, encoding="utf-8")
        agent.heal_pages()
        disk = self.w.disk("entities/foo.md")
        self.assertRegex(disk, r"(?m)^type: concept\s*$")

    def test_unrecognized_type_reported_not_guessed(self):
        p = self.w.page("entities/foo.md", title="Foo", type="entity",
                         body="## Overview\n\nX.\n")
        text = p.read_text(encoding="utf-8").replace("type: entity", "type: wibble")
        p.write_text(text, encoding="utf-8")
        result = agent.heal_pages()
        self.assertTrue(any("wibble" in m for m in result["manual"]))
        self.assertIn("type: wibble", self.w.disk("entities/foo.md"))

    def test_missing_created_filled_from_mtime(self):
        p = self.w.page("entities/foo.md", title="Foo", type="entity",
                         body="## Overview\n\nX.\n")
        text = p.read_text(encoding="utf-8")
        text = "\n".join(l for l in text.splitlines(keepends=False) if not l.startswith("created:"))
        text = text.replace("---\n\n", "---\n\n") + "\n" if not text.endswith("\n") else text
        p.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
        self.w.touch("entities/foo.md", days_ago=10)
        expected = datetime.date.fromtimestamp(p.stat().st_mtime).isoformat()
        agent.heal_pages()
        disk = self.w.disk("entities/foo.md")
        self.assertIn(f"created: {expected}", disk)

    def test_missing_title_reported_never_invented(self):
        p = self.w.page("entities/foo.md", title="Foo", type="entity",
                         body="## Overview\n\nX.\n")
        text = p.read_text(encoding="utf-8")
        text = "\n".join(l for l in text.splitlines() if not l.startswith("title:"))
        p.write_text(text + "\n", encoding="utf-8")
        result = agent.heal_pages()
        self.assertTrue(any("missing title" in m for m in result["manual"]))
        self.assertNotIn("title:", self.w.disk("entities/foo.md").split("---", 2)[1])


class UpdateFileTypeRestoreTest(TempWikiTestCase):

    def test_corrupted_type_from_caller_overridden_by_disk(self):
        self.w.page("entities/foo.md", title="Foo", type="entity",
                     body="## Overview\n\nX.\n")
        self.w.read("wiki/entities/foo.md")
        bad_content = ('---\ntitle: "Foo"\ntype: not-a-real-type\ntags: []\n'
                       'sources: []\n---\n\n## Overview\n\nX revised.\n')
        result = agent._update_file("wiki/entities/foo.md", bad_content)
        self.assertFalse(result.startswith("Error:"), result)
        disk = self.w.disk("entities/foo.md")
        self.assertIn("type: entity", disk)
        self.assertNotIn("not-a-real-type", disk)


class RepairLinksTest(TempWikiTestCase):

    def test_wrong_relative_path_corrected(self):
        self.w.page("entities/bar.md", title="Bar", type="entity", body="## Overview\n\nX.\n")
        self.w.page("concepts/foo.md", title="Foo", type="concept",
                     body="## Definition\n\nSee [Bar](../../entities/bar.md) for more.\n")
        result = repair_links.repair_links()
        self.assertGreaterEqual(result["fixed_links"], 1)
        disk = self.w.disk("concepts/foo.md")
        self.assertIn("[Bar](../entities/bar.md)", disk)

    def test_reader_url_unwrapped(self):
        real = "https://www.nytimes.com/2026/01/01/example.html"
        wrapped = "about:reader?url=" + real.replace(":", "%3A").replace("/", "%2F")
        self.w.page("sources/article.md", title="Article", type="source", url=wrapped,
                     body="## Summary\n\nS.\n\n## Claims\n\nC.\n\n"
                          "## Entities\n\n- X\n\n## Concepts\n\n- Y\n")
        result = repair_links.repair_links()
        self.assertGreaterEqual(result["fixed_links"], 1)
        disk = self.w.disk("sources/article.md")
        self.assertIn(real, disk)
        self.assertNotIn("about:reader", disk)

    def test_history_entry_recorded(self):
        self.w.page("entities/bar.md", title="Bar", type="entity", body="## Overview\n\nX.\n")
        self.w.page("entities/foo.md", title="Foo", type="entity",
                     body="## Overview\n\nSee [Bar](../../entities/bar.md) for more.\n")
        repair_links.repair_links()
        hist_dir = agent.HISTORY_DIR / "entities" / "foo.md"
        self.assertTrue(hist_dir.is_dir())
        self.assertEqual(len(list(hist_dir.glob("*.md"))), 1)

    def test_repair_links_never_touches_real_repo(self):
        # Sanity check on the fixture other tests here rely on: rebinding
        # agent.WIKI_DIR/RAW_DIR must actually redirect repair_links, since it computes
        # its wiki/raw paths from those globals at call time, not at import time.
        self.assertNotEqual(agent.WIKI_DIR, agent.REPO_ROOT.parent / "wiki")


if __name__ == "__main__":
    unittest.main()
