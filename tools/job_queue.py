#!/usr/bin/env python3
"""
Background job queue for LLM agent turns.

POST /chat/send  → enqueues job, returns {job_id} immediately
GET  /chat/stream/<job_id>  → tails the job event file

The worker thread is daemon so it doesn't block clean shutdown.
Jobs are processed one at a time — fine for a single-user server and
avoids hammering free-tier rate limits.
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
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
        while True:
            try:
                with open(f, "rb") as fp:
                    fp.seek(offset)
                    chunk = fp.read()
            except OSError:
                return

            if chunk:
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

            time.sleep(0.05)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _event_file(self, job_id: str) -> Path:
        return self._dir / f"{job_id}.ndjson"

    def _worker(self):
        from agent import stream_agent_turn
        while True:
            job_id, client, model, messages, system, on_done = self._q.get()
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
                if on_done:
                    try:
                        on_done(messages)
                    except Exception:
                        pass
            self._q.task_done()
