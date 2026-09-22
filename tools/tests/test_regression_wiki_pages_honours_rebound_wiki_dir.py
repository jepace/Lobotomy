"""Regression: wiki_pages() used to take `root: Path = WIKI_DIR` as a default argument
value. A default binds once at import time, so every caller that omitted the argument
scanned whatever WIKI_DIR pointed at THEN — silently the real wiki, even inside a test
harness that had already rebound the module global to a throwaway one. Fixed by resolving
`root` at call time. This is also why TempWiki asserts this on every single test's setup
(see harness.py) rather than trusting it here alone — but this file is the test that
would go red if the bug ever came back.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent


class WikiPagesHonoursReboundWikiDir(TempWikiTestCase):

    def test_wiki_pages_with_no_argument_sees_only_the_rebound_wiki(self):
        # A brand new temp wiki has nothing in it yet.
        self.assertEqual(list(agent.wiki_pages()), [])
        p = self.w.page("entities/foo.md", title="Foo", type="entity",
                         body="## Overview\n\nX.\n")
        pages = list(agent.wiki_pages())
        self.assertEqual(pages, [p])

    def test_wiki_pages_never_sees_the_real_repo_wiki(self):
        real_wiki = Path(__file__).resolve().parent.parent.parent / "wiki"
        for p in agent.wiki_pages():
            self.assertNotEqual(p.resolve().parent.parent, real_wiki.resolve(),
                                 f"wiki_pages() returned a page from the real repo: {p}")


if __name__ == "__main__":
    unittest.main()
