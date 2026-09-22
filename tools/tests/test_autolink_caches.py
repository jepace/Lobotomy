"""Title-map determinism, cache invalidation, and no_autolink — docs/test-plan.md section 5
/ CLAUDE.md "The autolinker": this invalidation logic is a repeat bug source.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent


class TitleMapDeterminism(TempWikiTestCase):

    def test_build_twice_over_same_wiki_is_identical(self):
        # test_regression_title_map_is_deterministic.py covers the historical bug in
        # detail; this is the same property checked here as an ordinary cache test.
        for i in range(20):
            self.w.page(f"entities/page-{i:02d}.md", title=f"Page {i:02d}", type="entity",
                         body="## Overview\n\nX.\n")
        agent._title_map_cache = None
        first = agent._build_title_map()
        agent._title_map_cache = None
        second = agent._build_title_map()
        self.assertEqual(first, second)


class CacheInvalidation(TempWikiTestCase):

    def test_safe_write_keeps_cache(self):
        self.w.page("entities/foo.md", title="Foo", type="entity", body="## Overview\n\nX.\n")
        agent._build_title_map()
        cache_before = agent._title_map_cache
        self.w.read("wiki/entities/foo.md")
        agent._update_file("wiki/entities/foo.md", "## Overview\n\nX revised.\n")
        # Body-only write: title/aliases/no_autolink unchanged, so the cache object is
        # the exact same list, not merely an equal one.
        self.assertIs(agent._title_map_cache, cache_before)

    def test_title_change_invalidates(self):
        p = self.w.page("entities/foo.md", title="Foo", type="entity",
                         body="## Overview\n\nX.\n")
        agent._build_title_map()
        self.w.read("wiki/entities/foo.md")
        new_content = p.read_text(encoding="utf-8").replace('title: "Foo"', 'title: "Bar"')
        agent._update_file("wiki/entities/foo.md", new_content)
        # _atomic_write invalidated the cache, and _autolink_now (called by update_file
        # right after) already rebuilt it once — so the interesting assertion is that the
        # rebuilt map reflects the rename, not that the cache is literally still None.
        titles = {t for t, _ in agent._build_title_map()}
        self.assertIn("Bar", titles)
        self.assertNotIn("Foo", titles)

    def test_bypass_write_is_detected(self):
        p = self.w.page("entities/foo.md", title="Foo", type="entity",
                         body="## Overview\n\nX.\n")
        agent._build_title_map()
        # A write that does NOT go through _atomic_write — the backstop this cache
        # relies on to catch drift from another process or a hand edit.
        import time
        time.sleep(0.01)
        p.write_text(p.read_text(encoding="utf-8").replace('title: "Foo"', 'title: "Baz"'),
                     encoding="utf-8")
        import os
        t = time.time() + 5
        os.utime(p, (t, t))
        titles = {t for t, _ in agent._build_title_map()}
        self.assertIn("Baz", titles)
        self.assertNotIn("Foo", titles)

    def test_safe_write_does_not_mask_concurrent_bypass(self):
        # A vetted (body-only) write to page A must not vouch for an unrelated bypass
        # write to page B that landed just before it — only page A's own mtime is
        # recorded as vetted.
        a = self.w.page("entities/a.md", title="Alpha", type="entity", body="## Overview\n\nX.\n")
        b = self.w.page("entities/b.md", title="Beta", type="entity", body="## Overview\n\nY.\n")
        agent._build_title_map()

        import time, os
        time.sleep(0.01)
        b.write_text(b.read_text(encoding="utf-8").replace('title: "Beta"', 'title: "Gamma"'),
                     encoding="utf-8")
        t = time.time() + 5
        os.utime(b, (t, t))

        self.w.read("wiki/entities/a.md")
        agent._update_file("wiki/entities/a.md", "## Overview\n\nX revised.\n")

        titles = {t for t, _ in agent._build_title_map()}
        self.assertIn("Gamma", titles)
        self.assertNotIn("Beta", titles)


class NoAutolinkTest(TempWikiTestCase):

    def test_no_autolink_page_still_in_title_map(self):
        self.w.page("entities/quiet.md", title="Quiet Page", type="entity",
                     no_autolink=True, body="## Overview\n\nX.\n")
        titles = {t.lower() for t, _ in agent._build_title_map()}
        self.assertIn("quiet page", titles)

    def test_no_autolink_excludes_from_linking(self):
        self.w.page("entities/quiet.md", title="Quiet Page", type="entity",
                     no_autolink=True, body="## Overview\n\nX.\n")
        target = self.w.page("entities/other.md", title="Other", type="entity",
                              body="## Overview\n\nMentions Quiet Page here.\n")
        agent._autolink({"path": "wiki/entities/other.md"})
        disk = target.read_text(encoding="utf-8")
        self.assertNotIn("](../entities/quiet.md)", disk)
        self.assertIn("Quiet Page", disk)


if __name__ == "__main__":
    unittest.main()
