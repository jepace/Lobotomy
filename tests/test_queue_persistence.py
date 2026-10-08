"""A queued batch survives a restart, and there is a page that shows and manages it.

Reported as *"it's frustrating that reloading or navigating away resets the reading list so
I can't see what's in the ingest queue / wrong data is displayed. Can that be fixed, and or
maybe a way to view and manage the queue is needed?"*

Reloading the PAGE was fixed the day before — `list_inbox` reports each article's in-flight
state from the queue. What was left is worse and explains the same symptom: **the queue
lived only in memory.** Queue thirty articles, restart the server — and a deploy is a
restart — and all thirty were gone. The reading list then correctly showed every one as
idle, which reads exactly like the display being wrong. `_recover` even said "the work
itself is not lost either way", which is true of the article and false of the queue: at
`max_rpm: 1` a thirty-article batch is ~20 hours of queued work, and it silently became
nothing.

Three decisions, each with a test here:

  * **Only the KEYS are persisted.** An ingest's messages are derivable — the key is
    `ingest:<raw filename>` and `serve._ingest_job` rebuilds the history from that file.
    Writing the messages would store every queued article's full text plus the orientation
    message, then replay a prompt built against a wiki that has moved on.
  * **Only the WAITING ones.** A job in flight when the process died is deliberately not
    re-queued: if that article is what killed the server, re-queueing it is a crash loop
    that survives restarts.
  * **A chat turn is never persisted.** It carries no key, its messages are derivable from
    nothing, and an interactive turn whose browser has gone is not worth resuming.

**The bug this found was in the mirroring, and only a real queue showed it.** Two threads
write the file — the submitting thread records a job joining, the WORKER records one
leaving — and the first version shared one `pending.tmp`. Both wrote it, both called
`replace`, the loser got `ENOENT`, and the surviving file was two interleaved writes
(`["ingest:c.md"] "ingest:c.md"]`) which `_load_pending` then refused as malformed. So a
restart silently resumed nothing — the exact failure the feature exists to prevent, and
with a single thread it looked perfect.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki, parked_job_queue

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import job_queue

TEMPLATES = Path(__file__).resolve().parent.parent / "tools" / "templates"


class MirrorTest(unittest.TestCase):
    def setUp(self):
        self.w = TempWiki().__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self.d = self.w.root / "jobs"
        self.q = parked_job_queue(self.d)

    def _mirror(self):
        f = self.d / "pending.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None

    def test_a_queued_ingest_is_recorded_in_order(self):
        for n in ("a.md", "b.md", "c.md"):
            self.q.submit("c", "m", [], "sys", key=f"ingest:{n}")
        self.assertEqual(self._mirror(),
                         ["ingest:a.md", "ingest:b.md", "ingest:c.md"])

    def test_a_keyless_chat_turn_is_not_recorded(self):
        """Its messages are derivable from nothing, and an interactive turn whose browser
        has gone is not worth resuming."""
        self.q.submit("c", "m", [], "sys", key="ingest:a.md")
        self.q.submit("c", "m", [], "sys")
        self.assertEqual(self._mirror(), ["ingest:a.md"])

    def test_the_running_job_leaves_the_waiting_list(self):
        """A job in flight when the process dies must NOT be re-queued, or an article that
        crashes the server becomes a crash loop that survives restarts."""
        self.q.submit("c", "m", [], "sys", key="ingest:a.md")
        self.q.submit("c", "m", [], "sys", key="ingest:b.md")
        job = self.q._q.get()                       # the worker taking it
        with self.q._lock:
            self.q._current_job_id = job[0]
            k = self.q._job_keys.get(job[0])
            if k in self.q._pending_keys:
                self.q._pending_keys.remove(k)
        self.q._save_pending()
        self.assertEqual(self._mirror(), ["ingest:b.md"])

    def test_draining_clears_the_mirror(self):
        for n in ("a.md", "b.md"):
            self.q.submit("c", "m", [], "sys", key=f"ingest:{n}")
        self.assertEqual(self.q.drain(), 2)
        self.assertEqual(self._mirror(), [])

    def test_dropping_one_removes_only_that_one(self):
        ids = {n: self.q.submit("c", "m", [], "sys", key=f"ingest:{n}")[0]
               for n in ("a.md", "b.md", "c.md")}
        self.assertTrue(self.q.drop(ids["b.md"]))
        self.assertEqual(self._mirror(), ["ingest:a.md", "ingest:c.md"])

    def test_a_malformed_mirror_is_ignored_rather_than_crashing_startup(self):
        """It is a convenience. A corrupt one must cost the resume, never the server —
        and it WAS corrupt in practice, from the two-thread race above."""
        (self.d / "pending.json").write_text('["a"] ["b"]', encoding="utf-8")
        self.assertEqual(self.q._load_pending(), [])

    def test_a_mirror_that_is_not_a_list_is_ignored(self):
        (self.d / "pending.json").write_text('{"not": "a list"}', encoding="utf-8")
        self.assertEqual(self.q._load_pending(), [])

    def test_the_temp_file_name_is_unique_per_writer(self):
        """The two-thread race: one shared `pending.tmp` meant both writers raced on the
        same path, the loser got ENOENT, and the file that survived was two interleaved
        writes. Asserted structurally because reproducing the interleaving reliably is a
        timing test, and the fix is that the name cannot collide."""
        src = Path(job_queue.__file__).read_text(encoding="utf-8")
        self.assertIn("os.getpid()", src)
        self.assertIn("threading.get_ident()", src)
        self.assertNotIn('with_suffix(".tmp")', src)


class ResumeTest(unittest.TestCase):
    def setUp(self):
        self.w = TempWiki().__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self.d = self.w.root / "jobs"

    def test_a_restart_re_queues_what_was_waiting_in_order(self):
        q = parked_job_queue(self.d)
        for n in ("a.md", "b.md", "c.md"):
            q.submit("c", "m", [], "sys", key=f"ingest:{n}")

        q2 = parked_job_queue(self.d)            # the new process
        seen = []

        def builder(key):
            seen.append(key)
            return ("c", "m", [], "sys", None, None)

        self.assertEqual(q2.resume_pending(builder), 3)
        self.assertEqual(seen, ["ingest:a.md", "ingest:b.md", "ingest:c.md"])
        self.assertEqual(q2._q.qsize(), 3)

    def test_a_builder_that_declines_drops_the_job(self):
        """The article may have been wikified, archived or deleted while the server was
        down. Re-running it would spend ~40 minutes re-folding a source already in the
        wiki."""
        q = parked_job_queue(self.d)
        q.submit("c", "m", [], "sys", key="ingest:gone.md")
        q2 = parked_job_queue(self.d)
        self.assertEqual(q2.resume_pending(lambda _k: None), 0)
        self.assertEqual(q2._q.qsize(), 0)

    def test_a_builder_that_raises_does_not_stop_the_rest(self):
        q = parked_job_queue(self.d)
        for n in ("bad.md", "good.md"):
            q.submit("c", "m", [], "sys", key=f"ingest:{n}")

        def builder(key):
            if "bad" in key:
                raise RuntimeError("cannot build this one")
            return ("c", "m", [], "sys", None, None)

        q2 = parked_job_queue(self.d)
        self.assertEqual(q2.resume_pending(builder), 1)

    def test_the_mirror_is_cleared_before_any_attempt(self):
        """One attempt per queued job is the contract. Left in place, a builder that keeps
        failing — or an article that keeps being unreadable — would be retried on every
        restart forever."""
        q = parked_job_queue(self.d)
        q.submit("c", "m", [], "sys", key="ingest:a.md")
        q2 = parked_job_queue(self.d)
        q2.resume_pending(lambda _k: None)
        q3 = parked_job_queue(self.d)
        self.assertEqual(q3._load_pending(), [])

    def test_resuming_an_empty_mirror_does_nothing(self):
        q = parked_job_queue(self.d)
        self.assertEqual(q.resume_pending(lambda _k: None), 0)


class ListingTest(unittest.TestCase):
    def setUp(self):
        self.w = TempWiki().__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self.q = parked_job_queue(self.w.root / "jobs")

    def test_the_listing_is_in_run_order(self):
        for n in ("a.md", "b.md", "c.md"):
            self.q.submit("c", "m", [], "sys", key=f"ingest:{n}")
        self.assertEqual([w["key"] for w in self.q.listing()["waiting"]],
                         ["ingest:a.md", "ingest:b.md", "ingest:c.md"])

    def test_the_running_job_is_separate_from_the_waiting_ones(self):
        jid, _ = self.q.submit("c", "m", [], "sys", key="ingest:a.md")
        self.q.submit("c", "m", [], "sys", key="ingest:b.md")
        self.q._q.get()
        with self.q._lock:
            self.q._current_job_id = jid
        listing = self.q.listing()
        self.assertEqual(listing["running"]["key"], "ingest:a.md")
        self.assertEqual([w["key"] for w in listing["waiting"]], ["ingest:b.md"])

    def test_listing_does_not_consume_the_queue(self):
        """There is no non-destructive public way to look at a `queue.Queue`, so this
        reads its deque under the mutex. Getting that wrong would EAT the batch."""
        for n in ("a.md", "b.md"):
            self.q.submit("c", "m", [], "sys", key=f"ingest:{n}")
        self.q.listing()
        self.q.listing()
        self.assertEqual(self.q._q.qsize(), 2)

    def test_dropping_releases_the_key_so_the_article_can_run_again(self):
        """A dropped job never runs, so the worker's release never fires for it — the same
        reason `drain` releases. Leak the key and that article can never be wikified."""
        jid, _ = self.q.submit("c", "m", [], "sys", key="ingest:a.md")
        self.assertTrue(self.q.drop(jid))
        _jid2, dup = self.q.submit("c", "m", [], "sys", key="ingest:a.md")
        self.assertFalse(dup, "the key leaked; that article is now un-wikifiable")

    def test_dropping_an_unknown_job_reports_false(self):
        self.assertFalse(self.q.drop("nosuchjob"))

    def test_dropping_does_not_touch_the_running_job(self):
        """`drop` is the safe action by construction — nothing half-written is abandoned.
        Cancelling is a separate, louder call."""
        jid, _ = self.q.submit("c", "m", [], "sys", key="ingest:a.md")
        self.q._q.get()
        with self.q._lock:
            self.q._current_job_id = jid
        self.assertFalse(self.q.drop(jid))
        self.assertEqual(self.q.listing()["running"]["job_id"], jid)


class QueuePageTest(unittest.TestCase):
    """The page asked for. Structural, like the other template tests here."""

    @classmethod
    def setUpClass(cls):
        cls.src = (TEMPLATES / "queue.html").read_text(encoding="utf-8")

    def test_it_shows_the_running_article_and_the_waiting_ones(self):
        self.assertIn("Writing now", self.src)
        self.assertIn("waiting", self.src)

    def test_every_management_action_is_offered(self):
        for fn in ("dropJob", "drainQueue", "cancelRunning"):
            with self.subTest(fn=fn):
                self.assertIn(fn, self.src)

    def test_cancelling_the_running_job_says_what_it_costs(self):
        """It abandons a part-written article: the pages already changed stay, and the
        source is not marked wikified, so a re-run folds the whole thing in again. A
        confirm that does not say so is a trap."""
        i = self.src.index("async function cancelRunning")
        body = self.src[i:i + 900]
        self.assertIn("confirm(", body)
        self.assertIn("part-written", body)

    def test_removing_a_waiting_article_says_it_stays_in_the_reading_list(self):
        """Whitespace-collapsed and scoped to the note. An exact-substring assertion
        failed because the phrase wraps across a line in the template — and printed the
        whole 8KB file to say so, which is the third time this week a template assertion
        has buried its own message."""
        i = self.src.index('class="q-note"')
        note = " ".join(self.src[i:i + 900].split())
        self.assertIn("stays in the reading list", note, note[:200])

    def test_the_badge_links_here(self):
        """The count was the only thing that had ever reported the queue, so the thing
        that tells you there is one is also the way into it."""
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        self.assertIn("label.href = '/queue'", base)


if __name__ == "__main__":
    unittest.main()
