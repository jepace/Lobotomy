#!/usr/bin/env python3
"""MCP server for Lobotomy — put an LLM's judgement where the report-only tools stop.

Asked for as *"I made a mcp for another app. How about mcp for lobotomy? Then I can get
you to rewrite pages..."*, and it lands on a gap this project has written down six times.
Every mechanical report here ends the same way — `overview_drift.py`, `page_report.py`,
`bleeding_titles.py`, the RATCHET check — with no `--apply`, because *"every repair here
is a judgement about what the page is for"*. A machine can FIND those; only judgement
fixes them. This is the pipe that puts judgement on the other end.

TWO DESIGN DECISIONS, BOTH LOAD-BEARING
---------------------------------------

**1. It is an HTTP client of the running server, never a second process editing `wiki/`.**
CLAUDE.md records that splitting the ingest into its own process was deferred for one
specific reason: it *"turns a single-writer wiki into a two-writer one, which is exactly
what the autolinker's cache invalidation assumes is not true — the mtime backstop would
become load-bearing rather than a backstop"*. An MCP server with filesystem access is that
same second writer, arriving through a different door. Going through `/api/mcp/*` keeps
serve.py the only writer, and every read and edit inherits `_atomic_write`, the history
snapshot, the autolink, the write-path guards and the ownership inheritance for free.

It is also the only design that reaches the data: the wiki lives on the jail and the
client does not, so a filesystem MCP server would read an empty tree.

**2. The write guards stay ON.** All of them — read-before-write, the heading rules,
summary accretion, the shrink refusal, `wiki/sources/` immutability, `raw/` untouchable.
An MCP client is an LLM editing the wiki, which is the exact condition those guards exist
for; turning them off for the client that seems clever enough not to need them is how the
invariants rot, and this project has receipts on that. `allow_shrink=true` is already the
designed opt-out for consolidation, which is what "rewrite pages" mostly means.

WHAT THE BUDGET IS FOR
----------------------
Four guards derive their threshold from the caller's output budget, and all four used to
read `cfg_int("llm", "max_tokens")` — correct for the configured provider, wrong for this
client. A capable client was being refused a rewrite **it can actually perform**, and the
sections where that bites are precisely the RATCHET ones no agent tool can repair. So the
budget travels with the request (`--budget`, default below) and `agent.output_budget`
scopes it to that request. See `agent.output_budget_tokens`.

READ-BEFORE-WRITE NEEDS NO NEW MACHINERY, WHICH IS WORTH KNOWING
----------------------------------------------------------------
These calls are stateless, so read coverage recorded by one does not survive into the
next, and `update_section` will refuse a blind write. That is fine rather than a problem:
the refusal **hands the section back and credits coverage**, so a blind write costs one
extra round trip and then succeeds. Over MCP a round trip is milliseconds; the reason
those handbacks were built is that the same round costs ~60s at `max_rpm: 1`. Nothing has
to be invented, and no guard has to be weakened, to make this work.

USAGE
-----
    python3 tools/mcp_server.py --base-url https://lobotomy.example --key-env LOBOTOMY_MCP_KEY

The key is read from the environment, never from the command line: an argument is visible
in `ps` to every user on the box and lands in shell history. `config.json` is NOT read
here — this process may run on a different machine from the server entirely.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

#: What this client can emit in one response, in tokens. The default is deliberately far
#: above `max_tokens`' 16,384: that is the whole reason the budget is a request parameter
#: rather than a config read. Override with --budget when driving from a smaller model.
DEFAULT_BUDGET_TOKENS = 200_000

#: Seconds. A read of a 200KB page across the public internet is not instant, and the
#: server may be mid-autolink; `agent`'s own guards make the work bounded, not fast.
HTTP_TIMEOUT = 120

PROTOCOL_VERSION = "2024-11-05"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class ApiError(Exception):
    """A refusal from the server, carried to the client as tool text rather than raised.

    A tool that throws gives the model a transport error; a tool that RETURNS the refusal
    gives it the thing the refusal says to do next. Every write-path guard here is written
    to name a call that works (principle 4), so losing that text to an exception would
    discard the most useful part of the reply.
    """


class Client:
    def __init__(self, base_url: str, key: str, budget: int = DEFAULT_BUDGET_TOKENS):
        self.base = base_url.rstrip("/")
        self.key = key
        self.budget = budget

    def get(self, route: str, **params) -> dict:
        params.setdefault("budget", self.budget)
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        url = f"{self.base}{route}?{qs}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.key}",
            "Accept": "application/json",
            # Three independent signals say "this is a fetch" to require_login, which
            # answers 401 JSON to one and a redirect to a navigation. Without this a
            # session-protected route would hand back the login page's HTML with status
            # 200 and json.loads would die on the "<" of "<!DOCTYPE" — the exact bug
            # CLAUDE.md records being reported three times.
            "X-Requested-With": "XMLHttpRequest",
        })
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                raw = r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
            except ValueError:
                # An HTML body did not come from Lobotomy: every route answers JSON and
                # `_unhandled` turns even a crash into JSON, so this is the proxy.
                raise ApiError(
                    f"HTTP {e.code} with a non-JSON body — the request did not reach "
                    f"Lobotomy itself. Check the reverse proxy's log. First 200 bytes: "
                    f"{raw[:200]!r}") from None
            raise ApiError(body.get("error") or f"HTTP {e.code}: {raw[:200]}") from None
        except urllib.error.URLError as e:
            raise ApiError(f"could not reach {self.base}: {e.reason}") from None
        try:
            return json.loads(raw)
        except ValueError:
            raise ApiError(f"server did not answer JSON: {raw[:200]!r}") from None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
#
# Read-only for now, deliberately. The writes are a separate, louder change and want their
# own review: this half is enough to ask the live wiki what is wrong with it, and it cannot
# damage anything.

TOOLS = [
    {
        "name": "read_page",
        "description": (
            "Read a wiki page, or one named section of it. Above the caller's read limit a "
            "whole page comes back as an OUTLINE (every section, its size, its opening "
            "characters) rather than text; pass offset=0 to page through it instead. A "
            "section longer than half the output budget also comes back as an outline of "
            "its blocks, because it cannot be rewritten in one response."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "wiki-relative, e.g. wiki/entities/donald-trump.md"},
                "section": {"type": "string",
                            "description": "optional section name, e.g. Overview"},
                "offset": {"type": "integer",
                           "description": "character offset for paging a large page; "
                                          "omit for the outline"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_wiki",
        "description": "Full-text search across the wiki. Returns matching pages, best first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "description": "1-100, default 20"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "page_report",
        "description": (
            "Everything mechanically wrong with one page, measured rather than read: empty "
            "sections, illegal or duplicate headings, summary drift, dumping grounds, "
            "duplicated paragraphs and bullets, dead and split links, competing link "
            "targets, source problems, and RATCHET — sections too long for any agent tool "
            "to consolidate, which can only be repaired by hand. Report-only; nothing is "
            "changed. These are the findings whose fix is a judgement, which is what this "
            "server is for."),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
]


def call_tool(client: Client, name: str, args: dict) -> str:
    if name == "read_page":
        r = client.get("/api/mcp/read", path=args.get("path", ""),
                       section=args.get("section", ""),
                       offset=args.get("offset", -1))
        return r.get("content", "")
    if name == "search_wiki":
        r = client.get("/api/mcp/search", q=args.get("query", ""),
                       limit=args.get("limit", 20))
        if not r.get("results"):
            return f"No pages matched {args.get('query','')!r}."
        return json.dumps(r["results"], indent=1)
    if name == "page_report":
        r = client.get("/api/mcp/report", path=args.get("path", ""))
        return json.dumps(r, indent=1, default=str)
    raise ApiError(f"unknown tool: {name}")


# ---------------------------------------------------------------------------
# JSON-RPC over stdio
# ---------------------------------------------------------------------------
#
# Hand-rolled on purpose. requirements.txt is "flask, markdown — nothing else; API calls
# use stdlib urllib", and the framing here is a length-delimited header plus a JSON body.
# Adding a dependency to write eighty lines would be the more expensive choice.

def _respond(obj) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _result(req_id, result) -> None:
    _respond({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id, code, message) -> None:
    _respond({"jsonrpc": "2.0", "id": req_id,
              "error": {"code": code, "message": message}})


def handle(client: Client, msg: dict):
    """Dispatch one JSON-RPC message. Returns nothing; replies on stdout.

    A NOTIFICATION (no `id`) must get no reply at all — answering one is a protocol error
    that some clients treat as fatal, and `notifications/initialized` is the first thing
    every client sends.
    """
    method = msg.get("method") or ""
    req_id = msg.get("id")
    if req_id is None:
        return

    if method == "initialize":
        _result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "lobotomy", "version": "1"},
        })
    elif method == "tools/list":
        _result(req_id, {"tools": TOOLS})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        try:
            text = call_tool(client, name, args)
        except ApiError as e:
            # isError, not a JSON-RPC error: a refusal is a RESULT the model should read
            # and act on, and every guard here is written to name the call that works.
            _result(req_id, {"content": [{"type": "text", "text": str(e)}],
                             "isError": True})
        except Exception as e:                        # noqa: BLE001 — never kill the loop
            _result(req_id, {"content": [{"type": "text",
                                          "text": f"{type(e).__name__}: {e}"}],
                             "isError": True})
        else:
            _result(req_id, {"content": [{"type": "text", "text": text}]})
    elif method == "ping":
        _result(req_id, {})
    else:
        _error(req_id, -32601, f"method not found: {method}")


def serve_stdio(client: Client, stdin=None, stdout=None) -> int:
    """Read newline-delimited JSON-RPC from stdin until EOF.

    One malformed line must not end the session: a client that sends a bad frame is a
    client with a bug, and dying takes every working tool down with it.
    """
    stream = stdin if stdin is not None else sys.stdin
    for line in stream:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _error(None, -32700, "parse error")
            continue
        if not isinstance(msg, dict):
            _error(None, -32600, "invalid request")
            continue
        handle(client, msg)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default=os.environ.get("LOBOTOMY_BASE_URL", ""),
                    help="e.g. https://lobotomy.example (or $LOBOTOMY_BASE_URL)")
    ap.add_argument("--key-env", default="LOBOTOMY_MCP_KEY",
                    help="environment variable holding api.mcp_key (default "
                         "LOBOTOMY_MCP_KEY). The key is never taken as an argument: an "
                         "argument is visible in ps and lands in shell history.")
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET_TOKENS,
                    help=f"this client's output budget in tokens (default "
                         f"{DEFAULT_BUDGET_TOKENS}). Four server-side guards scale with "
                         f"it; the server clamps an absurd value rather than trusting it.")
    args = ap.parse_args(argv)

    if not args.base_url:
        ap.error("--base-url is required (or set $LOBOTOMY_BASE_URL)")
    key = os.environ.get(args.key_env, "").strip()
    if not key:
        ap.error(f"${args.key_env} is empty — set it to api.mcp_key from the server's "
                 f"config.json")
    return serve_stdio(Client(args.base_url, key, args.budget))


if __name__ == "__main__":
    sys.exit(main())
