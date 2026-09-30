"""A fetch-failed raw file has nothing to ingest, so done() accepts it.

Observed deadlock. The raw file was marked `fetch_failed: true`, so there was no article
text. The agent called done() and said so. It was refused with:

    this ingest created a source page but no entity or concept pages

— a source page it had not created and could not create, since there was nothing to write
one from. It called done() again and got the same answer. Two rounds, and the refusal was
making a false statement about what the session had done.

`fetch_failed` was already a recognised case in two other places: `serve.py`'s `on_done`
refuses to mark such an article wikified, and done()'s own `ingested` derivation reports 0
for it. Only the COMPLETENESS guards did not know, and a fetch-failed article has exactly
the shape they look for — no source page, no entity pages, no listed names. There was no
move that satisfied them, which is principle 4's worst case: a guard demanding work that
cannot be performed.

The second half of this module is the refusal's wording. It claimed "created a source page"
unconditionally and then pointed at that page's lists for the next move, so an ingest that
had written nothing was being told about a page it did not have.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class FetchFailedTest(TempWikiTestCase):

    def _raw(self, name, fm_extra="", body="Real article text here.\n"):
        (self.w.raw / name).write_text(
            f'---\ntitle: "T"\nurl: "https://example.com/x"\n{fm_extra}---\n\n{body}',
            encoding="utf-8")
        agent.init_session(inbox_path=f"raw/{name}")

    def test_done_is_accepted(self):
        self._raw("a.md", "fetch_failed: true\n",
                  "<!-- Content could not be fetched -->\n")
        out = agent._done({"summary": "Raw file has fetch_failed; nothing to ingest."})
        self.assertTrue(out.startswith("__AGENT_DONE__:"), out[:200])

    def test_it_reports_not_ingested(self):
        """Nothing was ingested, so serve.py must not mark the article wikified."""
        self._raw("a.md", "fetch_failed: true\n", "<!-- no content -->\n")
        self.assertTrue(agent._done({"summary": "x"}).startswith("__AGENT_DONE__:0"))

    def test_it_is_not_refused_even_once(self):
        """The deadlock cost two rounds before the refusal cap released it."""
        self._raw("a.md", "fetch_failed: true\n", "<!-- no content -->\n")
        agent._done({"summary": "x"})
        self.assertEqual(agent._ctx()._done_refusals, 0)

    def test_quoted_and_odd_spellings_count(self):
        for val in ('fetch_failed: true\n', 'fetch_failed: "true"\n',
                    'fetch_failed: True\n', 'fetch_failed: yes\n'):
            with self.subTest(val):
                self._raw("a.md", val, "<!-- no content -->\n")
                self.assertTrue(
                    agent._done({"summary": "x"}).startswith("__AGENT_DONE__:"), val)

    def test_false_does_not_count(self):
        self._raw("a.md", "fetch_failed: false\n")
        self.assertIn("refused", agent._done({"summary": "x"}))

    def test_a_normal_raw_file_is_still_refused(self):
        """The guard must keep working — this is the case it exists for."""
        self._raw("b.md")
        self.assertIn("refused", agent._done({"summary": "x"}))

    def test_a_missing_raw_file_is_not_treated_as_fetch_failed(self):
        """Absence is not evidence: an unreadable raw file must not open the gate."""
        agent.init_session(inbox_path="raw/nope.md")
        self.assertIn("refused", agent._done({"summary": "x"}))

    def test_a_chat_session_is_unaffected(self):
        """No inbox path at all — none of this applies."""
        agent.init_session()
        self.assertFalse(agent._inbox_fetch_failed(agent._ctx()))


class RefusalDescribesTheSessionTest(TempWikiTestCase):

    def _raw(self):
        (self.w.raw / "b.md").write_text('---\ntitle: "T"\n---\n\nReal text.\n',
                                         encoding="utf-8")
        agent.init_session(inbox_path="raw/b.md")

    def test_with_no_source_page_it_says_so(self):
        self._raw()
        out = agent._done({"summary": "x"})
        self.assertIn("created no pages at all", out)
        self.assertNotIn("the source page you just created", out)

    def test_with_no_source_page_it_names_writing_one_as_the_next_move(self):
        """Principle 4: the refusal has to name a call that works."""
        self._raw()
        out = agent._done({"summary": "x"})
        self.assertIn("create_file", out)
        self.assertIn("wiki/sources/", out)

    def test_with_a_source_page_the_wording_is_unchanged(self):
        self._raw()
        self.w.page("sources/s.md", title="S", type="source",
                    body="# S\n\n## Summary\n\nx\n")
        agent._ctx()._current_source_page = "sources/s.md"
        out = agent._done({"summary": "x"})
        self.assertIn("created a source page but no entity or concept pages", out)
        self.assertIn("in the source page you just created", out)


if __name__ == "__main__":
    unittest.main()
