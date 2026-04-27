#!/usr/bin/env python3
"""
Lobotomy — Wiki Keyword Search

Searches all markdown files in wiki/ recursively.
Ranks results by total keyword match count (descending).
Shows matching lines with ±2 lines of context.

Usage: python3 tools/search.py <keyword> [keyword2 ...]
       python3 tools/search.py --help

Multiple keywords are treated as OR (each keyword searched independently).
Results are ranked by total combined match count across all keywords.

No external dependencies required.
"""

import sys
import re
from pathlib import Path


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


def search_file(filepath, patterns):
    """Return list of match records for all pattern hits in a file."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    lines = text.splitlines()
    matches = []
    seen = set()

    for i, line in enumerate(lines):
        hits = [p.pattern for p in patterns if p.search(line)]
        if hits and i not in seen:
            seen.add(i)
            matches.append({
                "line_num": i + 1,
                "line": line,
                "before": lines[max(0, i - 2):i],
                "after": lines[i + 1:min(len(lines), i + 3)],
                "hits": hits,
            })

    return matches


def highlight(line, patterns, use_ansi):
    """Bold-highlight matched text when outputting to a TTY."""
    if not use_ansi:
        return line
    BOLD, RESET = "\033[1m", "\033[0m"
    result = line
    for p in patterns:
        result = p.sub(lambda m: f"{BOLD}{m.group()}{RESET}", result)
    return result


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        sys.exit(0 if args else 1)

    keywords = args
    patterns = []
    for kw in keywords:
        try:
            patterns.append(re.compile(re.escape(kw), re.IGNORECASE))
        except re.error as e:
            print(f"Invalid keyword '{kw}': {e}", file=sys.stderr)
            sys.exit(1)

    try:
        wiki_dir = find_wiki_root(Path(__file__))
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    md_files = sorted(wiki_dir.rglob("*.md"))
    if not md_files:
        print("No markdown files found in wiki/.")
        sys.exit(0)

    results = []
    for f in md_files:
        matches = search_file(f, patterns)
        if matches:
            results.append((f, matches))

    # Rank by total match count descending, then path ascending for ties
    results.sort(key=lambda x: (-sum(len(m["hits"]) for m in x[1]), str(x[0])))

    total_hits = sum(sum(len(m["hits"]) for m in ms) for _, ms in results)
    print(f"\nSearch: {' + '.join(keywords)}")
    print(
        f"Found {total_hits} match{'es' if total_hits != 1 else ''} "
        f"in {len(results)} file{'s' if len(results) != 1 else ''}."
    )

    if not results:
        sys.exit(0)

    use_ansi = sys.stdout.isatty()
    repo_root = wiki_dir.parent

    for rank, (filepath, matches) in enumerate(results, 1):
        rel = filepath.relative_to(repo_root)
        total = sum(len(m["hits"]) for m in matches)
        print(f"\n{'=' * 60}")
        print(f"[{rank}] {rel}  ({total} match{'es' if total != 1 else ''})")
        print("=" * 60)

        for m in matches:
            base = m["line_num"] - len(m["before"])
            for j, ctx in enumerate(m["before"]):
                print(f"  {base + j:>4} | {ctx}")
            hl = highlight(m["line"], patterns, use_ansi)
            print(f"> {m['line_num']:>4} | {hl}")
            for j, ctx in enumerate(m["after"], 1):
                print(f"  {m['line_num'] + j:>4} | {ctx}")
            print()


if __name__ == "__main__":
    main()
