"""create_file reports every reason it refused, not the first one it found.

A call can be wrong in several independent ways at once, and each refusal costs a full
model round. An observed ingest spent three refusals and about three minutes on one page:
told its body had no ## Overview, it fixed that and resent — and was then told it needed
to create the source page first, which had been true from the start and said nothing
about the body at all.

The checks that stay as immediate returns are the ones that make the rest meaningless: no
path, a path outside wiki/, a bad filename, a page that already exists (that reply carries
the existing content), a missing title or type. Everything after those collects.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class CollectedProblemsTest(TempWikiTestCase):

    def test_the_observed_case_reports_both_at_once(self):
        # No source page established yet, and a body with no ## Overview. These are
        # independent: one is about the session, the other about the text.
        agent.init_session(inbox_path="raw/florida-dengue.md")
        r = agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/florida-department-of-health.md",
            "title": "Florida Department of Health", "type": "entity",
            "body": "## Summary\n\nX.\n\n## Role and Responsibilities\n\nY.\n"})
        self.assertTrue(r.startswith("Error:"), r)
        self.assertIn("## Overview", r)
        self.assertIn("source page", r)
        self.assertIn("2 things need fixing", r)

    def test_a_single_problem_reads_as_one_sentence(self):
        # The list framing would be noise for one item.
        agent.init_session()
        r = agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/x.md", "title": "X Corp", "type": "entity",
            "body": "## Background\n\nStuff.\n"})
        self.assertTrue(r.startswith("Error: create_file refused — an entity page"), r)
        self.assertNotIn("things need fixing", r)

    def test_four_independent_problems_are_all_named(self):
        agent.init_session()
        r = agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/y.md", "title": "y corp", "type": "entity",
            "body": "## Background\n\nA.\n\n## Background\n\nB.\n\n## 2026 Outbreak\n\nC.\n"})
        self.assertIn("4 things need fixing", r)
        self.assertIn("repeats this heading", r)       # duplicate
        self.assertIn("2026 Outbreak", r)              # dated heading
        self.assertIn("all lowercase", r)              # title
        self.assertIn("'## Overview'", r)              # opener

    def test_nothing_is_written_when_problems_are_reported(self):
        agent.init_session(inbox_path="raw/x.md")
        agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/z.md", "title": "z corp", "type": "entity",
            "body": "## Background\n\nA.\n"})
        self.assertFalse(self.w.exists("entities/z.md"))

    def test_a_clean_call_still_succeeds(self):
        agent.init_session()
        r = agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/ok.md", "title": "OK Corp", "type": "entity",
            "body": "## Overview\n\nA firm.\n"})
        self.assertFalse(r.startswith("Error:"), r)
        self.assertTrue(self.w.exists("entities/ok.md"))

    def test_absorbed_mistakes_are_not_reported_as_problems(self):
        # The opener swap and the trailing date qualifier are fixed silently. Listing them
        # would turn a successful call into a refusal.
        agent.init_session()
        r = agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/acme.md", "title": "Acme Corp", "type": "entity",
            "body": "## Definition\n\nA firm.\n\n## Fiscal Challenges (2026)\n\nX.\n"})
        self.assertFalse(r.startswith("Error:"), r)
        body = self.w.disk("entities/acme.md")
        self.assertIn("## Overview", body)
        self.assertIn("## Fiscal Challenges\n", body)

    def test_structural_failures_still_return_immediately(self):
        # These make the later checks meaningless, so they are not collected — and the
        # already-exists reply carries the page's content, which a list would bury.
        agent.init_session()
        self.assertIn("requires a 'path'", agent.TOOL_FNS["create_file"]({}))
        self.assertIn("only writes inside wiki/", agent.TOOL_FNS["create_file"](
            {"path": "raw/x.md", "title": "X", "type": "entity", "body": "## Overview\n\nX.\n"}))
        self.assertIn("is missing", agent.TOOL_FNS["create_file"](
            {"path": "wiki/entities/x.md", "body": "## Overview\n\nX.\n"}))

    def test_an_existing_page_still_hands_back_its_content(self):
        agent.init_session()
        self.w.page("entities/acme.md", title="Acme Corp", type="entity",
                    body="# Acme Corp\n\n## Overview\n\nThe existing text.\n")
        r = agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/acme.md", "title": "Acme Corp", "type": "entity",
            "body": "## Background\n\nNo overview here.\n"})
        self.assertIn("The existing text.", r,
                      "the already-exists reply was replaced by a problem list")


if __name__ == "__main__":
    unittest.main()
