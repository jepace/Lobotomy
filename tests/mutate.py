#!/usr/bin/env python3
"""Check that the test suite would actually catch a regression.

A green suite proves nothing on its own. The failure mode this project keeps hitting is a
test that passes while testing nothing — a helper tested directly while the guard that
calls it is gone, a fixture that silently pointed at the real wiki. The only way to know a
guard is protected is to break the guard and watch a test go red.

So: for each mutation below, disable one guard in agent.py, run the suite, and report
whether it noticed. Every mutation must be CAUGHT. A MISSED line names a guard that could
be deleted tomorrow without a single test objecting — which is exactly how the duplicate
heading guard and the read_file dispatch layer came to be uncovered.

agent.py is restored afterwards, including on Ctrl-C or a crash. Nothing else is touched;
this only ever writes to agent.py and only inside the repo it is run from.

    python3 tests/mutate.py            # all mutations
    python3 tests/mutate.py timeline   # only mutations whose name matches

Add a mutation whenever you add a guard. If you cannot write one that the suite catches,
the guard is untested.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "tools" / "agent.py"
RUNNER = REPO / "tests" / "run_all.py"

# (name, find, replace) — `find` must appear exactly once in agent.py, or the mutation is
# reported as STALE rather than silently skipped: an anchor that stopped matching means
# the code moved and the mutation is no longer testing what it claims.
MUTATIONS = [
    ("heading-rule: date",
     '        elif _DATED_HEADING_RE.search(name):\n            bad.append((name, "names a date"))',
     '        elif False:\n            bad.append((name, "names a date"))'),
    ("heading-rule: page title",
     '        if level > 1 and want_title and _norm_heading(name) == want_title:',
     '        if False:'),
    ("heading-rule: link in heading",
     '        elif _MD_LINK_RE.search(name):\n            bad.append((name, "contains a link"))',
     '        elif False:\n            bad.append((name, "contains a link"))'),
    ("heading-rule: duplicates (create_file)",
     '    _dupes = _heading_dupes(body)\n    if _dupes:',
     '    _dupes = _heading_dupes(body)\n    if False:'),
    ("opener required",
     '    _opener = _OPENER.get(pg_type)\n    if _opener:',
     '    _opener = _OPENER.get(pg_type)\n    if False:'),
    ("date qualifier absorption",
     '        stripped = _TRAILING_DATE_RE.sub("", name).strip(" -–—:,")',
     '        stripped = name'),
    ("timeline: loose bullet parsing",
     # Narrow the reader back to the exact shape the renderer emits. A hand-written
     # `- 2026-08: ...` then goes unrecognised, is filed as prose, and the same fact is
     # written again beneath it.
     r'    r"^[-*][ \t]*(?:\*\*|__)?[ \t]*(\d{4}(?:-\d{2}){0,2})(?![-\d])[ \t]*(?:\*\*|__)?[ \t]*"',
     r'    r"^[-*][ \t]*\*\*(\d{4}(?:-\d{2}){0,2})\*\*[ \t]*"'),
    ("timeline: date consumed whole",
     # Without the guard the engine backtracks 2026-09-12 to 2026-09 and reads "-12" as
     # the separator.
     '(\\d{4}(?:-\\d{2}){0,2})(?![-\\d])',
     '(\\d{4}(?:-\\d{2}){0,2})'),
    ("timeline: restatements are folded",
     '    rendered = _render_timeline(_tl_dedupe(entries))',
     '    rendered = _render_timeline(entries)'),
    ("timeline: every write path normalizes",
     '    content = normalize_timeline(content)\n    content = _inject_sources_section(content, p)\n    _mkdir_inheriting(p.parent)',
     '    content = _inject_sources_section(content, p)\n    _mkdir_inheriting(p.parent)'),
    ("timeline: a fuller wording replaces the restatement it absorbs",
     '    if [(d, t) for d, t, _ in _merged] == [(d, t) for d, t, _ in _kept]:',
     '    if len(_merged) == len(_kept):'),
    ("timeline sorts rather than appends",
     '    return "\\n".join(f"- **{d}** — {t}" for d, t, _ in\n'
     '                      sorted(entries, key=lambda e: (_tl_sort_key(e[0]), e[2])))',
     '    return "\\n".join(f"- **{d}** — {t}" for d, t, _ in entries)'),
    ("update_file writes the body it validated",
     '    content, _renamed, _collided = _absorb_date_qualifiers(content, _disk_body, _title)\n'
     '    _new_body = _re.sub(r"^---\\s*\\n.*?\\n---\\s*\\n", "", content, flags=_re.DOTALL)',
     '    _new_body, _renamed, _collided = _absorb_date_qualifiers(_new_body, _disk_body, _title)'),
    ("read_file dispatch keeps offset=0 distinct from absent",
     '        int(a["offset"]) if str(a.get("offset", "")).strip() not in ("", "None") else -1),',
     '        int(a.get("offset", 0) or 0)),'),
    ("wiki_pages resolves WIKI_DIR at call time",
     '    root = root if root is not None else WIKI_DIR',
     '    root = root if root is not None else _WIKI_DIR_AT_IMPORT'),
    ("read_section not-found hides the page's own H1",
     '        if name.lower() in skip or len(m.group(1)) == 1:',
     '        if False:'),
    ("promote_openers refuses to invent a missing lead",
     '        if len(lead) < 40:',
     '        if False:'),
    ("root-run tools hand new dirs to the server user",
     '    for new in reversed(missing):\n        _inherit_owner(new)',
     '    for new in reversed(missing):\n        pass'),
    ("page title comes from frontmatter, not the slug",
     '    return _fm_title(text) or (stem.replace("-", " ").title() if stem else "")',
     '    return stem.replace("-", " ").title() if stem else ""'),
    ("create_file writes the page's H1",
     '    body_text = ensure_h1(body_text, title)',
     '    body_text = body_text'),
    ("ensure_h1 does not mistake '## Section' for an H1",
     'r"\\A\\s*#(?!#)[ \\t]+(\\S[^\\n]*?)[ \\t]*\\n"',
     'r"\\A\\s*#[ \\t]*(\\S[^\\n]*?)[ \\t]*\\n"'),
    ("rename_section refuses to merge two populated sections",
     '        if len(droppable) != len(others):',
     '        if False:'),
    ("allow_shrink is opt-in, not the default",
     '    if not _allow_shrink and _old_cmp >= 800 and _new_cmp < _old_cmp * 0.6:',
     '    if False:'),
    ("merge_page refuses while the loser still says something new",
     '    if result["outstanding"] and not force:',
     '    if False:'),
    ("deprecated pages leave the title map",
     '            if title and not deprecated:',
     '            if title:'),
    ("history records why each change happened",
     '        _suffix = f"__{_why}" if _why else ""',
     '        _suffix = ""'),
    ("a version is labelled by what created it, not what replaced it",
     '        maker = revs[i - 1] if i > 0 else None',
     '        maker = revs[i]'),
    ("the newest write's label lands on the current page",
     '        rows.append({"current": True, "id": None,\n'
     '                     "when": revs[-1]["when"].strftime("%Y-%m-%d %H:%M:%S"),\n'
     '                     "why": revs[-1]["why"], "added": a, "removed": r,',
     '        rows.append({"current": True, "id": None,\n'
     '                     "when": "", \n'
     '                     "why": "", "added": a, "removed": r,'),
    ("create_file reports every problem, not the first",
     '    if _problems:\n        if len(_problems) == 1:',
     '    if _problems:\n        _problems = _problems[:1]\n        if len(_problems) == 1:'),
    ("read-before-write on update_file",
     '_WIKI_READ_LIMIT = 20_000',
     '_WIKI_READ_LIMIT = 20_000_000'),
]

PRELUDE = ('WIKI_DIR  = REPO_ROOT / "wiki"',
           'WIKI_DIR  = REPO_ROOT / "wiki"\n_WIKI_DIR_AT_IMPORT = WIKI_DIR')


def main() -> int:
    pattern = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    original = AGENT.read_text(encoding="utf-8")
    seeded = original.replace(*PRELUDE)          # only used by the wiki_pages mutation

    caught = missed = stale = 0
    try:
        for name, find, replace in MUTATIONS:
            if pattern and pattern not in name.lower():
                continue
            base = seeded if "_WIKI_DIR_AT_IMPORT" in replace else original
            if base.count(find) != 1:
                print(f"  STALE   {name}  (anchor matched {base.count(find)}x — "
                      f"the code moved; fix this mutation)")
                stale += 1
                continue
            AGENT.write_text(base.replace(find, replace), encoding="utf-8")
            r = subprocess.run([sys.executable, str(RUNNER)],
                               capture_output=True, text=True, cwd=REPO)
            summary = next((l.strip() for l in reversed(r.stdout.splitlines())
                            if "failure(s)" in l), "")
            if r.returncode != 0:
                caught += 1
                print(f"  CAUGHT  {name}\n            {summary}")
            else:
                missed += 1
                print(f"  MISSED  {name}  ** no test objected **")
    finally:
        AGENT.write_text(original, encoding="utf-8")
        assert AGENT.read_text(encoding="utf-8") == original, "agent.py was not restored!"

    print(f"\n{caught} caught, {missed} missed, {stale} stale.")
    if missed or stale:
        print("A MISSED guard can be deleted without any test objecting. Write the test.")
    return 1 if (missed or stale) else 0


if __name__ == "__main__":
    sys.exit(main())
