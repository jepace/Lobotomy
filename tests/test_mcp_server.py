"""The MCP server and the `/api/mcp/*` routes behind it.

Asked for as *"I made a mcp for another app. How about mcp for lobotomy? Then I can get
you to rewrite pages..."* — which lands on the gap every report-only tool here stops at:
`overview_drift.py`, `page_report.py`, `bleeding_titles.py` and RATCHET all refuse an
`--apply` because the repair is a judgement about what the page is for.

Two things these tests exist to pin, both of them design decisions rather than details:

**The caller's output budget is not `config.json`'s.** Four guards derive a threshold from
it and all four read `cfg_int("llm", "max_tokens")` before this. That is right for the
configured provider and wrong for an MCP client, which was being refused a rewrite it can
actually perform — on exactly the RATCHET sections no agent tool can repair, i.e. the
single best reason the server exists.

**The server is an HTTP client, never a second writer.** CLAUDE.md records that the ingest
split was deferred because a second writer is what the autolinker's cache invalidation
assumes is not true. A filesystem MCP server is that writer by another door, so
`NoSecondWriterTest` asserts the module cannot become one.
"""
import json
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import harness
import agent
import serve
import mcp_server


HUGE = "\n\n".join(f"Paragraph {i} opens the discussion. " + "word " * 900
                   for i in range(9))

PAGE = ("---\ntitle: Donald Trump\ntype: entity\ncreated: 2026-01-01\n---\n\n"
        "# Donald Trump\n\n## Overview\nHe is a politician who favours tariffs.\n\n"
        f"## Key Policies\n{HUGE}\n")


# ===========================================================================
# The caller's budget
# ===========================================================================

class CallerBudgetTest(unittest.TestCase):
    """`agent.output_budget_*` — the fix without which this server is locked out of its
    best job by a guard I shipped the week before."""

    def test_it_defaults_to_the_configured_providers_budget(self):
        self.assertEqual(agent.output_budget_tokens(),
                         agent.cfg_int("llm", "max_tokens", default=16384))

    def test_a_caller_can_declare_a_bigger_one(self):
        with agent.output_budget(200_000):
            self.assertEqual(agent.output_budget_tokens(), 200_000)

    def test_it_is_restored_afterwards(self):
        """serve.py's threads are reused between requests, so a budget left set would
        apply to whatever that thread served next — an ingest silently running with an
        MCP client's limits. write_reason's rule, for write_reason's reason."""
        before = agent.output_budget_tokens()
        with agent.output_budget(200_000):
            pass
        self.assertEqual(agent.output_budget_tokens(), before)

    def test_it_is_restored_even_when_the_block_raises(self):
        before = agent.output_budget_tokens()
        with self.assertRaises(RuntimeError):
            with agent.output_budget(200_000):
                raise RuntimeError("boom")
        self.assertEqual(agent.output_budget_tokens(), before)

    def test_an_absurd_budget_is_clamped_not_trusted(self):
        """A client knows its own limit; it does not get to switch a guard off by
        claiming an infinite one, because every threshold here scales with it."""
        with agent.output_budget(10 ** 9):
            self.assertEqual(agent.output_budget_tokens(),
                             agent._MAX_OUTPUT_BUDGET_TOKENS)

    def test_garbage_neither_raises_nor_changes_anything(self):
        before = agent.output_budget_tokens()
        agent.set_output_budget_tokens("not a number")
        self.assertEqual(agent.output_budget_tokens(), before)

    def test_every_budget_guard_reads_the_caller_not_the_config(self):
        """The assertion that makes this a fix rather than one helper nobody calls.

        Four guards share the rule. Grepping for the helper's NAME would pass on a file
        that merely mentions it, so this drives the behaviour each one controls and
        requires every answer to move with the caller's budget.
        """
        big = 93_637
        self.assertFalse(agent._section_rewritable(big))
        with agent.output_budget(200_000):
            self.assertTrue(agent._section_rewritable(big),
                            "_section_rewritable still reads config")

    def test_no_guard_still_derives_its_threshold_from_config_directly(self):
        """Structural half: the two remaining `max_tokens` config reads are the actual
        provider request payloads, which SHOULD read config. A third is a guard that was
        missed."""
        src = (Path(agent.__file__)).read_text(encoding="utf-8")
        offenders = [ln.strip() for ln in src.splitlines()
                     if 'cfg_int("llm", "max_tokens"' in ln
                     and "* 4" in ln]
        self.assertEqual(offenders, [],
                         f"a guard still derives its budget from config: {offenders}")


# ===========================================================================
# The HTTP surface
# ===========================================================================

class McpRouteTest(unittest.TestCase):
    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self._saved = (serve.RAW_DIR, serve.WIKI_DIR, serve.REPO_ROOT,
                       agent.REPO_ROOT)
        serve.RAW_DIR, serve.WIKI_DIR = self.w.raw, self.w.wiki
        serve.REPO_ROOT = agent.REPO_ROOT = self.w.root

        def _restore():
            (serve.RAW_DIR, serve.WIKI_DIR, serve.REPO_ROOT,
             agent.REPO_ROOT) = self._saved
        self.addCleanup(_restore)

        self.page = self.w.wiki / "entities" / "donald-trump.md"
        self.page.parent.mkdir(parents=True, exist_ok=True)
        self.page.write_text(PAGE, encoding="utf-8")

        self._cfg = serve.cfg_get
        serve.cfg_get = lambda s, k, d=None: (
            "secret-mcp" if (s, k) == ("api", "mcp_key") else self._cfg(s, k, d))
        self.addCleanup(lambda: setattr(serve, "cfg_get", self._cfg))
        self.c = serve.app.test_client()

    def _get(self, route, key="secret-mcp", **params):
        return self.c.get(route, query_string=params,
                          headers={"Authorization": f"Bearer {key}"})

    # -- auth ---------------------------------------------------------------

    def test_it_uses_its_own_key_not_the_push_key(self):
        """push_key is on a phone and in a browser bookmark and can only add a reading-
        list item; mcp_key reads every page in the wiki. Different blast radius, so
        revoking one must not depend on the other."""
        serve.cfg_get = lambda s, k, d=None: (
            "the-push-key" if (s, k) == ("api", "push_key") else
            "secret-mcp" if (s, k) == ("api", "mcp_key") else self._cfg(s, k, d))
        r = self._get("/api/mcp/read", key="the-push-key", path="wiki/entities/donald-trump.md")
        self.assertEqual(r.status_code, 403)

    def test_it_fails_closed_when_no_key_is_configured(self):
        """`"" == ""` authenticating everyone is the failure this shape exists to avoid,
        on routes that need no login."""
        serve.cfg_get = lambda s, k, d=None: ("" if (s, k) == ("api", "mcp_key")
                                              else self._cfg(s, k, d))
        r = self._get("/api/mcp/read", key="", path="wiki/entities/donald-trump.md")
        self.assertEqual(r.status_code, 501)

    def test_a_wrong_key_is_refused(self):
        self.assertEqual(
            self._get("/api/mcp/read", key="nope",
                      path="wiki/entities/donald-trump.md").status_code, 403)

    def test_every_mcp_route_is_behind_the_key(self):
        """Asserted over the ROUTES Flask knows about, so a route added later without
        auth fails here rather than being discovered by a stranger."""
        paths = [str(r.rule) for r in serve.app.url_map.iter_rules()
                 if str(r.rule).startswith("/api/mcp/")]
        self.assertTrue(paths, "no /api/mcp routes registered")
        for rule in paths:
            with self.subTest(rule=rule):
                r = self.c.get(rule, query_string={"path": "wiki/entities/donald-trump.md",
                                                   "q": "x"})
                self.assertIn(r.status_code, (401, 501),
                              f"{rule} answered {r.status_code} with no key")

    # -- path containment ---------------------------------------------------

    def test_a_path_escaping_the_wiki_is_refused(self):
        for bad in ("wiki/../config.json", "wiki/../../etc/passwd.md",
                    "raw/something.md", "/etc/passwd", "wiki/entities/x.txt"):
            with self.subTest(bad=bad):
                r = self._get("/api/mcp/read", path=bad)
                self.assertEqual(r.status_code, 400, f"{bad} was not refused")

    def test_the_history_store_is_not_a_page(self):
        """A revision is a record, never a page — the `.history` rule every tool that
        walks this tree has to honour."""
        h = self.w.wiki / ".history" / "entities" / "donald-trump.md"
        h.mkdir(parents=True, exist_ok=True)
        (h / "20260101000000000000.md").write_text("old", encoding="utf-8")
        r = self._get("/api/mcp/read",
                      path="wiki/.history/entities/donald-trump.md/20260101000000000000.md")
        self.assertEqual(r.status_code, 400)

    # -- reads --------------------------------------------------------------

    def test_a_section_is_read_through_the_agents_own_tool(self):
        r = self._get("/api/mcp/read", path="wiki/entities/donald-trump.md",
                      section="Overview")
        self.assertEqual(r.status_code, 200)
        self.assertIn("favours tariffs", r.get_json()["content"])

    def test_a_huge_section_is_outlined_at_the_default_budget(self):
        r = self._get("/api/mcp/read", path="wiki/entities/donald-trump.md",
                      section="Key Policies", budget=16384)
        body = r.get_json()["content"]
        self.assertIn("append_section", body)
        self.assertLess(len(body), agent._unlinked_len(HUGE) // 4)

    def test_and_handed_over_WHOLE_at_a_bigger_one(self):
        """The unlock, and the reason the budget is a request parameter: this client can
        rewrite that section, so refusing to show it to them is the bug."""
        r = self._get("/api/mcp/read", path="wiki/entities/donald-trump.md",
                      section="Key Policies", budget=200_000)
        body = r.get_json()["content"]
        self.assertIn("Paragraph 8 opens the discussion.", body)
        self.assertNotIn("append_section", body)

    def test_the_reply_says_which_budget_was_applied(self):
        """A caller that cannot tell whether its budget took effect cannot tell an
        outline from a short section."""
        r = self._get("/api/mcp/read", path="wiki/entities/donald-trump.md",
                      section="Overview", budget=200_000)
        self.assertEqual(r.get_json()["budget_tokens"], 200_000)

    def test_a_missing_page_is_reported_not_crashed(self):
        r = self._get("/api/mcp/read", path="wiki/entities/nope.md")
        self.assertEqual(r.status_code, 200)
        self.assertIn("not", r.get_json()["content"].lower())

    def test_search_finds_a_page(self):
        r = self._get("/api/mcp/search", q="tariffs")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.get_json()["count"], 1)

    def test_search_requires_a_query(self):
        self.assertEqual(self._get("/api/mcp/search", q="").status_code, 400)

    def test_the_report_names_the_ratchet_section(self):
        """The finding this server exists to act on."""
        r = self._get("/api/mcp/report", path="wiki/entities/donald-trump.md",
                      budget=16384)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([x["name"] for x in r.get_json()["ratchet"]], ["Key Policies"])

    def test_the_report_does_not_re_send_the_whole_page(self):
        """`analyze` carries every section's parsed BODY for the CLI's printer. Shipping
        that over the wire would send the page twice in one reply, to a caller that asked
        what is wrong with it rather than what it says.

        Measured as a SIZE, not as the absence of a sentence: findings legitimately quote
        snippets — a REPEAT names both copies, a PILE quotes its dated openers — so a
        string assertion here would forbid the report from being useful. The property is
        that the reply is a report and not a copy of the page.
        """
        r = self._get("/api/mcp/report", path="wiki/entities/donald-trump.md")
        self.assertEqual(r.status_code, 200)
        page_chars = len(PAGE)
        self.assertLess(len(r.get_data(as_text=True)), page_chars // 2,
                        "the report is as big as the page it describes")

    def test_a_long_excerpt_in_a_finding_is_clipped(self):
        """A REPEAT quotes BOTH copies of a paragraph in full, so dropping the section
        bodies left a 331KB reply describing a 40KB page — `_section_outline`'s
        `max_blocks` lesson a level down: bounding the container bounds nothing while the
        items inside it are unbounded."""
        r = self._get("/api/mcp/report", path="wiki/entities/donald-trump.md").get_json()
        for group in ("repeats", "echoes"):
            for finding in r.get(group, []):
                for v in finding.values() if isinstance(finding, dict) else []:
                    if isinstance(v, str):
                        self.assertLessEqual(len(v), serve._MCP_EXCERPT)

    def test_a_capped_finding_list_says_how_many_there_were(self):
        """A truncated list that looks complete is worse than a long one: the count is
        what tells a caller to go and look at the page itself."""
        r = self._get("/api/mcp/report", path="wiki/entities/donald-trump.md").get_json()
        self.assertLessEqual(len(r.get("repeats", [])), serve._MCP_MAX_FINDINGS)
        if r.get("repeats_total"):
            self.assertGreater(r["repeats_total"], len(r["repeats"]))

    def test_the_report_404s_rather_than_inventing_findings(self):
        self.assertEqual(
            self._get("/api/mcp/report", path="wiki/entities/nope.md").status_code, 404)

    def test_each_read_starts_a_clean_session(self):
        """Flask threads are reused, so without `init_session()` a read would inherit the
        previous request's read coverage and `update_section`'s read-before-write guard
        would pass on a page this caller has never seen."""
        self._get("/api/mcp/read", path="wiki/entities/donald-trump.md",
                  section="Overview")
        self._get("/api/mcp/read", path="wiki/entities/donald-trump.md",
                  section="Key Policies", budget=16384)
        # The second request must not have been credited the first's section.
        self.assertNotIn(("entities/donald-trump.md", "overview"),
                         agent._ctx()._session_read_sections)


# ===========================================================================
# The MCP protocol layer
# ===========================================================================

class StubClient:
    def __init__(self, reply=None, raises=None):
        self.reply, self.raises, self.calls = reply or {}, raises, []
        self.budget = mcp_server.DEFAULT_BUDGET_TOKENS

    def get(self, route, **params):
        self.calls.append((route, params))
        if self.raises:
            raise self.raises
        return self.reply


def _drive(client, messages):
    """Run the real stdio loop over a scripted conversation; return parsed replies."""
    out = io.StringIO()
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
    real = sys.stdout
    sys.stdout = out
    try:
        mcp_server.serve_stdio(client, stdin=stdin)
    finally:
        sys.stdout = real
    return [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]


class ProtocolTest(unittest.TestCase):
    def test_initialize_then_list_then_call(self):
        c = StubClient({"content": "## Overview\nHe is a politician."})
        replies = _drive(c, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "read_page",
                        "arguments": {"path": "wiki/entities/x.md"}}},
        ])
        self.assertEqual([r["id"] for r in replies], [1, 2, 3],
                         "a notification must get no reply")
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "lobotomy")
        self.assertEqual({t["name"] for t in replies[1]["result"]["tools"]},
                         {"read_page", "search_wiki", "page_report"})
        self.assertIn("He is a politician.",
                      replies[2]["result"]["content"][0]["text"])

    def test_a_notification_is_never_answered(self):
        """Answering one is a protocol error some clients treat as fatal, and
        notifications/initialized is the first thing every client sends."""
        self.assertEqual(_drive(StubClient(), [
            {"jsonrpc": "2.0", "method": "notifications/initialized"}]), [])

    def test_a_refusal_comes_back_as_a_RESULT_the_model_can_act_on(self):
        """Not a JSON-RPC error. Every write guard here is written to name a call that
        works, so turning that text into a transport failure discards the useful half."""
        c = StubClient(raises=mcp_server.ApiError(
            "Error: update_section refused — call append_section(...) instead"))
        r = _drive(c, [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "read_page",
                                   "arguments": {"path": "wiki/entities/x.md"}}}])[0]
        self.assertNotIn("error", r)
        self.assertTrue(r["result"]["isError"])
        self.assertIn("append_section", r["result"]["content"][0]["text"])

    def test_an_unexpected_exception_does_not_kill_the_session(self):
        c = StubClient(raises=ValueError("something odd"))
        replies = _drive(c, [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "read_page", "arguments": {"path": "wiki/x.md"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ])
        self.assertTrue(replies[0]["result"]["isError"])
        self.assertEqual(replies[1]["id"], 2, "the loop died on one bad call")

    def test_a_malformed_line_does_not_kill_the_session(self):
        out = io.StringIO()
        real, sys.stdout = sys.stdout, out
        try:
            mcp_server.serve_stdio(StubClient({"content": "ok"}), stdin=io.StringIO(
                "{not json\n" + json.dumps(
                    {"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n"))
        finally:
            sys.stdout = real
        replies = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual(replies[1]["id"], 7)

    def test_an_unknown_method_is_a_method_not_found(self):
        r = _drive(StubClient(), [{"jsonrpc": "2.0", "id": 1, "method": "nonsense"}])[0]
        self.assertEqual(r["error"]["code"], -32601)

    def test_the_budget_travels_with_every_request(self):
        """Server-side guards cannot infer this client's limits, so if the budget stops
        being sent the RATCHET sections silently become unreadable again.

        **Driven through the REAL `Client.get`.** The first version asserted this against
        `StubClient`, whose `get` is the test's own three lines — so it was checking that
        a stub does not do something the stub was never written to do, while the method
        that actually adds the parameter went untested. Testing the double is not testing
        the code: the same shape as `EventContractTest`'s lesson, where every test built
        the event dict by hand and nothing had ever driven the real loop.
        """
        seen = {}

        class _Resp:
            def read(self_):
                return b'{"content": "x"}'

            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            seen["auth"] = req.headers.get("Authorization")
            return _Resp()

        with mock.patch.object(mcp_server.urllib.request, "urlopen", fake_urlopen):
            mcp_server.Client("https://x.example", "k", 200_000).get(
                "/api/mcp/read", path="wiki/entities/x.md")
        self.assertIn("budget=200000", seen["url"])
        self.assertEqual(seen["auth"], "Bearer k")


class ClientTest(unittest.TestCase):
    """The HTTP half, including the failure this project has had reported three times."""

    def _client(self):
        return mcp_server.Client("https://lobotomy.example", "k", 200_000)

    def test_an_html_error_body_says_the_request_never_arrived(self):
        """Every route answers JSON and `_unhandled` turns even a crash into JSON, so an
        HTML body did not come from Lobotomy. Saying "unexpected character at line 1
        column 1" instead is the bug reported three separate times."""
        err = mock.Mock()
        err.read.return_value = b"<!DOCTYPE html><title>504 Gateway Time-out</title>"
        exc = __import__("urllib").error.HTTPError(
            "u", 504, "Gateway Time-out", {}, None)
        exc.read = err.read
        with mock.patch.object(mcp_server.urllib.request, "urlopen", side_effect=exc):
            with self.assertRaises(mcp_server.ApiError) as cm:
                self._client().get("/api/mcp/read", path="wiki/x.md")
        self.assertIn("did not reach Lobotomy", str(cm.exception))

    def test_a_json_error_body_is_relayed_verbatim(self):
        exc = __import__("urllib").error.HTTPError("u", 403, "Forbidden", {}, None)
        exc.read = lambda: json.dumps({"error": "Invalid API key"}).encode()
        with mock.patch.object(mcp_server.urllib.request, "urlopen", side_effect=exc):
            with self.assertRaises(mcp_server.ApiError) as cm:
                self._client().get("/api/mcp/read", path="wiki/x.md")
        self.assertIn("Invalid API key", str(cm.exception))

    def test_an_unreachable_server_says_so(self):
        exc = __import__("urllib").error.URLError("connection refused")
        with mock.patch.object(mcp_server.urllib.request, "urlopen", side_effect=exc):
            with self.assertRaises(mcp_server.ApiError) as cm:
                self._client().get("/api/mcp/read", path="wiki/x.md")
        self.assertIn("could not reach", str(cm.exception))


class KeyHandlingTest(unittest.TestCase):
    def test_the_key_is_never_a_command_line_argument(self):
        """An argument is visible in `ps` to every user on the box and lands in shell
        history. config.json is not read either: this process may run on a different
        machine from the server."""
        with mock.patch.dict(os.environ, {"LOBOTOMY_MCP_KEY": ""}, clear=False):
            with self.assertRaises(SystemExit):
                mcp_server.main(["--base-url", "https://x.example"])
        opts = mcp_server.main.__doc__ or ""
        src = Path(mcp_server.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"--key"', src)
        self.assertNotIn("'--key'", src)

    def test_a_base_url_is_required(self):
        with mock.patch.dict(os.environ, {"LOBOTOMY_BASE_URL": ""}, clear=False):
            with self.assertRaises(SystemExit):
                mcp_server.main([])


class NoSecondWriterTest(unittest.TestCase):
    """The design decision, asserted structurally because its failure is silent.

    CLAUDE.md records that splitting the ingest was deferred because a second writer "is
    exactly what the autolinker's cache invalidation assumes is not true — the mtime
    backstop would become load-bearing rather than a backstop". A filesystem MCP server is
    that writer by another door, and nothing about it would LOOK wrong: it would work, and
    the cache would go stale under it.
    """

    def test_it_imports_nothing_from_the_server_side(self):
        """Read with AST, not grep.

        The first version of this grepped for `_atomic_write` — and found it in this
        module's own docstring, which EXPLAINS that edits inherit it. So the test failed
        on correct code for mentioning the thing it forbids: the project's recurring trap,
        committed in the test written to prevent a different one. The parse tree contains
        imports and calls and no prose, which is the only version of this check that can
        tell the difference.
        """
        import ast
        tree = ast.parse(Path(mcp_server.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("agent", "serve", "page_report", "config", "flask"):
            self.assertNotIn(forbidden, imported,
                             f"mcp_server imports {forbidden} — it would become a second "
                             f"writer, or need the server's own dependencies")

    def test_it_opens_no_files(self):
        """A second process writing `wiki/` is the hazard; a second process READING it is
        the same hazard's quieter half, since it would answer from a tree the client is
        not actually talking to."""
        import ast
        tree = ast.parse(Path(mcp_server.__file__).read_text(encoding="utf-8"))
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertNotIn("open", called)
        attrs = {n.func.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        for forbidden in ("write_text", "read_text", "mkdir", "replace", "unlink"):
            self.assertNotIn(forbidden, attrs, f"mcp_server calls {forbidden} on a path")

    def test_the_read_only_half_exposes_no_write_tool(self):
        """Stated as a test so the writes are a deliberate, reviewable change rather than
        something that arrives by accident."""
        names = {t["name"] for t in mcp_server.TOOLS}
        for w in ("update_section", "append_section", "create_file", "update_file",
                  "add_timeline_entry", "merge_page", "rename_page"):
            self.assertNotIn(w, names)


if __name__ == "__main__":
    unittest.main()
