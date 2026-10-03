#!/usr/bin/env python3
"""
Add a story to the reading list from the command line, bypassing the browser.

The web paste box is one route into `raw/`, and when it fails — a proxy in front of the
server, a browser extension, a clipboard carrying something the request cannot survive —
there was no other way in, so an article you have in front of you could not be captured at
all. This is the second route. It touches no network, needs no LLM and costs nothing.

    python3 tools/add_story.py article.txt --url https://www.nytimes.com/...
    pbpaste | python3 tools/add_story.py --url https://www.nytimes.com/...
    python3 tools/add_story.py article.txt --title "How Meta Uses A.I. Data Centers"

It writes `raw/<slug>.md` with the same frontmatter the web capture writes, so the item
appears in the reading list and Wikify works on it exactly as if it had been pasted. The
write goes through `agent._atomic_write`, so it inherits the tree's ownership and is safe
to run as root beside a server running as another user.

**Input is read as bytes and decoded with errors="replace".** A clipboard that has been
through a browser can carry a lone UTF-16 surrogate — half an emoji from a truncated copy
— and that is exactly what used to make the web route lose a whole story. A capture tool
that fails on the characters the article actually contains is not a fallback.
"""
import argparse
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent
# NOT `from agent import RAW_DIR`: that binds the value at import, so anything that
# rebinds it later — the test harness, or a caller pointing at another tree — is ignored
# and this writes into whichever raw/ existed at import time. Resolved at call time
# instead, which is the rule CLAUDE.md states for all four of agent's path globals.


def slugify(text: str, limit: int = 60) -> str:
    """The web capture's rule, with its dotfile bug already fixed.

    An empty slug produced the filename ".txt" there — hidden, so the story saved and was
    then nowhere to be seen. Content opening with punctuation or in a non-Latin script is
    enough to produce one, and a URL slug can be all punctuation too.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())[:limit].strip("-")
    return slug or "story-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def title_from(content: str, url: str) -> str:
    """The headline, which is rarely the first line of a browser copy.

    A copied article opens with the site name — "nytimes.com" — and often a byline and a
    reading time before the headline. Taking the first non-empty line titled a story
    "nytimes.com", which is useless in a reading list of thirty. The headline is the first
    line that reads like one: several words, not a bare domain, not a duration.
    """
    lines = [l.strip().lstrip("#").strip() for l in content.splitlines()]
    lines = [l for l in lines if l and not l.startswith("<")]
    for line in lines[:8]:
        # A duration line can be long enough to pass the word test — "13 – 17 minutes
        # read" is five words — so it is excluded by shape.
        if re.match(r"^[\d\s–—-]+\s*(min|minute|hour)", line, re.I):
            continue
        # Three words or more. This alone excludes a bare site name, since a domain has no
        # spaces; an explicit domain check beside it was dead code, and mutate.py said so.
        if len(line.split()) >= 3:
            return line[:120]
    if lines:
        return lines[0][:120]
    if url:
        tail = url.rstrip("/").split("/")[-1].split("?")[0]
        return tail.replace("-", " ").replace("_", " ") or url
    return "Untitled story"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="file to read; omit to read stdin")
    ap.add_argument("--url", default="", help="the article's URL, for the source page")
    ap.add_argument("--title", default="", help="override the derived title")
    ap.add_argument("--name", default="", help="override the raw filename")
    args = ap.parse_args()

    # Bytes, then decode with errors="replace" — see the module docstring. A tool meant to
    # rescue a paste the web route choked on must not choke on the same bytes.
    if args.file:
        data = Path(args.file).read_bytes()
    elif not sys.stdin.isatty():
        data = sys.stdin.buffer.read()
    else:
        ap.error("give a file or pipe the text in on stdin")
    content = data.decode("utf-8", errors="replace").strip()
    if not content:
        print("Nothing to add: the input was empty.", file=sys.stderr)
        return 1

    title = args.title.strip() or title_from(content, args.url)
    name = args.name.strip() or slugify(title) + ".md"
    if not name.endswith(".md"):
        name += ".md"
    dest = agent.RAW_DIR / name
    if dest.exists():
        print(f"raw/{name} already exists — pass --name to choose another filename.",
              file=sys.stderr)
        return 1

    today = datetime.date.today().isoformat()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    fm = ["---", f"title: {agent.fm_quote(title)}"]
    if args.url:
        fm.append(f"url: {args.url}")
    fm += [f"saved: {today}", f"added: {now}", "wikified: false",
           "source: manual", "---", "", ""]
    agent._atomic_write(dest, "\n".join(fm) + content + "\n")

    print(f"Added raw/{name} ({len(content):,} chars)")
    print(f"  title: {title}")
    print("\nIt is in the reading list now. Wikify it from there, or:")
    print(f"  python3 tools/wiki.py \"ingest raw/{name}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
