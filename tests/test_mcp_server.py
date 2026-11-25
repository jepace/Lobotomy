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

    def test_the_clients_default_budget_is_one_it_can_actually_emit(self):
        """Asked *"could I say rewrite the Donald Trump page and it would read the whole
        thing and write it back?"* — and checking found this.

        `DEFAULT_BUDGET_TOKENS` shipped at 200,000, which declares ~800,000 characters in
        ONE RESPONSE. No model emits that. Four server-side guards scale off the number, so
        the claim taught them to approve a rewrite that runs out of room — and a rewrite
        that stops early is the silent condensation the shrink guards exist for.
        `update_file` refuses a body cut over 40%, so a 223KB page truncated to 140KB (37%)
        would slip UNDER it and reach disk.

        The bound is what makes the declaration meaningful: a budget nothing checks is a
        guard switched off politely. Pinned so raising it is a deliberate act with this
        reasoning in front of whoever does it.
        """
        self.assertLessEqual(mcp_server.DEFAULT_BUDGET_TOKENS, 100_000,
                             "the default claims more single-response output than any "
                             "model has; four guards scale off it")
        self.assertGreater(mcp_server.DEFAULT_BUDGET_TOKENS,
                           agent.cfg_int("llm", "max_tokens", default=16384),
                           "below the configured provider's budget the server is pointless")

    def test_the_default_makes_the_ratchet_section_rewritable_and_not_the_whole_page(self):
        """The two numbers that decide what this server can actually be asked to do.

        The 93,637-char section must come back whole — that is the unlock, and the reason
        the budget is a request parameter at all. The 223,659-char page must NOT, because
        no single response can emit it, and a guard that said otherwise would be inviting
        exactly the truncated rewrite above.
        """
        with agent.output_budget(mcp_server.DEFAULT_BUDGET_TOKENS):
            self.assertTrue(agent._section_rewritable(93_637),
                            "the section this server exists to repair is still refused")
            self.assertFalse(agent._section_rewritable(223_659),
                             "a whole-page rewrite of donald-trump.md is being approved")

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


class KeyFileTest(unittest.TestCase):
    """The key lives in a file, and the file's MODE is the reason that is an improvement.

    Asked for as *"Key should be in a file, not environment."* The first version read
    `$LOBOTOMY_MCP_KEY`, on the argument that an argument is visible in `ps`. True, and it
    did not follow that the environment was the answer: an env var is readable from
    `/proc/<pid>/environ` by the same user, is inherited by every child, and — the part
    that decides it — **an MCP client sets one by writing it into the client's own JSON
    config**, so the secret ends up in a file either way. The only question was whether it
    lands in a file something can check the permissions of.
    """

    def setUp(self):
        import tempfile
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.d, ignore_errors=True))
        self.f = self.d / "mcp.json"

    def _write(self, obj, mode=0o600):
        self.f.write_text(json.dumps(obj), encoding="utf-8")
        self.f.chmod(mode)

    def test_it_reads_base_url_key_and_budget(self):
        self._write({"base_url": "https://x.example", "mcp_key": "k", "budget": 123})
        cfg = mcp_server.load_client_config(str(self.f))
        self.assertEqual(cfg["base_url"], "https://x.example")
        self.assertEqual(cfg["mcp_key"], "k")
        self.assertEqual(cfg["budget"], 123)

    def test_a_world_readable_key_file_is_REFUSED_not_warned(self):
        """A warning goes to wherever the MCP client sends stderr, which is frequently
        nowhere — and a key file other accounts can read is strictly worse than the env
        var it replaced, so this is the one check that justifies the whole change."""
        self._write({"base_url": "https://x.example", "mcp_key": "k"}, mode=0o644)
        with self.assertRaises(mcp_server.ConfigError) as cm:
            mcp_server.load_client_config(str(self.f))
        self.assertIn("chmod 600", str(cm.exception))

    def test_a_group_readable_key_file_is_refused_too(self):
        self._write({"base_url": "https://x.example", "mcp_key": "k"}, mode=0o640)
        with self.assertRaises(mcp_server.ConfigError):
            mcp_server.load_client_config(str(self.f))

    def test_a_missing_file_names_the_commands_that_fix_it(self):
        """Principle 4 in a CLI: the message has to name a move that works."""
        with self.assertRaises(mcp_server.ConfigError) as cm:
            mcp_server.load_client_config(str(self.d / "absent.json"))
        msg = str(cm.exception)
        self.assertIn("/settings", msg)
        self.assertIn("chmod 600", msg)

    def test_malformed_json_says_so_rather_than_crashing(self):
        self.f.write_text("{not json", encoding="utf-8")
        self.f.chmod(0o600)
        with self.assertRaises(mcp_server.ConfigError) as cm:
            mcp_server.load_client_config(str(self.f))
        self.assertIn("not valid JSON", str(cm.exception))

    def test_the_key_is_never_a_command_line_argument(self):
        src = Path(mcp_server.__file__).read_text(encoding="utf-8")
        for flag in ('"--key"', "'--key'", '"--mcp-key"', '"--token"'):
            self.assertNotIn(flag, src)

    def test_main_refuses_when_there_is_no_key_anywhere(self):
        self._write({"base_url": "https://x.example"})
        with mock.patch.object(mcp_server, "load_server_config", return_value={}):
            with self.assertRaises(SystemExit):
                mcp_server.main(["--key-file", str(self.f)])

    def test_main_refuses_when_there_is_no_base_url(self):
        self._write({"mcp_key": "k"})
        with mock.patch.object(mcp_server, "load_server_config", return_value={}):
            with self.assertRaises(SystemExit):
                mcp_server.main(["--key-file", str(self.f)])

    def test_it_falls_back_to_the_servers_own_config_when_run_beside_it(self):
        """Running on the jail is a real case, and copying a key from one file on the box
        into another file on the same box would be ceremony."""
        captured = {}
        with mock.patch.object(mcp_server, "load_server_config",
                               return_value={"mcp_key": "from-config",
                                             "base_url": "https://jail.example"}):
            with mock.patch.object(mcp_server, "serve_stdio",
                                   lambda c, **kw: captured.update(
                                       key=c.key, base=c.base) or 0):
                mcp_server.main(["--key-file", str(self.d / "absent.json")])
        self.assertEqual(captured["key"], "from-config")
        self.assertEqual(captured["base"], "https://jail.example")

    def test_the_server_config_reader_never_imports_config_py(self):
        """`config.py` calls sys.exit() at import when config.json is missing, which would
        turn "you are not on the server" into a dead process — and importing it would
        break the no-server-side-imports rule this module is held to."""
        self.assertEqual(mcp_server.load_server_config.__module__, "mcp_server")
        import ast
        tree = ast.parse(Path(mcp_server.__file__).read_text(encoding="utf-8"))
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                names.add(n.module)
        self.assertNotIn("config", names)


class SettingsMcpKeyTest(unittest.TestCase):
    """`/settings/mcp-key` — generate, rotate, revoke.

    Asked as *"User settings page has all the mcp stuff settable there?"* The KEY half
    belongs here; the client half (which machine, which base URL, which budget) lives on
    whatever machine runs the client and cannot be set from a web page, so the page hands
    that over as a file to save instead.
    """

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self._store = {}
        self._gs, self._us = serve.get_settings, serve.update_settings
        serve.get_settings = lambda: dict(self._store)
        serve.update_settings = lambda patch: self._store.update(patch)

        def _restore():
            serve.get_settings, serve.update_settings = self._gs, self._us
        self.addCleanup(_restore)
        self.c = serve.app.test_client()
        with self.c.session_transaction() as s:
            s["logged_in"] = True

    def test_generate_returns_a_key_and_stores_it(self):
        r = self.c.post("/settings/mcp-key", json={"action": "generate"})
        self.assertEqual(r.status_code, 200)
        key = r.get_json()["key"]
        self.assertEqual(len(key), 64)
        self.assertEqual(self._store["api_mcp_key"], key)

    def test_generating_twice_replaces_rather_than_adding(self):
        """One secret with one lifecycle is what makes revoke mean something."""
        first = self.c.post("/settings/mcp-key", json={}).get_json()["key"]
        second = self.c.post("/settings/mcp-key", json={}).get_json()["key"]
        self.assertNotEqual(first, second)
        self.assertEqual(self._store["api_mcp_key"], second)

    def test_settings_wins_over_config_json(self):
        self._store["api_mcp_key"] = "from-settings"
        cfg = serve.cfg_get
        serve.cfg_get = lambda s, k, d=None: ("from-config" if (s, k) == ("api", "mcp_key")
                                              else cfg(s, k, d))
        try:
            self.assertEqual(serve._api_key("mcp_key"), "from-settings")
        finally:
            serve.cfg_get = cfg

    def test_revoke_beats_a_key_still_sitting_in_config_json(self):
        """The one outcome this must not produce: revoking and having the old key keep
        working. An empty settings entry means REVOKED, not "fall through to the
        default" — which is why it is stored rather than deleted."""
        cfg = serve.cfg_get
        serve.cfg_get = lambda s, k, d=None: ("from-config" if (s, k) == ("api", "mcp_key")
                                              else cfg(s, k, d))
        try:
            self.c.post("/settings/mcp-key", json={"action": "revoke"})
            self.assertEqual(serve._api_key("mcp_key"), "")
        finally:
            serve.cfg_get = cfg

    def test_config_json_is_used_when_settings_has_never_been_touched(self):
        cfg = serve.cfg_get
        serve.cfg_get = lambda s, k, d=None: ("from-config" if (s, k) == ("api", "mcp_key")
                                              else cfg(s, k, d))
        try:
            self.assertEqual(serve._api_key("mcp_key"), "from-config")
        finally:
            serve.cfg_get = cfg

    def test_a_damaged_user_file_does_not_open_the_door(self):
        """Fail closed: a settings store that raises must not be read as "no key set, so
        allow everything" — `_api_auth` answers 501 on an empty key, never 200."""
        def boom():
            raise RuntimeError("damaged")
        serve.get_settings = boom
        self.assertEqual(serve._api_key("mcp_key"), serve.cfg_get("api", "mcp_key", ""))

    def test_an_unknown_action_is_refused(self):
        r = self.c.post("/settings/mcp-key", json={"action": "sideways"})
        self.assertEqual(r.status_code, 400)

    def test_it_needs_a_login(self):
        c = serve.app.test_client()
        r = c.post("/settings/mcp-key", json={},
                   headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(r.status_code, 401)

    def test_the_key_is_never_written_to_the_log(self):
        """The log outlives a rotation, which is exactly how a revoked secret survives."""
        import logging
        seen = []

        class _Grab(logging.Handler):
            def emit(self, rec):
                seen.append(rec.getMessage())

        h = _Grab()
        serve.log.addHandler(h)
        try:
            key = self.c.post("/settings/mcp-key", json={}).get_json()["key"]
        finally:
            serve.log.removeHandler(h)
        for line in seen:
            self.assertNotIn(key, line)


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

    def test_it_never_writes_anything(self):
        """A second process WRITING `wiki/` is the hazard, and the cheapest way to be sure
        is that this module writes nothing at all — it has no reason to."""
        import ast
        tree = ast.parse(Path(mcp_server.__file__).read_text(encoding="utf-8"))
        attrs = {n.func.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        for forbidden in ("write_text", "write_bytes", "mkdir", "replace", "unlink",
                          "rmtree", "rename"):
            self.assertNotIn(forbidden, attrs, f"mcp_server calls {forbidden}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "open":
                mode = node.args[1] if len(node.args) > 1 else None
                if isinstance(mode, ast.Constant):
                    self.assertNotIn("w", str(mode.value))
                    self.assertNotIn("a", str(mode.value))

    def test_every_tool_reaches_the_wiki_over_HTTP(self):
        """Reading the wiki directly is the hazard's quieter half — it would answer from a
        tree the client is not actually talking to, which looks like working and is not.

        **Asserted behaviourally, after two static attempts failed for the same reason.**
        A blanket "opens no files" was wrong rather than strict: the key file has to be
        read, and so does the server's `config.json` where this runs beside it. Narrowing
        it to "names no `wiki/` path" then matched the module's own DOCSTRING, which
        promises the property, and after excluding prose it matched a tool SCHEMA
        description whose job is to show the client what a path looks like
        (`"wiki-relative, e.g. wiki/entities/donald-trump.md"`). Both were the
        grep-matches-the-prose trap wearing different hats.

        The property was never about string literals. It is that **every tool's data comes
        from an HTTP call**, so driving each one and requiring a request on the wire proves
        it, and no amount of prose can satisfy it by accident.
        """
        args = {"read_page": {"path": "wiki/entities/x.md"},
                "search_wiki": {"query": "tariffs"},
                "page_report": {"path": "wiki/entities/x.md"}}
        self.assertEqual(set(args), {t["name"] for t in mcp_server.TOOLS},
                         "a tool was added or renamed without being covered here")
        for name, a in args.items():
            with self.subTest(tool=name):
                c = StubClient({"content": "x", "results": [{"path": "p"}]})
                mcp_server.call_tool(c, name, a)
                self.assertEqual(len(c.calls), 1,
                                 f"{name} did not make exactly one HTTP call")
                self.assertTrue(c.calls[0][0].startswith("/api/mcp/"),
                                f"{name} called {c.calls[0][0]}")

    def test_the_read_only_half_exposes_no_write_tool(self):
        """Stated as a test so the writes are a deliberate, reviewable change rather than
        something that arrives by accident."""
        names = {t["name"] for t in mcp_server.TOOLS}
        for w in ("update_section", "append_section", "create_file", "update_file",
                  "add_timeline_entry", "merge_page", "rename_page"):
            self.assertNotIn(w, names)


SETTINGS_TEMPLATE = (Path(__file__).resolve().parent.parent
                     / "tools" / "templates" / "settings.html")


class SettingsPageTest(unittest.TestCase):
    """The page itself, rendered — and its one lifted function, RUN."""

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self.c = serve.app.test_client()
        with self.c.session_transaction() as s:
            s["logged_in"] = True

    def _page(self):
        r = self.c.get("/settings")
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)

    def test_the_page_offers_the_key_and_the_client_file(self):
        h = self._page()
        self.assertIn("MCP Server", h)
        self.assertIn("mcp.json", h)
        self.assertIn("chmod 600", h)

    def test_it_says_the_client_half_cannot_be_set_here(self):
        """The honest half of the answer to "is all the mcp stuff settable there?" — the
        base URL and budget belong to the machine running the client, so claiming the page
        owns them would be a promise it cannot keep."""
        self.assertIn("cannot set them", self._page())

    def test_the_two_keys_are_described_as_separate(self):
        self.assertIn("different key", self._page())


class McpConfigJsonTest(unittest.TestCase):
    """RUN under node, not grepped.

    The JSON block is rendered twice — once by Jinja on load, once by this script after a
    rotate — which is the two-renderers hazard recorded here repeatedly. Lifting the
    function and executing it is the only check that the browser's copy produces what the
    client can actually parse.
    """

    @classmethod
    def setUpClass(cls):
        import shutil
        if not shutil.which("node"):
            raise unittest.SkipTest("node is not installed")
        import re
        src = SETTINGS_TEMPLATE.read_text(encoding="utf-8")
        m = re.search(r"function mcpConfigJson\(baseUrl, key, budget\)[\s\S]*?\n\}", src)
        if not m:
            raise AssertionError("mcpConfigJson is gone; every assertion here is vacuous")
        cls.fn = m.group(0)

    def test_it_produces_json_the_client_loader_accepts(self):
        import subprocess
        js = (self.fn + "\nprocess.stdout.write(mcpConfigJson("
              "'https://lobotomy.example', 'abc123', 200000));")
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        cfg = json.loads(out.stdout)        # the real parse the client will do
        self.assertEqual(cfg, {"base_url": "https://lobotomy.example",
                               "mcp_key": "abc123", "budget": 200000})

    def test_the_file_it_writes_loads_through_the_real_client_loader(self):
        """End to end across the two halves: what the browser shows you to paste is what
        `load_client_config` reads back."""
        import subprocess, tempfile
        js = (self.fn + "\nprocess.stdout.write(mcpConfigJson("
              "'https://x.example', 'k', 123));")
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True)
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        f = d / "mcp.json"
        f.write_text(out.stdout, encoding="utf-8")
        f.chmod(0o600)
        cfg = mcp_server.load_client_config(str(f))
        self.assertEqual(cfg["mcp_key"], "k")
        self.assertEqual(cfg["base_url"], "https://x.example")
        self.assertEqual(cfg["budget"], 123)


if __name__ == "__main__":
    unittest.main()
