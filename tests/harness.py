"""The shared fixture for every test in this suite. Read CLAUDE.md's "Module state"
section before touching this file — agent.py hides state in three places, and a harness
that gets one of them wrong produces tests that pass while testing nothing. That has
already happened twice in this codebase.

    1. Module globals (REPO_ROOT, WIKI_DIR, RAW_DIR, HISTORY_DIR) — rebound per test and
       restored in teardown, even when the test raises.
    2. Thread-local session state (_ctx()/init_session()) — persists between tests in the
       same process/thread, so it is reset on every entry.
    3. Autolinker caches (_title_map_cache, _title_regex_cache, _title_tokens_cache,
       _no_autolink_titles) — keyed by title, not by wiki, so a title from a previous
       test's throwaway wiki is otherwise still "live".

No test built on this harness may touch the real repo's wiki/ or raw/ — TempWiki works
entirely inside a fresh temp directory and rebinds agent.py's globals to point at it.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


def _ensure_config():
    """agent.py imports config.py, which calls sys.exit() at import time if config.json is
    missing. The test suite must run on a clean checkout where only config.json.example
    exists, so this has to happen BEFORE agent (and therefore config) is ever imported —
    copying the example is enough, since no test here ever makes a real LLM call."""
    repo_root = Path(__file__).resolve().parent.parent
    cfg = repo_root / "config.json"
    example = repo_root / "config.json.example"
    if not cfg.exists() and example.exists():
        shutil.copy(example, cfg)


_ensure_config()

import agent


class TempWiki:
    """Context manager building one throwaway repo tree (wiki/{sources,entities,concepts,
    synthesis}, raw/) and rebinding agent.py to it.

    Usage:
        with TempWiki() as w:
            w.page("entities/jd-vance.md", title="JD Vance", type="entity",
                   body="## Overview\\n\\nA politician.\\n")
            w.read("wiki/entities/jd-vance.md")
    """

    def __init__(self):
        self._tmpdir = None
        self._saved_globals = {}

    def __enter__(self):
        self._tmpdir = tempfile.mkdtemp(prefix="lobotomy-test-")
        root = Path(self._tmpdir).resolve()
        wiki = root / "wiki"
        raw = root / "raw"
        for sd in ("sources", "entities", "concepts", "synthesis"):
            (wiki / sd).mkdir(parents=True)
        raw.mkdir(parents=True)

        # 1. Module globals. Saved so teardown restores them even if the test raises —
        # otherwise one test that raises before cleanup leaks the temp dir into every test
        # that runs after it in the same process.
        self._saved_globals = {
            "REPO_ROOT": agent.REPO_ROOT,
            "WIKI_DIR": agent.WIKI_DIR,
            "RAW_DIR": agent.RAW_DIR,
            "HISTORY_DIR": agent.HISTORY_DIR,
        }
        agent.REPO_ROOT = root
        agent.WIKI_DIR = wiki
        agent.RAW_DIR = raw
        agent.HISTORY_DIR = wiki / ".history"

        # 2. Thread-local session state.
        agent.init_session()

        # 3. Autolinker caches.
        _clear_autolink_caches()

        self.root = root
        self.wiki = wiki
        self.raw = raw

        # wiki_pages() once took a default argument that bound WIKI_DIR at import time, so
        # a caller omitting it silently scanned the real wiki even after this rebind took
        # place. Assert loudly, on every single test, that the rebind actually stuck and
        # that the freshly-rebound tree really is empty.
        assert agent.WIKI_DIR == wiki, "TempWiki: agent.WIKI_DIR did not rebind"
        assert list(agent.wiki_pages()) == [], (
            "TempWiki: agent.wiki_pages() sees pages in a brand-new temp wiki — "
            "something is still reading the old WIKI_DIR")

        # The rebind check above only proves agent SEES the temp tree. It cannot catch a
        # module that captured RAW_DIR or WIKI_DIR at import — `from agent import RAW_DIR`
        # binds the value, so the rebind is invisible to it and its writes land in the real
        # tree. tools/add_story.py did exactly that, and nine test stories were written into
        # the repo's raw/ and then committed by a `git add -A`. Nothing failed; the suite
        # was green the whole time. So snapshot what the real trees hold and check on the
        # way out. ~4ms across the whole suite.
        # RAW_DIR and WIKI_DIR only. REPO_ROOT is the whole checkout, whose __pycache__
        # churns on every run, and HISTORY_DIR lives inside WIKI_DIR already.
        self._outside = {self._saved_globals[k]: self._listing(self._saved_globals[k])
                         for k in ("RAW_DIR", "WIKI_DIR")
                         if isinstance(self._saved_globals.get(k), Path)}
        return self

    @staticmethod
    def _listing(d):
        """Names and sizes under a real tree, for the leak check. Cheap by construction:
        the repo's raw/ and wiki/ hold a handful of files, unlike a deployed one."""
        try:
            return {str(p.relative_to(d)): p.stat().st_size
                    for p in d.rglob("*") if p.is_file()}
        except (OSError, ValueError):
            return {}

    def __exit__(self, exc_type, exc, tb):
        for k, v in self._saved_globals.items():
            setattr(agent, k, v)
        # Did anything write outside the temp tree while this test ran? Reported even when
        # the test itself failed, because a leak is the more serious of the two and the
        # files are evidence either way.
        for d, before in getattr(self, "_outside", {}).items():
            after = self._listing(d)
            if after != before:
                added = sorted(set(after) - set(before))
                changed = sorted(k for k in set(after) & set(before)
                                 if after[k] != before[k])
                removed = sorted(set(before) - set(after))
                raise AssertionError(
                    f"TempWiki: the REAL tree at {d} changed while a test ran — "
                    f"added={added} changed={changed} removed={removed}. Something is "
                    f"holding a path captured at import (`from agent import RAW_DIR` "
                    f"binds the value) instead of reading agent.RAW_DIR at call time.")
        agent.init_session()
        _clear_autolink_caches()
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        return False

    # -- fixture builders -----------------------------------------------------------

    def page(self, relpath, *, title, type="entity", body="", tags=None, sources=None,
              aliases=None, no_autolink=False, url=None, raw_source=None, created=None,
              updated=None, mtime_days_ago=None):
        """Write a wiki page's on-disk bytes directly — bypassing every write-path guard,
        which is the point: this builds fixtures for guards to be tested against, not
        ingest output. `relpath` is wiki-relative, e.g. "entities/jd-vance.md".
        """
        p = self.wiki / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f'title: "{title}"',
            f"type: {type}",
            "tags: [" + ", ".join(json.dumps(t) for t in (tags or [])) + "]",
            f"created: {created or '2026-01-01'}",
            f"updated: {updated or created or '2026-01-01'}",
            "sources: [" + ", ".join(json.dumps(s) for s in (sources or [])) + "]",
        ]
        if aliases:
            lines.append("aliases: [" + ", ".join(json.dumps(a) for a in aliases) + "]")
        if no_autolink:
            lines.append("no_autolink: true")
        if url:
            lines.append(f'url: "{url}"')
        if raw_source:
            lines.append(f'raw_source: "{raw_source}"')
        content = "---\n" + "\n".join(lines) + "\n---\n\n" + body
        if not content.endswith("\n"):
            content += "\n"
        p.write_text(content, encoding="utf-8")
        if mtime_days_ago is not None:
            self.touch(relpath, days_ago=mtime_days_ago)
        # A fixture page written straight to disk (not through _atomic_write) must not be
        # served from a title-map cache built before it existed.
        agent._title_map_cache = None
        return p

    def raw_file(self, relpath, content, mtime_days_ago=None):
        """Write a raw/ fixture. `relpath` is raw-relative, e.g. "foo.md"."""
        p = self.raw / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        if mtime_days_ago is not None:
            t = time.time() - mtime_days_ago * 86400
            os.utime(p, (t, t))
        return p

    def touch(self, wiki_relpath, days_ago):
        """Set a wiki page's mtime N days in the past. heal_pages() and the stub-event
        smell both derive dates from mtime, so a test of either needs a real, controllable
        one — not "whenever the test happened to run"."""
        t = time.time() - days_ago * 86400
        os.utime(self.wiki / wiki_relpath, (t, t))

    # -- calling the real tools -------------------------------------------------------

    def read(self, path, offset=-1):
        """agent._read_file(path, offset) for real, so read-coverage bookkeeping (which
        several write-path guards depend on) is genuine rather than simulated."""
        return agent._read_file(path, offset)

    def read_section(self, path, section):
        return agent._read_section({"path": path, "section": section})

    # -- assertions ---------------------------------------------------------------

    def disk(self, wiki_relpath):
        """Raw on-disk bytes of a wiki-relative path — for asserting on the file itself,
        not just a tool's return string."""
        return (self.wiki / wiki_relpath).read_text(encoding="utf-8")

    def exists(self, wiki_relpath):
        return (self.wiki / wiki_relpath).exists()


def _clear_autolink_caches():
    agent._title_map_cache = None
    agent._title_regex_cache = {}
    agent._title_tokens_cache = {}
    agent._no_autolink_titles = set()
    agent._title_map_built_at = 0.0
    agent._vetted_mtimes = {}


class TempWikiTestCase(unittest.TestCase):
    """Convenience base class: self.w is a fresh TempWiki for the duration of the test."""

    def setUp(self):
        self._tw = TempWiki()
        self.w = self._tw.__enter__()
        self.addCleanup(lambda: self._tw.__exit__(None, None, None))


def parked_job_queue(jobs_dir):
    """A real `JobQueue` with its worker thread never started, so the test decides when a
    job finishes and the queue stays full for inspection.

    **One place, because three modules were each duplicating `__init__`'s attribute list**
    and every attribute added to the real class broke all of them at once — fourteen
    errors in one run when the pending-queue mirror landed, none of them about the thing
    being tested. The real `__init__` is still the source of truth for behaviour; this
    only skips the thread.
    """
    import queue as _queue
    import threading as _threading
    from pathlib import Path as _Path
    import job_queue as _jq

    d = _Path(jobs_dir)
    d.mkdir(parents=True, exist_ok=True)
    q = _jq.JobQueue.__new__(_jq.JobQueue)
    q._dir = d
    q._q = _queue.Queue()
    q._current_job_id = None
    q._cancel_events = {}
    q._lock = _threading.Lock()
    q._keys = {}
    q._job_keys = {}
    q._pending_keys = []
    q._pending_file = d / "pending.json"
    q._started = True
    return q
