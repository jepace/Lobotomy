"""The history of a page that no longer exists.

`merge_page` deletes the loser, and it now snapshots the loser's final content first —
without that, a merge that carried the wrong lines lost the difference permanently, since
`loser.unlink()` walks past `_atomic_write` and therefore past `_snapshot_version`. The
route that performs a merge over MCP hands back `/wiki/<loser>/history` as where that
content lives.

**And that URL 404'd.** `_resolve_wiki_page_or_404` aborts on a missing file and
`page_history` read the live page, so the one history that is the only copy of anything
was the one history you could not open. The snapshot was real on disk and unreachable
through the address advertised for it — which turns "a merge is recoverable" into a claim
the UI does not honour, and a backstop nobody can reach is not a backstop.

So: a deleted page resolves for its history, its final content is the newest row, the
diff view works against nothing, and Revert puts the page back. A path with no history at
all still 404s — the existence test moved from the file to the record, it did not go away.
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

        # Two pages about one subject, which is the shape a merge is for.
        self.w.page("entities/acme-co.md", title="Acme Co.", type="entity",
                    body="# Acme Co.\n\n## Overview\n\nAcme Co. makes anvils.\n")
        self.w.page("entities/acme-company.md", title="Acme Company", type="entity",
                    body="# Acme Company\n\n## Overview\n\nAcme Co. makes anvils.\n")
        agent._title_map_cache = None

    def tearDown(self):
        self.serve.WIKI_DIR, self.serve.RAW_DIR = self._saved
        self.w.__exit__(None, None, None)

    def _merge(self):
        r = agent.merge_page("wiki/entities/acme-company.md",
                             "wiki/entities/acme-co.md", dry_run=False)
        self.assertFalse(r.get("error"), r)
        self.assertFalse((self.w.wiki / "entities/acme-company.md").exists())
        return r


class DeletedPageHistoryTest(_Base):
    def test_the_merge_snapshots_the_losers_final_content(self):
        self._merge()
        d = agent.HISTORY_DIR / "entities/acme-company.md"
        revs = sorted(d.glob("*.md"))
        self.assertTrue(revs, "the deleted page left no revision at all")
        self.assertIn("Acme Company", revs[-1].read_text(encoding="utf-8"),
                      "the newest revision is not the page's final content")
        self.assertIn("merge", revs[-1].stem)

    def test_page_history_reports_a_deleted_page(self):
        self._merge()
        rows = agent.page_history(self.w.wiki / "entities/acme-company.md")
        self.assertTrue(rows, "page_history returned nothing for a deleted page")
        self.assertTrue(rows[0]["current"])
        self.assertTrue(rows[0]["deleted"],
                        "the current row does not say the page is gone")
        self.assertEqual(rows[0]["size"], 0)
        # Every word it said was removed by the write that deleted it.
        self.assertGreater(rows[0]["removed"], 0)
        self.assertEqual(rows[0]["added"], 0)

    def test_a_live_page_is_not_marked_deleted(self):
        rows = agent.page_history(self.w.wiki / "entities/acme-co.md")
        self.assertTrue(rows)
        self.assertFalse(rows[0].get("deleted"))
        self.assertGreater(rows[0]["size"], 0)

    def test_page_history_of_nothing_at_all_is_empty(self):
        # No file and no revisions: there is no page to report on, and inventing a row
        # would make a typo in a URL look like a deleted page.
        self.assertEqual(agent.page_history(self.w.wiki / "entities/nope.md"), [])

    def test_the_history_route_opens_for_a_deleted_page(self):
        self._merge()
        r = self.c.get("/wiki/entities/acme-company.md/history")
        self.assertEqual(r.status_code, 200,
                         "the URL the merge route advertises still 404s")
        body = r.get_data(as_text=True)
        self.assertIn("no longer exists", body)
        self.assertIn("Deleted", body)
        # Its title comes from the last revision's frontmatter, not from the filename.
        self.assertIn("Acme Company", body)

    def test_the_deleted_page_itself_still_404s(self):
        # Only the HISTORY tolerates a missing page. The page view, the editor and the
        # preview must keep saying it is gone.
        self._merge()
        for url in ("/wiki/entities/acme-company.md",
                    "/api/wiki/entities/acme-company.md/preview"):
            with self.subTest(url=url):
                self.assertEqual(self.c.get(url).status_code, 404)

    def test_a_path_with_no_history_404s(self):
        r = self.c.get("/wiki/entities/never-existed.md/history")
        self.assertEqual(r.status_code, 404,
                         "the existence check was dropped rather than moved")

    def test_the_diff_view_works_against_a_deleted_page(self):
        self._merge()
        rows = agent.page_history(self.w.wiki / "entities/acme-company.md")
        rev = [r for r in rows if r["id"]][0]["id"]
        r = self.c.get(f"/wiki/entities/acme-company.md/history/{rev}")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertNotIn("identical to the current page", body)

    def test_revert_restores_a_deleted_page(self):
        self._merge()
        rows = agent.page_history(self.w.wiki / "entities/acme-company.md")
        rev = [r for r in rows if r["id"]][0]["id"]
        r = self.c.post(f"/api/wiki/entities/acme-company.md/revert/{rev}")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        p = self.w.wiki / "entities/acme-company.md"
        self.assertTrue(p.exists(), "revert did not bring the page back")
        self.assertIn("Acme Company", p.read_text(encoding="utf-8"))

    def test_path_traversal_through_the_rev_name_is_still_refused(self):
        self._merge()
        r = self.c.get("/wiki/entities/acme-company.md/history/..%2f..%2fetc%2fpasswd")
        self.assertIn(r.status_code, (404, 400))


class HistoryRootIsResolvedAtCallTimeTest(_Base):
    def test_the_history_dir_follows_a_rebound_agent_global(self):
        """`from agent import HISTORY_DIR` binds the VALUE, and this now decides a 404.

        Capturing it was harmless while it only built a path to read; a stale base would
        now report "no history" for a page that has some — and every test in this file
        would pass against the real tree while proving nothing.
        """
        p = self.w.wiki / "entities/acme-co.md"
        got = self.serve._history_dir_for(p)
        self.assertEqual(got, agent.HISTORY_DIR / "entities/acme-co.md")
        self.assertTrue(str(got).startswith(str(self.w.wiki)),
                        f"{got} is outside the temp wiki — a captured path")


if __name__ == "__main__":
    unittest.main()
