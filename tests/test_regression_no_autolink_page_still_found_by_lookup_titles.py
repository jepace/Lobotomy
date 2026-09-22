"""Regression: pages with no_autolink: true were once dropped from the title map entirely,
which meant lookup_titles (and the autolinker's own "does a page exist" reasoning) could no
longer see them — the agent would be told no page existed for a name that in fact had one,
and create a duplicate. Fixed by keeping no_autolink pages IN the title map; only _autolink
itself consults the no_autolink set, to decline to add new links TO the page, never to hide
its existence.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class NoAutolinkPageStillFoundByLookupTitles(TempWikiTestCase):

    def test_lookup_titles_finds_a_no_autolink_page(self):
        self.w.page("entities/quiet.md", title="Quiet Page", type="entity",
                     no_autolink=True, body="## Overview\n\nX.\n")
        result = agent._lookup_titles({"names": ["Quiet Page"]})
        self.assertIn("UPDATE", result)
        self.assertIn("entities/quiet.md", result)
        self.assertNotIn("CREATE", result)

    def test_no_autolink_page_resolvable_by_name(self):
        # _resolve_page is what lookup_titles (and the agent's create-vs-update decision)
        # actually calls — a page dropped from the map here is a page that gets duplicated.
        self.w.page("entities/quiet.md", title="Quiet Page", type="entity",
                     no_autolink=True, body="## Overview\n\nX.\n")
        by_key = {t.lower(): rel for t, rel in agent._build_title_map()}
        self.assertEqual(agent._resolve_page("Quiet Page", by_key), "entities/quiet.md")


if __name__ == "__main__":
    unittest.main()
