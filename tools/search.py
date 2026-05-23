#!/usr/bin/env python3
"""Lobotomy wiki search — CLI wrapper around the shared search_wiki_core engine."""

import sys
import re
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


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE.rstrip())
        sys.exit(0 if args else 1)

    try:
        wiki_dir = find_wiki_root()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    res = search_wiki_core(" ".join(args), wiki_dir)
    if res["error"]:
        print(f"Error: {res['error']}", file=sys.stderr)
        sys.exit(1)

    keywords = res["keywords"]
    results = res["results"]
    patterns = [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords]
    use_ansi = sys.stdout.isatty()

    scope_desc = f" in {res['scope']}/" if res["scope"] else ""
    kw_desc = " + ".join(keywords) if keywords else "(filter only)"
    print(f"\nSearch: {kw_desc}{scope_desc}")
    print(f"Found {len(results)} page{'s' if len(results) != 1 else ''}.")

    if not results:
        sys.exit(0)

    repo_root = wiki_dir.parent
    for rank, r in enumerate(results, 1):
        rel = r["path"].relative_to(repo_root)
        created = f"  created: {r['created']}" if r["created"] else ""
        score = r["score"]
        print(f"\n{'=' * 60}")
        print(f"[{rank}] {rel}  ({score} match{'es' if score != 1 else ''}){created}")
        print("=" * 60)

        if r["lines"]:
            snippet_idx = next(
                (i for i, ln in enumerate(r["lines"]) if ln.strip()[:120] == r["snippet"]),
                len(r["lines"]) // 2,
            )
            for j, ctx_line in enumerate(r["lines"]):
                prefix = "> " if j == snippet_idx else "  "
                display = highlight(ctx_line, patterns, use_ansi) if j == snippet_idx else ctx_line
                print(f"{prefix} {display}")
            print()
        elif r["snippet"]:
            print(f"  {highlight(r['snippet'], patterns, use_ansi)}\n")


if __name__ == "__main__":
    main()
