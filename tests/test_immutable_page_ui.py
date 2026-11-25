"""A page the save route refuses must not offer an editor.

`immutable_page_reason` was hoisted out of `_section_guard` so the web editor and the
agent tools could not disagree about what may be rewritten — a source page is a snapshot
of what an article said, and `log.md`/`index.md` are generated and **not revertable**,
since `_snapshot_version` skips both.

Closing the save route left the door's HANDLE in place. Every wiki page rendered an Edit
button, so a source page handed you a textarea, your edit, and then a refusal — the exact
shape this project has already repaired three times from the other direction (text typed
into a box and lost). The refusal was correct and arrived after the work.

So the rule now holds at all three doors: the agent tools, `wiki_save`, and the button
that leads to it — plus `/edit` itself, because a URL typed by hand must not reach an
editor the save will reject either.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class _Base(unittest.TestCase):
    def setUp(self):
        self.w = TempWiki()
        self.w.__enter__()
        import serve
        self.serve = serve
        self._saved = (serve.WIKI_DIR, serve.RAW_DIR)
        serve.WIKI_DIR, serve.RAW_DIR = self.w.wiki, self.w.raw
        self.c = serve.app.test_client()
        with self.c.session_transaction() as s:
            s["logged_in"] = True
        self.w.page("entities/wsl.md", title="WSL", type="entity",
                    body="# WSL\n\n## Overview\n\nText.\n")
        self.w.page("sources/x-2026-y.md", title="X 2026 Y", type="source",
                    body="# X 2026 Y\n\n## Summary\n\nText.\n")
        (self.w.wiki / "index.md").write_text("---\ntitle: Index\n---\n\n# Index\n",
                                              encoding="utf-8")
        agent._title_map_cache = None

    def tearDown(self):
        self.serve.WIKI_DIR, self.serve.RAW_DIR = self._saved
        self.w.__exit__(None, None, None)


class ImmutablePageUITest(_Base):
    IMMUTABLE = ("sources/x-2026-y.md", "index.md")

    def test_a_writable_page_offers_the_edit_button(self):
        body = self.c.get("/wiki/entities/wsl.md").get_data(as_text=True)
        self.assertIn("/wiki/entities/wsl.md/edit", body)
        self.assertNotIn("Read-only", body)

    def test_an_immutable_page_offers_no_edit_button(self):
        for rel in self.IMMUTABLE:
            with self.subTest(rel=rel):
                body = self.c.get(f"/wiki/{rel}").get_data(as_text=True)
                self.assertNotIn(f"/wiki/{rel}/edit", body,
                                 "the button leads to an editor the save will refuse")
                self.assertIn("Read-only", body)

    def test_the_reason_is_shown_not_just_the_absence(self):
        """A control that is simply missing is a bug report waiting to be filed."""
        body = self.c.get("/wiki/sources/x-2026-y.md").get_data(as_text=True)
        self.assertIn("source page", body.lower())

    def test_the_edit_route_itself_refuses(self):
        for rel in self.IMMUTABLE:
            with self.subTest(rel=rel):
                r = self.c.get(f"/wiki/{rel}/edit")
                self.assertEqual(r.status_code, 403,
                                 "a hand-typed /edit URL still reaches the textarea")

    def test_the_edit_route_still_works_for_a_normal_page(self):
        self.assertEqual(self.c.get("/wiki/entities/wsl.md/edit").status_code, 200)

    def test_the_save_route_and_the_button_share_one_rule(self):
        """Two copies of this rule is how it held on one door and not the other."""
        self.assertIs(self.serve.immutable_page_reason, agent.immutable_page_reason)


if __name__ == "__main__":
    unittest.main()
