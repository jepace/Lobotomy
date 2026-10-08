#!/usr/bin/env python3
"""
Background job queue for LLM agent turns.

Design philosophy: assume the AI takes up to 2 hours.
- Jobs run in a daemon thread — survive page navigation, tab close, anything short of server restart.
- Event files (.ndjson) are written to disk — reconnecting clients can tail from offset 0.
- On server restart, any unfinished .ndjson files get a terminal error event so tail() never hangs.
- status() exposes the currently running job so the UI can auto-reconnect.

POST /chat/send  → enqueues job, returns {job_id} immediately
GET  /chat/stream/<job_id>  → tails the job event file (works on reconnect too)
GET  /chat/status → {running: bool, job_id: str|null}
"""

import json
import logging
import queue
import secrets
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("lobotomy.jobs")

sys.path.insert(0, str(Path(__file__).parent))


class JobQueue:
    def __init__(self, jobs_dir: Path):
        self._dir = jobs_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._q = queue.Queue()
        self._current_job_id: "str | None" = None
        self._cancel_events: "dict[str, threading.Event]" = {}
        self._lock = threading.Lock()
        # key -> job_id for jobs waiting or running, and the reverse so a finishing job
        # can release its own key without scanning. Both guarded by _lock.
        self._keys = {}
        self._job_keys = {}
        self._recover()
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Return the currently running job, if any, and how many are waiting."""
        with self._lock:
            jid = self._current_job_id
        return {"running": jid is not None, "job_id": jid, "pending": self._q.qsize()}

    def in_flight(self) -> dict:
        """Every deduplication key the queue is holding, as `{key: "running"|"queued"}`.

        **The authoritative answer to "is this article being ingested?"**, and it was
        previously nowhere. `_keys` has held it since the dedupe guard was added — the
        queue already knows exactly which articles are waiting and which one is being
        written — but nothing could ask, so the browser guessed from what it had itself
        started. Three ways that guess was wrong:

          * **Clear Queue left every row reading "Queued".** The jobs were dropped and
            their keys released, so the server would happily take them again, while the
            rows stayed disabled until a page reload.
          * **A reload during a batch showed every queued row as ready to Wikify.** The
            dedupe key refused the click, so nothing was double-ingested, but the page
            was lying about the state of thirty articles.
          * **Nothing said WHICH article was being written.** The badge gave a count and
            the nav went busy; the one row actually in flight looked like the other
            twenty-nine.

        Cheap enough for the 8-second `/inbox/list` poll: one dict copy under the lock,
        sized by the queue rather than by the wiki.
        """
        with self._lock:
            running_key = self._job_keys.get(self._current_job_id)
            keys = dict(self._keys)
        return {k: ("running" if k == running_key else "queued") for k in keys}

    def submit(self, client, model: str, messages: list,
               system: str, on_done=None, setup=None, key: str = None) -> tuple:
        """
        Enqueue an agent turn. Returns (job_id, is_duplicate) immediately.
        setup() is called in the worker thread immediately before the job runs.
        on_done(messages) is called in the worker thread after completion.

        **`key` makes the job unique.** If a job carrying the same key is already waiting
        or running, nothing is queued and that job's id comes back with is_duplicate=True,
        so the caller attaches to the work already in flight.

        Wikify had no such guard, and the only thing stopping a second one was
        `btn.disabled` in the browser — lost on a page reload, absent in a second tab, and
        reset whenever the poll re-rendered the row. Two clicks therefore queued two full
        ingests of the SAME article. At `max_rpm: 1` that is a second ~40-minute run
        spending a per-day quota on work already done, and it re-folds the same source into
        pages the first run has already written.

        Returning the existing id rather than refusing is the deliberate choice: clicking
        Wikify twice should watch the one job, not raise an error about a thing that is
        already happening (write-path principle 4 — name a move that works, and here the
        move is "it is already running, here it is").
        """
        with self._lock:
            if key is not None and key in self._keys:
                return self._keys[key], True
            job_id = secrets.token_hex(8)
            stop_event = threading.Event()
            self._cancel_events[job_id] = stop_event
            if key is not None:
                self._keys[key] = job_id
                self._job_keys[job_id] = key
        self._event_file(job_id).write_text("", encoding="utf-8")
        self._q.put((job_id, client, model, messages, system, on_done, stop_event, setup))
        return job_id, False

    def _release_key(self, job_id: str) -> None:
        """Forget a finished job's key. **Must happen on every exit path** — leak one and
        that article can never be wikified again, which is a worse failure than the
        duplicate this exists to prevent."""
        # The `pop` is what makes a double release safe, and it is the only thing that
        # needs to: a job appears in _job_keys once and is removed here, so a second
        # release for the same job finds nothing and cannot free the key a successor has
        # since taken. An extra `_keys[k] == job_id` test looked like protection against
        # that and was unreachable — no mutation of it could be caught, which is how it
        # was found.
        with self._lock:
            k = self._job_keys.pop(job_id, None)
            if k is not None:
                self._keys.pop(k, None)

    def cancel(self, job_id: str) -> bool:
        """Signal the running job to stop. Returns True if the job was found."""
        with self._lock:
            ev = self._cancel_events.get(job_id)
        if ev:
            ev.set()
            return True
        return False

    def drain(self) -> int:
        """Discard every job still waiting to start. Returns how many were dropped.

        The running job is left alone deliberately: the point is to stop *after* the
        current article rather than abandon it half-written. Queue up a dozen wikifies,
        notice a bug, and this is what lets the server reach a quiet point so it can be
        restarted onto new code without losing the one in flight.

        Each dropped job gets a terminal event written to its stream, so anything tailing
        it in a browser finishes instead of hanging until the 30-minute idle timeout.
        """
        dropped = 0
        while True:
            try:
                job_id, _client, _model, _messages, _system, _on_done, _ev, _setup = \
                    self._q.get_nowait()
            except queue.Empty:
                break
            try:
                with open(self._event_file(job_id), "a", encoding="utf-8") as fp:
                    fp.write(json.dumps({"type": "error",
                                         "content": "Queue cleared before this job started — "
                                                    "the article is still in the reading list."}) + "\n")
                    fp.write(json.dumps({"type": "done"}) + "\n")
            except OSError as e:
                log.warning("drain: could not close out %s: %s", job_id, e)
            with self._lock:
                self._cancel_events.pop(job_id, None)
            # A drained job never runs, so the worker's release never fires for it.
            self._release_key(job_id)
            self._q.task_done()
            dropped += 1
        if dropped:
            log.info("Queue drained: %d job(s) dropped, running job left to finish", dropped)
        return dropped

    def tail(self, job_id: str):
        """
        Generator yielding ndjson event lines as the worker writes them.
        Safe to call on reconnect — replays from offset 0 then continues.
        Stops when the 'done' event is seen or the file disappears.
        """
        f = self._event_file(job_id)
        for _ in range(100):           # wait up to 10s for file to appear
            if f.exists():
                break
            time.sleep(0.1)
        else:
            yield json.dumps({"type": "error", "content": "Job not found."}) + "\n"
            yield json.dumps({"type": "done"}) + "\n"
            return

        offset = 0
        buf    = b""
        idle   = 0
        while True:
            try:
                with open(f, "rb") as fp:
                    fp.seek(offset)
                    chunk = fp.read()
            except OSError:
                return

            if chunk:
                idle    = 0
                offset += len(chunk)
                buf    += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.strip().decode("utf-8", errors="replace")
                    if not line:
                        continue
                    yield line + "\n"
                    try:
                        if json.loads(line).get("type") == "done":
                            return
                    except json.JSONDecodeError:
                        pass
            else:
                idle += 1
                # Heartbeat every 15s to keep the HTTP connection alive
                if idle % 300 == 0:
                    yield json.dumps({"type": "ping"}) + "\n"
                # If no new data for 30 min and job is no longer current, give up
                if idle > 36000:
                    log.warning("Job %s: stream idle timeout after 30 min", job_id)
                    yield json.dumps({"type": "error", "content": "Stream idle timeout — partial response received"}) + "\n"
                    yield json.dumps({"type": "done"}) + "\n"
                    return

            time.sleep(0.05)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _event_file(self, job_id: str) -> Path:
        return self._dir / f"{job_id}.ndjson"

    def _recover(self):
        """
        On startup: any .ndjson without a 'done' event was interrupted by a server
        restart. Write terminal events so tail() doesn't hang on reconnect.

        This does NOT resume anything, despite the name it used to log. The job's messages
        and setup were never persisted — only its event stream was — so there is nothing
        to restart from. What it does is close out the stream. The work itself is not lost
        either way: an ingest that did not reach done() never marked its source wikified,
        so the article is still sitting in the reading list waiting to be run again.
        """
        for f in sorted(self._dir.glob("*.ndjson")):
            try:
                content = f.read_bytes()
                if b'"done"' not in content:
                    log.warning("Job %s was interrupted by a restart and is NOT being "
                                "resumed — closing out its stream. Its article was never "
                                "marked wikified, so it is still in the reading list; "
                                "re-run it from there.", f.stem)
                    with open(f, "ab") as fp:
                        fp.write((json.dumps({
                            "type": "error",
                            "content": "Server restarted — job was interrupted. Please resubmit."
                        }) + "\n").encode())
                        fp.write((json.dumps({"type": "done"}) + "\n").encode())
            except OSError as e:
                log.warning("Could not recover job file %s: %s", f.name, e)
        self._cleanup()

    def _cleanup(self, keep: int = 50):
        """Delete oldest job files when count exceeds keep."""
        files = sorted(self._dir.glob("*.ndjson"), key=lambda f: f.stat().st_mtime)
        for f in files[:-keep]:
            try:
                f.unlink()
            except OSError:
                pass

    def _worker(self):
        from agent import stream_agent_turn
        while True:
            job_id, client, model, messages, system, on_done, stop_event, setup = self._q.get()
            with self._lock:
                self._current_job_id = job_id
            if setup is not None:
                try:
                    setup()
                except Exception as e:
                    log.error("Job %s: setup() failed: %s", job_id, e, exc_info=True)
            f = self._event_file(job_id)
            log.info("Job %s started (model=%s, messages=%d)", job_id, model, len(messages))
            cancelled = False
            try:
                with open(f, "a", encoding="utf-8") as fp:
                    for line in stream_agent_turn(client, model, messages, system,
                                                  stop_event=stop_event):
                        fp.write(line)
                        fp.flush()
                        try:
                            ev = json.loads(line.strip())
                            if ev.get("type") == "cancelled":
                                cancelled = True
                        except json.JSONDecodeError:
                            pass
                log.info("Job %s %s", job_id, "cancelled" if cancelled else "completed")
            except Exception as e:
                log.error("Job %s crashed: %s", job_id, e, exc_info=True)
                with open(f, "a", encoding="utf-8") as fp:
                    fp.write(json.dumps({"type": "error", "content": str(e)}) + "\n")
                    fp.write(json.dumps({"type": "done"}) + "\n")
                    fp.flush()
            finally:
                with self._lock:
                    self._current_job_id = None
                    self._cancel_events.pop(job_id, None)
                try:
                    if on_done and not cancelled:
                        try:
                            on_done(messages)
                        except Exception as e:
                            log.error("Job %s: save_history failed: %s", job_id, e, exc_info=True)
                finally:
                    # AFTER on_done, not before. on_done is what marks the article
                    # wikified; releasing first opens a window in which the row still
                    # offers Wikify and a click would queue a second, redundant ingest.
                    # It is in its own finally because a leaked key makes that article
                    # permanently un-wikifiable — worse than the duplicate it prevents.
                    self._release_key(job_id)
                self._cleanup()
            self._q.task_done()
