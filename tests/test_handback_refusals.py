"""Every refusal that hands back content also forbids re-reading it.

Handing the content back is only half the job. An observed ingest hit update_section's
read-before-write refusal, was given the section text in the reply, and called read_section
for it anyway — a whole round, and one of the pacing windows between rounds, to fetch what
it was already holding:

    update_section wiki/entities/harvard-medical-school.md § Overview
    ⏳ Pacing requests — waiting for next window…
    read_section wiki/entities/harvard-medical-school.md § Overview

update_file and create_file had said "— do NOT call read_file first" for a long time and
wrapped the payload in `<file path="...">` delimiters. update_section was the one that said
neither: no instruction not to re-read, and a bare body with nothing marking where it began
or ended. Those are the two differences, and the model took the detour the others rule out.

This is guard principle 4 in its cheapest form. A refusal that names the move but leaves
the wasteful route open is a refusal the model will answer wrongly, and each wrong answer
here costs a full round trip.

The test is written against the set of refusals rather than one of them, so the four cannot
drift apart again: if a refusal says "is now marked as read", it must also say "do NOT
call read" and delimit what it hands over.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class HandbackRefusalTest(TempWikiTestCase):
    """All four scenarios share one wiki, on four pages.

    Deliberately NOT one page re-created per scenario: calling setUp() again from inside a
    test leaves the previous TempWiki un-torn-down with REPO_ROOT still rebound to it, and
    the next module to call system_prompt() fails looking for CLAUDE.md in a temp
    directory. That is the module-state trap in CLAUDE.md, and this file fell into it.
    init_session() is enough between scenarios — it resets the thread-local context and
    touches no globals.
    """

    BODY = ("# Harvard Medical School\n\n## Overview\n\nA medical school in Boston.\n\n"
            "## Background\n\nFounded in 1782.\n")

    def setUp(self):
        super().setUp()
        for n in ("a", "b", "c", "d"):
            self.w.page(f"entities/hms-{n}.md", title="Harvard Medical School",
                        type="entity", body=self.BODY)

    # --- the four refusals that hand content back ---------------------------------

    def _update_section_unread(self):
        agent.init_session()
        return agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/hms-a.md", "section": "Overview",
            "content": "New text.\n"})

    def _update_file_unread(self):
        agent.init_session()
        return agent.TOOL_FNS["update_file"]({
            "path": "wiki/entities/hms-b.md",
            "content": "# Harvard Medical School\n\n## Overview\n\nNew.\n"})

    def _create_file_exists(self):
        agent.init_session()
        return agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/hms-c.md", "title": "Harvard Medical School",
            "type": "entity", "body": "## Overview\n\nNew.\n"})

    def _update_file_stale(self):
        agent.init_session()
        agent.TOOL_FNS["read_file"]({"path": "wiki/entities/hms-d.md"})
        agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/hms-d.md", "section": "Overview",
            "content": "A medical school in Boston, founded early.\n"})
        return agent.TOOL_FNS["update_file"]({
            "path": "wiki/entities/hms-d.md",
            "content": "# Harvard Medical School\n\n## Overview\n\nStale rewrite.\n"})

    def _all(self):
        return {
            "update_section (unread section)": self._update_section_unread,
            "update_file (unread page)": self._update_file_unread,
            "update_file (stale page)": self._update_file_stale,
            "create_file (page exists)": self._create_file_exists,
        }

    # --- the contract -------------------------------------------------------------

    def test_each_one_actually_refuses(self):
        for name, fn in self._all().items():
            with self.subTest(name):
                self.assertTrue(fn().startswith("Error:"), name)

    def test_each_one_hands_the_content_back(self):
        for name, fn in self._all().items():
            with self.subTest(name):
                self.assertIn("A medical school in Boston", fn(),
                              f"{name} refused without handing back the content")

    def test_each_one_says_the_content_is_now_marked_as_read(self):
        for name, fn in self._all().items():
            with self.subTest(name):
                self.assertIn("marked as read", fn(), name)

    def test_each_one_forbids_the_re_read(self):
        # The whole point. Without this clause the model spends a round fetching what it
        # is already holding — observed on update_section, which was the one missing it.
        for name, fn in self._all().items():
            with self.subTest(name):
                self.assertRegex(fn(), r"do NOT call read_(file|section) first", name)

    def test_each_one_delimits_what_it_hands_over(self):
        # A bare body blends into the prose of the refusal; the model cannot see where the
        # payload starts and stops.
        for name, fn in self._all().items():
            with self.subTest(name):
                r = fn()
                self.assertRegex(r, r"<(file|section) [^>]*>", name)
                self.assertRegex(r, r"</(file|section)>", name)

    def test_the_handback_really_satisfies_the_guard(self):
        # The refusal claims the content is marked as read. If that were untrue, following
        # its instructions would produce the identical refusal forever.
        self.assertTrue(self._update_section_unread().startswith("Error:"))
        second = agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/hms-a.md", "section": "Overview",
            "content": "A medical school in Boston, and a GLP-1 research centre.\n"})
        self.assertFalse(second.startswith("Error:"), second)
        self.assertIn("GLP-1", self.w.disk("entities/hms-a.md"))

    def test_the_section_body_is_handed_back_not_the_whole_page(self):
        r = self._update_section_unread()
        self.assertIn("## Overview", r)
        self.assertNotIn("Founded in 1782", r,
                         "update_section handed back a section it was not asked about")


if __name__ == "__main__":
    unittest.main()
