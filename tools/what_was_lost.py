#!/usr/bin/env python3
"""
What a write REMOVED from a page, as opposed to what it merely reshortened.

The history row tells you a write cut 2,461 words and added 479. It cannot tell you whether
that was duplication collapsing — which is what you asked for — or material walking out of
the door, and that is the only question you actually have. On the write that took
artificial-intelligence.md from 28,401 to 13,564 bytes the two look identical from the
metadata.

So this reports the things whose disappearance is not a matter of taste:

  * SECTIONS that existed before and do not exist after. A heading is not prose; it does
    not get tightened away. One that is gone means its subject is gone or was folded
    somewhere else, and you are the only one who can say which.
  * LINK TARGETS present before and absent after. A wiki link is a reference to another
    page, so a dropped link is a dropped reference — the strongest mechanical signal that
    content left rather than contracted. (Repeat links to the same page are collapsed
    first, because the autolinker's once-per-section rule legitimately removes duplicates
    of a link the page still carries.)
  * TIMELINE DATES present before and absent after. A dated entry is a record, and
    add_timeline_entry exists precisely so those accumulate rather than get rewritten.
  * The word and byte arithmetic, so a big cut is visible next to what it cost.

No LLM, no API cost, and it writes nothing at all — it only reads `wiki/.history`.

    python3 tools/what_was_lost.py wiki/concepts/artificial-intelligence.md
    python3 tools/what_was_lost.py wiki/concepts/artificial-intelligence.md --all
    python3 tools/what_was_lost.py wiki/concepts/artificial-intelligence.md --rev 20260926163407123456

With no flags it compares the newest stored revision against the page as it stands now,
which is "what did the most recent write do". `--all` walks every stored revision, oldest
first, so a page that was whittled down over several passes shows where it went. `--rev`
compares one specific revision against the current page, matching the History view's
Compare button.

A revision file holds the content as it was BEFORE the write that produced it, so
"revision -> current" is the write that destroyed that content. That is the same
off-by-one the history view has to get right (see CLAUDE.md), and it is why the newest
revision — not the oldest — is the baseline for the default comparison.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent
from agent import _MD_LINK_RE, _fm_title, _page_section_names, _parse_revision_stem


def _body(text: str) -> str:
    m = re.match(r"^---\s*\n.*?\n---\s*\n", text, re.DOTALL)
    return text[m.end():] if m else text


def _sections(text: str) -> "list[str]":
    return [n for _, n in _page_section_names(_body(text), _fm_title(text))]


def _link_targets(text: str) -> "set[str]":
    """Distinct internal link targets. Distinct is the point: the once-per-section rule
    removes REPEAT links legitimately, so counting occurrences would report loss on every
    relink sweep. A target that no longer appears at all is a reference that is gone."""
    out = set()
    for _disp, target in _MD_LINK_RE.findall(_body(text)):
        t = target.strip()
        if t and not re.match(r"^[a-z][a-z0-9+.-]*://", t) and not t.startswith("#"):
            out.add(t.split("#")[0])
    return out


def _timeline_dates(text: str) -> "set[str]":
    out = set()
    for line in _body(text).splitlines():
        m = agent._TL_BULLET_RE.match(line.strip())
        if m:
            out.add(m.group(1))
    return out


def _words(text: str) -> int:
    return len(_body(text).split())


def _revisions(page_dir: Path):
    """(id, path) oldest first. Lexical order is chronological — the timestamp prefix is
    fixed width, which is the whole reason the filenames are built that way."""
    if not page_dir.is_dir():
        return []
    return [(f.stem, f) for f in sorted(page_dir.glob("*.md"))]


def _report(label: str, before: str, after: str, reason: str = "", tool: str = "") -> bool:
    b_secs, a_secs = _sections(before), _sections(after)
    gone_secs = [s for s in b_secs if s not in set(a_secs)]
    new_secs = [s for s in a_secs if s not in set(b_secs)]
    gone_links = sorted(_link_targets(before) - _link_targets(after))
    gone_dates = sorted(_timeline_dates(before) - _timeline_dates(after))

    wb, wa = _words(before), _words(after)
    bb, ba = len(before.encode("utf-8")), len(after.encode("utf-8"))
    pct = f"{ba / bb * 100:.0f}%" if bb else "n/a"
    stamp = " ".join(x for x in (reason, tool) if x and x != "-")

    print(f"\n{label}{('  [' + stamp + ']') if stamp else ''}")
    print(f"  words {wb:,} -> {wa:,}   bytes {bb:,} -> {ba:,}  ({pct} of what it was)")
    print(f"  sections {len(b_secs)} -> {len(a_secs)}")

    lost = bool(gone_secs or gone_links or gone_dates)
    if gone_secs:
        print(f"  SECTIONS GONE ({len(gone_secs)}):")
        for s in gone_secs:
            print(f"      - {s}")
    if new_secs:
        print(f"  sections added ({len(new_secs)}): {', '.join(new_secs)}")
    if gone_dates:
        print(f"  TIMELINE DATES GONE ({len(gone_dates)}): {', '.join(gone_dates)}")
    if gone_links:
        print(f"  LINK TARGETS GONE ({len(gone_links)}):")
        for t in gone_links:
            print(f"      - {t}")
    if not lost:
        print("  nothing structural lost — no section, dated entry or link target "
              "disappeared")
    return lost


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    want_all = "--all" in sys.argv[1:]
    rev_arg = ""
    for i, a in enumerate(sys.argv[1:]):
        if a == "--rev" and i + 2 <= len(sys.argv[1:]):
            rev_arg = sys.argv[1:][i + 1]
            if rev_arg in args:
                args.remove(rev_arg)
    if not args:
        print(__doc__)
        return 2

    rel = args[0]
    page = (agent.REPO_ROOT / rel) if not Path(rel).is_absolute() else Path(rel)
    if not page.is_file():
        print(f"No such page: {rel}")
        return 2
    try:
        wiki_rel = page.resolve().relative_to(agent.WIKI_DIR.resolve())
    except ValueError:
        print(f"Not a wiki page: {rel}")
        return 2

    current = page.read_text(encoding="utf-8", errors="replace")
    revs = _revisions(agent.HISTORY_DIR / str(wiki_rel))
    if not revs:
        print(f"{wiki_rel}: no stored history — nothing to compare against.")
        return 0

    print(f"{wiki_rel}  ({len(revs)} stored revision(s))")
    any_lost = False
    if want_all:
        # Oldest first. Each revision holds the content BEFORE the write that made the
        # next one, so consecutive pairs are the writes, and the last pair is
        # newest-revision -> current.
        chain = [(i, r) for i, r in enumerate(revs)]
        for i, (rid, rpath) in chain:
            before = rpath.read_text(encoding="utf-8", errors="replace")
            if i + 1 < len(revs):
                nxt_id, nxt_path = revs[i + 1]
                after = nxt_path.read_text(encoding="utf-8", errors="replace")
                _, reason, _src, tool = _parse_revision_stem(nxt_id)
                label = f"{rid} -> {nxt_id}"
            else:
                after = current
                reason = tool = ""
                label = f"{rid} -> current"
            any_lost |= _report(label, before, after, reason, tool)
    else:
        if rev_arg:
            match = [(i, p) for i, p in revs if i.startswith(rev_arg)]
            if not match:
                print(f"No revision matching {rev_arg!r}. Available:")
                for rid, _ in revs[-10:]:
                    print(f"  {rid}")
                return 2
            rid, rpath = match[0]
        else:
            rid, rpath = revs[-1]
        before = rpath.read_text(encoding="utf-8", errors="replace")
        any_lost = _report(f"{rid} -> current", before, current)

    if any_lost:
        print("\nSomething structural is gone. That may be exactly what you asked for — "
              "a folded-in section, a duplicate reference — but it is not prose being "
              "tightened, so it is worth a look. Revert from the page's History view; the "
              "revert is itself undoable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
