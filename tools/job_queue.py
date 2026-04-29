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
import queue
import secrets
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


class JobQueue:
    def __init__(self, jobs_dir: Path):
        self._dir = jobs_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._q = queue.Queue()
        self._current_job_id: "str | None" = None
        self._lock = threading.Lock()
        self._recover()
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Return the currently running job, if any."""
        with self._lock:
            jid = self._current_job_id
        return {"running": jid is not None, "job_id": jid}

    def submit(self, client, model: str, messages: list,
               system: str, on_done=None) -> str:
        """
        Enqueue an agent turn. Returns job_id immediately.
        on_done(messages) is called in the worker thread after completion.
        """
        job_id = secrets.token_hex(8)
        self._event_file(job_id).write_text("", encoding="utf-8")
        self._q.put((job_id, client, model, messages, system, on_done))
        return job_id

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
                # If no new data for 10 min and job is no longer current, give up
                if idle > 12000:
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
        """
        for f in sorted(self._dir.glob("*.ndjson")):
            try:
                content = f.read_bytes()
                if b'"done"' not in content:
                    with open(f, "ab") as fp:
                        fp.write((json.dumps({
                            "type": "error",
                            "content": "Server restarted — job was interrupted. Please resubmit."
                        }) + "\n").encode())
                        fp.write((json.dumps({"type": "done"}) + "\n").encode())
            except Exception:
                pass
        self._cleanup()

    def _cleanup(self, keep: int = 50):
        """Delete oldest job files when count exceeds keep."""
        files = sorted(self._dir.glob("*.ndjson"), key=lambda f: f.stat().st_mtime)
        for f in files[:-keep]:
            try:
                f.unlink()
            except Exception:
                pass

    def _worker(self):
        from agent import stream_agent_turn
        while True:
            job_id, client, model, messages, system, on_done = self._q.get()
            with self._lock:
                self._current_job_id = job_id
            f = self._event_file(job_id)
            try:
                with open(f, "a", encoding="utf-8") as fp:
                    for line in stream_agent_turn(client, model, messages, system):
                        fp.write(line)
                        fp.flush()
            except Exception as e:
                with open(f, "a", encoding="utf-8") as fp:
                    fp.write(json.dumps({"type": "error", "content": str(e)}) + "\n")
                    fp.write(json.dumps({"type": "done"}) + "\n")
                    fp.flush()
            finally:
                with self._lock:
                    self._current_job_id = None
                if on_done:
                    try:
                        on_done(messages)
                    except Exception:
                        pass
                self._cleanup()
            self._q.task_done()
