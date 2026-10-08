"""One ingest per article, however many times Wikify is clicked.

`submit()` minted a fresh `secrets.token_hex(8)` on every call, so two clicks queued two
full agent turns over the same raw file. The only thing in the way was `btn.disabled = true`
in the browser, which is lost on a reload, absent in a second tab, and reset whenever the
30-second poll re-renders the row.

That is expensive rather than merely untidy. At `max_rpm: 1` an ingest is ~40 minutes, and
the duplicate spends a per-day quota re-deriving pages the first run already wrote — then
folds the same source into them a second time.

`key` makes a job unique while it is waiting or running. A duplicate submit queues nothing
and returns the id of the job already in flight, so the caller attaches to it.

**The release matters more than the guard.** A leaked key makes that article permanently
un-wikifiable, which is worse than the duplicate, so every exit path is pinned here: clean
completion, a crash in the agent, and `drain()` — where the worker's release never fires
because the job never ran.
"""
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki  # noqa: F401  (import for config side effect / path setup)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import job_queue as jq_mod
import serve
import tempfile


# Was a local `_Q` subclass duplicating `JobQueue.__init__`'s attribute list. Every
# attribute added to the real class broke it — and the two other modules doing the same
# thing — with errors about nothing to do with what was being tested.
from harness import parked_job_queue as _Q


class SubmitDedupeTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.q = _Q(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _submit(self, key):
        return self.q.submit(None, "m", [], "sys", key=key)

    def test_a_second_submit_returns_the_first_jobs_id(self):
        first, dup1 = self._submit("ingest:a.md")
        second, dup2 = self._submit("ingest:a.md")
        self.assertFalse(dup1)
        self.assertTrue(dup2)
        self.assertEqual(first, second)

    def test_a_second_submit_queues_nothing(self):
        """The whole point: not one extra agent turn."""
        self._submit("ingest:a.md")
        self._submit("ingest:a.md")
        self._submit("ingest:a.md")
        self.assertEqual(self.q._q.qsize(), 1)

    def test_a_different_article_is_unaffected(self):
        a, _ = self._submit("ingest:a.md")
        b, dup = self._submit("ingest:b.md")
        self.assertFalse(dup)
        self.assertNotEqual(a, b)
        self.assertEqual(self.q._q.qsize(), 2)

    def test_an_unkeyed_submit_is_never_deduplicated(self):
        """Chat messages carry no key. Saying the same thing twice in a conversation is a
        legitimate thing to do, and collapsing it would be a bug of its own."""
        a, dup_a = self.q.submit(None, "m", [], "sys")
        b, dup_b = self.q.submit(None, "m", [], "sys")
        self.assertFalse(dup_a)
        self.assertFalse(dup_b)
        self.assertNotEqual(a, b)
        self.assertEqual(self.q._q.qsize(), 2)

    def test_submit_returns_a_pair(self):
        """Both call sites unpack it; a bare id would be a silent TypeError at runtime."""
        out = self._submit("ingest:a.md")
        self.assertIsInstance(out, tuple)
        self.assertEqual(len(out), 2)

    def test_the_key_is_released_and_the_article_can_run_again(self):
        first, _ = self._submit("ingest:a.md")
        self.q._release_key(first)
        second, dup = self._submit("ingest:a.md")
        self.assertFalse(dup)
        self.assertNotEqual(first, second)

    def test_releasing_a_job_does_not_free_another_jobs_key(self):
        """A second release for a finished job must not unlock the key its successor now
        holds. `_job_keys.pop` is what makes that safe, and it is the only thing that
        needs to — an added `_keys[k] == job_id` test looked like belt-and-braces and was
        unreachable, which `mutate.py` reported as a MISSED guard."""
        first, _ = self._submit("ingest:a.md")
        self.q._release_key(first)
        second, _ = self._submit("ingest:a.md")
        self.q._release_key(first)            # late release from the finished job
        _, dup = self._submit("ingest:a.md")
        self.assertTrue(dup, "the running job's key was freed by a stale release")

    def test_releasing_an_unknown_job_is_quiet(self):
        self.q._release_key("deadbeef")

    def test_drain_releases_the_key(self):
        """A drained job never runs, so the worker's release never fires for it. Without
        the release in drain(), clearing the queue would make every drained article
        un-wikifiable for the life of the process."""
        self._submit("ingest:a.md")
        self.assertEqual(self.q.drain(), 1)
        _, dup = self._submit("ingest:a.md")
        self.assertFalse(dup, "drain() leaked the key")


class WorkerReleaseTest(unittest.TestCase):
    """The real worker thread, because the release lives in its `finally`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _run_one(self, lines, on_done=None):
        """Start a real JobQueue whose agent turn yields `lines`, submit one keyed job,
        wait for it to finish, and return the queue."""
        import agent
        saved = agent.stream_agent_turn

        def fake(client, model, messages, system, stop_event=None):
            for l in lines:
                yield l

        agent.stream_agent_turn = fake
        try:
            q = jq_mod.JobQueue(Path(self.tmp.name))
            jid, dup = q.submit(None, "m", [], "sys", on_done=on_done, key="ingest:a.md")
            self.assertFalse(dup)
            for _ in range(200):
                if q.status()["running"] is False and not q._keys:
                    break
                time.sleep(0.02)
            return q, jid
        finally:
            agent.stream_agent_turn = saved

    def test_a_completed_job_releases_its_key(self):
        # Asserted by inspecting the map rather than by submitting again: a second real
        # submit here is picked up by the live worker thread, which then runs against the
        # unpatched agent and a None client and writes its crash into a temp directory
        # tearDown has already removed. Re-submittability after a release is covered on
        # the worker-free queue above.
        q, _ = self._run_one(['{"type": "done"}\n'])
        self.assertEqual(q._keys, {})
        self.assertEqual(q._job_keys, {})

    def test_a_crashing_agent_turn_still_releases(self):
        """If a crash leaked the key, one failed ingest would lock that article out until
        the server restarted — and a failed ingest is exactly when you re-run it."""
        import agent
        saved = agent.stream_agent_turn

        def boom(client, model, messages, system, stop_event=None):
            raise RuntimeError("kaboom")
            yield  # pragma: no cover

        agent.stream_agent_turn = boom
        try:
            q = jq_mod.JobQueue(Path(self.tmp.name))
            q.submit(None, "m", [], "sys", key="ingest:a.md")
            for _ in range(200):
                if not q._keys:
                    break
                time.sleep(0.02)
            self.assertEqual(q._keys, {}, "a crashed job leaked its key")
        finally:
            agent.stream_agent_turn = saved

    def test_the_key_is_held_until_on_done_has_run(self):
        """on_done is what marks the article wikified. Release before it and the row still
        offers Wikify while the key is already free, so a click queues a second ingest of
        an article that has in fact just been ingested."""
        seen = {}

        def on_done(messages):
            seen["keys_during"] = dict(self._q_ref._keys)

        import agent
        saved = agent.stream_agent_turn
        agent.stream_agent_turn = lambda *a, **k: iter(['{"type": "done"}\n'])
        try:
            q = jq_mod.JobQueue(Path(self.tmp.name))
            self._q_ref = q
            q.submit(None, "m", [], "sys", on_done=on_done, key="ingest:a.md")
            for _ in range(200):
                if "keys_during" in seen and not q._keys:
                    break
                time.sleep(0.02)
            self.assertEqual(list(seen.get("keys_during", {})), ["ingest:a.md"])
            self.assertEqual(q._keys, {})
        finally:
            agent.stream_agent_turn = saved

    def test_a_failing_on_done_still_releases(self):
        def on_done(messages):
            raise RuntimeError("save_history exploded")

        q, _ = self._run_one(['{"type": "done"}\n'], on_done=on_done)
        self.assertEqual(q._keys, {}, "a failing on_done leaked the key")


class CallSiteTest(unittest.TestCase):
    """serve.py's two submit sites: both unpack the pair, and both key inbox ingests."""

    @classmethod
    def setUpClass(cls):
        cls.src = Path(serve.__file__).read_text(encoding="utf-8")

    def test_no_call_site_takes_a_single_value(self):
        import re
        for m in re.finditer(r"^\s*(\S.*?)=\s*job_queue\.submit\(", self.src,
                             re.MULTILINE):
            self.assertIn(",", m.group(1),
                          "a submit() call site is not unpacking the pair: " + m.group(0))

    def test_both_call_sites_pass_a_key(self):
        import re
        calls = re.findall(r"job_queue\.submit\((?:[^()]|\([^()]*\))*\)", self.src)
        self.assertEqual(len(calls), 2, calls)
        for c in calls:
            self.assertIn("key=", c, c)

    def test_the_chat_route_keys_only_an_inbox_ingest(self):
        self.assertIn('key=f"ingest:{Path(inbox_file).name}" if inbox_file else None',
                      self.src)

    def _process_all_code(self):
        """`inbox_process_all`'s executable body, with its docstring removed.

        Asserting over the whole of serve.py was wrong twice over: the docstring EXPLAINS
        that the chain was removed, so it names `_submit_item` and `_batch_running` and
        every such assertion failed on the explanation rather than on the code. And a
        failing `assertNotIn` against a 173KB string prints the whole file, which buries
        the one line that matters.
        """
        block = self.src[self.src.index("def inbox_process_all"):]
        block = block[:block.index("\ndef ")]
        head, sep, rest = block.partition('"""')
        return head + (rest.partition('"""')[2] if sep else "")

    def test_process_all_does_not_chain_submissions(self):
        """**This test used to assert the opposite**, and it was right to at the time: the
        batch submitted one item and chained the next from its `on_done`, so a duplicate —
        whose `on_done` belongs to the click that started it — stopped the batch dead, and
        the old assertion pinned the `_submit_item(items, index + 1)` that worked around it.

        The chain is gone, so the workaround it needed is gone with it. Reported as *"the
        wikify all button doesn't seem to tie into the work queue? The number didn't go up
        in the q"* — `status()` reports `qsize()`, and with one job running and nothing
        behind it that is 0 for the whole batch, while the route claimed `{"queued": N}`.
        """
        code = self._process_all_code()
        self.assertNotIn("_submit_item", code,
                         "process-all chains again; the queue depth goes back to 0")

    def test_the_batch_flag_is_gone(self):
        """`_batch_running` was cleared at the END of the chain, and `_worker` skips
        `on_done` on a cancelled job — so one cancel left it True forever and every later
        click answered 409 until the server restarted. The per-item dedupe key already
        prevents double submission, so the flag was a second mechanism answering a settled
        question, with a failure mode of its own."""
        self.assertNotIn("_batch_running", self._process_all_code())

    def test_process_all_reports_what_it_actually_queued(self):
        """The old route answered `{"queued": len(unprocessed)}` having submitted exactly
        one job. A count the caller cannot act on is worse than no count."""
        code = self._process_all_code()
        self.assertIn('"queued": len(queued)', code)
        self.assertNotIn('"queued": len(unprocessed)', code)

    def test_a_duplicate_does_not_stop_the_rest_being_queued(self):
        """`continue`, not `return`: an item already in flight is skipped and the loop
        carries on. This is the property the chain could not have."""
        code = self._process_all_code()
        block = code[code.index("if duplicate:"):][:400]
        self.assertIn("continue", block)
        self.assertNotIn("return", block)

    def test_every_per_item_closure_binds_its_own_values(self):
        """Thirty closures built in one loop with late binding would give every job the
        LAST article's session — invisible until you read the pages it wrote.

        Addressed to `_ingest_job` rather than the route: the construction was hoisted
        there so the route and the restart-resume path cannot build an ingest two ways."""
        block = self.src[self.src.index("def _ingest_job"):]
        block = block[:block.index("\n@app.route")]
        self.assertIn("def _setup(_p=inbox_path_str, _u=inbox_url):", block)
        self.assertIn("def on_done(messages, _fname=filename):", block)

    def test_the_route_builds_its_jobs_through_the_shared_builder(self):
        """Two copies of the ingest prompt and the wikified-marking would drift on the
        first change, and the resume path is the one nobody watches."""
        code = self._process_all_code()
        self.assertIn("_ingest_job(filename)", code)
        self.assertNotIn("orientation_message()", code,
                         "the route is building the history itself again")


class TemplateTest(unittest.TestCase):
    """The browser's half. It cannot replace the server guard — a second tab knows nothing
    about this one — but it stops the double click before the round trip."""

    @classmethod
    def setUpClass(cls):
        cls.src = (Path(__file__).resolve().parent.parent / "tools" / "templates"
                   / "inbox.html").read_text(encoding="utf-8")

    def test_wikify_tracks_what_is_in_flight(self):
        self.assertIn("window.wikifying", self.src)

    def test_wikify_returns_early_when_already_in_flight(self):
        self.assertRegex(self.src, r"if \(window\.wikifying\.has\(name\)\) return;")

    def test_wikify_all_posts_to_the_queue_instead_of_looping(self):
        """**The answer to "the wikify all button doesn't seem to tie into the work queue?
        The number didn't go up in the q".** The button never called the endpoint. It
        looped here — `for (const nm of names) await wikifyItem(nm)` — submitting one
        article and waiting out a ~40-minute ingest before the next, so the queue behind
        the running job was always empty and the badge's `pending` count was 0 for the
        whole batch.

        Worse than the badge: **the browser WAS the batch.** Close the tab after article
        three of thirty and the other twenty-seven were never submitted, with nothing
        anywhere recording that they were meant to be."""
        body = self.src[self.src.index("async function wikifyAll()"):]
        body = body[:body.index("\n}")]
        self.assertIn("/inbox/process-all", body)
        self.assertNotIn("await wikifyItem", body,
                         "the batch is looping in the browser again")

    def test_a_single_row_still_streams_its_own_job(self):
        """The split is deliberate: watching one ingest is useful, watching thirty is not.
        `wikifyItem` keeps the live tool-call stream for a single click."""
        self.assertIn("async function wikifyItem(name)", self.src)
        body = self.src[self.src.index("async function wikifyItem(name)"):]
        self.assertIn("/chat/send", body[:1500])

    def test_queued_rows_are_disabled_so_they_cannot_be_started_twice(self):
        """The server's dedupe key would refuse the second run anyway, but a row that
        still offers Wikify after being queued reads as untouched."""
        body = self.src[self.src.index("async function wikifyAll()"):]
        body = body[:body.index("\n}")]
        self.assertIn("b.disabled = true", body)

    def test_the_batch_result_is_reported_to_the_user(self):
        """It queues and then says nothing would be indistinguishable from the old
        silent-loop behaviour that started this."""
        body = self.src[self.src.index("async function wikifyAll()"):]
        body = body[:body.index("\n}")]
        self.assertIn("r.queued", body)
        self.assertIn("already_running", body)

    def test_it_is_marked_before_the_fetch(self):
        """Marked after the await and a double click lands two calls anyway."""
        add = self.src.index("window.wikifying.add(name)")
        send = self.src.index("'/chat/send'")
        self.assertLess(add, send)

    def test_it_is_released_in_a_finally(self):
        """There are three exits from wikifyItem, including an early return. A leaked entry
        makes the article un-wikifiable until the page is reloaded."""
        self.assertRegex(self.src,
                         r"\} finally \{[\s\S]{0,400}?window\.wikifying\.delete\(name\)")

    def test_a_duplicate_is_reported_rather_than_claimed_as_a_new_run(self):
        self.assertIn("job.duplicate", self.src)
        self.assertRegex(self.src, r"Already being ingested")


if __name__ == "__main__":
    unittest.main()
