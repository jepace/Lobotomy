"""The log can say whether a request ever reached the application.

A user hit a failing save, the on-screen message told them the server log held a traceback,
and the log was empty. Both halves of that were broken:

  * **The message asserted something it could not know.** `apiFetch` said "the server log
    has the traceback" for ANY status >= 500, including an HTML body from a proxy, which by
    definition means the application never ran. They looked where it said and found
    nothing. An HTML body is the tell — every route here answers JSON, and `_unhandled`
    turns even a crash into JSON — so that is now what picks the explanation.
  * **An empty log was not evidence.** Two very different situations produce one: the
    request never arrived, or it arrived and nothing logged it. Werkzeug's access log is
    the line that separates them, and it is a different logger that was going to stderr
    and never into the file anyone reads.

With both fixed, silence in the log means the request never got here, which points at the
proxy rather than at Flask. That is worth more than any single message.
"""
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: F401  — makes `import serve` possible

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import serve


class LoggerWiringTest(unittest.TestCase):

    def _file_handlers(self, name):
        return {h for h in logging.getLogger(name).handlers
                if isinstance(h, logging.FileHandler)}

    def test_the_app_logger_writes_to_a_file(self):
        self.assertTrue(self._file_handlers("lobotomy"),
                        "no file handler on the lobotomy logger")

    def test_werkzeug_writes_to_the_same_file(self):
        """The access line proves a request arrived. On its own logger it went to stderr,
        so the file anyone actually reads could not answer the question."""
        self.assertTrue(self._file_handlers("lobotomy") & self._file_handlers("werkzeug"),
                        "werkzeug's access log does not reach the application's log file")

    def test_werkzeug_is_at_info_or_lower(self):
        """Access lines are INFO; at WARNING the logger would be silent on every success."""
        wz = logging.getLogger("werkzeug")
        self.assertLessEqual(wz.level, logging.INFO)
        self.assertNotEqual(wz.level, logging.NOTSET)

    def test_werkzeug_does_not_double_log(self):
        """It keeps its own stderr handler; propagating as well would print each line
        twice in the terminal."""
        self.assertFalse(logging.getLogger("werkzeug").propagate)


class RequestLineTest(unittest.TestCase):
    """What `after_request` records, which is the half that works inside the app."""

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self._saved = (serve.RAW_DIR, serve.WIKI_DIR)
        serve.RAW_DIR, serve.WIKI_DIR = self.w.raw, self.w.wiki
        self.c = serve.app.test_client()
        with self.c.session_transaction() as s:
            s["logged_in"] = True
        (self.w.raw / "x.md").write_text('---\ntitle: "T"\n---\n\n', encoding="utf-8")
        self.records = []

        class _Cap(logging.Handler):
            def emit(_s, record):
                self.records.append(record)

        self.h = _Cap()
        serve.log.addHandler(self.h)

    def tearDown(self):
        serve.log.removeHandler(self.h)
        serve.RAW_DIR, serve.WIKI_DIR = self._saved
        self.w.__exit__(None, None, None)

    def _lines(self):
        return [r.getMessage() for r in self.records]

    def test_a_successful_write_is_logged(self):
        self.c.post("/inbox/edit", json={"filename": "x.md", "content": "hi"})
        self.assertTrue([l for l in self._lines()
                         if l.startswith("POST /inbox/edit -> 200")], self._lines())

    def test_a_failure_is_logged_at_warning(self):
        self.c.post("/inbox/edit", json={"filename": "nope.md", "content": "x"})
        hit = [r for r in self.records if "-> 404" in r.getMessage()]
        self.assertTrue(hit)
        self.assertEqual(hit[0].levelno, logging.WARNING)

    def test_the_line_carries_a_duration(self):
        """A proxy read timeout shows up as a request that ran long, so the number is the
        point rather than decoration.

        No longer anchored at end-of-line: the client address was appended after it, and
        `$` here failed against a line that had gained information rather than lost any.
        The duration is still required to be there, which is what this test is for.
        """
        self.c.post("/inbox/edit", json={"filename": "x.md", "content": "hi"})
        line = next(l for l in self._lines() if l.startswith("POST /inbox/edit"))
        self.assertRegex(line, r"in \d+ms\b")

    def test_the_line_names_who_made_the_request(self):
        """Everything arrives through nginx, so without this every line in the log reads
        as the proxy — a scan and the owner's own browsing are indistinguishable."""
        self.c.post("/inbox/edit", json={"filename": "x.md", "content": "hi"})
        line = next(l for l in self._lines() if l.startswith("POST /inbox/edit"))
        self.assertRegex(line, r"from \S+$")

    def test_the_inbox_poll_is_not_logged(self):
        """It runs every 8 seconds while a tab is open and would bury everything else."""
        self.c.get("/inbox/list")
        self.assertEqual([l for l in self._lines() if "/inbox/list" in l], [])


if __name__ == "__main__":
    unittest.main()


class SlowGetIsLoggedTest(unittest.TestCase):
    """A successful GET was never logged, at any duration.

    Reported as *"clicking Read page after successful ingestion is taking like 18
    seconds"* and *"hitting the back button took 46 seconds"* — with nothing in the log
    for either, because the condition was `method != "GET" or status >= 400`.
    `_SLOW_REQUEST_MS` existed and only chose the LEVEL for lines already being written;
    it never admitted one.

    That inverts this project's own rule. An empty log was being read as "the request
    never arrived" when it could equally mean "it arrived and took 46 seconds", and a
    slow page view is the single most useful line the server can write about itself.
    """

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self._saved = (serve.RAW_DIR, serve.WIKI_DIR, serve._SLOW_REQUEST_MS)
        serve.RAW_DIR, serve.WIKI_DIR = self.w.raw, self.w.wiki
        self.addCleanup(lambda: setattr(serve, "_SLOW_REQUEST_MS", self._saved[2]))
        self.addCleanup(lambda: (setattr(serve, "RAW_DIR", self._saved[0]),
                                 setattr(serve, "WIKI_DIR", self._saved[1])))
        self.c = serve.app.test_client()
        with self.c.session_transaction() as s:
            s["logged_in"] = True
        d = self.w.wiki / "entities"
        d.mkdir(parents=True, exist_ok=True)
        (d / "alpha.md").write_text(
            '---\ntitle: "Alpha"\ntype: entity\ntags: []\ncreated: 2026-01-01\n'
            'updated: 2026-01-01\nsources: []\n---\n\n# Alpha\n\n## Overview\n\nText.\n',
            encoding="utf-8")
        self.records = []

        class _Cap(logging.Handler):
            def emit(_s, record):
                self.records.append(record)

        self.h = _Cap()
        serve.log.addHandler(self.h)
        self.addCleanup(serve.log.removeHandler, self.h)

    def _lines(self):
        return [r.getMessage() for r in self.records]

    def test_a_slow_page_view_is_logged(self):
        serve._SLOW_REQUEST_MS = -1          # every request counts as slow
        self.c.get("/wiki/entities/alpha.md")
        hits = [l for l in self._lines() if "GET /wiki/entities/alpha.md" in l]
        self.assertTrue(hits, f"a slow page view left no trace: {self._lines()}")
        self.assertIn("SLOW", hits[0])

    def test_it_is_logged_at_warning_so_it_stands_out(self):
        serve._SLOW_REQUEST_MS = -1
        self.c.get("/wiki/entities/alpha.md")
        hit = [r for r in self.records if "GET /wiki/entities/alpha.md" in r.getMessage()]
        self.assertEqual(hit[0].levelno, logging.WARNING)

    def test_an_ordinary_fast_page_view_is_still_not_logged(self):
        """The reason this was not simply logging every GET: one tab left open writes a
        line per navigation, and this project has already had a disk filled by its own
        log volume. Only the ones that have already cost seconds are written."""
        serve._SLOW_REQUEST_MS = 60_000
        self.c.get("/wiki/entities/alpha.md")
        self.assertEqual([l for l in self._lines() if "GET /wiki/entities" in l], [])

    def test_a_slow_poll_is_still_quiet(self):
        """`_QUIET_PATHS` wins over slowness: /inbox/list runs every 8 seconds, and a
        slow one would write a line every 8 seconds for as long as it stayed slow."""
        serve._SLOW_REQUEST_MS = -1
        self.c.get("/inbox/list")
        self.assertEqual([l for l in self._lines() if "/inbox/list" in l], [])

    def test_the_line_carries_the_duration(self):
        serve._SLOW_REQUEST_MS = -1
        self.c.get("/wiki/entities/alpha.md")
        hits = [l for l in self._lines() if "GET /wiki/entities/alpha.md" in l]
        self.assertRegex(hits[0], r"-> 200 in \d+ms")
