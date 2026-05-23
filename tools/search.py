#!/usr/bin/env python3
"""
Lobotomy — Wiki Keyword Search

Searches all markdown files in wiki/ using the same logic as the AI agent.

Usage: python3 tools/search.py <keyword> [keyword2 ...]
       python3 tools/search.py tag:<tag> [keyword ...]
       python3 tools/search.py after:YYYY-MM-DD [keyword ...]
       python3 tools/search.py before:YYYY-MM-DD [keyword ...]
       python3 tools/search.py in:<subdir> <keyword>

Multiple keywords use AND logic (all must appear in the page).
Filter tokens (in:, tag:, after:, before:) can be combined with keywords.
"""

import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agent import search_wiki_core


def find_wiki_root(script_path):
    repo_root = script_path.resolve().parent.parent
    wiki_dir = repo_root / "wiki"
    if wiki_dir.is_dir():
        return wiki_dir
    cwd_wiki = Path.cwd() / "wiki"
    if cwd_wiki.is_dir():
        return cwd_wiki
    raise FileNotFoundError(
        f"Cannot find wiki/ at {wiki_dir} or {cwd_wiki}\n"
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
        print(__doc__.strip())
        sys.exit(0 if args else 1)

    query = " ".join(args)

    try:
        wiki_dir = find_wiki_root(Path(__file__))
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    res = search_wiki_core(query, wiki_dir)
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
        print(f"\n{'=' * 60}")
        print(f"[{rank}] {rel}  ({r['score']} match{'es' if r['score'] != 1 else ''})"
              + (f"  created: {r['created']}" if r["created"] else ""))
        print("=" * 60)

        if r["lines"]:
            # Find index of snippet line within context window
            snippet_idx = next(
                (i for i, l in enumerate(r["lines"]) if l.strip()[:120] == r["snippet"]),
                len(r["lines"]) // 2,
            )
            # Compute starting line number (approximate — we don't track it in core)
            for j, ctx_line in enumerate(r["lines"]):
                prefix = "> " if j == snippet_idx else "  "
                display = highlight(ctx_line, patterns, use_ansi) if j == snippet_idx else ctx_line
                print(f"{prefix} {display}")
            print()
        elif r["snippet"]:
            print(f"  {highlight(r['snippet'], patterns, use_ansi)}\n")


if __name__ == "__main__":
    main()
