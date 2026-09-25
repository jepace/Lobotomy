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

# (name, find, replace) or (name, find, replace, "tools/other.py") — the guard being
# broken usually lives in agent.py, so that is the default; a fourth element names another
# file, because the maintenance tools carry guards too and a guard nothing tests is a guard
# nothing tests wherever it lives. `find` must appear exactly once in the target file, or
# the mutation is reported as STALE rather than silently skipped: an anchor that stopped
# matching means the code moved and the mutation is no longer testing what it claims.
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
    ("dangling links are unwrapped",
     '            if display.strip() and _is_dangling(_page, link_path, index):',
     '            if False:', "tools/repair_links.py"),
    ("dangling: repointing beats unwrapping",
     # Unwrap first and a link that merely took the wrong route to a page that still
     # exists is destroyed instead of repaired.
     '            fixed     = _repair_path(_page, link_path, index)\n'
     '            if fixed:',
     '            fixed     = None\n'
     '            if fixed:', "tools/repair_links.py"),
    ("dangling: .history is not a link target",
     # rglob matches directories, and a deleted page leaves
     # wiki/.history/entities/united.md/ behind — a directory with the page's name. Let
     # it into the index and every dead link is "repaired" to point inside the history
     # store.
     '        try:\n'
     '            p.relative_to(history_dir)\n'
     '            continue          # a stored revision is a record, never a link target\n'
     '        except ValueError:\n'
     '            pass',
     '        pass', "tools/repair_links.py"),
    ("duplicate-heading refusal names the call that works",
     # Revert to the message that shipped: correct about the constraint, silent about
     # where the caller's text should go. The observed model resent the same call twice
     # and then dumped the other section's content into Overview.
     '        _existing = {_norm_heading(n) for _lvl, n in _page_section_names(body, _fm_title(frontmatter))}\n'
     '        _elsewhere = [d for d in _dupes if _norm_heading(d) in _existing]\n'
     '        if _elsewhere:',
     '        _elsewhere = []\n'
     '        if _elsewhere:'),
    ("replace_text refusal names the call that works",
     '            f"replacement text; to change what is under it, call update_section on "\n'
     '            f"{_dupes[0]!r}, or append_section to add to it."',
     '            f"replacement text."'),
    ("index blurbs carry no borrowed links",
     # The blurb is another page's prose, so its links are relative to THAT page. Copied
     # into wiki/index.md they resolve outside the wiki: 5,900 of them, regenerated on
     # every ingest, so no repair pass could ever win.
     '                return _MD_LINK_RE.sub(r"\\1", s)[:120]',
     '                return s[:120]'),
    ("repair passes leave generated files alone",
     # index.md is rewritten by _rebuild_index and log.md is append-only, and
     # _snapshot_version skips both by name — so a write to either cannot be reverted.
     '        if f.name in _GENERATED:\n            continue\n',
     '', "tools/repair_links.py"),
    ("fallback warning names the quota it is cooling on",
     # "unavailable" is one word for two situations needing opposite responses:
     # slowing down protects a per-minute limit and cannot preserve a daily one.
     '                _left, _daily = _model_cooldown_left(chain[0])',
     '                _left, _daily = 0, False'),
    ("history: the whole source slug is stored",
     # A capture slugs to 87 characters; truncating at 72 names no file, so the
     # row silently loses its link and renders as plain text.
     '                _src = _REASON_RE.sub("-", Path(_rel).stem.lower())[:120].strip("-")',
     '                _src = _REASON_RE.sub("-", Path(_rel).stem.lower())[:72].strip("-")'),
    ("history: the writing tool is recorded",
     # "ingest" does not say whether it was a whole-page regenerate or a one-line
     # fix, and those are very different things to find in fifty rows.
     '    with write_tool(fn_name if fn_name in _TOOL_LABELS else ""):\n        return fn(args) if fn else f"Unknown tool: {fn_name}"',
     '    if True:\n        return fn(args) if fn else f"Unknown tool: {fn_name}"'),
    ("history: counts are words, not lines",
     # Pages are written unwrapped, so one paragraph is one line: a rewritten
     # paragraph reported "+1 -1" and looked like a typo fix.
     '        b = _re_words.findall(before)\n        a = _re_words.findall(after)',
     '        b = before.splitlines()\n        a = after.splitlines()'),
    ("history: empty filename slots are placeheld",
     # Skip an empty slot and every later part shifts left, so the tool parses as
     # the source.
     '        if _why or _src or _tool:\n            _suffix += f"__{_why or \'-\'}"',
     '        if _why:\n            _suffix += f"__{_why}"'),
    ("history: rows name the sections that changed",
     '            if nm and nm not in seen:',
     '            if False:'),
    ("history: an ingest records which source it folded in",
     '        if _why == "ingest":',
     '        if False:'),
    ("history: the page title is not a section",
     # The H1 is not a section anywhere else either, and a row claiming the page
     # own name as a changed heading is noise on every title fix.
     '            m = re.match(r"^#{2,6}[ \\t]*(\\S.*?)[ \\t]*$", line)',
     '            m = re.match(r"^#{1,6}[ \\t]*(\\S.*?)[ \\t]*$", line)'),
    ("done(): the ingested flag is derived, not asked for",
     # An optional boolean twenty rounds after the work, with no feedback when it
     # is forgotten and the only consequence on a page the model never sees: the
     # article sits unwikified in the reading list.
     '    ingested = "1" if (args.get("ingested") or _derived) else "0"',
     '    ingested = "1" if args.get("ingested") else "0"'),
    ("merge: generated pages are not repointed",
     # Patching index.md is undone by the rebuild seconds later; patching log.md
     # rewrites what the log says happened, and neither can be reverted.
     '        if f.resolve() == loser.resolve() or is_generated_page(f):',
     '        if f.resolve() == loser.resolve():'),
    ("rename: generated pages are not repointed",
     '    if page.resolve() == src or is_generated_page(page):',
     '    if page.resolve() == src:', "tools/rename_page.py"),
    ("merge: the subject's names are folded before comparing",
     # Without it, merging two pages for one hospital is refused because each
     # sentence uses that page's own name for it — blocking the cleanup that the
     # naming mismatch made necessary in the first place.
     '                if _subject_re:\n                    line = _subject_re.sub("\\u00absubject\\u00bb", line)',
     '                if False:\n                    line = _subject_re.sub("\\u00absubject\\u00bb", line)'),
    ("resolver: a spelled-out name finds its initialised page",
     # Without it, done() demands a page that exists under an initialised title,
     # lookup_titles confirms the demand, and the model makes a duplicate.
     '    _first = next((c for c in name.lower() if c.isalnum()), "")\n    for title, rel in by_key.items():\n        if _first and title[:1].isalnum() and title[:1] != _first:\n            continue\n        if _initialism_match(name, title):\n            return rel\n    return ""',
     '    return ""'),
    ("handback: the refusal forbids the re-read",
     # Handing the content back is half the job. Without this clause an observed
     # ingest called read_section for the text it had just been given — a whole
     # round and a pacing window, to fetch what it was already holding.
     'f"into it and call update_section again — do NOT call read_section first.\\n\\n"',
     'f"into it and call update_section again.\\n\\n"'),
    ("handback: the payload is delimited",
     # A bare body blends into the prose of the refusal.
     'f\'<section path="{path}" name="{section}">\\n{heading}\\n{old_text}\\n</section>\'',
     'f"{heading}\\n{old_text}"'),
    ("paths: group 1 consumes bare relative paths",
     # The one that actually bit: "../sources/backgammon-wikipedia.md" in prose had
     # its titles linked inside it, and group 1 then protected the damage forever.
     '                r"(\\[[^\\]]*\\]\\([^)]*\\)|" + _BARE_URL + r"|" + _BARE_PATH + r")"',
     '                r"(\\[[^\\]]*\\]\\([^)]*\\)|" + _BARE_URL + r")"'),
    ("nested repair: never invent a directory target",
     # The old fallback truncated at the first '[' and produced "[X](../sources)",
     # which resolves — so lint went quiet while the real target was gone.
     'title map to do it properly.\n    return m.group(0)',
     'title map to do it properly.\n    return f"[{link_text}](" + bad_url[:bad_url.index(\'[\')].rstrip(\'/\') + ")"', "tools/repair_links.py"),
    ("urls: group 1 consumes bare URLs",
     # Without this a title appearing in a URL path gets linked inside the URL,
     # breaking the URL and linking a page the sentence was not about.
     '                r"(\\[[^\\]]*\\]\\([^)]*\\)|" + _BARE_URL + r"|" + _BARE_PATH + r")"',
     '                r"(\\[[^\\]]*\\]\\([^)]*\\)" + r")"'),
    ("urls: mangled ones are healed",
     # Prevention alone leaves every page that already carries one broken forever, since
     # group 1 protects the damage.
     '    body, _healed = _MANGLED_URL_RE.subn(\n'
     '        lambda m: _MD_LINK_RE.sub(r"\\1", m.group(0)), body)',
     '    _healed = 0'),
    ("repeat links: section resets the first mention",
     # Without the reset a title links once per PAGE. On a 136KB page that leaves
     # everything below the first section with no navigation at all.
     '            if is_heading[i]:\n'
     '                _seen[0] = False                # a new section gets its own first mention',
     '            if is_heading[i]:\n'
     '                pass'),
    ("repeat links: lists always link",
     # A source page's ## Entities and a Timeline are lookup tables read out of order.
     '    is_listish = [bool(re.match(r"^\\s*(?:[-*+]\\s|\\d+[.)]\\s|\\|)", ln)) for ln in lines]',
     '    is_listish = [False for ln in lines]'),
    ("repeat links: existing repeats are unlinked",
     # Without this the rule applies only to newly written text, and the ~9,000 pages
     # written under the old rule keep every repeat forever.
     '                if _seen[0]:\n'
     '                    return inner.group(1)       # a repeat in prose — unlink it',
     '                if False:\n'
     '                    return inner.group(1)'),
    ("repeat links: only unlink what the autolinker wrote",
     # Drop the display-text check and a hand-written alias link,
     # "[the disease](../concepts/measles.md)", gets stripped to plain text.
     '                if frozenset(_WORD_RE.findall(inner.group(1).lower())) != _title_toks:\n'
     '                    return m.group(1)',
     '                if False:\n'
     '                    return m.group(1)'),
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
     '        _suffix = ""\n        if _why or _src or _tool:\n            _suffix += f"__{_why or \'-\'}"',
     '        _suffix = ""\n        if False:\n            pass'),
    ("a version is labelled by what created it, not what replaced it",
     '        maker = revs[i - 1] if i > 0 else None',
     '        maker = revs[i]'),
    ("the newest write's label lands on the current page",
     '        rows.append({"current": True, "id": None,\n'
     '                     "when": revs[-1]["when"].strftime("%Y-%m-%d %H:%M:%S"),\n'
     '                     "why": revs[-1]["why"], "source": revs[-1]["source"],',
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
    # Every file any mutation touches, so the finally block restores all of them. Read
    # once, before anything is written, and never re-read: restoring from a file this
    # script has already mutated would make the damage permanent.
    originals = {AGENT: original}

    caught = missed = stale = 0
    try:
        for name, find, replace, *rest in MUTATIONS:
            if pattern and pattern not in name.lower():
                continue
            target = REPO / rest[0] if rest else AGENT
            if target not in originals:
                originals[target] = target.read_text(encoding="utf-8")
            base = originals[target]
            if target == AGENT and "_WIKI_DIR_AT_IMPORT" in replace:
                base = seeded
            if base.count(find) != 1:
                print(f"  STALE   {name}  (anchor matched {base.count(find)}x — "
                      f"the code moved; fix this mutation)")
                stale += 1
                continue
            # Restore every file touched so far BEFORE applying this one. Without this,
            # mutations in different files stack: the run that added multi-file support
            # left agent.py mutated while mutating rename_page.py, and reported CAUGHT for
            # a guard whose removal no test actually noticed — the failures came from the
            # previous mutation. A mutation runner that lies about coverage is worse than
            # not having one.
            for _t, _text in originals.items():
                _t.write_text(_text, encoding="utf-8")
            target.write_text(base.replace(find, replace), encoding="utf-8")
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
        for target, text in originals.items():
            target.write_text(text, encoding="utf-8")
            assert target.read_text(encoding="utf-8") == text, \
                f"{target.name} was not restored!"

    print(f"\n{caught} caught, {missed} missed, {stale} stale.")
    if missed or stale:
        print("A MISSED guard can be deleted without any test objecting. Write the test.")
    return 1 if (missed or stale) else 0


if __name__ == "__main__":
    sys.exit(main())
