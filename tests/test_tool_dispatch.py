"""The layer between the model and the tool functions.

Every other test here calls `agent._read_file(...)` and friends directly. The model never
does that: it emits a tool name and a JSON object, and `TOOL_FNS[name](args)` translates
that into a Python call. Nothing exercised the translation, and translation is where a
real shipped bug lived — `int(a.get("offset", 0) or 0)` collapsed "no offset given" and
"offset 0" into the same call, because 0 is falsy, which made the outline unreachable in
one direction and paging unreachable in the other.

Reverting that exact line used to leave all 90 tests green. These are the tests that go
red instead.

Arguments here are deliberately model-shaped: plain dicts, string values where a model
would send strings, and missing keys rather than defaults.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class ToolRegistryTest(unittest.TestCase):
    """TOOL_FNS and TOOL_DEFS are two hand-maintained lists of the same tools."""

    def test_every_advertised_tool_is_callable(self):
        advertised = {t["function"]["name"] for t in agent.TOOL_DEFS}
        missing = sorted(advertised - set(agent.TOOL_FNS))
        self.assertEqual(missing, [], f"described to the model but not implemented: {missing}")

    def test_every_callable_tool_is_advertised(self):
        advertised = {t["function"]["name"] for t in agent.TOOL_DEFS}
        unadvertised = sorted(set(agent.TOOL_FNS) - advertised)
        self.assertEqual(unadvertised, [],
                         f"implemented but never described to the model: {unadvertised}")

    def test_every_tool_appears_in_the_quick_reference(self):
        # system_prompt() appends a table the model reads before the schemas. A tool
        # missing from it is one the model has to discover by guessing.
        prompt = agent.system_prompt()
        missing = [n for n in agent.TOOL_FNS if n not in prompt]
        self.assertEqual(missing, [], f"absent from the quick-reference table: {missing}")

    def test_declared_required_args_exist_in_properties(self):
        for t in agent.TOOL_DEFS:
            fn = t["function"]
            params = fn.get("parameters", {})
            props, required = params.get("properties", {}), params.get("required", [])
            for r in required:
                self.assertIn(r, props, f"{fn['name']}: required arg {r!r} has no schema")


class ReadFileOffsetDispatchTest(TempWikiTestCase):
    """The regression the direct-call tests cannot see."""

    def _big_page(self):
        sections = "".join(f"## Section {i}\n\n{'word ' * 400}\n\n" for i in range(40))
        self.w.page("entities/big.md", title="Big", type="entity",
                    body="## Overview\n\nIntro.\n\n" + sections)
        return "wiki/entities/big.md"

    def test_no_offset_key_returns_an_outline(self):
        path = self._big_page()
        out = agent.TOOL_FNS["read_file"]({"path": path})
        self.assertIn("[OUTLINE", out)
        self.assertNotIn("[TRUNCATED", out)

    def test_explicit_offset_zero_returns_text_not_an_outline(self):
        path = self._big_page()
        out = agent.TOOL_FNS["read_file"]({"path": path, "offset": 0})
        self.assertIn("[TRUNCATED", out)
        self.assertNotIn("[OUTLINE", out)

    def test_offset_sent_as_a_string_still_pages(self):
        # Models routinely send numbers as strings.
        path = self._big_page()
        out = agent.TOOL_FNS["read_file"]({"path": path, "offset": "0"})
        self.assertIn("[TRUNCATED", out)
        self.assertNotIn("[OUTLINE", out)

    def test_offset_none_is_treated_as_absent(self):
        path = self._big_page()
        out = agent.TOOL_FNS["read_file"]({"path": path, "offset": None})
        self.assertIn("[OUTLINE", out)

    def test_outline_and_offset_zero_differ(self):
        # The heart of it: these two calls must not produce the same thing.
        path = self._big_page()
        outline = agent.TOOL_FNS["read_file"]({"path": path})
        paged = agent.TOOL_FNS["read_file"]({"path": path, "offset": 0})
        self.assertNotEqual(outline, paged)


class GuardsReachableThroughDispatchTest(TempWikiTestCase):
    """A guard that only fires on the direct call is not protecting anything."""

    def test_create_file_heading_guard_fires_through_dispatch(self):
        r = agent.TOOL_FNS["create_file"]({
            "path": "wiki/concepts/ebola.md", "title": "Ebola", "type": "concept",
            "body": "## Definition\n\nA virus.\n\n## 2026 Outbreak\n\nCases rose.\n"})
        self.assertTrue(r.startswith("Error:"), r)

    def test_create_file_opener_swap_absorbed_through_dispatch(self):
        r = agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/acme.md", "title": "Acme Corp", "type": "entity",
            "body": "## Definition\n\nA firm.\n"})
        self.assertFalse(r.startswith("Error:"), r)
        self.assertIn("## Overview", self.w.disk("entities/acme.md"))

    def test_add_timeline_entry_sorts_through_dispatch(self):
        self.w.page("entities/outbreak.md", title="Outbreak", type="entity",
                    body="## Overview\n\nOngoing.\n")
        for d, t in (("2026-05-02", "Declared over."), ("2026-02-27", "First case.")):
            agent.TOOL_FNS["add_timeline_entry"]({
                "path": "wiki/entities/outbreak.md", "date": d, "text": t})
        body = self.w.disk("entities/outbreak.md")
        self.assertLess(body.index("First case."), body.index("Declared over."))

    def test_update_file_requires_a_read_through_dispatch(self):
        self.w.page("entities/x.md", title="X Corp", type="entity",
                    body="## Overview\n\nA firm.\n")
        r = agent.TOOL_FNS["update_file"]({
            "path": "wiki/entities/x.md",
            "content": "## Overview\n\nRewritten without reading.\n"})
        self.assertTrue(r.startswith("Error:"), r)

    def test_missing_required_arg_is_an_error_not_a_crash(self):
        # TOOL_FNS uses .get() throughout precisely so a malformed call comes back as a
        # tool-level error the model can correct, never a KeyError that unwinds the loop.
        for name, args in (("read_file", {}), ("create_file", {}), ("update_file", {}),
                           ("update_section", {}), ("append_section", {}),
                           ("replace_text", {}), ("add_timeline_entry", {}),
                           ("lookup_titles", {}), ("search_wiki", {})):
            with self.subTest(tool=name):
                try:
                    result = agent.TOOL_FNS[name](args)
                except Exception as e:                       # noqa: BLE001 - that is the point
                    self.fail(f"{name} raised {type(e).__name__} on empty args: {e}")
                self.assertIsInstance(result, (str, list))
                if isinstance(result, str):
                    self.assertTrue(result.startswith("Error:"),
                                    f"{name} on empty args should be an Error:, got {result[:80]!r}")


if __name__ == "__main__":
    unittest.main()
