"""The log says which route was handed out, and when it was not taken.

Asked how we knew the pages in an ingest log were too large to read whole, the honest
answer was that we did not. The route is inside `lookup_titles`' reply, the reply is
truncated to ~150 chars in the log, and the routes are the part that gets cut — so the only
evidence was the call the model made next, which is circular. **A model ignoring the route
leaves a log that looks exactly like one obeying it**, because both end in a read.

Two lines close that:

  * `lookup_titles: 11 found, routes — read_file: 3, read_section: 8` — what went out.
  * `route not taken: entities/big.md is 36155 chars, routed to read_section, but
    read_file was called` — what came back.

`_note_route_taken` is instrumentation, NOT a guard: nothing is refused, because a
deviation is often legitimate (the model may be doing something the route did not
anticipate, like a regenerate). The reason it is worth having is the pattern this project
keeps re-learning — a reply that gives procedural advice has to track the tools underneath
it, and the advice has gone stale twice. There was no way to notice that from production
logs.
"""
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.setLevel(logging.INFO)
        self.records = []

    def emit(self, record):
        self.records.append(record)


class RouteLoggingTest(TempWikiTestCase):

    def setUp(self):
        super().setUp()
        self.w.page("entities/small.md", title="Small", type="entity",
                    body="# Small\n\n## Overview\n\nshort.\n")
        big = "# Big\n\n" + "".join(f"## S{i}\n\n" + ("word " * 900) + "\n\n"
                                   for i in range(8))
        self.w.page("entities/big.md", title="Big", type="entity", body=big)
        agent._title_map_cache = None
        self.h = _Capture()
        agent.log.addHandler(self.h)
        # The LOGGER filters by level before any handler is consulted, so a handler set to
        # INFO sees nothing unless the logger admits INFO too. Restored in tearDown, since
        # leaving it raised would change what every later test module prints.
        self._lvl = agent.log.level
        agent.log.setLevel(logging.INFO)

    def tearDown(self):
        agent.log.setLevel(self._lvl)
        agent.log.removeHandler(self.h)
        super().tearDown()

    def _lines(self, level=None):
        return [r.getMessage() for r in self.h.records
                if level is None or r.levelno == level]

    def _warnings(self):
        return self._lines(logging.WARNING)

    # ---- what went out -----------------------------------------------------

    def test_the_lookup_logs_the_routes_it_handed_out(self):
        agent._lookup_titles({"names": ["Small", "Big"]})
        line = next(l for l in self._lines() if l.startswith("lookup_titles:"))
        self.assertIn("read_file: 1", line)
        self.assertIn("read_section: 1", line)

    def test_it_names_the_tool_not_the_marker(self):
        """A log reader's question is 'which call should it have made'."""
        agent._lookup_titles({"names": ["Big"]})
        line = next(l for l in self._lines() if l.startswith("lookup_titles:"))
        self.assertNotIn("too large to read whole", line)

    def test_a_lookup_that_finds_nothing_logs_no_routes(self):
        agent._lookup_titles({"names": ["Nobody At All"]})
        self.assertEqual([l for l in self._lines() if l.startswith("lookup_titles:")], [])

    def test_the_counts_match_the_rows(self):
        out = agent._lookup_titles({"names": ["Small", "Big"]})
        rows = [l for l in out.splitlines() if l.startswith("  - ")]
        line = next(l for l in self._lines() if l.startswith("lookup_titles:"))
        self.assertIn(f"{len(rows)} found", line)

    # ---- what came back ----------------------------------------------------

    def test_reading_a_large_page_whole_is_reported(self):
        agent._lookup_titles({"names": ["Big"]})
        agent._read_file("wiki/entities/big.md")
        w = [l for l in self._warnings() if l.startswith("route not taken")]
        self.assertEqual(len(w), 1, self._warnings())
        self.assertIn("routed to read_section", w[0])
        self.assertIn("but read_file was called", w[0])

    def test_the_warning_carries_the_size(self):
        """The number that answers 'how do we know it was too big', in the log itself."""
        agent._lookup_titles({"names": ["Big"]})
        agent._read_file("wiki/entities/big.md")
        self.assertRegex(self._warnings()[-1], r"is \d{4,} chars")

    def test_section_reading_a_short_page_is_reported(self):
        """The opposite deviation: one section seen where the whole page was free."""
        agent._lookup_titles({"names": ["Small"]})
        agent._read_section({"path": "wiki/entities/small.md", "section": "Overview"})
        w = [l for l in self._warnings() if l.startswith("route not taken")]
        self.assertEqual(len(w), 1)
        self.assertIn("routed to read_file", w[0])

    def test_following_the_route_says_nothing(self):
        """Instrumentation that fires on the correct path is noise, and noise gets muted."""
        agent._lookup_titles({"names": ["Small", "Big"]})
        agent._read_file("wiki/entities/small.md")
        agent._read_section({"path": "wiki/entities/big.md", "section": "S1"})
        self.assertEqual([l for l in self._warnings() if l.startswith("route not taken")],
                         [])

    def test_a_page_that_was_never_routed_says_nothing(self):
        """Most reads in a chat session were never handed a route; they are not deviations."""
        agent._read_file("wiki/entities/big.md")
        agent._read_section({"path": "wiki/entities/small.md", "section": "Overview"})
        self.assertEqual([l for l in self._warnings() if l.startswith("route not taken")],
                         [])

    def test_an_already_read_page_is_not_sent_back_to_either_tool(self):
        agent._read_file("wiki/entities/small.md")
        agent._lookup_titles({"names": ["Small"]})       # routes to 'already read'
        agent._read_file("wiki/entities/small.md")
        self.assertIn("but read_file was called", self._warnings()[-1])

    def test_nothing_is_refused(self):
        """It is instrumentation, not a guard — a deviation can be legitimate."""
        agent._lookup_titles({"names": ["Big"]})
        out = agent._read_file("wiki/entities/big.md")
        self.assertNotIn("refused", out.lower())
        self.assertIn("OUTLINE", out)

    def test_the_record_does_not_survive_into_the_next_job(self):
        """Session state on a reused worker thread — a leaked route would mislabel the
        next ingest's reads as deviations."""
        agent._lookup_titles({"names": ["Big"]})
        agent.init_session()
        agent._read_file("wiki/entities/big.md")
        self.assertEqual([l for l in self._warnings() if l.startswith("route not taken")],
                         [])

    def test_the_done_refusal_also_records_its_routes(self):
        """done()'s refusal hands routes too, so a deviation after it must be visible."""
        agent.init_session()
        rel = "entities/big.md"
        agent._write_route(rel)
        agent._read_file(f"wiki/{rel}")
        self.assertIn("route not taken", self._warnings()[-1])


if __name__ == "__main__":
    unittest.main()
