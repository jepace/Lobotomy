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
     '            return _MD_LINK_RE.sub(r"\\1", s)[:120]',
     '            return s[:120]'),
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
    ("source list: a lowercased entity list is refused",
     # Each row becomes a page title in Step 5, and a source page cannot be edited
     # afterwards, so the form is permanent.
     '        if len(_alpha) >= 2 and len(_lower) * 2 > len(_alpha):',
     '        if False:'),
    ("autolink: a prose bullet is not a lookup row",
     # "## Claims" is full sentences in bullet form; always-link there repeats a
     # title in every claim, which is what once-per-section exists to stop.
     '        return len(_MD_LINK_RE.sub(r"\\1", re.sub(r"^\\s*(?:[-*+]|\\d+[.)])\\s*", "", ln))) <= 60',
     '        return True'),
    ("autolink: the upgrade never starts inside another link",
     # Without the balance check a shorter title steals the tail of a longer
     # title's link and manufactures the malformed [[a](b)](c) shape.
     '                    _depth = 0\n                    for _k, _ch in enumerate(m.group(0)):',
     '                    _depth = 99\n                    for _k, _ch in enumerate(m.group(0)):'),
    ("autolink: typographic variants match",
     # A curly apostrophe in the page and a straight one in the title silently
     # skipped one name in a list where every other name linked.
     '    return "".join(_FLEX_CHARS.get(ch) or re.escape(ch) for ch in word)',
     '    return re.escape(word)'),
    ("autolink: the per-line probe is punctuation-free",
     # A word carrying the other spelling is never found by a plain substring test,
     # so the probe would reject the line before the flexible pattern ran.
     '        _probe = max(re.findall(r"\\w+", title.lower()), key=len, default="")',
     '        _probe = max(title.lower().split(), key=len, default="")'),
    ("autolink: group 1 consumes a malformed [[a](b)](c) whole",
     # The growth engine. The plain pattern eats "[[a](b)" and leaves "](c)", so
     # the scanner meets that path as bare text, links the title inside it, and the
     # link gains a layer on every pass.
     '_LINK_G1 = r"\\[(?:[^\\[\\]]|\\[[^\\]]*\\])*\\]\\([^)]*\\)"',
     '_LINK_G1 = r"\\[[^\\]]*\\]\\([^)]*\\)"'),
    ("autolink: a short link inside a longer title is upgraded",
     # Group 1 wins at every position, so once "[New York University](x) Langone
     # Health" exists no later pass can fix it — the linked span starts the phrase,
     # so group 2 is never reached there.
     '            if _upgrade not in (None, _UPGRADE_PENDING) and "](" in line:',
     '            if False:'),
    ("template assumption: update_section names the page's other sections",
     # The model guesses "Overview" from the template without reading. A guess that
     # MISSES already gets the full list; a guess that HITS got the section and
     # nothing else, so material was merged into Overview with nothing to notice a
     # better home existed — and the hit is the common case.
     '        _others = [n for _lvl, n in _page_section_names(body, _fm_title(frontmatter))\n                   if _norm_heading(n) != _norm_heading(section)]',
     '        _others = []'),
    ("template assumption: append_section reports what it created beside",
     # Asked for the template's "Positions" on a page calling it "Political
     # Stances", it made a second section for one subject. _heading_dupes cannot
     # see that, because the names differ.
     '        _existing = [n for _lvl, n in _page_section_names(body, _fm_title(frontmatter))]',
     '        _existing = []'),
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
     '                r"(" + _LINK_G1 + r"|" + _BARE_URL + r"|" + _BARE_PATH + r")"',
     '                r"(" + _LINK_G1 + r"|" + _BARE_URL + r")"'),
    ("nested repair: never invent a directory target",
     # The old fallback truncated at the first '[' and produced "[X](../sources)",
     # which resolves — so lint went quiet while the real target was gone.
     'title map to do it properly.\n    return m.group(0)',
     'title map to do it properly.\n    return f"[{link_text}](" + bad_url[:bad_url.index(\'[\')].rstrip(\'/\') + ")"', "tools/repair_links.py"),
    ("urls: group 1 consumes bare URLs",
     # Without this a title appearing in a URL path gets linked inside the URL,
     # breaking the URL and linking a page the sentence was not about.
     '                r"(" + _LINK_G1 + r"|" + _BARE_URL + r"|" + _BARE_PATH + r")"',
     '                r"(" + _LINK_G1 + r"|" + _BARE_PATH + r")"'),
    ("urls: mangled ones are healed",
     # Prevention alone leaves every page that already carries one broken forever,
     # since group 1 protects the damage.
     '    body, _healed = _MANGLED_URL_RE.subn(_heal_mangled, body)',
     '    _healed = 0'),
    ("repeat links: section resets the first mention",
     # Without the reset a title links once per PAGE. On a 136KB page that leaves
     # everything below the first section with no navigation at all.
     '        _sec_of.append(_sec_n)',
     '        _sec_of.append(0)'),
    ("repeat links: lookup rows always link",
     # A source page's ## Entities and a Timeline are lookup tables read out of order.
     '    is_listish = [_is_lookup_row(ln) for ln in lines]',
     '    is_listish = [False for ln in lines]'),
    ("repeat links: existing repeats are unlinked",
     # Without this the rule applies only to newly written text, and the ~9,000 pages
     # written under the old rule keep every repeat forever.
     '                if _seen_key[0] in _linked_here:',
     '                if False:'),
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
     # The empty `()` keeps the group count at three, so the mutation tests the reader's
     # generosity rather than renumbering the text group out from under _parse_timeline.
     r'''    r"^[-*][ \t]*(?:\*\*|__)?[ \t]*(\d{4}(?:-\d{2}){0,2})(?![-\d])" + _TL_RANGE +
    r"[ \t]*(?:\*\*|__)?[ \t]*"''',
     r'    r"^[-*][ \t]*\*\*(\d{4}(?:-\d{2}){0,2})\*\*()[ \t]*"'),
    ("timeline: date consumed whole",
     # Without the guard the engine backtracks 2026-09-12 to 2026-09 and reads "-12" as
     # the separator. Anchored through `+ _TL_RANGE` because the range alternative carries
     # the same date pattern, so the bare string now appears twice.
     '(\\d{4}(?:-\\d{2}){0,2})(?![-\\d])" + _TL_RANGE',
     '(\\d{4}(?:-\\d{2}){0,2})" + _TL_RANGE'),
    ("timeline: restatements are folded",
     '    rendered = _render_timeline(_tl_dedupe(entries))',
     '    rendered = _render_timeline(entries)'),
    ("timeline: every write path normalizes",
     '    content = normalize_timeline(content)\n    # A source page is written once and is immutable afterwards, so its lookup lists are\n    # ordered here or never. (heal_pages covers the ones written before this existed.)\n    content = sort_lookup_lists(content)\n    content = _inject_sources_section(content, p)',
     '    # A source page is written once and is immutable afterwards, so its lookup lists are\n    # ordered here or never. (heal_pages covers the ones written before this existed.)\n    content = sort_lookup_lists(content)\n    content = _inject_sources_section(content, p)'),
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
    # A tag is one string however the model wrapped it. The backtick is the character
    # that mattered: the model writes a tag name as markdown code, and _collect_tags
    # feeding it back made one page's habit the wiki's vocabulary.
    ("tag: backtick is a wrapper",
     '_TAG_WRAP = "\\"\'`“”‘’ \\t"',
     '_TAG_WRAP = "\\"\'“”‘’ \\t"'),
    ("tag: update_file normalizes the line it is given",
     '            if _canon != _tags_m.group(0):',
     '            if False:'),
    ("tag: heal_pages repairs pages already on disk",
     '                    if _canon != _tg.group(0):',
     '                    if False:'),
    # Frontmatter scalars: one reading, one rendering. A backticked title was
    # invisible to the autolinker, and quoting without escaping made two readers
    # disagree about the same page's title.
    ('fm: backticks out of a scalar',
     '    v = v.replace("`", "")',
     '    v = v'),
    ('fm: strip matched wrapper pairs only',
     '    while len(v) >= 2 and v[0] == v[-1] and v[0] in "\\"\'`":\n        v = v[1:-1].strip()',
     '    v = v.strip("\\"\'`")'),
    ('fm: escape on write',
     '    return \'"\' + str(value).replace("\\\\", "\\\\\\\\").replace(\'"\', \'\\\\"\') + \'"\'',
     '    return \'"\' + str(value) + \'"\''),
    ('fm: heal a malformed title line',
     '                    if _canon_t != f"title: {_ti.group(1).strip()}":',
     '                    if False:'),
    # The outline has to name the route that fits the job. It used to warn only
    # when a page was too large to rewrite, and say nothing when it wasn't — so a
    # reorganize was steered into per-section rewrites the 40% guard refuses.
    ('outline: names the whole-page route when it is available',
     '            _rewritable = total <= (cfg_int("llm", "max_tokens", default=16384) * 4) // 2',
     '            _rewritable = False'),
    ('outline: says why a reorganize is not per-section',
     '                f"page: this moves material BETWEEN sections, so it cannot be done as a "',
     '                f"page: "'),
    # A history row that caps its section list without saying so read as a
    # four-section edit on a write that had deleted a dozen.
    ('history: the changed-section list is not silently capped',
     '        return names[:limit] if limit else names',
     '        return names[:limit] if limit else names[:4]'),
    # A section written `# Name` is invisible to every listing — including the
    # outline an agent plans a reorganization from.
    ('h1-demote: only a stray level-1 heading, not the page title',
     '        if len(m.group(1)) == 1 and _norm_heading(m.group(2)) != norm_title:',
     '        if len(m.group(1)) == 1 :'),
    ('h1-demote: a fenced code block is not headings',
     '        if stripped.startswith("```") or stripped.startswith("~~~"):',
     '        if False:'),
    ('h1-demote: heal_pages applies it',
     '                    if _demoted:',
     '                    if False:'),
    # A section list that arrives in the refusal arrives after the decision. The
    # page's shape has to be where the agent is still choosing what to write.
    ("lookup: names each page's sections",
     '            found.append(f"  - {n} → wiki/{rel}{note}{_page_shape(rel)}")',
     '            found.append(f"  - {n} → wiki/{rel}{note}")'),
    # The diagnostic that finds common-word titles. Its value is entirely in not
    # crying wolf — a report that flags every page is one nobody reads.
    ('bleeding: a page does not report its own title',
     '                    if any(r == rel for _t, r in cands[key]):',
     '                    if False:',
     'tools/bleeding_titles.py'),
    ('bleeding: headings are not linkable, so not counted',
     '            if re.match(r"^\\s*#{1,6}\\s", line):',
     '            if False:',
     'tools/bleeding_titles.py'),
    ('bleeding: an existing link is not counted twice',
     '        bare = _MD_LINK_RE.sub(" ", body)',
     '        bare = body',
     'tools/bleeding_titles.py'),
    ('bleeding: a concept page is not a bug report',
     '    bleeds = [r for r in keep if r["type"] not in ("concept", "?")]',
     '    bleeds = keep',
     'tools/bleeding_titles.py'),
    # rename_page's messages, which are the only guidance its callers get.
    ('rename: a bad slug is refused with the corrected one',
     '    _fix = re.sub(r"[^a-z0-9]+", "-", dst.stem.lower()).strip("-")',
     '    _fix = ""',
     'tools/rename_page.py'),
    ('rename: says when the title is left unchanged',
     'if not NEW_TITLE and not DRY:',
     'if False:',
     'tools/rename_page.py'),
    # A short page is handed back whole, which also stops the same page being
    # refused once per section.
    ('handback: a short page comes back whole',
     '        if _full_len <= _WIKI_READ_LIMIT:',
     '        if False:'),
    ('handback: full read coverage is credited',
     '            _cov[wiki_rel] = max(_cov.get(wiki_rel, 0), _full_len)',
     '            pass'),
    # lookup_titles must not send the agent off to read first: that is never
    # cheaper and is a round dearer on a long page.
    # The marker that decides the route. Wrong for a long page it sends the
    # agent to read_file for an outline it cannot use, and costs a round.
    # One answer for "how do I reach this page to write to it", shared by lookup_titles,
    # create_file's worklist handback and done()'s refusal. They had three copies and the
    # copies disagreed on a large page.
    ('route: the read route matches the page size',
     '    elif size and size <= _WIKI_READ_LIMIT:',
     '    elif size:'),
    # The legend quotes the per-page phrases rather than restating them, so a route cannot
    # be marked on a row with nothing explaining it.
    # A fetch gets JSON back even when refused. A 302 to the login page is followed
    # transparently by fetch, so resp.json() met <!DOCTYPE and the user saw
    # "JSON.parse: unexpected character at line 1 column 1" with their article unsaved.
    ('auth: a fetch is answered with JSON, not a redirect',
     '    if _wants_json():\n        where = url_for("setup") if setup else url_for("auth_login")',
     '    if False:\n        where = url_for("setup") if setup else url_for("auth_login")',
     'tools/serve.py'),
    # ...and a browser NAVIGATION must still redirect, or nobody can log in.
    ('auth: a navigation still redirects to the login page',
     '    if request.headers.get("X-Requested-With") == "fetch":\n        return True',
     '    if True:\n        return True',
     'tools/serve.py'),
    ('auth: the client recognises an HTML page instead of parsing it',
     "      if (/^\\s*<(!doctype|html)/i.test(body)) {",
     "      if (false) {",
     'tools/templates/base.html'),
    # An empty textarea reads as an empty file, and saving it wrote the article away.
    ('inbox: a textarea that was never loaded cannot be saved',
     "  if (textarea.dataset.loaded !== '1') {",
     "  if (false) {",
     'tools/templates/inbox.html'),
    # The log could not say which route went out — the tool result is truncated there and
    # the routes are the part that is cut — so "did it follow the route" was unanswerable
    # from a production log.
    # A 500 is not an expired session, and telling the user to sign in again is a move
    # that cannot fix it (principle 4, in the UI).
    # Code is not prose. `container.exe` came out as `[container](…).exe`, and a ```sh
    # block came out with links in a command the reader copies into a terminal.
    # A comma in a title is optional in the text. Without it the long title missed and the
    # shorter PARENT matched, so a sentence about a campus linked to the whole system — a
    # wrong link that resolves, which nothing reports.
    ('autolink: a comma in a title is optional in the text',
     '_FLEX_CHARS[","] = ",?"',
     '_FLEX_CHARS[","] = ","'),
    ('autolink: a code span is protected like a link',
     '                + r"|" + _CODE_SPAN + r")"',
     '                + r")"'),
    ('autolink: a fenced block is skipped',
     '            if is_heading[i] or is_fenced[i]:',
     '            if is_heading[i]:'),
    ('autolink: a fence closes only on its own character',
     "            if m and m.group(1)[0] == _fence[0] and len(m.group(1)) >= len(_fence):",
     "            if m:"),
    ('autolink: a hash inside a fence is not a heading',
     '    is_heading = [h and not f for h, f in zip(is_heading, is_fenced)]',
     '    is_heading = list(is_heading)'),
    # Prevention alone freezes the damage: group 1 protects a link once it is in a span.
    ('autolink: links already written inside code are flattened',
     '    body, _code_healed = _unlink_in_code(body)',
     '    body, _code_healed = (body, 0)'),
    # ...but only the autolinker's own shape, so a markdown example in a code span lives.
    ('autolink: healing code spans spares a real markdown example',
     r'_CODE_LINK_RE = re.compile(r"\[([^\]\n]*)\]\(((?:\.{1,2}/)*(?:[\w.\-]+/)*[\w.\-]+\.md)\)")',
     r'_CODE_LINK_RE = re.compile(r"\[([^\]\n]*)\]\(([^)]*)\)")'),
    # The wiki page editor had NO test at all until a user hit a JSON.parse error in it.
    # Three paragraphs of normally-indented HTML produced ELEVEN blank-looking lines:
    # handle_data appends the whitespace BETWEEN tags, which \n{3,} cannot match.
    ('fetch: whitespace-only lines are emptied before blank runs are collapsed',
     '        text = re.sub(r"[ \\t]+(?=\\n)|[ \\t]+$", "", "".join(parser.parts))',
     '        text = "".join(parser.parts)',
     'tools/serve.py'),
    ('reader: the view tidies text already on disk',
     '    body = _tidy_for_reading(body)',
     '    body = (body or "").strip()',
     'tools/serve.py'),
    ('reader: indented content is left alone, because it is a code block',
     '    body = re.sub(r"[ \\t]+(?=\\n)|[ \\t]+$", "", body or "")',
     '    body = "\\n".join(l.strip() for l in (body or "").splitlines())',
     'tools/serve.py'),
    # Search greps the whole wiki, so a slow one was indistinguishable from a broken one:
    # the popup kept showing the PREVIOUS query's results while the new one ran.
    ('search: a slow search says it is working',
     "      box.innerHTML = '<div class=\"search-busy\"><div class=\"search-spinner\"></div>'",
     "      box.innerHTML = '<div class=\"search-busy\">'",
     'tools/templates/wiki.html'),
    ('search: a failed search says so instead of leaving stale results',
     '      box.innerHTML = `<div class="search-error">Search failed: ${searchEsc(e.message)}</div>`;',
     '      box.innerHTML = ``;',
     'tools/templates/wiki.html'),
    ('search: the error text is escaped',
     '    .replace(/&/g, \'&amp;\').replace(/</g, \'&lt;\').replace(/>/g, \'&gt;\')\n    .replace(/"/g, \'&quot;\');',
     '    ;',
     'tools/templates/wiki.html'),
    # A gigabyte of nginx access log filled the disk, and this code wrote it: /chat/status
    # was polled every 2s on every page with no visibility check.
    ('poll: the queue poller backs off when nothing is running',
     "        schedule(busy ? BUSY_MS : IDLE_MS);",
     "        schedule(BUSY_MS);",
     'tools/templates/base.html'),
    ('poll: the queue poller skips a hidden tab',
     "      if (document.hidden) { schedule(_every); return; }   // nobody is looking",
     "      if (false) { schedule(_every); return; }",
     'tools/templates/base.html'),
    ('poll: the inbox poller backs off when nothing is running',
     "    schedule(anythingRunning() ? BUSY_MS : IDLE_MS);",
     "    schedule(BUSY_MS);",
     'tools/templates/inbox.html'),
    # Three long articles failed while short notes went through; the number that tests
    # that correlation was shown to nobody.
    ('client: a failure reports how big the request was',
     "      const _size = _sent ? ' The request was ' + _sent.toLocaleString() + ' bytes.' : '';",
     "      const _size = '';",
     'tools/templates/base.html'),
    # The on-screen message claimed "the server log has the traceback" for any 5xx,
    # including a proxy's HTML page, which means the app never ran. A user looked where it
    # said and found nothing.
    ('client: an HTML 5xx is not blamed on the application',
     "      if (/^\\s*<(!doctype|html)/i.test(body)) {",
     "      if (false) {",
     'tools/templates/base.html'),
    # Werkzeug's access line is what makes an empty log mean "never arrived".
    ('serve: werkzeug logs to the application log file',
     '    wz.addHandler(fh)',
     '    pass',
     'tools/serve.py'),
    # Nine test stories were written into the repo's real raw/ and committed. The suite
    # was green: nothing checked either the write or the commit.
    ('harness: a write to the real raw/ or wiki/ fails the test that made it',
     '            if after != before:',
     '            if False:',
     'tests/harness.py'),
    # The browser route failed for one user on one article and there was no other way in.
    ('add_story: input is decoded tolerantly, like every other capture path',
     '    content = data.decode("utf-8", errors="replace").strip()',
     '    content = data.decode("utf-8").strip()',
     'tools/add_story.py'),
    ('add_story: an existing file is refused, never overwritten',
     '    if dest.exists():',
     '    if False:',
     'tools/add_story.py'),
    ('add_story: a reading-time line is not mistaken for the headline',
     "        if re.match(r\"^[\\d\\s–—-]+\\s*(min|minute|hour)\", line, re.I):",
     '        if False:',
     'tools/add_story.py'),
    # Found by another session, fuzzing /inbox/add: pasted text carrying a lone UTF-16
    # surrogate (half an emoji from a truncated copy) made the utf-8 write raise, and the
    # 500 took the whole story with it. JSON permits a lone surrogate; utf-8 does not.
    # Guards shipped without mutations, so these are the proof the tests hold them.
    ('atomic_write: a lone surrogate does not lose the write',
     '        with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as f:',
     '        with os.fdopen(fd, "w", encoding="utf-8") as f:'),
    ('inbox_add: an overlong filename is trimmed to what the filesystem allows',
     '        name = stem[:100] + ("." + ext if ext else "")',
     '        name = stem + ("." + ext if ext else "")',
     'tools/serve.py'),
    ('inbox_add: a non-string body is coerced rather than crashing',
     '    content = str(data.get("content") or "").strip()',
     '    content = (data.get("content") or "").strip()',
     'tools/serve.py'),
    # A background fetch landing after the user pasted their own text replaced it.
    ('fetch_and_patch: a page edited while fetching keeps the edit',
     '            if expect_body is not None and body.strip() != expect_body.strip():',
     '            if False:',
     'tools/serve.py'),
    ('inbox_add: punctuation-only content is not saved as a dotfile',
     "        name = f\"{slug or 'note-' + _stamp}.txt\"",
     '        name = f"{slug}.txt"',
     'tools/serve.py'),
    ('inbox_add: a pasted note goes through _atomic_write',
     '    _atomic_write(dest, content)\n    return {"ok": True, "filename": dest.name}',
     '    dest.write_text(content, encoding="utf-8")\n    return {"ok": True, "filename": dest.name}',
     'tools/serve.py'),
    ('fetch_and_patch: the title it adopts is escaped',
     '                    lines.append(f"{k}: {fm_quote(str(v))}" if k == "title"',
     '                    lines.append(f\'{k}: "{v}"\' if k == "title"',
     'tools/serve.py'),
    ('wiki_save: the write goes through _atomic_write',
     '    begin_write_scope()   # this edit gets its own history revision, even on a reused thread\n    with write_reason("user edit"):',
     '    begin_write_scope()\n    with write_reason("ingest"):',
     'tools/serve.py'),
    ('wiki_save: a missing page is refused',
     '    if not p.exists():\n        return {"error": "Page not found"}, 404',
     '    if False:\n        return {"error": "Page not found"}, 404',
     'tools/serve.py'),
    ('wiki_save: the saved page is autolinked',
     '        log.info("wiki_save %s: %s", rel, _autolink({"path": f"wiki/{rel}"}))',
     '        log.info("wiki_save %s", rel)',
     'tools/serve.py'),
    ('client: a 5xx is not reported as a sign-out',
     "      if (/^\\s*<(!doctype|html)/i.test(body) || resp.status >= 500) {\n        if (resp.status >= 500) {",
     "      if (/^\\s*<(!doctype|html)/i.test(body) || resp.status >= 500) {\n        if (false) {",
     'tools/templates/base.html'),
    # raw/ writes bypass _atomic_write, so a root-owned file surfaces as a bare 500 with
    # the cause only in the traceback.
    ('inbox_edit: a write that fails names the file and the reason',
     '        return _inbox_edit_write(p, content)\n    except OSError as e:',
     '        return _inbox_edit_write(p, content)\n    except _NeverRaised as e:',
     'tools/serve.py'),
    ('serve: an unhandled crash answers a fetch in JSON',
     '    log.exception("unhandled error serving %s %s", request.method, request.path)',
     '    log.debug("x")',
     'tools/serve.py'),
    ('serve: an HTTPException is not reported as a crash',
     '    if isinstance(e, HTTPException):\n        return e',
     '    if False:\n        return e',
     'tools/serve.py'),
    ('serve: a promoted .url title is escaped, not interpolated',
     "        fm = f'---\\ntitle: {fm_quote(title)}\\n'",
     "        fm = f'---\\ntitle: \"{title}\"\\n'",
     'tools/serve.py'),
    ('route: the handed-out route is recorded',
     '    _ctx()._routes_given[rel] = (key, size)',
     '    pass'),
    ('route: a read that ignores the route is logged',
     '    log.warning("route not taken: %s is %d chars, routed to %s, but %s was called",',
     '    log.debug("x",'),
    ('route: following the route logs nothing',
     '    if want == tool:\n        return',
     '    if False:\n        return'),
    ('route: the legend quotes the phrase it explains',
     "    f\"  * '{_route_head(_ROUTE_WHOLE)}' — call read_file on it FIRST.",
     "    f\"  * 'short' — call read_file on it FIRST."),
    # LOBOTOMY.md spells out the same three phrases, so the model reads the route in the
    # schema instead of discovering it from a refusal. Changing one here must not silently
    # leave the schema describing something else.
    ('route: the schema is kept in step with the phrases',
     '    _ROUTE_WHOLE:   "reads whole — read_file first, then update_section",',
     '    _ROUTE_WHOLE:   "read it first",'),
    ('route: a page already read in full is not read again',
     '    if size and _ctx()._session_read_coverage.get(rel, 0) >= size:',
     '    if False:'),
    # The hover card's blurb comes from the index's own helper, so a page's index
    # entry and its card cannot say different things.
    ('preview: the blurb is the index blurb',
     '        "snippet":  first_desc_line(text),',
     '        "snippet":  "",',
     'tools/serve.py'),
    # A source page's lookup lists are sorted; Claims and Timeline are NOT — the
    # first is prose in bullet form, the second is chronological.
    ('sort: only Entities and Concepts are sorted',
     '        if not m or _norm_heading(m.group(1)) not in _LOOKUP_LIST_SECTIONS:',
     '        if not m:'),
    ('sort: heal_pages orders lists already on disk',
     '                _sorted = sort_lookup_lists(new)',
     '                _sorted = new'),
    # The once-per-section budget belongs to the PAGE, not the name. Key it by
    # title and every alias buys the same page another link in the same section.
    ('autolink: one mention per PAGE, not per name',
     '            _seen_key[0] = (_page_key, _sec_of[i])',
     '            _seen_key[0] = (title, _sec_of[i])'),
    # done()'s refusal is where the agent re-plans, so its routing must know what
    # the session already read — or it sends it to re-fetch a page it holds.
    ('done: the refusal routes each page through the shared answer',
     '                _rows = [f"  - {name} → wiki/{rel} [{_write_route(rel)[1]}]"',
     '                _rows = [f"  - {name} → wiki/{rel} [read_section it]"'),
    # A lookup taken before the source page exists is over a guessed list: one log
    # showed 7 names looked up against 10 committed. This hands back a lookup over
    # the committed list, in the message that creates it.
    ('create_file: a source page hands back its worklist lookup',
     '            _next = ("\\n\\nThe names this source page lists are your worklist — done() "',
     '            _next = ""'),
    # A fetch-failed article has exactly the shape the completeness guards look
    # for, and no move satisfies them — the deadlock cost two rounds.
    ('done: a fetch-failed raw file has nothing to ingest',
     '    if ctx._current_inbox_path and not _fetch_failed:',
     '    if ctx._current_inbox_path:'),
    ('done: the refusal describes what the session actually did',
     '                    "created no pages at all — no source page, no entity or concept pages")',
     '                    "created a source page but no entity or concept pages")'),
    # The 429 log line keeps the quotaId and drops the 1,200-char body. The quotaId is
    # the body's one unique contribution, so losing it makes the change a regression.
    ('429 log: the line still names the quota',
     '                        _brief, _quota_id(raw_body) or "quota unnamed", len(data) // 1024)',
     '                        _brief, "", len(data) // 1024)'),
    # All violations, not the first: a per-minute one listed ahead of a per-day one must
    # still take the coarse retry interval.
    ('429: every quotaId in the body is read, not just the first',
     '    for q in _QUOTA_ID_RE.findall(raw_body or ""):',
     '    for q in _QUOTA_ID_RE.findall(raw_body or "")[:1]:'),
    # A provider that returns the error object hands back the whole multi-line quota
    # blurb; uncapped, the "one short line" is the dump again.
    ('429 log: a long provider message is capped to one line',
     '            _brief = msg.strip().splitlines()[0][:160] if msg else "rate limited"',
     '            _brief = msg'),
    # One ingest per article. Two Wikify clicks used to queue two full agent turns over
    # the same raw file; at max_rpm: 1 that is a second ~40-minute run spending a per-day
    # quota on work the first run already did.
    ('wikify: a keyed job already in flight is not queued again',
     '            if key is not None and key in self._keys:\n                return self._keys[key], True',
     '            if False:\n                return self._keys[key], True',
     'tools/job_queue.py'),
    # The release matters more than the guard: leak a key and that article can never be
    # wikified again, which is worse than the duplicate.
    ('wikify: a finished job releases its key',
     '                    self._release_key(job_id)\n                self._cleanup()',
     '                    pass\n                self._cleanup()',
     'tools/job_queue.py'),
    # A drained job never runs, so the worker's release never fires for it.
    ('wikify: drain releases the keys it drops',
     '            # A drained job never runs, so the worker\'s release never fires for it.\n            self._release_key(job_id)',
     '            pass',
     'tools/job_queue.py'),
    # process-all's own on_done chains to the next item, and a duplicate submit means that
    # on_done belongs to someone else's job and will never run for us.
    ('wikify: process-all advances past an item already in flight',
     '                     filename, job_id)\n            _submit_item(items, index + 1)\n            return',
     '                     filename, job_id)',
     'tools/serve.py'),
    ('wikify: the browser does not fire a second click through',
     '  if (window.wikifying.has(name)) return;\n  window.wikifying.add(name);',
     '  window.wikifying.add(name);',
     'tools/templates/inbox.html'),
    # /inbox/add had no duplicate check, and the filename is derived deterministically —
    # so a second add resolved to the same path and silently OVERWROTE it. Re-adding a URL
    # wiped pasted text and reset wikified:true; two different articles from one site
    # collapsed into one file, destroying the first.
    ('inbox/add: a URL already in the reading list is not re-captured',
     '        _dup = _find_existing_capture(url=url)\n        if _dup:',
     '        _dup = _find_existing_capture(url=url)\n        if False:',
     'tools/serve.py'),
    ('inbox/add: a capture that already holds text is never rewritten',
     '            if _body.strip() or _wikified:',
     '            if False:',
     'tools/serve.py'),
    ('inbox/add: identical pasted text is not saved twice',
     '    _dup = _find_existing_capture(content=content)\n    if _dup:',
     '    _dup = _find_existing_capture(content=content)\n    if False:',
     'tools/serve.py'),
    # Two different articles whose first 60 characters are the same site chrome.
    ('inbox/add: a colliding slug gets its own filename',
     '    name = _unique_raw_name(name)\n    dest = RAW_DIR / name',
     '    dest = RAW_DIR / name',
     'tools/serve.py'),
    ('inbox/add: a colliding URL capture gets its own filename',
     '        base_name = _unique_raw_name(name or f"{slug}.md")',
     '        base_name = name or f"{slug}.md"',
     'tools/serve.py'),
    # Over-normalizing merges two different articles, which loses the second as surely
    # as the overwrite did — so only known-meaningless parameters come off.
    ('inbox/add: only tracking parameters are ignored when comparing URLs',
     '            if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")]',
     '            if False]',
     'tools/serve.py'),
    ('inbox/add: host, scheme and trailing slash do not make a new story',
     '    path = p.path.rstrip("/")\n    return f"{host}{path}"',
     '    path = p.path\n    return f"{p.scheme}{host}{path}"',
     'tools/serve.py'),
    # A summary section grown by one dated news sentence per ingest. The size guard is the
    # mirror image — it catches a section that SHRINKS — and this failure is the one its
    # own comment already names, "the pile the wiki is not supposed to become".
    ('summary: a dated sentence bolted onto Overview is refused',
     '    _dated = _accreted_dated_sentences(section, old_text, new_text)\n    if _dated:',
     '    _dated = _accreted_dated_sentences(section, old_text, new_text)\n    if False:'),
    # Guard one write path and the model simply reaches the section by the other.
    ('summary: append_section is guarded too',
     '        if _dated:\n            log.warning("append_section: refused',
     '        if False:\n            log.warning("append_section: refused'),
    # Both conditions are needed: the old text surviving verbatim is the accretion
    # signature, and a genuine rewrite must pass.
    ('summary: only an edit that preserves the old text verbatim is accretion',
     '    if not old_norm or old_norm not in new_norm or len(new_norm) <= len(old_norm):',
     '    if not old_norm:'),
    # Only Overview/Definition. A dated sentence in Background is where it belongs.
    ('summary: only summary sections are policed',
     '    if _norm_heading(section) not in _SUMMARY_SECTIONS:\n        return []',
     '    if False:\n        return []'),
    # The page on disk is autolinked and the agent sends plain text, so a byte comparison
    # reports "changed" for a sentence nobody touched.
    ('summary: links are flattened before the versions are compared',
     '    return " ".join(_MD_LINK_RE.sub(r"\\1", text).split())',
     '    return text'),
    # "U.S. Senate" split in two, and the fragment then opened with a capital — so no
    # heuristic about the following text could repair it.
    ('summary: an initialism is not a sentence boundary',
     '_SENTENCE_SPLIT_RE = re.compile(r"(?<![A-Z]\\.)(?<=[.!?])\\s+(?=[\\"\'\\[(]?[A-Z])")',
     '_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\\s+(?=[\\"\'\\[(]?[A-Z])")'),
    # Principle 4: the refusal has to name the calls that work, not just the constraint.
    # The first live ingest resent the identical refused call twice. Four named moves were
    # not enough on their own, because nothing in the reply changed between attempts.
    ('summary: a repeated refusal escalates instead of repeating itself',
     '    if _n > 1:',
     '    if False:'),
    ('summary: the repeat count is per page and section',
     '        _key = f"{path}§{_norm_heading(section)}"',
     '        _key = "x"'),
    # Option 1 steered the agent into the date-in-heading rule: told to name a section it
    # chose '2026 Senate Campaign'. Principle 4's worst case, renaming one violation
    # into another.
    ('summary: the refusal warns that the new heading must carry no date',
     '        f"name must contain NO year and NO date',
     '        f"name is up to you. ("'),
    # Every example was a slow-burning event — outbreak, election, trial — so a single
    # day's remark read as not qualifying, and the most consequential claim in a source
    # ended up as 224 characters on a city page with no page of its own.
    ('summary: a single incident is a story in its own right too',
     '        f"remark, a raid, a resignation, a verdict, an order signed all qualify. If this "',
     '        f"outbreak, an election or a trial qualifies. If this "'),
    ('summary: the refusal gives the participant-vs-subject test',
     '        f"page is a PARTICIPANT in what happened rather than the subject of it, the event "',
     '        f"page is involved, the event "'),
    ('summary: the refusal names the moves',
     '        f"  1. It is a standing feature of the subject → append_section(path, "',
     '        f"  1. Put it somewhere else. ("'),
    # The bottom row of the history view is either the page's first version or the oldest
    # one that survived pruning, and calling both "origin unknown" lied about the first.
    ('history: an unpruned bottom row is the original, not merely the earliest kept',
     '    pruned = len(revs) >= _HISTORY_KEEP',
     '    pruned = True'),
    # Dated from THAT revision's own text, so a later edit cannot date the row.
    ('history: the original is dated from its own revision',
     '                       revs[0]["text"] if revs else current_text, re.MULTILINE)',
     '                       current_text, re.MULTILINE)'),
    # The first revision of a new page is its own autolink snapshot, so a create_file stamp
    # describes the creation. Any other tool says nothing about what came before it.
    ('history: only a create_file stamp is read back onto the original',
     '    _by_create = bool(born) and bool(revs) and revs[0]["tool"] == _TOOL_LABELS["create_file"]',
     '    _by_create = bool(born) and bool(revs)'),
    ('history: a page written once still shows its creation date',
     '        rows.append({"current": True, "id": None, "when": born, "why": "", "source": "",',
     '        rows.append({"current": True, "id": None, "when": "", "why": "", "source": "",'),
    ('history: the view tells the original from the earliest kept',
     '{%- elif r.original %}Original version{% if r.when %} — {{ r.when }}{% endif %}',
     '{%- elif r.original %}Earliest kept version',
     'tools/templates/wiki-history.html'),
    # A span of days is one of the shapes a hand-written timeline arrives in. Unrecognised,
    # it is filed as prose: kept above the list, out of order, and undeduplicated.
    ('timeline: a date span is recognised',
     '_TL_RANGE = r"(?:[ \\t]*(?:to|through|until|–|—|-)[ \\t]*(\\d{4}(?:-\\d{2}){0,2})(?![-\\d]))?"',
     '_TL_RANGE = r"()?"'),
    # Sorting, the future check and the duplicate check all key on the START, or a span
    # sorts and validates by its tail.
    ('timeline: a span sorts by its start',
     '    parts = _tl_start(date).split("-")',
     '    parts = date.split("-")'),
    ('timeline: to/through/until/- all store one shape',
     '    return f"{start} to {end}" if end and end != start else start',
     '    return start'),
    ('timeline: the END of a span is checked against today too',
     '    for _d in {_tl_start(date), date.split(" to ")[-1]}:',
     '    for _d in {_tl_start(date)}:'),
    ('timeline: a span is accepted by the tool',
     '    if not (_TL_DATE_RE.match(date) or _TL_SPAN_RE.match(date)):',
     '    if not _TL_DATE_RE.match(date):'),
    ('wikify: the browser releases on every exit path',
     '    window.wikifying.delete(name);',
     '    ;',
     'tools/templates/inbox.html'),
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
            # --failfast: the verdict below is the exit code and nothing else, so ONE red
            # test is the whole answer and running the other five hundred buys nothing.
            # Not a loosening — a CAUGHT mutation is exactly as caught, it is just found
            # sooner. A MISSED one still pays for the full suite, because proving that
            # nothing objected means running everything.
            r = subprocess.run([sys.executable, str(RUNNER), "--failfast"],
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
