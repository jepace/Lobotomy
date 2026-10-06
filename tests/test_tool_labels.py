"""One label for a tool call, in every view that shows one.

Asked whether the chat log and the reading-list log differ, and then narrowed: *"it's the
chat log for AFTER an ingest is complete that does not display sections."* Fifty-odd lines
of this, against a handful of pages:

    ⚙ update_section wiki/entities/alberta.md
    ⚙ update_section wiki/entities/alberta-prosperity-project.md
    ⚙ update_section wiki/entities/alberta-prosperity-project.md

**The live view was never the problem; the record of it was.** Three places built this
label and `serve.py`'s saved display log was the odd one out — it took `args["path"]` and
nothing else, so a turn that read

    update_section wiki/entities/alberta.md § Overview

while it ran redrew from history as the first form the moment it finished. `agent.py`'s
live `/chat/events` stream and its debug log both appended `§ <section>` and always had.

`tool_arg_preview()` is now the only builder, and `serve.py` imports the NAME — `agent.foo`
inside that module is a NameError, and `save_history` wraps its parse in a try/except that
swallows everything, which is exactly how a mistake there stays invisible.

A second, independent drift in the same comparison, and this one was a bug rather than a
difference of wording: **`chat.html` rendered the same `tools` array with two different map
expressions**, one prefixing `⚙ ` and one not. Both ran over the whole array, so whichever
event arrived LAST decided how every earlier line was drawn — a single retry stripped the
gear off every tool line above it, which is why the pasted chat log had no `⚙` anywhere
while the reading list's had one per line. The icon lives in the TEXT now, and one
`renderTools()` draws the list.

The retry wording differed too, and the reading list had the worse half: `"Retrying in 60s…"`
against chat's `"AI busy — retrying in 60s (attempt 1/60)"`. Seven of the first in a row say
nothing about whether anything is happening; seven of the second count 1/60 to 7/60 and name
the cap. `window.agentEventLine` is the one formatter for both.
"""
import json
import pathlib
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import agent
import serve

TPL = pathlib.Path(__file__).resolve().parent.parent / "tools" / "templates"


def _run_event_line(ev):
    """Execute the REAL window.agentEventLine from base.html under node.

    Lifted rather than copied, for the reason every browser test in this suite is: a copy
    drifts from what ships, and drift between two copies of this exact function is the bug
    the module is about.
    """
    import json as _json
    import shutil as _shutil
    import subprocess as _subprocess
    if _shutil.which("node") is None:
        raise unittest.SkipTest("node not installed")
    src = (TPL / "base.html").read_text(encoding="utf-8")
    m = re.search(r"window\.agentEventLine = function\(ev\) \{.*?\n    \};", src, re.DOTALL)
    assert m, "could not lift agentEventLine out of base.html"
    script = ("var window = {};\n" + m.group(0)
              + "\nprocess.stdout.write(String(window.agentEventLine("
              + _json.dumps(ev) + ") || ''));")
    r = _subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise AssertionError("node failed: " + r.stderr)
    return r.stdout


class PreviewTest(unittest.TestCase):
    """The builder itself."""

    def p(self, fn, **args):
        return agent.tool_arg_preview(fn, args)

    def test_a_section_tool_names_its_section(self):
        self.assertEqual(
            self.p("update_section", path="wiki/entities/alberta.md", section="Overview"),
            "wiki/entities/alberta.md § Overview")

    def test_all_three_section_tools_do(self):
        for fn in ("read_section", "update_section", "append_section"):
            with self.subTest(fn=fn):
                self.assertIn("§", self.p(fn, path="w/a.md", section="S"))

    def test_a_timeline_entry_names_its_date(self):
        self.assertEqual(self.p("add_timeline_entry", path="w/o.md", date="2026-10-01",
                                text="t"),
                         "w/o.md @ 2026-10-01")

    def test_a_whole_page_tool_is_just_the_path(self):
        for fn in ("create_file", "update_file", "read_file"):
            with self.subTest(fn=fn):
                self.assertEqual(self.p(fn, path="w/a.md", title="X"), "w/a.md")

    def test_a_search_names_its_query(self):
        self.assertEqual(self.p("search_wiki", query="measles"), "measles")

    def test_order_of_the_arguments_does_not_change_the_label(self):
        """Dict order varies between calls; a preview that took the first value showed the
        path on some and the section on others."""
        a = agent.tool_arg_preview("update_section",
                                   {"path": "w/a.md", "section": "Overview"})
        b = agent.tool_arg_preview("update_section",
                                   {"section": "Overview", "path": "w/a.md"})
        self.assertEqual(a, b)

    def test_a_missing_section_is_visible_rather_than_blank(self):
        """A '?' says the call did not carry one; a blank reads as a page-level edit."""
        self.assertIn("?", self.p("update_section", path="w/a.md"))

    def test_an_unknown_tool_still_produces_something(self):
        self.assertEqual(self.p("some_new_tool", whatever="v"), "v")

    def test_no_arguments_at_all_is_safe(self):
        self.assertEqual(agent.tool_arg_preview("read_file", {}), "")
        self.assertEqual(agent.tool_arg_preview("read_file", None), "")

    def test_it_is_capped(self):
        self.assertLessEqual(len(self.p("search_wiki", query="x" * 500)), 80)


class SharedBuilderTest(unittest.TestCase):
    """Three callers, one function — the point of the exercise."""

    @classmethod
    def setUpClass(cls):
        cls.agent_src = pathlib.Path(agent.__file__).read_text(encoding="utf-8")
        cls.serve_src = pathlib.Path(serve.__file__).read_text(encoding="utf-8")

    def test_serve_uses_the_same_object(self):
        """serve.py imports names, so `agent.x` inside it is a NameError — and
        save_history swallows exceptions, so the mistake would be invisible."""
        self.assertIs(serve.tool_arg_preview, agent.tool_arg_preview)

    def test_serve_no_longer_builds_its_own(self):
        self.assertNotIn('args.get("path") or args.get("query")', self.serve_src)

    def test_the_live_stream_uses_it(self):
        self.assertIn("arg_preview = tool_arg_preview(fn_name, args)", self.agent_src)

    def test_the_debug_log_uses_it(self):
        self.assertIn("_preview = tool_arg_preview(fn_name, args)", self.agent_src)

    def test_there_is_exactly_one_definition(self):
        self.assertEqual(self.agent_src.count("def tool_arg_preview("), 1)

    def test_nothing_else_hand_builds_the_section_label(self):
        """The shape that drifted. One occurrence is the helper's own."""
        pattern = r'\{args\.get\("path", "\?"\)\} § '
        self.assertEqual(len(re.findall(pattern, self.agent_src)), 1)
        self.assertEqual(len(re.findall(pattern, self.serve_src)), 0)


class SavedHistoryTest(unittest.TestCase):
    """End to end: what the chat page redraws after the ingest finishes."""

    def setUp(self):
        self.w = TempWiki()
        self.w.__enter__()
        self._saved = serve.DISPLAY_LOG_FILE
        self.d = pathlib.Path(tempfile.mkdtemp())
        serve.DISPLAY_LOG_FILE = self.d / "display.json"

    def tearDown(self):
        serve.DISPLAY_LOG_FILE = self._saved
        self.w.__exit__(None, None, None)

    def _tools(self, calls):
        msgs = [{"role": "user", "content": "Ingest raw/x.md"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": n, "arguments": json.dumps(a)}}
                    for n, a in calls]}]
        serve.save_history(msgs, source="inbox")
        return json.loads(serve.DISPLAY_LOG_FILE.read_text(encoding="utf-8"))[-1]["tools"]

    def test_the_reported_line_keeps_its_section(self):
        tools = self._tools([("update_section",
                              {"path": "wiki/entities/alberta.md", "section": "Overview"})])
        self.assertEqual(tools, ["update_section  wiki/entities/alberta.md § Overview"])

    def test_two_edits_to_one_page_are_distinguishable(self):
        """The actual complaint: thirty update_section lines against a handful of pages,
        with no way to tell which section any of them touched."""
        tools = self._tools([
            ("update_section", {"path": "wiki/entities/alberta.md", "section": "Overview"}),
            ("update_section", {"path": "wiki/entities/alberta.md", "section": "Politics"}),
        ])
        self.assertEqual(len(set(tools)), 2, tools)

    def test_a_whole_page_write_is_unchanged(self):
        tools = self._tools([("create_file", {"path": "wiki/entities/x.md",
                                              "title": "X", "type": "entity"})])
        self.assertEqual(tools, ["create_file  wiki/entities/x.md"])

    def test_done_is_still_excluded(self):
        tools = self._tools([("read_file", {"path": "w/a.md"}),
                             ("done", {"summary": "finished"})])
        self.assertEqual(tools, ["read_file  w/a.md"])

    def test_malformed_arguments_do_not_lose_the_turn(self):
        """save_history swallows exceptions, so a raise here would silently drop the whole
        record rather than one label."""
        msgs = [{"role": "user", "content": "x"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "update_section", "arguments": "{not json"}}]}]
        serve.save_history(msgs, source="inbox")
        self.assertTrue(serve.DISPLAY_LOG_FILE.exists())


class EventLineTest(unittest.TestCase):
    """The browser half: one formatter for the live stream, in base.html."""

    @classmethod
    def setUpClass(cls):
        cls.base = (TPL / "base.html").read_text(encoding="utf-8")
        cls.chat = (TPL / "chat.html").read_text(encoding="utf-8")
        cls.inbox = (TPL / "inbox.html").read_text(encoding="utf-8")

    def test_the_formatter_is_defined_once_and_shared(self):
        self.assertIn("window.agentEventLine = function(ev)", self.base)
        self.assertEqual(self.chat.count("window.agentEventLine(ev)"), 2)
        self.assertEqual(self.inbox.count("window.agentEventLine(ev)"), 2)

    def test_neither_page_composes_its_own_retry_text(self):
        """The reading list said 'Retrying in 60s…' and chat said 'AI busy — retrying in
        60s (attempt 1/60)' from the same event."""
        for name, src in (("chat", self.chat), ("inbox", self.inbox)):
            with self.subTest(page=name):
                self.assertNotIn("'Retrying in '", src)
                self.assertNotIn("'AI busy", src)

    def test_the_better_retry_wording_is_the_one_that_survived(self):
        """RUN the real function rather than grepping for its text.

        The first version of this asserted `"attempt "` appears in base.html — which it
        does, in the COMMENT above the function explaining the bug. `mutate.py` deleted the
        attempt counter from the code and the test still passed. A substring that also
        appears in prose proves nothing about behaviour.
        """
        out = _run_event_line({"type": "retrying", "delay": 60, "attempt": 1, "max": 60})
        self.assertIn("AI busy", out)
        self.assertIn("(attempt 1/60)", out)

    def test_a_retry_without_a_cap_omits_the_counter(self):
        out = _run_event_line({"type": "retrying", "delay": 5, "attempt": 0, "max": 0})
        self.assertIn("retrying in 5s", out)
        self.assertNotIn("attempt", out)

    def test_a_server_supplied_message_wins(self):
        out = _run_event_line({"type": "retrying", "delay": 9, "msg": "Provider unavailable"})
        self.assertIn("Provider unavailable", out)

    def test_a_tool_event_carries_its_arg(self):
        out = _run_event_line({"type": "tool", "name": "update_section",
                               "arg": "wiki/entities/alberta.md § Overview"})
        self.assertIn("update_section", out)
        self.assertIn("§ Overview", out)

    def test_both_kinds_carry_their_own_icon(self):
        """So no renderer has to add one, which is what let a retry strip the gear."""
        tool = _run_event_line({"type": "tool", "name": "x", "arg": ""})
        retry = _run_event_line({"type": "retrying", "delay": 1, "attempt": 1, "max": 2})
        self.assertTrue(tool.startswith("\u2699"), repr(tool))
        self.assertTrue(retry.startswith("\u23f3"), repr(retry))

    def test_an_unknown_event_produces_no_line(self):
        self.assertEqual(_run_event_line({"type": "ping"}), "")

    def test_the_icon_is_in_the_text_not_in_one_branchs_markup(self):
        """The bug: chat rendered one array with two map expressions, one prefixing the
        gear and one not, so a single retry stripped it off every tool line above.

        Scoped to the JS map expressions. The first version of this test excluded the
        server-rendered Jinja line by stripping `{{ t }}`, which left the literal
        `<div class="tool-item">⚙ </div>` behind and failed against correct markup.
        """
        for expr in re.findall(r"tools\.map\(.*?\)\.join\(''\)", self.chat):
            self.assertNotIn("⚙", expr, f"a JS render expression prefixes the gear: {expr}")
        self.assertIn("\\u2699", self.base)

    def test_chat_has_exactly_one_render_expression(self):
        self.assertEqual(self.chat.count("""'<div class="tool-item">' + escHtml(t)"""), 1)
        self.assertIn("function renderTools()", self.chat)

    def test_the_server_rendered_history_still_draws_its_own_gear(self):
        """Completed turns come from Jinja, not from the event stream, so that one keeps
        its literal — and it is a single expression, so it cannot disagree with itself."""
        self.assertIn('<div class="tool-item">⚙ {{ t }}</div>', self.chat)


if __name__ == "__main__":
    unittest.main()
