"""Regression: _build_title_map once scanned with a bare, unsorted glob() and no
tiebreak on equal-length titles. Directory order is not guaranteed stable across a
filesystem's own rewrites, so which of two same-length titles won when they collided (and
therefore which link target a name resolved to) could differ between two builds over the
IDENTICAL set of files. relink_all() never converged: two consecutive whole-wiki runs both
reported hundreds of changes, each undoing part of the other's work. Fixed by sorting file
discovery and adding an explicit tiebreak (title text) to the map's own sort key.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent


class TitleMapIsDeterministic(TempWikiTestCase):

    def test_build_twice_over_identical_wiki_gives_identical_map(self):
        # Many pages, including same-length titles, so an unsorted or untiebroken build
        # would have room to disagree with itself between runs.
        for i in range(30):
            self.w.page(f"entities/page-{i:03d}.md", title=f"Page Number {i:03d}",
                         type="entity", body="## Overview\n\nX.\n")
        # A pair of distinct pages with equal-length titles that could tie under a sort
        # keyed only on length.
        self.w.page("entities/alpha.md", title="Utility Company One", type="entity",
                     body="## Overview\n\nX.\n")
        self.w.page("entities/bravo.md", title="Utility Company Two", type="entity",
                     body="## Overview\n\nX.\n")

        agent._title_map_cache = None
        first = agent._build_title_map()
        agent._title_map_cache = None
        second = agent._build_title_map()
        agent._title_map_cache = None
        third = agent._build_title_map()

        self.assertEqual(first, second)
        self.assertEqual(second, third)

    def test_relink_all_converges_in_one_pass(self):
        # The practical consequence of nondeterminism: relink_all() should make changes on
        # the first pass and report nothing left to do on the second, over an unchanged
        # wiki. If the map's order can flip between runs, the second pass finds "changes"
        # too — the autolinker undoing and redoing its own links.
        self.w.page("entities/widgets.md", title="Widgets", type="entity",
                     body="## Overview\n\nWidgets are things.\n")
        self.w.page("entities/other.md", title="Other Thing", type="entity",
                     body="## Overview\n\nMentions Widgets and Widgets again.\n")

        first = agent.relink_all()
        second = agent.relink_all()
        self.assertEqual(second.get("changed", 0), 0,
                          f"second relink_all pass was not a no-op: {second}")


if __name__ == "__main__":
    unittest.main()
