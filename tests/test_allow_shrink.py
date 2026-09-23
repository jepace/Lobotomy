"""update_section's shrink guard, and the allow_shrink opt-out that makes cleanup possible.

The guard exists to catch an ingest that silently condensed a page it was supposed to add
to — the model runs out of output budget and hands back a shorter version rather than
failing loudly, and nothing else would notice.

But consolidating a page IS shrinkage. Moving a duplicated point out of one section and
into another makes the first one smaller, so without an opt-out the section tools cannot
clean a page up at all — and on a page over half the output budget, update_file is steered
away too, leaving no route that works. allow_shrink is the deliberate one.

It is off by default on purpose: an accidental condensation is never deliberate, so the
flag can only be set by a caller that meant it.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

LONG = "A detailed point about tariffs and the trade negotiations. " * 30


class ShrinkGuardTest(TempWikiTestCase):

    def setUp(self):
        super().setUp()
        self.p = self.w.page("entities/mark-carney.md", title="Mark Carney", type="entity",
                             body=f"# Mark Carney\n\n## Overview\n\nShort.\n\n"
                                  f"## Positions\n\n{LONG}\n")
        self.w.read("wiki/entities/mark-carney.md")
        self.w.read_section("wiki/entities/mark-carney.md", "Positions")

    def _shrink(self, **extra):
        args = {"path": "wiki/entities/mark-carney.md", "section": "Positions",
                "content": "Trimmed to one line."}
        args.update(extra)
        return agent.TOOL_FNS["update_section"](args)

    def test_shrink_refused_by_default(self):
        before = self.p.read_text(encoding="utf-8")
        r = self._shrink()
        self.assertTrue(r.startswith("Error:"), r)
        self.assertEqual(self.p.read_text(encoding="utf-8"), before,
                         "a refused shrink still wrote to the page")

    def test_the_refusal_names_allow_shrink(self):
        # A refusal must name a move that works — otherwise a deliberate cleanup has no
        # route at all and the agent just retries a shortened rewrite.
        r = self._shrink()
        self.assertIn("allow_shrink", r)

    def test_the_refusal_says_to_write_the_destination_first(self):
        # Order is the whole safety property: shrink the source before the destination
        # write lands and the text is gone.
        r = self._shrink()
        self.assertIn("first", r.lower())

    def test_allow_shrink_true_permits_it(self):
        r = self._shrink(allow_shrink=True)
        self.assertFalse(r.startswith("Error:"), r)
        self.assertIn("Trimmed to one line.", self.w.disk("entities/mark-carney.md"))

    def test_allow_shrink_accepts_the_string_a_model_sends(self):
        r = self._shrink(allow_shrink="true")
        self.assertFalse(r.startswith("Error:"), r)

    def test_allow_shrink_false_is_still_refused(self):
        self.assertTrue(self._shrink(allow_shrink=False).startswith("Error:"))
        self.assertTrue(self._shrink(allow_shrink="false").startswith("Error:"))

    def test_growth_never_needs_the_flag(self):
        r = agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/mark-carney.md", "section": "Positions",
            "content": LONG + " And one more point."})
        self.assertFalse(r.startswith("Error:"), r)

    def test_allow_shrink_does_not_disable_the_other_guards(self):
        # It is an exemption from ONE check, not a skeleton key: the read-before-write
        # requirement and the heading rules still apply.
        agent.init_session()                       # forget that the section was read
        r = agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/mark-carney.md", "section": "Positions",
            "content": "Trimmed.", "allow_shrink": True})
        self.assertTrue(r.startswith("Error:"), "read-before-write was bypassed")
        self.assertIn("had not read", r)

    def test_allow_shrink_still_refuses_a_dated_heading(self):
        r = agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/mark-carney.md", "section": "Positions",
            "content": "Short.\n\n## 2026 Outbreak\n\nX.", "allow_shrink": True})
        self.assertTrue(r.startswith("Error:"), "the heading rules were bypassed")

    def test_the_shrink_is_revertable(self):
        self._shrink(allow_shrink=True)
        revs = [x for x in agent.HISTORY_DIR.rglob("*.md") if x.is_file()]
        self.assertTrue(revs, "a deliberate shrink left no history entry to undo it")


class ToolSchemaTest(unittest.TestCase):

    def test_allow_shrink_is_advertised_to_the_model(self):
        # A flag the model is never told about is a flag it will never use.
        spec = [t for t in agent.TOOL_DEFS
                if t["function"]["name"] == "update_section"][0]
        props = spec["function"]["parameters"]["properties"]
        self.assertIn("allow_shrink", props)
        self.assertNotIn("allow_shrink", spec["function"]["parameters"]["required"])

    def test_the_reorganize_workflow_reaches_the_model(self):
        self.assertIn("Reorganize Workflow", agent.system_prompt())


if __name__ == "__main__":
    unittest.main()
