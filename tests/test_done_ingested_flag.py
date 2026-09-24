"""done() works out for itself whether the raw file was ingested.

Observed: a full 23-round ingest of a Mark Carney interview updated fourteen pages, created
its source page, and finished cleanly — and the article stayed unwikified in the reading
list.

    Tool result [done]: __AGENT_DONE__:0|Updated existing entity and concept pages for…
    on_done: inbox_file=…mark-carney-ca.md but no __ingested__:1 in messages
             — not marking wikified

`ingested` was an optional boolean on done(), and the model simply did not set it. Nothing
in the reply told it so; the only consequence was on a web page it never sees. Every round
of that run was served by the fallback model, which is the kind of variation that turns
"usually remembers" into "did not".

It never needed asking. done() knows the session is processing an inbox file and knows the
source page it established, and the refusals above this point guarantee both — an ingest
that wrote pages without a source page is refused, so by the time the flag is computed
there is nothing left to guess. That is also the schema's own definition of the flag
("true only if a wiki source page was created in this session").

The argument stays, as a backstop, and an explicit true still wins. Same shape as
ensure_h1: derivable from what the code already has, so it is filled in rather than
demanded, and forgetting it costs nothing.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class DoneIngestedFlagTest(TempWikiTestCase):

    def _ingest_session(self, with_source=True):
        """A session in the state the observed run was in at done(): an inbox file, a
        source page, and entity pages updated."""
        agent.init_session(inbox_path="raw/mark-carney.md")
        self.w.page("entities/ukraine.md", title="Ukraine", type="entity",
                    body="# Ukraine\n\n## Overview\n\nA country.\n")
        ctx = agent._ctx()
        ctx._session_updated_pages.append("entities/ukraine.md")
        if with_source:
            self.w.page("sources/carney-2026-interview.md", title="Carney 2026 Interview",
                        type="source", body="# Carney 2026 Interview\n\n## Summary\n\nX.\n")
            ctx._current_source_page = "sources/carney-2026-interview.md"
        return ctx

    def _flag(self, r):
        self.assertTrue(r.startswith(agent._DONE_SENTINEL), r)
        return r[len(agent._DONE_SENTINEL):].partition("|")[0]

    def test_a_real_ingest_reports_one_even_when_the_model_forgets(self):
        self._ingest_session()
        r = agent.TOOL_FNS["done"]({"summary": "Updated existing entity and concept pages."})
        self.assertEqual(self._flag(r), "1",
                         "the article would be left unwikified in the reading list")

    def test_an_explicit_true_still_reports_one(self):
        self._ingest_session()
        r = agent.TOOL_FNS["done"]({"summary": "Done.", "ingested": True})
        self.assertEqual(self._flag(r), "1")

    def test_the_summary_is_untouched(self):
        self._ingest_session()
        r = agent.TOOL_FNS["done"]({"summary": "Updated ten pages."})
        self.assertEqual(r[len(agent._DONE_SENTINEL):].partition("|")[2], "Updated ten pages.")

    def test_a_session_with_no_source_page_reports_zero(self):
        # The fetch_failed / no-article-content case: the model correctly creates no source
        # page, so there is nothing to derive from and the flag must stay 0. (done() itself
        # refuses first here, which is the real protection — this asserts the flag is not
        # fabricated if that refusal is ever passed.)
        ctx = self._ingest_session(with_source=False)
        ctx._done_refusals = 99          # past the refusal cap, so done() goes through
        r = agent.TOOL_FNS["done"]({"summary": "The fetch failed; nothing to ingest."})
        self.assertEqual(self._flag(r), "0",
                         "a failed fetch would be marked wikified")

    def test_a_chat_session_that_is_not_an_ingest_reports_zero(self):
        agent.init_session()             # no inbox_path
        r = agent.TOOL_FNS["done"]({"summary": "Answered a question about the wiki."})
        self.assertEqual(self._flag(r), "0")

    def test_the_flag_is_what_serve_looks_for(self):
        # The contract crosses a module boundary: agent emits __ingested__:<flag> and
        # serve.py's on_done matches on the literal "__ingested__:1".
        self._ingest_session()
        flag = self._flag(agent.TOOL_FNS["done"]({"summary": "Done."}))
        self.assertEqual(f"__ingested__:{flag}", "__ingested__:1")
        serve = (Path(__file__).resolve().parent.parent / "tools" / "serve.py"
                 ).read_text(encoding="utf-8")
        self.assertIn('"__ingested__:1"', serve,
                      "serve.py no longer looks for the string agent emits")


if __name__ == "__main__":
    unittest.main()
