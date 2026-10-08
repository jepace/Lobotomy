"""Whether an article is being ingested is the SERVER's answer, not the browser's guess.

Asked *"will clear queue remove those?"* about the batch the Wikify All button had just
queued. It does — `drain()` drops every waiting job, releases its dedupe key and leaves the
running one to finish — **and then the reading list went on saying "Queued" for all of
them until a page reload.**

That was a regression from the commit before: Wikify All now disables every row it queues,
so where one stale row used to be possible there were suddenly thirty. But looking at it
found two older faults of the same shape, and all three have one cause — **the browser was
inferring in-flight state from what it had itself started**:

  * **Clear Queue left every row disabled.** The jobs were gone and the keys released, so
    the server would have accepted every one of them again.
  * **A reload mid-batch showed every queued row as ready to Wikify.** Nothing was
    double-ingested, because the dedupe key refused the click — but the page was wrong
    about thirty articles, and the click it invited did nothing a user could see.
  * **Nothing said WHICH article was being written.** The badge gave a count and the nav
    went busy; the one row in flight looked like the other twenty-nine.

`JobQueue.in_flight()` has held this all along in `_keys` — the dedupe guard's own record —
and nothing could ask. `list_inbox` asks once per listing and every item carries
`ingest: "" | "queued" | "running"`, so a page load, an 8-second poll and a drain all give
the same answer.

**The asymmetry is the bug worth remembering.** Every other branch of `patchItemStatus`
patches a one-way transition — content arrives, a page becomes wikified, neither ever goes
back — so the shape of that function quietly assumed state only moves forwards. In-flight
state moves both ways, and the direction nobody wrote is the one a drain needs.
"""
import queue
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent
import job_queue

TEMPLATE = Path(__file__).resolve().parent.parent / "tools" / "templates" / "inbox.html"


from harness import parked_job_queue as _bare_queue   # one shared fixture; see harness.py


class InFlightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempWiki().__enter__()
        self.addCleanup(self.tmp.__exit__, None, None, None)
        self.q = _bare_queue(self.tmp.root)

    def _submit(self, name):
        return self.q.submit("c", "m", [], "sys", key=f"ingest:{name}")

    def test_a_queued_article_is_reported_queued(self):
        self._submit("a.md")
        self.assertEqual(self.q.in_flight(), {"ingest:a.md": "queued"})

    def test_the_running_article_is_distinguished_from_the_waiting_ones(self):
        """The fact nothing could previously ask for: a count told you thirty were in the
        queue and nothing told you which one was being written."""
        jid, _ = self._submit("a.md")
        self._submit("b.md")
        with self.q._lock:
            self.q._current_job_id = jid
        self.assertEqual(self.q.in_flight(),
                         {"ingest:a.md": "running", "ingest:b.md": "queued"})

    def test_an_empty_queue_reports_nothing(self):
        self.assertEqual(self.q.in_flight(), {})

    def test_a_drained_article_is_no_longer_in_flight(self):
        """**The question that started this.** Clear Queue drops the waiting jobs and
        releases their keys, so the article is free to be queued again — and the listing
        has to say so, or the row stays disabled against a server that would take it."""
        self._submit("a.md")
        self._submit("b.md")
        self.assertEqual(len(self.q.in_flight()), 2)
        self.assertEqual(self.q.drain(), 2)
        self.assertEqual(self.q.in_flight(), {})

    def test_draining_leaves_the_running_article_in_flight(self):
        """`drain` deliberately does not abandon the article being written, so its row must
        keep saying so while the others are freed."""
        jid, _ = self._submit("running.md")
        self._submit("waiting.md")
        with self.q._lock:
            self.q._current_job_id = jid
        # The running job is no longer in the Queue object, which is what drain walks.
        self.q._q.get_nowait()
        self.q.drain()
        self.assertEqual(self.q.in_flight(), {"ingest:running.md": "running"})

    def test_a_duplicate_submit_does_not_double_count(self):
        self._submit("a.md")
        _jid, dup = self._submit("a.md")
        self.assertTrue(dup)
        self.assertEqual(self.q.in_flight(), {"ingest:a.md": "queued"})

    def test_it_is_one_lock_held_dict_copy(self):
        """`/inbox/list` is polled every 8 seconds while the tab is visible, and this is
        called once per listing rather than once per item — the same rule the source-page
        map here learned the hard way. Sized by the queue, never by the wiki."""
        for i in range(50):
            self._submit(f"a{i}.md")
        self.assertEqual(len(self.q.in_flight()), 50)


class ListInboxReportsItTest(unittest.TestCase):
    def test_every_item_carries_the_servers_answer(self):
        with TempWiki() as w:
            import serve
            q = _bare_queue(w.root)
            w.raw_file("queued.md", "---\nurl: \"https://example.com/1\"\n---\n\nOne.\n")
            w.raw_file("idle.md", "---\nurl: \"https://example.com/2\"\n---\n\nTwo.\n")
            q.submit("c", "m", [], "sys", key="ingest:queued.md")

            _old_q, _old_raw = serve.job_queue, serve.RAW_DIR
            serve.job_queue, serve.RAW_DIR = q, agent.RAW_DIR
            try:
                by_name = {i["name"]: i for i in serve.list_inbox()}
            finally:
                serve.job_queue, serve.RAW_DIR = _old_q, _old_raw

            self.assertEqual(by_name["queued.md"]["ingest"], "queued")
            self.assertEqual(by_name["idle.md"]["ingest"], "")

    def test_a_queue_failure_does_not_break_the_listing(self):
        """`/inbox/list` renders the reading list and is on the poll. A queue that cannot
        answer must cost the in-flight column, never the page."""
        with TempWiki() as w:
            import serve

            class _Broken:
                def in_flight(self):
                    raise RuntimeError("queue is unavailable")

            w.raw_file("a.md", "---\nurl: \"https://example.com/1\"\n---\n\nOne.\n")
            _old_q, _old_raw = serve.job_queue, serve.RAW_DIR
            serve.job_queue, serve.RAW_DIR = _Broken(), agent.RAW_DIR
            try:
                items = serve.list_inbox()
            finally:
                serve.job_queue, serve.RAW_DIR = _old_q, _old_raw
            self.assertEqual([i["name"] for i in items], ["a.md"])
            self.assertEqual(items[0]["ingest"], "")


class TemplatePatchesBothDirectionsTest(unittest.TestCase):
    """The asymmetry that caused it. Every other branch of `patchItemStatus` watches a
    one-way transition, so nothing was watching for in-flight going back to idle — which
    is exactly what a drain does."""

    @classmethod
    def setUpClass(cls):
        cls.src = TEMPLATE.read_text(encoding="utf-8")

    def _patch_body(self):
        i = self.src.index("function patchItemStatus(item)")
        return self.src[i:self.src.index("\nfunction markRowWikified", i)]

    def test_the_poll_compares_the_in_flight_field(self):
        self.assertIn('(item.ingest || "") !== (prev.ingest || "")', self._patch_body())

    def test_it_re_enables_a_row_the_queue_has_let_go(self):
        body = self._patch_body()
        self.assertIn("btn.disabled = false", body,
                      "nothing frees a row after a drain; it stays Queued until a reload")
        self.assertIn("'Wikify'", body)

    def test_it_names_the_article_being_written(self):
        self.assertIn("'Working…'", self._patch_body())

    def test_the_local_in_flight_set_follows_the_server(self):
        """`window.wikifying` refuses a second click before the round trip. Left holding a
        name the queue has released, it refuses the click that should now work."""
        body = self._patch_body()
        self.assertIn("window.wikifying.delete(item.name)", body)
        self.assertIn("window.wikifying.add(item.name)", body)

    def test_the_initial_state_is_seeded_from_what_the_server_rendered(self):
        """Seeded from "" instead, every queued row looks like a fresh transition on the
        first poll and gets re-rendered for nothing."""
        i = self.src.index("function initItems()")
        body = self.src[i:self.src.index("})();", i)]
        self.assertIn("ingest:", body)
        self.assertIn("Queued", body)


if __name__ == "__main__":
    unittest.main()
