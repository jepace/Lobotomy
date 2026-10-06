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
import tempfile
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
        """Two expressions drawing one array is the bug this module exists for. Matched on
        the <div> rather than on whichever helper escapes, because the helper changed when
        the paths became links and this assertion should not have to."""
        self.assertEqual(self.chat.count("""'<div class="tool-item">' + """), 1)
        self.assertIn("function renderTools()", self.chat)

    def test_the_server_rendered_history_still_draws_its_own_gear(self):
        """Completed turns come from Jinja, not from the event stream, so that one keeps
        its literal — and it is a single expression, so it cannot disagree with itself."""
        self.assertIn('<div class="tool-item">⚙ {{ t }}</div>', self.chat)


if __name__ == "__main__":
    unittest.main()


def _run_tool_line(line):
    """Execute the REAL window.toolLineHtml from base.html under node."""
    import json as _json
    import shutil as _shutil
    import subprocess as _subprocess
    if _shutil.which("node") is None:
        raise unittest.SkipTest("node not installed")
    src = (TPL / "base.html").read_text(encoding="utf-8")
    i = src.index("    function _esc(t) {")
    j = src.index("    window.agentEventLine")
    script = ("var window = {};\n" + src[i:j]
              + "\nprocess.stdout.write(String(window.toolLineHtml("
              + _json.dumps(line) + ")));")
    r = _subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise AssertionError("node failed: " + r.stderr)
    return r.stdout


class ToolLineLinkTest(unittest.TestCase):
    """The logs name the file every write touched; reading one meant copying the path into
    the URL bar by hand."""

    def test_a_page_path_becomes_a_link(self):
        out = _run_tool_line("read_file  wiki/concepts/measles.md")
        self.assertIn('href="/wiki/concepts/measles.md"', out)

    def test_a_section_edit_links_to_the_section(self):
        """The line already says which section was edited, and that is where you want to
        land. python-markdown's toc extension gives every heading that id."""
        out = _run_tool_line("update_section  wiki/entities/alberta.md \u00a7 Overview")
        self.assertIn('href="/wiki/entities/alberta.md#overview"', out)

    def test_the_slug_matches_what_the_renderer_emits(self):
        out = _run_tool_line(
            "update_section  wiki/concepts/measles.md \u00a7 Resurgence & Elimination Status")
        self.assertIn("#resurgence-elimination-status", out)

    def test_a_slash_in_the_heading_slugs_the_same_way(self):
        out = _run_tool_line("read_section  wiki/entities/x.md \u00a7 Key Works / Products")
        self.assertIn("#key-works-products", out)

    def test_the_tool_name_is_left_outside_the_link(self):
        out = _run_tool_line("read_file  wiki/concepts/measles.md")
        self.assertTrue(out.startswith("read_file"), out)

    def test_a_search_query_is_not_linked(self):
        out = _run_tool_line("search_wiki  measles outbreak")
        self.assertNotIn("<a", out)

    def test_a_retry_line_is_not_linked(self):
        out = _run_tool_line("\u23f3 AI busy \u2014 retrying in 60s (attempt 1/60)")
        self.assertNotIn("<a", out)

    def test_an_empty_line_is_safe(self):
        self.assertEqual(_run_tool_line(""), "")

    def test_markup_in_the_text_is_escaped(self):
        """Every one of these strings is a path or a section name the MODEL chose."""
        out = _run_tool_line("create_file  <img src=x onerror=alert(1)>")
        self.assertNotIn("<img", out)
        self.assertIn("&lt;img", out)

    def test_a_hostile_path_is_not_turned_into_a_link(self):
        out = _run_tool_line("create_file  wiki/entities/<script>alert(1)</script>.md")
        self.assertNotIn("<a", out)
        self.assertNotIn("<script", out)

    def test_a_quote_in_a_section_name_cannot_break_out_of_the_href(self):
        out = _run_tool_line('update_section  wiki/entities/x.md \u00a7 A" onmouseover="x')
        self.assertNotIn('onmouseover="x"', out)
        self.assertNotIn('" onmouseover', out)


class LinkWiringTest(unittest.TestCase):
    """Three views show these lines; one function links them."""

    @classmethod
    def setUpClass(cls):
        cls.base = (TPL / "base.html").read_text(encoding="utf-8")
        cls.chat = (TPL / "chat.html").read_text(encoding="utf-8")
        cls.inbox = (TPL / "inbox.html").read_text(encoding="utf-8")

    def test_it_is_defined_once(self):
        self.assertEqual(self.base.count("window.toolLineHtml = function"), 1)

    def test_the_chat_live_stream_uses_it(self):
        self.assertIn("window.toolLineHtml(t)", self.chat)

    def test_the_reading_list_uses_it(self):
        self.assertIn("window.toolLineHtml(t)", self.inbox)

    def test_completed_turns_are_linkified_too(self):
        """They come from Jinja rather than the event stream — the half that was missing
        the sections in the first place."""
        self.assertIn('class="tool-list static"', self.chat)
        self.assertIn(".tool-list.static .tool-item", self.chat)
        self.assertIn("window.toolLineHtml(el.textContent)", self.chat)

    def test_the_static_pass_cannot_touch_a_live_list(self):
        """Scoped to .static so it never re-processes what the live renderer owns."""
        m = re.search(r"querySelectorAll\('([^']*tool-item[^']*)'\)", self.chat)
        self.assertIsNotNone(m)
        self.assertIn(".static", m.group(1))

    def test_neither_page_escapes_by_hand_any_more(self):
        """Hand-rolled escaping beside a helper that also escapes is how one of them ends
        up doing neither."""
        self.assertNotIn("replace(/</g, '&lt;')", self.inbox)

    def test_the_link_is_styled_in_both_lists(self):
        self.assertIn(".tool-link", self.chat)
        self.assertIn(".tool-link", self.inbox)



class RefusedCallTest(unittest.TestCase):
    """A tool line is written whether the call succeeded or was REFUSED.

    Reported as a link that 404s: the log showed

        ⚙ create_file wiki/entities/2026-r-a-f-fairford-bombing-plot.md

    and the page did not exist. It never had. The agent called `create_file` for the event
    page BEFORE the source page existed, `create_file` refused — "create the source page
    first" — and the line appeared anyway, because the event is emitted whatever the tool
    returned. That was merely ambiguous until the path became a link; **a link asserts the
    page exists**, so making these clickable turned a vague line into a false one.

    The outcome was already known at the yield and already recorded in
    `_session_tool_calls`; it just never reached the view. It does now, and a refused line
    is marked, carries its reason, and is NOT linked — a 404 is worse than plain text,
    because it claims otherwise.
    """

    REFUSAL = ("Error: create_file refused — create the source page "
               "(wiki/sources/...) first, then create entity/concept pages.")

    def test_a_refused_call_is_marked(self):
        out = _run_event_line({"type": "tool", "name": "create_file",
                               "arg": "wiki/entities/x.md", "ok": False,
                               "why": "create the source page first"})
        self.assertTrue(out.startswith("✗"), repr(out))

    def test_a_successful_call_keeps_the_gear(self):
        out = _run_event_line({"type": "tool", "name": "create_file",
                               "arg": "wiki/entities/x.md", "ok": True})
        self.assertTrue(out.startswith("⚙"), repr(out))

    def test_an_event_with_no_ok_field_is_treated_as_success(self):
        """Older stored events carry no flag; marking them all as failures would be worse
        than the ambiguity being fixed."""
        out = _run_event_line({"type": "tool", "name": "read_file", "arg": "w/a.md"})
        self.assertTrue(out.startswith("⚙"), repr(out))

    def test_the_refusal_says_why(self):
        out = _run_event_line({"type": "tool", "name": "create_file", "arg": "w/a.md",
                               "ok": False, "why": "create the source page first"})
        self.assertIn("create the source page first", out)

    def test_a_refused_line_is_not_linked(self):
        """The whole point. The path names a page the call did not write.

        The line must END in the path. The first version of this test appended a trailing
        reason, and the linking regex is anchored at end-of-string — so it never matched,
        the marker guard was never exercised, and mutate.py removed the guard with this
        test still green. A fixture that cannot reach the code it names proves nothing.
        """
        out = _run_tool_line("\u2717 create_file  wiki/entities/x.md")
        self.assertNotIn("<a", out)
        # The same line WITHOUT the marker IS linked — proof the fixture reaches the guard.
        self.assertIn("<a", _run_tool_line("\u2699 create_file  wiki/entities/x.md"))

    def test_a_refused_line_is_still_escaped(self):
        out = _run_tool_line("✗ create_file  <img src=x>")
        self.assertIn("&lt;img", out)
        self.assertNotIn("<img", out)

    def test_a_successful_line_is_still_linked(self):
        out = _run_tool_line("⚙ create_file  wiki/entities/x.md")
        self.assertIn("<a", out)


class RefusedCallInSavedLogTest(unittest.TestCase):
    """The same, for the view that redraws after the ingest finishes."""

    def setUp(self):
        self.w = TempWiki()
        self.w.__enter__()
        self._saved = serve.DISPLAY_LOG_FILE
        serve.DISPLAY_LOG_FILE = pathlib.Path(tempfile.mkdtemp()) / "display.json"

    def tearDown(self):
        serve.DISPLAY_LOG_FILE = self._saved
        self.w.__exit__(None, None, None)

    def _tools(self, calls, results):
        msgs = [{"role": "user", "content": "Ingest raw/x.md"},
                {"role": "assistant", "tool_calls": [
                    {"id": i, "function": {"name": n, "arguments": json.dumps(a)}}
                    for i, n, a in calls]}]
        msgs += [{"role": "tool", "tool_call_id": i, "name": "x", "content": c}
                 for i, c in results.items()]
        serve.save_history(msgs, source="inbox")
        return json.loads(serve.DISPLAY_LOG_FILE.read_text(encoding="utf-8"))[-1]["tools"]

    def test_the_reported_case(self):
        tools = self._tools(
            [("c1", "create_file", {"path": "wiki/entities/2026-r-a-f-fairford-bombing-plot.md"})],
            {"c1": "Error: create_file refused — create the source page first."})
        self.assertTrue(tools[0].startswith("✗"), tools)
        self.assertIn("create the source page first", tools[0])

    def test_a_successful_call_is_unmarked(self):
        tools = self._tools([("c1", "create_file", {"path": "wiki/sources/s.md"})],
                            {"c1": "Created wiki/sources/s.md (900 bytes)"})
        self.assertEqual(tools, ["create_file  wiki/sources/s.md"])

    def test_a_missing_result_counts_as_success(self):
        """A provider that omits the id would otherwise mark every line as a failure."""
        tools = self._tools([("", "read_file", {"path": "w/a.md"})], {})
        self.assertFalse(tools[0].startswith("✗"), tools)

    def test_both_outcomes_in_one_turn(self):
        tools = self._tools(
            [("c1", "create_file", {"path": "wiki/entities/e.md"}),
             ("c2", "create_file", {"path": "wiki/sources/s.md"})],
            {"c1": "Error: create_file refused — source page first.",
             "c2": "Created wiki/sources/s.md"})
        self.assertTrue(tools[0].startswith("✗"), tools)
        self.assertFalse(tools[1].startswith("✗"), tools)


class EventContractTest(unittest.TestCase):
    """The SERVER's half: does the stream actually send the outcome?

    Every other test here builds the event dict by hand and checks the formatter. That
    proves the renderer, not the producer — `mutate.py` set `"ok": True` in agent.py and
    nothing objected, because no test had ever driven the real loop. This one scripts the
    model's replies through `_post_with_fallback` and reads what comes out of
    `stream_agent_turn`.
    """

    def setUp(self):
        self.w = TempWiki()
        self.w.__enter__()
        agent.init_session(inbox_path="raw/x.md")

    def tearDown(self):
        self.w.__exit__(None, None, None)

    def _events(self, calls):
        """Run the real loop with a scripted model: one round of `calls`, then stop."""
        replies = [
            {"choices": [{"message": {"tool_calls": [
                {"id": f"c{i}", "type": "function",
                 "function": {"name": n, "arguments": json.dumps(a)}}
                for i, (n, a) in enumerate(calls)]}}]},
            {"choices": [{"message": {"content": "done"}}]},
        ]
        it = iter(replies)

        def fake_post(client, payload, primary):
            return next(it), primary

        # Pacing is deliberate in production and pure cost here. `inter_request_delay: 5`
        # from config.json.example times two requests per call times four tests is forty
        # seconds, which mutate.py would then pay once per mutation. Only that one key is
        # neutralized; every other config read still goes to the real value.
        _real_cfg_int = agent.cfg_int

        def no_delay(section, key, default=None):
            if (section, key) == ("llm", "inter_request_delay"):
                return 0
            return _real_cfg_int(section, key, default)

        saved = (agent._post_with_fallback, agent.cfg_int,
                 agent._rpm_wait_sync, agent._rpm_wait_streaming)
        agent._post_with_fallback = fake_post
        agent.cfg_int = no_delay
        agent._rpm_wait_sync = lambda: None
        agent._rpm_wait_streaming = lambda: iter(())
        try:
            out = []
            for line in agent.stream_agent_turn({}, "m", [{"role": "user", "content": "go"}],
                                                "sys"):
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            return [e for e in out if e.get("type") == "tool"]
        finally:
            (agent._post_with_fallback, agent.cfg_int, agent._rpm_wait_sync,
             agent._rpm_wait_streaming) = saved

    def test_a_refused_call_reports_ok_false(self):
        """create_file for an entity page before the source page exists — the reported
        case, which produced a link to a page that was never written."""
        evs = self._events([("create_file", {
            "path": "wiki/entities/2026-r-a-f-fairford-bombing-plot.md",
            "title": "2026 R.A.F. Fairford Bombing Plot", "type": "entity",
            "body": "## Overview\n\nA plot.\n"})])
        self.assertEqual(len(evs), 1, evs)
        self.assertIs(evs[0]["ok"], False)
        self.assertFalse((self.w.wiki / "entities"
                          / "2026-r-a-f-fairford-bombing-plot.md").exists())

    def test_the_refusal_reason_travels_with_it(self):
        evs = self._events([("create_file", {
            "path": "wiki/entities/e.md", "title": "E", "type": "entity",
            "body": "## Overview\n\nx\n"})])
        self.assertIn("source page", evs[0]["why"])

    def test_a_successful_call_reports_ok_true(self):
        evs = self._events([("create_file", {
            "path": "wiki/sources/s-2026-x.md", "title": "S 2026 X", "type": "source",
            "body": ("## Summary\n\nA source.\n\n## Claims\n\n- A claim.\n\n"
                     "## Entities\n\n- Someone\n\n## Concepts\n\n- Something\n\n"
                     "## Quotes\n\n- \"q\"\n\n## Context\n\nc\n")})])
        self.assertIs(evs[0]["ok"], True)
        self.assertEqual(evs[0]["why"], "")

    def test_the_arg_still_carries_the_section(self):
        """The label and the outcome ride the same event; neither displaced the other."""
        self.w.page("entities/a.md", title="A", type="entity",
                    body="## Overview\n\nx.\n")
        agent._read_file("wiki/entities/a.md")
        evs = self._events([("update_section", {"path": "wiki/entities/a.md",
                                                "section": "Overview",
                                                "content": "Rewritten overview text."})])
        self.assertIn("§ Overview", evs[0]["arg"])
