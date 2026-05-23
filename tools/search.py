#!/usr/bin/env python3
"""Lobotomy wiki search — CLI wrapper around the shared search_wiki_core engine."""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agent import search_wiki_core

USAGE = """\
Usage: search [OPTIONS] [KEYWORD ...]

Search all wiki pages. Multiple keywords use AND logic (all must appear).

Filter tokens (mix freely with keywords):
  in:<subdir>        Restrict to a subdirectory: sources, entities, concepts, synthesis
  tag:<value>        Require a frontmatter tag
  after:YYYY-MM-DD   Require created >= date
  before:YYYY-MM-DD  Require created <= date

Examples:
  search monterey
  search casa monterey
  search in:entities monterey
  search tag:nonprofit after:2026-01-01

Remote mode (set both env vars to search a running Lobotomy server):
  LOBOTOMY_URL   Base URL of the server, e.g. https://wiki.example.com
  LOBOTOMY_KEY   Bearer token — matches api.push_key in the server's config.json
"""


def find_wiki_root():
    candidates = [
        Path(__file__).resolve().parent.parent / "wiki",
        Path.cwd() / "wiki",
    ]
    for p in candidates:
        if p.is_dir():
            return p
    raise FileNotFoundError(
        f"Cannot find wiki/ directory (tried: {', '.join(str(c) for c in candidates)})\n"
        "Run from the repository root or the tools/ directory."
    )


def highlight(line, patterns, use_ansi):
    if not use_ansi:
        return line
    BOLD, RESET = "\033[1m", "\033[0m"
    for p in patterns:
        line = p.sub(lambda m: f"{BOLD}{m.group()}{RESET}", line)
    return line


def remote_search(base_url, api_key, query):
    """Call /api/search on a remote server. Returns same structure as search_wiki_core."""
    url = f"{base_url}/api/search?q={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            msg = json.loads(body).get("error", body)
        except ValueError:
            msg = body
        return {"error": f"HTTP {e.code}: {msg}", "keywords": [], "scope": None, "results": []}
    except urllib.error.URLError as e:
        return {"error": str(e.reason), "keywords": [], "scope": None, "results": []}
    # Normalise remote result dicts to match local structure (no lines field)
    for r in data.get("results", []):
        r.setdefault("lines", [])
    return {"error": None, "keywords": data.get("keywords", []),
            "scope": data.get("scope"), "results": data.get("results", [])}


def render_results(res, use_ansi, repo_root=None):
    keywords = res["keywords"]
    results = res["results"]
    patterns = [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords]

    scope_desc = f" in {res['scope']}/" if res["scope"] else ""
    kw_desc = " + ".join(keywords) if keywords else "(filter only)"
    print(f"\nSearch: {kw_desc}{scope_desc}")
    print(f"Found {len(results)} page{'s' if len(results) != 1 else ''}.")

    if not results:
        return

    for rank, r in enumerate(results, 1):
        path_display = r.get("rel") or str(r["path"])
        created = f"  created: {r['created']}" if r.get("created") else ""
        score = r.get("score", 0)
        print(f"\n{'=' * 60}")
        print(f"[{rank}] {path_display}  ({score} match{'es' if score != 1 else ''}){created}")
        print("=" * 60)

        lines = r.get("lines", [])
        snippet = r.get("snippet", "")
        if lines:
            snippet_idx = next(
                (i for i, ln in enumerate(lines) if ln.strip()[:120] == snippet),
                len(lines) // 2,
            )
            for j, ctx_line in enumerate(lines):
                prefix = "> " if j == snippet_idx else "  "
                display = highlight(ctx_line, patterns, use_ansi) if j == snippet_idx else ctx_line
                print(f"{prefix} {display}")
            print()
        elif snippet:
            print(f"  {highlight(snippet, patterns, use_ansi)}\n")


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE.rstrip())
        sys.exit(0 if args else 1)

    query = " ".join(args)
    use_ansi = sys.stdout.isatty()

    base_url = os.environ.get("LOBOTOMY_URL", "").rstrip("/")
    if base_url:
        api_key = os.environ.get("LOBOTOMY_KEY", "").strip()
        if not api_key:
            print("Error: LOBOTOMY_URL is set but LOBOTOMY_KEY is missing.", file=sys.stderr)
            print("Set LOBOTOMY_KEY to the api.push_key value from the server's config.json.", file=sys.stderr)
            sys.exit(1)
        res = remote_search(base_url, api_key, query)
    else:
        try:
            wiki_dir = find_wiki_root()
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        res = search_wiki_core(query, wiki_dir)

    if res["error"]:
        print(f"Error: {res['error']}", file=sys.stderr)
        sys.exit(1)

    render_results(res, use_ansi)


if __name__ == "__main__":
    main()
