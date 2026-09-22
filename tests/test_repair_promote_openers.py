"""promote_lead_to_opener: give a page the opener its template requires, where the text
for it is already there.

The rule that matters is what it REFUSES to do. Promoting an untitled lead paragraph is
mechanical — the text already says what Overview is for, it just has no heading. Renaming
an existing section, or writing prose that was never there, is not repair; those pages are
reported instead.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

LEAD = ("Lee Jae Myung is the President of South Korea, elected in 2025 after a snap "
        "election called when his predecessor was removed from office.")


class PromoteOpenerTest(TempWikiTestCase):

    def test_lead_paragraph_promoted_to_overview(self):
        self.w.page("entities/lee.md", title="Lee Jae Myung", type="entity",
                    body=f"# Lee Jae Myung\n\n{LEAD}\n\n## Political Career\n\nPragmatic.\n")
        agent.promote_lead_to_opener()
        body = self.w.disk("entities/lee.md")
        self.assertIn("## Overview", body)
        self.assertIn(LEAD, body)
        self.assertLess(body.index("## Overview"), body.index("## Political Career"))
        self.assertLess(body.index("## Overview"), body.index(LEAD),
                        "the heading must go above the prose it names")

    def test_concept_page_gets_definition_not_overview(self):
        self.w.page("concepts/tariffs.md", title="Tariffs", type="concept",
                    body=f"# Tariffs\n\n{LEAD}\n\n## How It Works\n\nMechanics.\n")
        agent.promote_lead_to_opener()
        body = self.w.disk("concepts/tariffs.md")
        self.assertIn("## Definition", body)
        self.assertNotIn("## Overview", body)

    def test_page_h1_is_kept_above_the_new_heading(self):
        self.w.page("entities/lee.md", title="Lee Jae Myung", type="entity",
                    body=f"# Lee Jae Myung\n\n{LEAD}\n\n## Political Career\n\nX.\n")
        agent.promote_lead_to_opener()
        body = self.w.disk("entities/lee.md")
        self.assertLess(body.index("# Lee Jae Myung"), body.index("## Overview"))

    def test_page_with_no_lead_paragraph_is_left_alone(self):
        self.w.page("entities/kim.md", title="Kim Jong-un", type="entity",
                    body="# Kim Jong-un\n\n## Background & Leadership\n\nHe has led.\n")
        before = self.w.disk("entities/kim.md")
        r = agent.promote_lead_to_opener()
        self.assertEqual(self.w.disk("entities/kim.md"), before,
                         "a page with nothing to promote must not be touched")
        self.assertTrue(any("kim.md" in x for x in r["needs_text"]), r)
        self.assertEqual(r["promoted"], [])

    def test_existing_section_is_never_renamed(self):
        self.w.page("entities/kim.md", title="Kim Jong-un", type="entity",
                    body="# Kim Jong-un\n\n## Background & Leadership\n\nHe has led.\n")
        agent.promote_lead_to_opener()
        self.assertIn("## Background & Leadership", self.w.disk("entities/kim.md"))

    def test_compliant_page_untouched(self):
        self.w.page("entities/ok.md", title="OK Corp", type="entity",
                    body="## Overview\n\nA firm.\n\n## Background\n\nB.\n")
        before = self.w.disk("entities/ok.md")
        r = agent.promote_lead_to_opener()
        self.assertEqual(self.w.disk("entities/ok.md"), before)
        self.assertEqual(r["pages"], 0)

    def test_source_and_synthesis_pages_ignored(self):
        # Only entity and concept pages have a required opener.
        self.w.page("sources/a-2026-x.md", title="A 2026 X", type="source",
                    body=f"# A 2026 X\n\n{LEAD}\n\n## Summary\n\nS.\n")
        before = self.w.disk("sources/a-2026-x.md")
        agent.promote_lead_to_opener()
        self.assertEqual(self.w.disk("sources/a-2026-x.md"), before)

    def test_dry_run_writes_nothing(self):
        self.w.page("entities/lee.md", title="Lee Jae Myung", type="entity",
                    body=f"# Lee Jae Myung\n\n{LEAD}\n\n## Political Career\n\nX.\n")
        before = self.w.disk("entities/lee.md")
        r = agent.promote_lead_to_opener(dry_run=True)
        self.assertEqual(self.w.disk("entities/lee.md"), before)
        self.assertEqual(len(r["promoted"]), 1, "a dry run must still report what it would do")

    def test_history_entry_recorded(self):
        self.w.page("entities/lee.md", title="Lee Jae Myung", type="entity",
                    body=f"# Lee Jae Myung\n\n{LEAD}\n\n## Political Career\n\nX.\n")
        agent.promote_lead_to_opener()
        revs = [x for x in agent.HISTORY_DIR.rglob("*.md") if x.is_file()]
        self.assertTrue(revs, "the promotion is not revertable — no history entry")

    def test_read_section_can_then_find_the_opener(self):
        # The whole point: the round the agent used to waste is gone.
        self.w.page("entities/lee.md", title="Lee Jae Myung", type="entity",
                    body=f"# Lee Jae Myung\n\n{LEAD}\n\n## Political Career\n\nX.\n")
        agent.promote_lead_to_opener()
        r = agent.TOOL_FNS["read_section"]({"path": "wiki/entities/lee.md",
                                            "section": "Overview"})
        self.assertFalse(r.startswith("Error:"), r)
        self.assertIn(LEAD, r)


if __name__ == "__main__":
    unittest.main()
