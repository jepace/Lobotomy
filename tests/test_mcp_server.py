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
import io
import json
import re
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


HUGE = "\n\n".join(f"Paragraph {i} opens the discussion. " + "word " * 900 + "end."
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
                # Send the method the route accepts: a GET to a POST-only route answers
                # 405 before auth runs, which would pass this test while proving nothing.
                r = self.c.get(rule, query_string={"path": "wiki/entities/donald-trump.md",
                                                   "q": "x"})
                if r.status_code == 405:
                    r = self.c.post(rule, json={"path": "wiki/entities/donald-trump.md"})
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

    def post(self, route, **body):
        self.calls.append((route, body))
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
                         {"read_page", "search_wiki", "page_report",
                          "replace_passage", "write_page"})
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
                "page_report": {"path": "wiki/entities/x.md"},
                "replace_passage": {"path": "wiki/entities/x.md",
                                    "old_text": "a", "new_text": "b"},
                "write_page": {"path": "wiki/entities/x.md", "content": "x"}}
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

    def test_it_exposes_the_write_tier_and_none_of_the_INGEST_tools(self):
        """**This test used to assert the opposite and had to be rewritten, not deleted.**

        In phase 1 it read "the read-only half exposes no write tool", which was true then
        and is a lie once writes exist — a suite that still claims read-only while a write
        route is live is worse than no assertion. The writes arrived as a deliberate,
        reviewable change, which is what that wording was protecting.

        The property that holds NOW is the tier: this client gets the editor's shape, and
        none of the agent's ingest machinery. `create_file` would let it mint pages outside
        any source-page discipline; `done()` writes `log.md` and marks reading-list
        articles wikified; `add_timeline_entry` belongs to an ingest folding in a source.
        None of that is what a person asking for a page to be reorganized wants done.
        """
        names = {t["name"] for t in mcp_server.TOOLS}
        self.assertEqual(names, {"read_page", "search_wiki", "page_report",
                                 "replace_passage", "write_page"})
        for ingest_only in ("create_file", "update_file", "update_section",
                            "append_section", "add_timeline_entry", "done",
                            "merge_page", "rename_page", "lookup_titles"):
            self.assertNotIn(ingest_only, names)


class ImmutablePageTest(unittest.TestCase):
    """`immutable_page_reason` — one rule, consulted by every door that writes a page.

    Asked for as *"Close the holes. If I need to I have vim."* The web editor refused only
    a path outside `wiki/` and a page that was not there, while the agent tools refused
    source pages and the generated ones — **the same rule described here as an invariant,
    holding on one door and not the other.** A shell on the box is a deliberate, visible
    way round; an unguarded HTTP route is an accident waiting for the next client.
    """

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)

    def test_an_ordinary_page_is_editable(self):
        """The not-crying-wolf half: this must refuse exactly two kinds of page."""
        p = self.w.wiki / "entities" / "x.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(PAGE, encoding="utf-8")
        self.assertIsNone(agent.immutable_page_reason(p))

    def test_a_source_page_is_refused(self):
        p = self.w.wiki / "sources" / "s.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(PAGE, encoding="utf-8")
        why = agent.immutable_page_reason(p)
        self.assertIn("snapshot", why or "")

    def test_the_generated_pages_are_refused_because_they_cannot_be_REVERTED(self):
        """The disqualifier is not tidiness: `_snapshot_version` skips both, so an edit
        here could not be undone, while every other write in this system can."""
        for name in agent._GENERATED_PAGES:
            with self.subTest(name=name):
                p = self.w.wiki / name
                p.write_text("x\n", encoding="utf-8")
                why = agent.immutable_page_reason(p)
                self.assertIn("revertable", why or "")

    def test_both_doors_consult_the_SAME_function(self):
        """Two copies of this rule is how the gap happened in the first place."""
        self.assertIs(serve.immutable_page_reason, agent.immutable_page_reason)

    def test_the_section_tools_still_refuse_them(self):
        """Hoisting the rule out must not have dropped it from the agent path."""
        for rel in ("wiki/sources/s.md", "wiki/log.md"):
            p = self.w.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(PAGE, encoding="utf-8")
            with self.subTest(rel=rel):
                _p, err = agent._section_guard(rel)
                self.assertIn("refused", err or "")

    def test_the_web_EDITOR_refuses_them_now(self):
        """The hole itself. Driven through the real route."""
        saved = (serve.WIKI_DIR, serve.REPO_ROOT)
        serve.WIKI_DIR, serve.REPO_ROOT = self.w.wiki, self.w.root
        self.addCleanup(lambda: (setattr(serve, "WIKI_DIR", saved[0]),
                                 setattr(serve, "REPO_ROOT", saved[1])))
        for rel in ("sources/s.md", "log.md"):
            p = self.w.wiki / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(PAGE, encoding="utf-8")
        c = serve.app.test_client()
        with c.session_transaction() as s:
            s["logged_in"] = True
        for rel in ("sources/s.md", "log.md"):
            with self.subTest(rel=rel):
                r = c.post(f"/api/wiki/{rel}/save", json={"content": "wiped"})
                self.assertEqual(r.status_code, 400, f"{rel} was still writable")
                self.assertEqual((self.w.wiki / rel).read_text(encoding="utf-8"), PAGE)

    def test_an_ordinary_page_still_saves_from_the_editor(self):
        """Closing a hole must not close the feature."""
        saved = (serve.WIKI_DIR, serve.REPO_ROOT)
        serve.WIKI_DIR, serve.REPO_ROOT = self.w.wiki, self.w.root
        self.addCleanup(lambda: (setattr(serve, "WIKI_DIR", saved[0]),
                                 setattr(serve, "REPO_ROOT", saved[1])))
        p = self.w.wiki / "entities" / "x.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(PAGE, encoding="utf-8")
        c = serve.app.test_client()
        with c.session_transaction() as s:
            s["logged_in"] = True
        body = PAGE.replace("He is a politician", "He is a politician and a broadcaster")
        r = c.post("/api/wiki/entities/x.md/save", json={"content": body})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        self.assertIn("broadcaster", p.read_text(encoding="utf-8"))


class TruncationGuardTest(unittest.TestCase):
    """`_truncation_refusal` — the one guard the editor lacks and MCP needs.

    Not about trust: a human editing a textarea does not stop typing at a token ceiling,
    and a model does. `update_file`'s 40% shrink refusal is the agent-path answer and is
    the wrong one here, because consolidating a page IS shrinking it — so the test is for
    the SHAPE of a truncation rather than its size.
    """

    def _old(self, n=4000):
        return "---\ntitle: X\n---\n\n# X\n\n## Overview\n" + ("word " * n) + "end."

    def test_a_complete_rewrite_passes(self):
        old = self._old()
        new = "---\ntitle: X\n---\n\n# X\n\n## Overview\nA complete, shorter rewrite."
        self.assertIsNone(serve._truncation_refusal(old, new, 256_000, acknowledged=True))

    def test_content_ending_mid_thought_is_refused(self):
        """The direct signature: a response cut at the ceiling stops on a bare word."""
        old = self._old()
        new = "---\ntitle: X\n---\n\n# X\n\n## Overview\nHe went to the shop and then"
        why = serve._truncation_refusal(old, new, 256_000, acknowledged=True)
        self.assertIn("mid-thought", why or "")

    def test_content_filling_the_declared_budget_is_refused(self):
        """And note the DIRECTION: a bigger declared budget makes this fire sooner relative
        to the text, so the budget cannot buy latitude here."""
        old = self._old()
        new = "---\ntitle: X\n---\n\n# X\n\n## Overview\n" + ("word " * 2000) + "end."
        why = serve._truncation_refusal(old, new, len(new) + 10, acknowledged=True)
        self.assertIn("stated ceiling", why or "")

    def test_missing_frontmatter_is_refused(self):
        why = serve._truncation_refusal(self._old(), "# X\n\nJust a body.",
                                        256_000, acknowledged=True)
        self.assertIn("frontmatter", why or "")

    def test_a_lost_h1_is_refused(self):
        why = serve._truncation_refusal(
            self._old(), "---\ntitle: X\n---\n\n## Overview\nBody only.",
            256_000, acknowledged=True)
        self.assertIn("H1", why or "")

    def test_an_empty_rewrite_is_refused(self):
        self.assertIn("empty", serve._truncation_refusal(self._old(), "   ", 256_000) or "")

    def test_a_big_cut_needs_acknowledging_but_is_then_allowed(self):
        """Consolidation is the point of the tool, so this is a flag and not a wall —
        `allow_shrink`'s bargain. Revertable either way."""
        old = self._old()
        new = "---\ntitle: X\n---\n\n# X\n\n## Overview\nConsolidated to one line."
        self.assertIn("acknowledge_shrink",
                      serve._truncation_refusal(old, new, 256_000) or "")
        self.assertIsNone(serve._truncation_refusal(old, new, 256_000, acknowledged=True))


class McpWriteTierTest(unittest.TestCase):
    """The write tier: `mcp_key` gets the editor's shape, the ingest agent gets nothing new.

    Asked for as *"I don't want the script and dumb Gemini to have access to this, but the
    mcp is higher access for higher quality, more human interactions."*
    """

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self._saved = (serve.RAW_DIR, serve.WIKI_DIR, serve.REPO_ROOT, agent.REPO_ROOT)
        serve.RAW_DIR, serve.WIKI_DIR = self.w.raw, self.w.wiki
        serve.REPO_ROOT = agent.REPO_ROOT = self.w.root

        def _restore():
            (serve.RAW_DIR, serve.WIKI_DIR, serve.REPO_ROOT,
             agent.REPO_ROOT) = self._saved
        self.addCleanup(_restore)
        self.page = self.w.wiki / "entities" / "x.md"
        self.page.parent.mkdir(parents=True, exist_ok=True)
        self.page.write_text(PAGE, encoding="utf-8")
        self._cfg = serve.cfg_get
        serve.cfg_get = lambda s, k, d=None: (
            "secret-mcp" if (s, k) == ("api", "mcp_key") else self._cfg(s, k, d))
        self.addCleanup(lambda: setattr(serve, "cfg_get", self._cfg))
        self.c = serve.app.test_client()

    def _post(self, route, key="secret-mcp", **body):
        return self.c.post(route, json=body,
                           headers={"Authorization": f"Bearer {key}"})

    # -- the tier ------------------------------------------------------------

    def test_the_ingest_agents_tool_table_gains_nothing(self):
        """The whole point of the tier. The unguarded whole-page write must not become an
        agent tool, or the script and a weak model inherit it."""
        for forbidden in ("write_page", "mcp_write", "save_page"):
            self.assertNotIn(forbidden, agent.TOOL_FNS)
        self.assertNotIn("write_page", {d.get("function", {}).get("name")
                                        for d in agent.TOOL_DEFS})

    def test_the_agents_own_guards_are_untouched(self):
        """update_section still refuses an unread section — the ingest path is unchanged."""
        agent.init_session()
        out = agent._update_section({"path": "wiki/entities/x.md",
                                     "section": "Overview", "content": "Rewritten."})
        self.assertIn("refused", out)

    def test_a_write_needs_the_mcp_key(self):
        self.assertEqual(self._post("/api/mcp/write", key="nope",
                                    path="wiki/entities/x.md", content=PAGE).status_code,
                         403)

    def test_both_write_routes_are_behind_the_key(self):
        for route in ("/api/mcp/write", "/api/mcp/replace"):
            with self.subTest(route=route):
                r = self.c.post(route, json={"path": "wiki/entities/x.md"})
                self.assertIn(r.status_code, (401, 501))

    # -- whole-page write ----------------------------------------------------

    def test_it_writes_the_whole_page_in_one_call(self):
        """The thing asked for: *"just get the page and write the page"*."""
        body = PAGE.replace("He is a politician", "Reorganised entirely")
        r = self._post("/api/mcp/write", path="wiki/entities/x.md", content=body,
                       acknowledge_shrink=True)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        self.assertIn("Reorganised entirely", self.page.read_text(encoding="utf-8"))

    def test_the_write_is_revertable(self):
        """What makes a flag an acceptable answer rather than a wall."""
        before = self.page.read_text(encoding="utf-8")
        self._post("/api/mcp/write", path="wiki/entities/x.md",
                   content=PAGE.replace("politician", "broadcaster"),
                   acknowledge_shrink=True)
        rows = agent.page_history(self.page)
        self.assertTrue(rows, "the rewrite left no history row to revert to")
        self.assertIn("broadcaster", self.page.read_text(encoding="utf-8"))
        self.assertNotEqual(before, self.page.read_text(encoding="utf-8"))

    def test_a_truncated_write_is_refused_and_the_page_is_untouched(self):
        before = self.page.read_text(encoding="utf-8")
        r = self._post("/api/mcp/write", path="wiki/entities/x.md",
                       content="---\ntitle: X\n---\n\n# X\n\n## Overview\nHe went and then")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.page.read_text(encoding="utf-8"), before)

    def test_a_source_page_is_refused(self):
        p = self.w.wiki / "sources" / "s.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(PAGE, encoding="utf-8")
        r = self._post("/api/mcp/write", path="wiki/sources/s.md", content=PAGE,
                       acknowledge_shrink=True)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(p.read_text(encoding="utf-8"), PAGE)

    def test_the_generated_pages_are_refused(self):
        for name in agent._GENERATED_PAGES:
            p = self.w.wiki / name
            p.write_text("generated\n", encoding="utf-8")
            with self.subTest(name=name):
                r = self._post("/api/mcp/write", path=f"wiki/{name}",
                               content="wiped", acknowledge_shrink=True)
                self.assertEqual(r.status_code, 400)
                self.assertEqual(p.read_text(encoding="utf-8"), "generated\n")

    def test_a_non_string_content_is_refused_rather_than_crashing(self):
        self.assertEqual(self._post("/api/mcp/write", path="wiki/entities/x.md",
                                    content={"not": "a string"}).status_code, 400)

    # -- replace -------------------------------------------------------------

    def test_a_passage_is_replaced_in_place(self):
        r = self._post("/api/mcp/replace", path="wiki/entities/x.md",
                       old_text="He is a politician who favours tariffs.",
                       new_text="He is a broadcaster.")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        text = self.page.read_text(encoding="utf-8")
        self.assertIn("He is a broadcaster.", text)
        self.assertIn("Key Policies", text, "the rest of the page was disturbed")

    def test_a_quote_matching_nothing_is_refused_rather_than_guessed(self):
        before = self.page.read_text(encoding="utf-8")
        r = self._post("/api/mcp/replace", path="wiki/entities/x.md",
                       old_text="a sentence that is not on this page at all",
                       new_text="x")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.page.read_text(encoding="utf-8"), before)

    def test_replace_shares_the_agents_implementation(self):
        """Not reimplemented: the uniqueness contract has to hold identically on both
        doors, and two copies would be two answers about what a unique quote is."""
        self.assertIs(serve._replace_text, agent._replace_text)

    def test_replace_refuses_a_source_page_too(self):
        p = self.w.wiki / "sources" / "s.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(PAGE, encoding="utf-8")
        self.assertEqual(
            self._post("/api/mcp/replace", path="wiki/sources/s.md",
                       old_text="He is a politician who favours tariffs.",
                       new_text="x").status_code, 400)

    def test_replace_needs_a_non_empty_quote(self):
        self.assertEqual(self._post("/api/mcp/replace", path="wiki/entities/x.md",
                                    old_text="", new_text="x").status_code, 400)

    # -- the budget is not an authorization parameter ------------------------

    def test_a_bigger_declared_budget_buys_no_write_latitude(self):
        """The trap this design exists to avoid. The budget is self-declared and the server
        cannot verify it, so if latitude scaled with it any client would escalate by
        claiming a bigger number. A truncated write stays refused at any budget."""
        cut = "---\ntitle: X\n---\n\n# X\n\n## Overview\nHe went and then"
        for budget in (16_384, 64_000, 400_000):
            with self.subTest(budget=budget):
                r = self._post("/api/mcp/write", path="wiki/entities/x.md",
                               content=cut, budget=budget)
                self.assertEqual(r.status_code, 409)

    def test_a_bigger_budget_does_not_unlock_an_immutable_page(self):
        p = self.w.wiki / "sources" / "s.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(PAGE, encoding="utf-8")
        self.assertEqual(
            self._post("/api/mcp/write", path="wiki/sources/s.md", content=PAGE,
                       budget=400_000, acknowledge_shrink=True).status_code, 400)


class MergeIsRevertableTest(unittest.TestCase):
    """**A merge was the one operation here that could not be undone**, and that had to be
    fixed before exposing it to anything.

    `merge_page` writes the SURVIVOR and never writes the loser, so `loser.unlink()` walked
    straight past `_atomic_write` and therefore past `_snapshot_version` — whose own
    docstring calls itself *"the backstop that makes [data loss] recoverable regardless of
    whether the guard that should have caught them works"*. What `wiki/.history/<loser>/`
    held was the content from before the loser's LAST write, not its final state, so a
    merge that carried the wrong lines lost the difference permanently.
    """

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self._root = agent.REPO_ROOT
        agent.REPO_ROOT = self.w.root
        self.addCleanup(lambda: setattr(agent, "REPO_ROOT", self._root))
        for rel, title in (("entities/a.md", "Alpha Corp"),
                           ("entities/b.md", "Alpha Corporation")):
            p = self.w.wiki / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"---\ntitle: {title}\ntype: entity\ncreated: 2026-01-01\n---\n\n"
                         f"# {title}\n\n## Overview\nA company that makes things.\n",
                         encoding="utf-8")
        agent.init_session()

    def test_the_deleted_page_content_survives_in_its_own_history(self):
        loser_text = (self.w.wiki / "entities" / "b.md").read_text(encoding="utf-8")
        agent.merge_page("entities/b.md", "entities/a.md", carry=True)
        self.assertFalse((self.w.wiki / "entities" / "b.md").exists(), "loser not deleted")
        hist = self.w.wiki / ".history" / "entities" / "b.md"
        revs = sorted(hist.glob("*.md")) if hist.is_dir() else []
        self.assertTrue(revs, "the merged-away page left no recoverable content")
        self.assertEqual(revs[-1].read_text(encoding="utf-8"), loser_text,
                         "the kept revision is not the loser's FINAL content")

    def test_the_revision_is_stamped_merge_so_the_row_says_what_happened(self):
        agent.merge_page("entities/b.md", "entities/a.md", carry=True)
        hist = self.w.wiki / ".history" / "entities" / "b.md"
        self.assertTrue(any("__merge" in p.name for p in hist.glob("*.md")),
                        f"not stamped: {[p.name for p in hist.glob('*.md')]}")

    def test_a_dry_run_deletes_nothing(self):
        agent.merge_page("entities/b.md", "entities/a.md", carry=True, dry_run=True)
        self.assertTrue((self.w.wiki / "entities" / "b.md").exists())


class DuplicateScanTest(unittest.TestCase):
    """`duplicate_page_groups` — moved out of another 54-line unreachable script."""

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        for rel, t in (("entities/pge-a.md", "Pacific Gas and Electric Company"),
                       ("entities/pge-b.md", "Pacific Gas & Electric Co."),
                       ("entities/pge-c.md", "Pacific Gas & Electric"),
                       ("entities/solo.md", "Solo Subject"),
                       ("sources/s.md", "Pacific Gas & Electric")):
            p = self.w.wiki / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"---\ntitle: {t}\ntype: entity\n---\n\n# {t}\n\n## Overview\nx\n",
                         encoding="utf-8")

    def test_it_groups_spellings_of_one_subject(self):
        g = agent.duplicate_page_groups()
        self.assertEqual(len(g), 1, g)
        self.assertEqual(len(g[0]["pages"]), 3)

    def test_a_page_with_no_twin_is_not_reported(self):
        """The not-crying-wolf half: a report that lists everything says nothing."""
        paths = {p["path"] for grp in agent.duplicate_page_groups() for p in grp["pages"]}
        self.assertNotIn("wiki/entities/solo.md", paths)

    def test_source_pages_are_excluded_by_design(self):
        """They are one-per-ingest and share wording constantly, so every ingest would
        look like a duplicate."""
        paths = {p["path"] for grp in agent.duplicate_page_groups() for p in grp["pages"]}
        self.assertFalse(any(p.startswith("wiki/sources/") for p in paths), paths)

    def test_the_cli_and_the_route_share_one_answer(self):
        self.assertIs(serve.duplicate_page_groups, agent.duplicate_page_groups)


class RenamePageFunctionTest(unittest.TestCase):
    """`agent.rename_page` — reachable in-process for the first time.

    CLAUDE.md recorded the cost of its old shape: *"the one maintenance tool whose logic is
    not in `agent.py`, so nothing in-process can reach it — which is exactly how its copy
    of the generated-pages bug went uncovered while `merge_page`'s was caught."*
    """

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self._root = agent.REPO_ROOT
        agent.REPO_ROOT = self.w.root
        self.addCleanup(lambda: setattr(agent, "REPO_ROOT", self._root))
        p = self.w.wiki / "entities" / "united.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\ntitle: United\ntype: entity\ncreated: 2026-01-01\n---\n\n"
                     "# United\n\n## Overview\nAn airline.\n", encoding="utf-8")
        self.other = self.w.wiki / "entities" / "other.md"
        self.other.write_text(
            "---\ntitle: Other\ntype: entity\ncreated: 2026-01-01\n---\n\n# Other\n\n"
            "## Overview\nThe [United](united.md) Nations met. "
            "Also [the airline](united.md) flies.\n", encoding="utf-8")
        agent.init_session()

    def _rename(self, **kw):
        return agent.rename_page("wiki/entities/united.md",
                                 "wiki/entities/united-airlines.md", **kw)

    def test_a_link_whose_text_was_the_old_title_is_STRIPPED(self):
        """That text is what the rename declares wrong: repointing it would relabel a
        country's name as an airline."""
        r = self._rename(new_title="United Airlines")
        self.assertTrue(r["ok"], r)
        body = self.other.read_text(encoding="utf-8")
        self.assertIn("The United Nations met.", body)
        self.assertNotIn("[United](", body)

    def test_a_link_with_other_display_text_is_REPOINTED(self):
        """The other half, and what proves the strip is selective rather than a blanket.

        Asserted by RESOLVING the target rather than matching the written path. The
        repoint writes `../entities/united-airlines.md` from a page already in
        `entities/` — valid, the same formula the original script used, and the convention
        CLAUDE.md states — but a test pinned to that spelling would fail on a later change
        that emitted the equivalent sibling path, which is not a defect.
        """
        r = self._rename(new_title="United Airlines")
        self.assertEqual(r["repointed"], 1, r)
        body = self.other.read_text(encoding="utf-8")
        m = re.search(r"\[the airline\]\(([^)]+)\)", body)
        self.assertIsNotNone(m, body)
        self.assertEqual((self.other.parent / m.group(1)).resolve(),
                         (self.w.wiki / "entities" / "united-airlines.md").resolve())

    def test_a_dry_run_writes_nothing_but_reports_the_counts(self):
        r = self._rename(new_title="United Airlines", dry_run=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["pages_touched"], 1)
        self.assertTrue((self.w.wiki / "entities" / "united.md").exists())
        self.assertIn("[United](united.md)", self.other.read_text(encoding="utf-8"))

    def test_a_bad_slug_is_refused_and_the_correction_is_RETURNED(self):
        """The library reports the corrected PATH; composing a command line is the CLI's
        job, because `agent.py` cannot know its caller's flag names."""
        r = agent.rename_page("wiki/entities/united.md", "wiki/entities/Bad_Name.md")
        self.assertIn("not a valid slug", r["error"])
        self.assertEqual(r["suggested_path"], "wiki/entities/bad-name.md")

    def test_a_move_between_directories_carries_type_along(self):
        """The half-move is worse than the misfile it corrects: the page sits where an
        entity lives while claiming to be a concept, and `_OPENER`, `section_inventory` and
        `bleeding_titles` all read the FIELD."""
        p = self.w.wiki / "concepts" / "ebola.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\ntitle: Ebola\ntype: concept\ncreated: 2026-01-01\n---\n\n"
                     "# Ebola\n\n## Definition\nA virus.\n", encoding="utf-8")
        r = agent.rename_page("wiki/concepts/ebola.md", "wiki/entities/ebola.md")
        self.assertEqual(r["type_changed"], ("concept", "entity"))
        self.assertIn("type: entity",
                      (self.w.wiki / "entities" / "ebola.md").read_text(encoding="utf-8"))

    def test_a_source_page_cannot_be_renamed(self):
        p = self.w.wiki / "sources" / "s.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\ntitle: S\ntype: source\n---\n\n# S\n", encoding="utf-8")
        r = agent.rename_page("wiki/sources/s.md", "wiki/sources/s2.md")
        self.assertIn("immutable", r.get("error", ""))

    def test_an_existing_destination_is_refused(self):
        r = agent.rename_page("wiki/entities/united.md", "wiki/entities/other.md")
        self.assertIn("already exists", r["error"])

    def test_the_cli_is_a_thin_wrapper_over_this(self):
        import rename_page as cli
        self.assertIs(cli.rename_page, agent.rename_page)


class CurateTierTest(unittest.TestCase):
    """The third privilege level.

    Asked for as *"Can those be added as a higher level mcp options? Like new priv level."*
    The blast radius is what justifies a separate key: everything in the write tier touches
    ONE page, while `rename_page` rewrites inbound links across the whole wiki and
    `merge_page` DELETES a page.
    """

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self._saved = (serve.RAW_DIR, serve.WIKI_DIR, serve.REPO_ROOT, agent.REPO_ROOT)
        serve.RAW_DIR, serve.WIKI_DIR = self.w.raw, self.w.wiki
        serve.REPO_ROOT = agent.REPO_ROOT = self.w.root

        def _restore():
            (serve.RAW_DIR, serve.WIKI_DIR, serve.REPO_ROOT,
             agent.REPO_ROOT) = self._saved
        self.addCleanup(_restore)
        for rel, t in (("entities/a.md", "Alpha Corp"),
                       ("entities/b.md", "Alpha Corporation")):
            p = self.w.wiki / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"---\ntitle: {t}\ntype: entity\ncreated: 2026-01-01\n---\n\n"
                         f"# {t}\n\n## Overview\nA company that makes things.\n",
                         encoding="utf-8")
        self._cfg = serve.cfg_get
        serve.cfg_get = lambda s, k, d=None: (
            "secret-mcp" if (s, k) == ("api", "mcp_key") else
            "secret-curate" if (s, k) == ("api", "curate_key") else self._cfg(s, k, d))
        self.addCleanup(lambda: setattr(serve, "cfg_get", self._cfg))
        self._gs = serve.get_settings
        serve.get_settings = lambda: {}
        self.addCleanup(lambda: setattr(serve, "get_settings", self._gs))
        self.c = serve.app.test_client()

    def _post(self, route, key="secret-curate", **body):
        return self.c.post(route, json=body, headers={"Authorization": f"Bearer {key}"})

    def test_the_WRITE_key_cannot_merge_or_rename(self):
        """The whole point of the tier: revoking the ability to delete pages must not
        require revoking the ability to fix a paragraph."""
        for route, body in (("/api/mcp/curate/merge",
                             {"loser": "wiki/entities/b.md",
                              "survivor": "wiki/entities/a.md"}),
                            ("/api/mcp/curate/rename",
                             {"path": "wiki/entities/b.md",
                              "new_path": "wiki/entities/c.md"})):
            with self.subTest(route=route):
                r = self._post(route, key="secret-mcp", **body)
                self.assertEqual(r.status_code, 403, r.get_data(as_text=True)[:200])

    def test_every_curate_route_is_behind_a_key(self):
        rules = [str(r.rule) for r in serve.app.url_map.iter_rules()
                 if "/api/mcp/curate/" in str(r.rule)]
        self.assertTrue(rules)
        for rule in rules:
            with self.subTest(rule=rule):
                r = self.c.get(rule)
                if r.status_code == 405:
                    r = self.c.post(rule, json={})
                self.assertIn(r.status_code, (401, 501),
                              f"{rule} answered {r.status_code} with no key")

    def test_it_fails_closed_when_no_curate_key_is_configured(self):
        serve.cfg_get = lambda s, k, d=None: ("" if (s, k) == ("api", "curate_key")
                                              else self._cfg(s, k, d))
        r = self._post("/api/mcp/curate/rename", key="",
                       path="wiki/entities/b.md", new_path="wiki/entities/c.md")
        self.assertEqual(r.status_code, 501)

    def test_merge_defaults_to_a_DRY_RUN(self):
        """A destructive default is the wrong way round for the one call that deletes, and
        the dry run's refusal — listing what the loser still says — is the useful output."""
        r = self._post("/api/mcp/curate/merge", loser="wiki/entities/b.md",
                       survivor="wiki/entities/a.md")
        self.assertIn(r.status_code, (200, 409), r.get_data(as_text=True)[:200])
        self.assertTrue((self.w.wiki / "entities" / "b.md").exists(),
                        "a merge with no dry_run argument deleted a page")

    def test_an_explicit_dry_run_false_actually_merges(self):
        r = self._post("/api/mcp/curate/merge", loser="wiki/entities/b.md",
                       survivor="wiki/entities/a.md", carry=True, dry_run=False)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        self.assertFalse((self.w.wiki / "entities" / "b.md").exists())
        self.assertIn("/history", r.get_json().get("deleted_content_at", ""))

    def test_merging_a_page_into_itself_is_refused(self):
        self.assertEqual(self._post("/api/mcp/curate/merge",
                                    loser="wiki/entities/a.md",
                                    survivor="wiki/entities/a.md").status_code, 400)

    def test_rename_reports_before_it_writes(self):
        r = self._post("/api/mcp/curate/rename", path="wiki/entities/b.md",
                       new_path="wiki/entities/alpha-corporation.md", dry_run=True)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        self.assertTrue(r.get_json()["dry_run"])
        self.assertTrue((self.w.wiki / "entities" / "b.md").exists())

    def test_rename_refuses_a_path_outside_the_wiki(self):
        self.assertEqual(self._post("/api/mcp/curate/rename", path="wiki/entities/b.md",
                                    new_path="raw/escape.md").status_code, 400)

    def test_the_duplicate_scan_sits_behind_the_WRITE_key(self):
        """Read-only: knowing which pages collide changes nothing, and it is the report you
        need before deciding whether a merge is even right."""
        # Addressed OUTSIDE /curate/ deliberately: a route's address IS its tier, and
        # under /curate/ this one was sent the curate key and checked against the write
        # key — a tool that could never succeed.
        r = self.c.get("/api/mcp/duplicates",
                       headers={"Authorization": "Bearer secret-mcp"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        self.assertIn("groups", r.get_json())

    def test_the_shared_implementations_are_shared(self):
        self.assertIs(serve.agent_rename_page, agent.rename_page)
        self.assertIs(serve.agent_merge_page, agent.merge_page)


class CurateToolGatingTest(unittest.TestCase):
    """Curation tools are advertised only when the key exists."""

    def test_without_a_curate_key_they_are_not_offered(self):
        """A tool that always answers 501 is worse than one that is not offered: the model
        spends a call discovering it and the refusal names no move it can make."""
        names = {t["name"] for t in mcp_server.tools_for(
            mcp_server.Client("https://x.example", "k"))}
        for curate in ("merge_pages", "rename_page", "find_duplicate_pages"):
            self.assertNotIn(curate, names)

    def test_with_one_they_are(self):
        names = {t["name"] for t in mcp_server.tools_for(
            mcp_server.Client("https://x.example", "k", curate_key="c"))}
        self.assertEqual(names, {"read_page", "search_wiki", "page_report",
                                 "replace_passage", "write_page",
                                 "find_duplicate_pages", "merge_pages", "rename_page"})

    def test_the_key_is_picked_from_the_ROUTE_not_the_call_site(self):
        """So a curation route added later cannot be accidentally authenticated with the
        lesser key by whoever writes its `call_tool` branch."""
        c = mcp_server.Client("https://x.example", "write", curate_key="curate")
        self.assertEqual(c._key_for("/api/mcp/read"), "write")
        self.assertEqual(c._key_for("/api/mcp/write"), "write")
        self.assertEqual(c._key_for("/api/mcp/curate/merge"), "curate")
        self.assertEqual(c._key_for("/api/mcp/curate/anything-new"), "curate")

    def test_without_a_curate_key_a_curate_route_still_sends_the_write_key(self):
        """Rather than an empty Authorization header, which would read as a malformed
        request instead of an honest 403."""
        c = mcp_server.Client("https://x.example", "write")
        self.assertEqual(c._key_for("/api/mcp/curate/merge"), "write")

    def test_merge_mirrors_the_servers_dry_run_DEFAULT(self):
        """A destructive call whose default differs between the two halves is how you
        delete a page you meant to preview."""
        sent = {}

        class _C:
            curate_key = "c"
            budget = 1

            def post(self_, route, **body):
                sent.update(body)
                return {"ok": True}

        mcp_server.call_tool(_C(), "merge_pages",
                             {"loser": "wiki/a.md", "survivor": "wiki/b.md"})
        self.assertTrue(sent["dry_run"], "the client defaulted a merge to destructive")


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


class McpPathNormalisationTest(unittest.TestCase):
    """`_mcp_path` validated one string and returned another.

    Containment is decided by RESOLVING, which is why `wiki/../etc/passwd` is caught where
    a prefix test on the raw string would pass it. But the raw string was then handed on,
    and it differs from the resolved one for anything carrying `.` or `..` — so every
    caller resolved it again for itself, and the check that passed and the write that
    landed were describing the same file only by agreement. It returns the normalised
    form now.
    """
    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        import serve
        self.serve = serve
        # REPO_ROOT too: _mcp_path resolves REPO_ROOT / path and contains it against
        # WIKI_DIR, so rebinding only one of the pair makes every path "escape the wiki" —
        # which would have passed the escape test for entirely the wrong reason.
        self._saved = (serve.WIKI_DIR, serve.REPO_ROOT)
        serve.WIKI_DIR, serve.REPO_ROOT = self.w.wiki, agent.REPO_ROOT

    def tearDown(self):
        self.serve.WIKI_DIR, self.serve.REPO_ROOT = self._saved
        self.w.__exit__(None, None, None)

    def test_a_dotted_path_comes_back_normalised(self):
        got, err = self.serve._mcp_path("wiki/entities/../entities/a.md")
        self.assertIsNone(err)
        self.assertEqual(got, "wiki/entities/a.md")

    def test_a_plain_path_is_unchanged(self):
        got, err = self.serve._mcp_path("wiki/entities/a.md")
        self.assertIsNone(err)
        self.assertEqual(got, "wiki/entities/a.md")

    def test_an_escape_is_still_refused(self):
        for bad in ("wiki/../etc/passwd.md", "wiki/.history/entities/a.md", "", "raw/a.md",
                    "wiki/entities/a.txt"):
            with self.subTest(bad=bad):
                got, err = self.serve._mcp_path(bad)
                self.assertEqual(got, "")
                self.assertIsNotNone(err)


if __name__ == "__main__":
    unittest.main()
