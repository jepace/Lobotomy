# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Lobotomy is a personal knowledge-base server where LLMs synthesize knowledge at ingest time and write it permanently into a wiki — not retrieved at query time. Three layers: immutable raw sources (`raw/`), LLM-generated wiki pages (`wiki/`), and an operating schema (`LOBOTOMY.md`).

## Running the Server

```sh
pip install -r requirements.txt      # flask, markdown — nothing else; API calls use stdlib urllib
cp config.json.example config.json   # then fill in the active provider's api_key, admin creds
python3 tools/serve.py               # web UI at http://127.0.0.1:8080
```

Alternative CLI (no Flask needed):
```sh
python3 tools/wiki.py                      # interactive REPL
python3 tools/wiki.py "ingest raw/file.md" # one-shot command
```

**`tools/README.md` documents every tool in `tools/` with sample command lines — read it
before adding another one.** The maintenance CLIs need no LLM and no API cost: `search.py`,
`relink.py` (the catch-up sweep that adds links to pages written before their subjects
existed), `rename_page.py`, `unlink_headings.py`, `repair_links.py`,
`repair_frontmatter.py`, `rebuild_sources.py`, `section_inventory.py`,
`promote_openers.py`, `rename_section.py`, `merge_page.py`, `find_duplicate_pages.py`, `find_duplicate_sections.py`,
`undo_pass.py` (put back everything one named pass wrote, skipping pages something
wrote after it), and `lint.sh`.

All of them go through `agent._atomic_write`, so their edits are recorded in page history
and keep the tree's ownership — an existing file keeps its own owner and mode, a new file
takes its parent's, and new directories go through `_mkdir_inheriting`. That is what makes
them safe to run as root beside a server running as another user, with no `su` ceremony;
**anything new that writes to the wiki must do the same.** `repair_links.py` did not for a
long time, and its repairs were neither revertable nor safe to run as root. Never call
`mkdir(parents=True)` directly under `wiki/`: a level created by root is root-owned inside
a tree the server has to keep writing, and it fails silently in the history path, which
deliberately never raises.

## Architecture

### Core modules

**`tools/agent.py`** — the heart of the system. All AI tool implementations (`_read_file`,
`_read_section`, `_update_file`, `_update_section`, `_append_section`, `_replace_text`,
`_add_timeline_entry`, `_create_file`, `_lookup_titles`, `_search_wiki`, `_search_raw`,
`_autolink`, `_fetch_url`, `_done`, `_rebuild_index`), the agentic loop
(`stream_agent_turn`, `run_agent_turn`), the LLM provider abstraction, page version
history, and the whole-wiki maintenance passes (`heal_pages`, `relink_all`,
`unlink_headings`). Both `serve.py` and `wiki.py` are thin layers over it.

Tools are registered in two places that must stay in sync: `TOOL_FNS` (name → callable)
and `TOOL_DEFS` (the JSON schema sent to the model). `system_prompt()` appends a
quick-reference table that also needs the new row.

**`tools/serve.py`** — Flask web server. Routes for: `/chat` (streaming AI), `/wiki/*` (rendered markdown), `/inbox` (read-it-later), auth, and settings. Imports `agent.py` for AI functionality and `job_queue.py` for background jobs.

**`tools/wiki.py`** — CLI wrapper around the same agent tools. An interactive REPL or one-shot runner; no Flask dependency.

**`tools/config.py`** — reads `config.json`. Use `cfg_get(section, key, default)` throughout. Config is never hardcoded.

**`tools/job_queue.py`** — background job queue used by `serve.py` for async inbox processing.

### Module state — read this before writing a test or a maintenance pass

`agent.py` keeps mutable state in three places, and code that ignores any of them will
appear to work while doing nothing:

- **Module globals** `REPO_ROOT`, `WIKI_DIR`, `RAW_DIR`, `HISTORY_DIR`. Never capture these
  as default argument values — a `def f(root=WIKI_DIR)` default binds at import, so every
  caller that omits it scans whatever `WIKI_DIR` pointed at *then*. `wiki_pages()` had this
  bug and silently scanned the real wiki under test. Resolve at call time.
- **A thread-local session context** (`_ctx()`, reset by `init_session()`): which pages
  this session has read, how much of each, which sections, which are stale, refusal
  counters. Guards depend on it, and it persists across calls on the same thread.
- **Autolinker caches**: `_title_map_cache`, `_title_regex_cache`, `_title_tokens_cache`,
  `_no_autolink_titles`. Keyed by title, not by wiki.

### The autolinker (common bug surface)

**`tools/agent.py:_autolink()`** — **the only way wiki links are ever created.** The LLM
never writes markdown links; `LOBOTOMY.md` tells it not to, and `_strip_broken_wiki_links`
removes what it writes anyway. It runs in three places: `_autolink_now(p)` after every
write tool, `_post_process_session()` over every touched page at `done()`, and
`relink.py`/`relink_all()` as the whole-wiki catch-up sweep.

Uses a combined regex where group 1 protects existing links and group 2 matches titles bare or with a sub-span already linked (via `_title_alts()`). When a partial match is found (e.g. `CASA of [Monterey County](url)`), the inner link is stripped and the whole phrase is replaced with the longer-title link.

**A title links once per section's prose, and on every list or table row.** This is
Wikipedia's MOS:REPEATLINK, which links once per article but relinks in infoboxes, tables,
captions, footnotes and lists — a reader arrives at those out of order. Two departures from
the letter of it, both forced by the shape of these pages:

- **Per section, not per page.** `donald-trump.md` is 136KB across twenty-odd sections; one
  link at the top leaves the rest with no navigation. A section here is about what an
  article is there. `_seen` resets on every heading line.
- **A list row never spends the section's prose mention** (`is_listish`). A source page's
  `## Entities` / `## Concepts` and a `## Timeline` are lookup tables, and a row whose link
  was spent in a paragraph above it is a dead row.

The half that is easy to miss: the rule must also **unlink** repeats already on disk, or it
applies only to newly written text while ~9,000 pages keep every repeat. Unlinking is
scoped to links the autolinker itself would have written — same target page *and* display
text whose `\w+` tokens are exactly the title's — so a hand-written
`[the disease](../concepts/measles.md)` alias and every external link survive.

Two consequences worth holding on to. **Substitutions now remove link syntax as well as
adding it**, which breaks the old invariant that nothing could expose text for a later
title to match; the result stays deterministic only because `_build_title_map()` is sorted,
so that ordering is now load-bearing for output, not just for convergence. And **group 1
matches every existing link on the line for every candidate title**, so anything expensive
in that branch runs a million times on a large page: parsing the link with a regex there
cost 19× (1.1s → 20.9s on a 63KB page with 1,200 links) until a plain `basename not in`
substring test was put in front of it.

The critical invariant: **never match inside existing markdown links — or inside a bare
URL**. Group 1 of the combined regex takes priority at each position, consuming both before
group 2 can fire. Heading lines are skipped entirely (`is_heading`), so a link inside a
heading was written by hand, not by this.

Bare URLs (`_BARE_URL`) were missing from group 1 for a long time, and the golden baseline
had the result recorded as correct: `https://example.com/meta/page` became
`https://example.com/[meta](../entities/meta.md)/page`. Two failures at once — the URL no
longer resolves, and the link points at a page the sentence was not about, because "meta"
there is a path segment. Prevention alone was not enough to fix it: group 1 protects
complete links, so a link already inside a URL is shielded by the very mechanism meant to
stop it. `_MANGLED_URL_RE` therefore unwraps them at the top of `_autolink`, before
anything else reads the text, and the wiki heals on the next write or relink with no
separate pass.

**A relative path is the same hazard without a scheme, and far commoner** — every page's
own links look like one, so prose that names a file gets mangled. `_BARE_PATH` covers
`../sources/foo.md`, `sources/foo.md` and a bare `foo.md`. Observed:
`Adapted from ../sources/backgammon-wikipedia.md` became
`../sources/[backgammon](…)-[wikipedia](…).md` — which **renders as an ordinary sentence
with two plausible links**, so it reads fine on the page and only `/wiki/lint` ever
notices. Do not conclude from a clean-looking page that link syntax is intact.

`repair_links._repair_nested` used to make this permanent. When it could recover no target
from inside the mangled URL it truncated at the first `[`, turning
`[Backgammon](../sources/[backgammon].md)` into `[Backgammon](../sources)` — a link to a
**directory**, which resolves, so lint fell silent while the real target was gone for good.
It now leaves what it cannot repair alone: visible damage that keeps getting reported beats
an invented link that hides it, and the dangling pass or `_MANGLED_URL_RE` handles the
shape properly on the next run.

Performance: the title+alias map and the per-title compiled regexes are cached in memory (`_title_map_cache`, `_title_regex_cache`). `_atomic_write` invalidates them only when a write actually changes `title`/`aliases`/`no_autolink`; an mtime-scan backstop in `_build_title_map()` catches writes that bypass `_atomic_write` (other processes, manual edits), with per-file vetted mtimes so the backstop doesn't false-fire on the autolinker's own body-only writes. This invalidation logic has been a repeat bug source — change it with care and test all of: safe write keeps cache, title change invalidates, bypass write is detected, safe write doesn't mask a concurrent bypass.

A per-title token prefilter (`_title_tokens_cache`) skips any title whose words are not all
present in the page — at ~9,000 titles this is what keeps a single autolink under a second.

`_build_title_map()` must be **deterministic**. It was not once (unsorted glob, no sort
tiebreak) and the resulting map order changed the output, so `relink` never converged: two
consecutive whole-wiki runs both reported hundreds of changes.

`merge_page()` has the same problem in miniature and solves it locally: `_claims`
compares the two bodies line by line, so two pages for one hospital each describing it by
their own title looked like two different sentences, and the merge was refused — blocking
the cleanup the naming mismatch had made necessary. Running the merge *is* the judgement
that these are one subject, so within that call every name either page has (titles and
aliases, longest first, word-bounded) folds to one placeholder before comparing. Note the
guard that is *not* there: a minimum name length looks like protection against folding
"NY" inside "company" and is not one, because the fold runs over both pages and mangles
them identically — `\b` is the real guard, and no mutation could catch the length cutoff
being removed.

**`_resolve_page()` is the one place that decides create-vs-update**, and `lookup_titles`,
`done()`'s listed-name check and the duplicate guards all share it. That sharing is a
feature — they cannot contradict each other — and it is also why a gap in it produces a
duplicate page rather than a stray round: every caller is wrong the same way at once.
Observed: an ingest listed "New York University Langone Health", created the page as
"NYU Langone Health", and then `done()` demanded the page, `lookup_titles` confirmed the
demand ("this answer is exact… do not double-check it"), and the model wrote a second page
for the same hospital. `_initialism_match()` closes that: it **declares** a match, unlike
`_norm_title_key()` which only suggests one, because every word outside the initialism has
to match exactly and both names must be consumed completely. A run of at least two
consecutive words is required, which keeps it off ordinary abbreviation — "MS Word" does
not match "Microsoft Word". A first-letter prefilter keeps the extra scan free: whether the
first words are the same word or one initialises the other, they share a first letter.

Pages can carry an `aliases:` frontmatter list (e.g. `aliases: ["gonzales", "uc davis"]`) for common short names that the autolinker should also match. The LLM is not instructed to set this field — it's a manual human override for when the formal page title differs from how the subject is typically referenced in prose. `no_autolink: true` excludes a page from *linking* but deliberately keeps it in the title map, because hiding it from `lookup_titles` made the agent create duplicate pages.

### Write-path guards

The densest logic in the file, and the reason the wiki survives an LLM editing it. Five
tools write pages — `create_file`, `update_file`, `update_section`, `append_section`,
`replace_text` — and each runs the same structural checks through shared helpers
(`_heading_dupes`, `_bad_headings`, `_absorb_date_qualifiers`) so they cannot disagree
about what is legal.

Three principles, each learned expensively:

1. **Absorb what is unambiguous; refuse only what needs a decision.** A refusal costs a
   full round trip (observed: 44 messages, 270KB, ~60s) and the model has to be able to act
   on it. An entity page opening with `## Definition` is one heading with the wrong name —
   renamed silently. `## Fiscal Challenges (2026)` is a standing heading with a date bolted
   on — the date is dropped. `## 2026 Outbreak` names the event itself, so there is nothing
   to strip and it is refused, pointing at `add_timeline_entry`.
2. **On update paths, check the delta, not the page.** Only violations the edit
   *introduces* are refused. An earlier duplicate-heading guard checked the whole resulting
   page and deadlocked 176 real pages: the only edits that could have fixed them were the
   ones it refused.
3. **A refusal names every problem, not the first one found.** `create_file` collects
   them and returns one numbered list. A call can be wrong in several independent ways at
   once, and each refusal costs a full round: an observed ingest spent three refusals and
   three minutes on one page, told about its missing `## Overview`, then — after fixing
   that — told it needed a source page first, which had been true from the start and said
   nothing about the body. Only the checks that make the rest meaningless return early:
   no path, outside `wiki/`, bad filename, page already exists (that reply carries the
   content), missing title or type.
4. **A refusal must name a move that works — the call, not the constraint.** The
   recurring failure here is a guard that is locally correct about its own question but
   answers a question the caller cannot act on — `lookup_titles` right that no title
   matched, `create_file` right that the path existed. Worst case: renaming one violation
   into another, so the refusal quotes a heading the model never wrote.
   The subtler version costs data rather than rounds. `update_section`'s duplicate-heading
   refusal used to end "Send the section's body only": true, and silent about where the
   material for that other section should go. An observed ingest resent the identical call
   twice, then complied by deleting the heading and leaving a second section's content
   inside `## Overview`. A guard that names no destination does not prevent the damage, it
   redirects it somewhere quieter. That refusal now names the call — `update_section(path,
   section='<the other one>', content=…)`, or `append_section` to create one — and says
   the text is not wasted. Where a refusal names a call, a test should follow its
   instructions and assert they succeed; that is the only version of the assertion that
   proves anything.

Also enforced: search limited to 2 per term per session; `done()` refused if an ingest
wrote no entity/concept pages or never established a source page; `update_file` refused
until the session has read the page's full content, and `update_section` until it has read
that section; all-lowercase titles refused; `wiki/log.md` and `wiki/index.md` refused;
`wiki/sources/` pages immutable after creation.

**Four refusals hand the needed content back in the same response** — `update_section`
(unread section), `update_file` (unread page, and stale page), `create_file` (page
exists) — and handing it back is only half the job. Each must also say
`— do NOT call read_file first` (or `read_section`) and wrap the payload in
`<file path="…">` / `<section path="…" name="…">` delimiters. `update_section` was the one
missing both, and an observed ingest was handed the section text and called `read_section`
for it anyway: a full round, plus one of the inter-round pacing windows, to fetch what it
was already holding. A refusal that leaves the wasteful route open is one the model will
take. `tests/test_handback_refusals.py` asserts the contract across all four at once so
they cannot drift apart again.

### Reading a large page

`_read_file` on a wiki page over `_WIKI_READ_LIMIT` (20,000 chars) returns an **outline** —
frontmatter, every section name, each section's size and its opening `_OUTLINE_PREVIEW`
chars — not the text. It used to return the first 20,000 chars and then instruct the model
not to work from them, so the ingest paid for that chunk on every subsequent round.

`offset` defaults to **-1, not 0**: "no offset given" and "offset 0" are different
requests. Without an offset you get the outline; with an explicit `offset=0` you get a
paged chunk. That distinction is load-bearing — the Regenerate Workflow rewrites a whole
page with `update_file`, which requires full read coverage, which requires paging to be
reachable.

### Reorganizing a page

`update_section` refuses a rewrite that cuts a section by more than 40% (`_old_cmp >= 800
and _new_cmp < _old_cmp * 0.6`), because that is what an ingest looks like when it runs
out of output budget and silently condenses a page instead of failing. But consolidating a
page *is* shrinkage — moving a duplicated point out of one section makes it smaller — so
`allow_shrink=true` is the deliberate opt-out, and both refusals name it. It exempts that
one check and nothing else: read-before-write and the heading rules still apply.

Without it there was no route at all on a large page: the section tools could not shrink,
and `update_file` is steered away above half the output budget. `LOBOTOMY.md` section 6b
is the workflow — read every section involved, plan the whole move, **write the
destination first and confirm it**, then shrink the source.

### Unfolding events and timelines

An event that arrives across several sources (an outbreak, an election, a trial) gets its
own entity page, with `## Overview` as the current state (replaced each visit) and
`## Timeline` as the record. `add_timeline_entry(path, date, text)` **inserts in
chronological position rather than appending**, because sources are not ingested in the
order events happened. It accepts partial dates (`2026-03`, `2026`), refuses future dates
and duplicates, and re-sorts the whole section on every insert, so a timeline that is
already out of order heals on the next write.

**Reading a timeline bullet and writing one are different jobs.** `_render_timeline`
emits exactly one shape; `_TL_BULLET_RE` accepts every shape a hand-written timeline
arrives in — bold date or bare, em dash, en dash, hyphen or colon — because anything it
does not match is filed as prose, and an unrecognised entry goes not merely unsorted but
*undeduplicated*. That is what produced a page carrying eight bullets for four events:
`create_file` writes a whole page in one call, so a page about an unfolding event is born
with a hand-written timeline before the tool has anything to add to; the tool did not
recognise those bullets, kept them above its own list, and wrote every one of the same
facts again underneath. `LOBOTOMY.md` does say the Timeline is never maintained by hand,
but a rule the model has no way to obey at `create_file` time is not worth a refusal —
principle 1 — so `normalize_timeline()` absorbs the hand-written form on every write path
and in `heal_pages`. The date is matched with `(?![-\d])` so the engine cannot backtrack
`2026-09-12` to `2026-09` and read `-12` as the separator.

Duplicates are decided by `_tl_dedupe`, shared by the tool and the normalizer so they
cannot disagree about what counts as a restatement. Exact-text comparison was not enough:
two sources wording one event differently produced two entries, and one page carried the
same death three times. The rule is deliberately narrow — same date, and every meaningful
word of one entry already present in the other — and the *longer* wording wins, in the
shorter one's position. Two entries that each contribute a word the other lacks are two
events on one day and both stay. Because a fuller wording *replaces* what it absorbs, the
list can stay the same length while the page changes, so "already there" is decided by
comparing what the section will say, never by counting entries.

`normalize_timeline` must be idempotent: `heal_pages` runs at startup and after every
ingest, and a normalizer that rewrote on every pass would fill page history with empty
revisions forever. A Timeline that is the page's last section is the case that breaks
this — watch the trailing blank line.

### Page version history

`_snapshot_version()` runs inside `_atomic_write` — the single chokepoint every wiki write
passes through — and copies the pre-write content to `wiki/.history/<relpath>/<microsecond
timestamp>.md` before the overwrite. Full copies, not diffs; stdlib only, no git. Capped at
`_HISTORY_KEEP` (50) revisions per page. It never raises: failing to record history must
not fail the write it protects.

Filenames use microsecond timestamps specifically so lexical sort is chronological — both
the history view and the pruning depend on that. An earlier collision-counter scheme was
wrong: after pruning removed the low numbers, the next write refilled the gap and a new
revision sorted as old.

Each revision is stamped with **why** it was written: `<ts>__ingest.md`, `__user-edit`,
`__relink`, `__revert`, `__heal`, `__merge`. The reason goes after the fixed-width
timestamp so lexical order stays chronological, which the history view and the pruning
both depend on. `write_reason("…")` scopes it to a block and restores the previous value —
never `set_write_reason` bare inside a pass, because `heal_pages` runs at startup and after
every ingest and a leaked reason would mislabel every write after it. `init_session()`
clears it, so a reason cannot survive into the next job. Revisions written before this
have no suffix and render without a label.

`page_history(p)` builds what the view shows and lives in `agent.py` rather than
`serve.py` so it is reachable without flask. **A row is a version, not a change** — which
is what the Compare button already assumes, since it diffs that version's content against
the current page. The storage makes this easy to get backwards: revision file `R_i` holds
the content as it was BEFORE the write at `T_i`, so `R_i`'s stamped reason describes the
write that **destroyed** that content, and `R_i`'s content is what the write at `T_{i-1}`
produced. Attaching `R_i`'s reason to `R_i`'s own row labels a version with the cause of
its own deletion and puts the newest write's label one row too low. Every version
therefore takes its timestamp and reason from the revision **below** it, and the newest
write's reason lands on the current page. The oldest row is the earliest content still
kept; whatever produced it has been pruned, so it carries no timestamp, reason or counts
rather than a guessed one.

**`wiki/.history/` is inside `wiki/`, and every tool that walks the tree must exclude it.**
A revision is a record, never a link target, a page, or a search hit. Two ways this bites,
both found in `repair_links.py`: `rglob` matches **directories**, and a page deleted from
the wiki leaves `wiki/.history/entities/united.md/` behind — a directory carrying the
page's name — so a filename search "found" the deleted page and repaired every dead link
to point inside the history store. And at ~9,000 pages × 50 revisions the store holds a
few hundred thousand files, so a per-item walk over it turns a pass into a hang: a dry run
that takes 1.7s with `.history` excluded ran over two minutes without, on a tree a quarter
the size. `_wiki_pages()` in that file is the pattern — filter on `relative_to(HISTORY_DIR)`,
and match files, not paths.

Served by `/wiki/<path>/history` (list), `/wiki/<path>/history/<rev>` (unified diff via
stdlib `difflib`), and `/api/wiki/<path>/revert/<rev>`. Revert goes through `_atomic_write`,
so it snapshots the current content first and is itself undoable.

### Wiki page lifecycle

1. `create_file` / `update_file` / the section tools → write frontmatter + body; `sources:` is merged from disk plus the session's source page (never trusted from the LLM); `_inject_sources_section` renders the `## Sources` section; `_autolink_now` links the page
2. `done()` → `_post_process_session()` runs once: patches `sources:` on every touched page, autolinks them all, re-injects `## Sources`, rebuilds the index
3. Server lint checks run after `done()`; results visible at `/wiki/lint`

### `system_prompt()` and `LOBOTOMY.md`

`agent.py:system_prompt()` reads `LOBOTOMY.md` as the LLM's operating schema and appends a tool quick-reference table. The LLM operating instructions (ingest workflow, query workflow, page format, naming conventions, etc.) all live in `LOBOTOMY.md`, not here.

**A guard added in code needs a matching line in `LOBOTOMY.md`.** Discovering a rule by
refusal costs a round; reading it in the schema costs nothing.

## Key Conventions

- **`raw/` is immutable for the LLM** — code in `_update_file` blocks the LLM from writing outside `wiki/`. Raw source files live flat in `raw/` (no subdirectories). `serve.py` manages their lifecycle via `_mark_inbox_wikified`.
- **`wiki/log.md` is append-only** — written by `_auto_write_log_entry` at `done()`; `update_file` refuses it.
- **No `[[wikilink]]` syntax** — standard relative markdown links only.
- **`create_file` for new pages, `update_file` for existing ones** — `create_file` auto-fills `created`/`updated`; `update_file` restores system-owned fields (`created`, `raw_source`, `type`) from disk.
- **Headings are plain text.** No links in them, no dates naming the section, no heading repeating the page's own title.
- **Every page opens with `# {title}`; sections are `##`.** `ensure_h1` adds the H1 in `create_file`, restores it in `update_file` and backfills it in `heal_pages` — it is derivable from `title:`, so it is filled in rather than demanded. The file carries it because the wiki must read correctly in any markdown viewer; `render_md` and `render_md_shareable` strip it because the web templates print the title themselves. Level-1 headings are skipped by `_page_section_names` and exempted by `_bad_headings`, so the H1 is not a section and never trips the title rule — but a *section* written as `# Overview` can never be read or edited by the section tools.
- Internal wiki links use paths relative to the page's location: `../entities/foo.md` from `wiki/sources/`.
- File names: `lowercase-hyphenated-slugs.md`. Source slugs encode `{author-or-org}-{year}-{short-title}`.
- The `## Sources` section in entity/concept pages is auto-generated from frontmatter — never write it manually.

## Tests

```sh
python3 tests/run_all.py              # everything; exits non-zero on failure
python3 tests/run_all.py test_timeline  # one module
python3 tests/mutate.py               # break each guard, check a test notices
```

`run_all.py` is stdlib `unittest` over `tests/` (repo root — the suite covers the
whole project, not just `tools/`), built on a `TempWiki` harness that
rebinds all four module globals, resets the thread-local session context, clears the
autolinker caches, and asserts its own isolation. Nothing there touches the repo's real
`wiki/` or `raw/`.

**Link-rewriting passes must skip `agent._GENERATED_PAGES`** (`index.md` anywhere,
`log.md`). `merge_page`, `rename_page.py` and `repair_links.py` share that one list so they
cannot drift. Two different reasons, and the log's is the serious one: patching `index.md`
is undone by `_rebuild_index` seconds later, but **repointing a link in `log.md` rewrites
what the log says happened** — the entry recording that an ingest created
`new-york-university-langone-health.md` would come to claim it created
`nyu-langone-health.md`, which it never did. A record edited to agree with the present is
not a record; the dead link is the true statement, and lint skips `log.md` so it costs
nothing. Neither file can be reverted either, since `_snapshot_version` skips both.
**The catch:** once a pass stops repointing `index.md` it *must* rebuild it, or it leaves
the dead link the repoint used to fix. `merge_page.py` already did; `rename_page.py` had to
start.

**`mutate.py` is the one that proves the suite works.** A green run says nothing on its
own: this disables one guard at a time and requires a test to go red for each. Every
mutation must report CAUGHT. Add one whenever you add a guard — if you cannot write a
mutation the suite catches, the guard is untested. It edits the file in place and restores
it, so do not run it anywhere near a live server. A mutation targets `agent.py` by default;
a fourth tuple element names another file (`"tools/repair_links.py"`), because the
maintenance tools carry guards too. **Every file a run touches is restored before the next
mutation is applied**, not just at the end — without that, mutations in different files
stack, and a guard whose removal nothing notices is reported CAUGHT on the strength of the
previous mutation's failures. That shipped briefly and inflated one result; a mutation
runner that lies about coverage is worse than not having one.

`rename_page.py` is the one maintenance tool whose logic is not in `agent.py`, so nothing
in-process can reach it — which is exactly how its copy of the generated-pages bug went
uncovered while `merge_page`'s was caught. `tests/test_rename_page_cli.py` runs the real
script against a `tools/` copied into a temp directory, since `REPO_ROOT` comes from the
script's own location. Worth moving into `agent.py` like the others.

**`deploy.sh` stamps `.version` into the jail** (`git log -1`, written after the rsync)
and `serve.py` logs it at startup — `Lobotomy starting — version: <sha> <date> <subject>`.
The repo's `.git` is not shipped, so that file is the only way the server can say what
code it is running. Check it before concluding a fix did not work: twice now, behaviour
the logs showed as unfixed was simply not deployed yet.

`deploy.sh` excludes `tests/` from what it ships — tests belong where the code is
changed, and `mutate.py` must never sit beside a running server. It does not run them
either: deploy copies files and nothing else. Run the suite yourself before deploying.

`tests/test_template_js.py` parses every inline `<script>` in `tools/templates/` with
`node --check`, skipping when node is absent. The templates carry real logic the Python
suite cannot reach, and a syntax error there kills the whole block silently — the search
popup's keyboard navigation would just stop working with nothing in any log.

`docs/test-plan.md` is the spec the suite was built from; read it before adding a module.

## Config Structure

`config.json` (gitignored, copy from `config.json.example`):
```json
{
  "admin":  { "email": "...", "password": "..." },
  "server": { "host": "127.0.0.1", "port": 8080, "https": false, "base_url": "..." },
  "llm":    { "active": "gemini",
              "providers": { "gemini": { "api_key": "...", "model": "...",
                                         "fallback_models": ["..."] } },
              "max_retries": 6, "retry_poll_interval": 300, "daily_quota_poll_interval": 1800,
              "max_rpm": 15, "inter_request_delay": 5, "max_tokens": 16384 },
  "email":  { "resend_api_key": "...", "from_address": "..." }
}
```

`max_tokens` (default 16384) is also the model's output budget, and the write paths derive
a whole-page-rewrite threshold from it — a page whose rewrite would exceed roughly half the
budget is steered to the section tools, because the model silently condenses rather than
failing loudly when it runs out of room.

LLM providers use OpenAI-compatible APIs. The `agent.py:PROVIDERS` dict maps provider names to base URLs and default models. Provider config can also override `api_base` and `model` per-provider inside `config.json`.

**Model fallback on 429.** A provider block may list `fallback_models`. Because free-tier quotas are per-model, `_post_with_fallback()` treats a 429 as "this model is spent" rather than "the provider is down": it reissues the same request against the next model in the chain immediately, and only raises — handing control back to the existing two-phase backoff — once every model is rate limited. Only 429 walks the chain (`_LLMError.rate_limited`); 500s, timeouts and connection errors are provider-wide and would fail identically on every model, so they propagate at once. A rate-limited model goes into `_model_cooldowns` and is skipped until it lapses (`retry_after` or 60s; `daily_quota_poll_interval` when the body names a `PerDay` quota), which keeps later rounds from burning a wasted call on a model already known to be exhausted. Cooldowns are the only state — nothing is sticky, so the primary is retried first as soon as its window passes.
