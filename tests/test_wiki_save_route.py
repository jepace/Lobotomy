"""Editing a wiki page through the web editor.

**There was no test for this at all.** 808 tests, and `/api/wiki/<path>/save` — the route
behind the Edit button on every wiki page — was never once called by the suite. It was
found the way such things are found: a user hit `Error: JSON.parse: unexpected character at
line 1 column 1 of the JSON data` while editing a page, which is what a non-JSON reply looks
like to a bare `resp.json()`.

So this module covers the route itself, not just the error path: that a save lands on disk,
that it is recorded in history under the right reason, that the autolinker runs over the
result, that a bad path is refused, and that a failure answers in JSON rather than an HTML
error page the caller cannot read.

Two behaviours here are easy to break and load-bearing:

  * The write goes through `_atomic_write`, not `p.write_text` — the only thing that keeps
    the in-memory title-map cache honest. A retitle through this editor that bypassed it
    would leave the autolinker matching the old title until something unrelated
    invalidated the cache.
  * `_atomic_write` happens BEFORE the autolink, so a failure in the autolink leaves the
    page saved. That is deliberate — losing the user's text to a linking error would be
    much worse — but it means a save can report an error and still have worked, which the
    reply should not deny.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

FM = '---\ntitle: "WSL"\ntype: entity\n---\n\n'


class _Base(unittest.TestCase):
    def setUp(self):
        self.w = TempWiki()
        self.w.__enter__()
        import serve
        self.serve = serve
        # serve.py binds WIKI_DIR/RAW_DIR at import, so a module imported earlier in the
        # suite still points at the real tree. Rebound per test, restored after.
        self._saved = (serve.WIKI_DIR, serve.RAW_DIR)
        serve.WIKI_DIR, serve.RAW_DIR = self.w.wiki, self.w.raw
        self.c = serve.app.test_client()
        with self.c.session_transaction() as s:
            s["logged_in"] = True
        self.w.page("concepts/docker.md", title="Docker", type="concept",
                    body="# Docker\n\n## Definition\n\nx\n")
        self.w.page("entities/wsl.md", title="WSL", type="entity",
                    body="# WSL\n\n## Overview\n\nOld text.\n")
        agent._title_map_cache = None

    def tearDown(self):
        self.serve.WIKI_DIR, self.serve.RAW_DIR = self._saved
        self.w.__exit__(None, None, None)

    def _save(self, body, path="entities/wsl.md"):
        return self.c.post(f"/api/wiki/{path}/save", json={"content": body})

    def _disk(self, rel="entities/wsl.md"):
        return (self.w.wiki / rel).read_text(encoding="utf-8")


class SavingWorksTest(_Base):

    def test_a_save_succeeds(self):
        r = self._save(FM + "# WSL\n\n## Overview\n\nBrand new text.\n")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(r.get_json()["ok"])

    def test_the_text_reaches_disk(self):
        self._save(FM + "# WSL\n\n## Overview\n\nBrand new text.\n")
        self.assertIn("Brand new text.", self._disk())

    def test_the_old_text_is_gone(self):
        self._save(FM + "# WSL\n\n## Overview\n\nBrand new text.\n")
        self.assertNotIn("Old text.", self._disk())

    def test_the_reply_is_json(self):
        """The whole reported failure was a reply that was not."""
        r = self._save(FM + "# WSL\n\n## Overview\n\nx\n")
        self.assertIn("application/json", r.headers.get("Content-Type", ""))

    def test_the_result_is_autolinked(self):
        """Prose typed by hand here would otherwise keep its mentions as plain text until
        some later ingest happened to touch the page for its own reasons."""
        self._save(FM + "# WSL\n\n## Overview\n\nIt runs on docker.\n")
        self.assertIn("../concepts/docker.md", self._disk())

    def test_a_previous_version_is_kept(self):
        self._save(FM + "# WSL\n\n## Overview\n\nBrand new text.\n")
        revs = agent.page_history(self.w.wiki / "entities/wsl.md")
        self.assertTrue(revs)

    def test_the_revision_is_labelled_a_user_edit(self):
        """Not 'ingest' — the history view's whole point is saying what caused a change."""
        self._save(FM + "# WSL\n\n## Overview\n\nBrand new text.\n")
        hist = self.w.wiki / ".history" / "entities" / "wsl.md"
        self.assertTrue(any("user-edit" in p.name for p in hist.iterdir()),
                        [p.name for p in hist.iterdir()])

    def test_the_saved_revision_is_the_text_before_the_edit(self):
        """Both the write and the autolink fall in one history scope, so the recorded
        revision is the page as it was, not the unlinked intermediate."""
        self._save(FM + "# WSL\n\n## Overview\n\nIt runs on docker.\n")
        hist = self.w.wiki / ".history" / "entities" / "wsl.md"
        kept = sorted(hist.iterdir())[-1].read_text(encoding="utf-8")
        self.assertIn("Old text.", kept)

    def test_a_retitle_is_visible_to_the_autolinker(self):
        """The reason this route uses _atomic_write rather than p.write_text: the title
        map is cached in memory, and a bypassing write leaves it matching the old name."""
        self._save('---\ntitle: "Windows Subsystem for Linux"\ntype: entity\n---\n\n'
                   '# Windows Subsystem for Linux\n\n## Overview\n\nx\n')
        titles = [t for t, _rel in agent._build_title_map()]
        self.assertIn("Windows Subsystem for Linux", titles)
        self.assertNotIn("WSL", titles)

    def test_saving_twice_is_fine(self):
        self._save(FM + "# WSL\n\n## Overview\n\nFirst.\n")
        self._save(FM + "# WSL\n\n## Overview\n\nSecond.\n")
        self.assertIn("Second.", self._disk())


class RefusalsTest(_Base):

    def test_a_path_outside_the_wiki_is_refused(self):
        r = self.c.post("/api/wiki/../../etc/passwd/save", json={"content": "x"})
        self.assertIn(r.status_code, (400, 404))
        self.assertNotEqual(r.status_code, 200)

    def test_a_missing_page_is_refused(self):
        r = self._save("x", path="entities/nope.md")
        self.assertEqual(r.status_code, 404)
        self.assertIn("error", r.get_json())

    def test_a_missing_content_field_is_refused(self):
        r = self.c.post("/api/wiki/entities/wsl.md/save", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_a_refusal_is_json_too(self):
        r = self._save("x", path="entities/nope.md")
        self.assertIn("application/json", r.headers.get("Content-Type", ""))

    def test_a_logged_out_save_gets_json_not_a_login_page(self):
        """The first reported form of this bug, on the other editor."""
        c = self.serve.app.test_client()
        r = c.post("/api/wiki/entities/wsl.md/save", json={"content": "x"})
        self.assertEqual(r.status_code, 401)
        self.assertTrue(r.get_json()["login_required"])

    def test_a_logged_out_save_does_not_write(self):
        c = self.serve.app.test_client()
        c.post("/api/wiki/entities/wsl.md/save", json={"content": "WIPED"})
        self.assertNotIn("WIPED", self._disk())


class FailureIsReportedAsJsonTest(_Base):
    """Whatever breaks, the caller must be able to read the answer."""

    def test_a_crash_in_the_write_is_json(self):
        # Patched on SERVE, not on agent: serve.py does `from agent import _atomic_write`,
        # so it holds its own reference and patching agent's does nothing. The same import
        # trap that made serve's tag readers silently fall back to filenames.
        with mock.patch.object(self.serve, "_atomic_write",
                               side_effect=OSError(13, "denied")):
            r = self._save(FM + "x")
        self.assertEqual(r.status_code, 500)
        self.assertIn("application/json", r.headers.get("Content-Type", ""))

    def test_a_crash_in_the_autolinker_still_leaves_the_page_saved(self):
        """_atomic_write runs BEFORE the autolink, deliberately: losing the user's text to
        a linking error would be far worse than a page that is saved but unlinked."""
        with mock.patch.object(agent, "_autolink", side_effect=RuntimeError("boom")):
            self._save(FM + "# WSL\n\n## Overview\n\nKeep me.\n")
        self.assertIn("Keep me.", self._disk())


class EditorContentTest(_Base):
    """Content the editor can legitimately be handed."""

    def test_unicode_survives(self):
        self._save(FM + "# WSL\n\n## Overview\n\nWorks — “quoted” ✓ café.\n")
        self.assertIn("“quoted” ✓ café", self._disk())

    def test_a_code_span_is_not_linked(self):
        self._save(FM + "# WSL\n\n## Overview\n\nRun `docker.exe` now.\n")
        self.assertIn("`docker.exe`", self._disk())
        self.assertNotIn("[docker]", self._disk())

    def test_a_fenced_block_is_not_linked(self):
        self._save(FM + "# WSL\n\n## Overview\n\n```sh\ndocker run\n```\n")
        self.assertNotIn("[docker]", self._disk())

    def test_an_empty_body_is_accepted(self):
        """Clearing the box is a legitimate edit, and history makes it recoverable."""
        r = self._save("")
        self.assertEqual(r.status_code, 200)

    def test_a_large_page_saves(self):
        big = FM + "# WSL\n\n" + "".join(
            f"## S{i}\n\n" + ("docker and words " * 400) + "\n\n" for i in range(12))
        r = self._save(big)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        self.assertIn("## S11", self._disk())


if __name__ == "__main__":
    unittest.main()
