# tools/

Everything that runs Lobotomy. Two kinds of file live here: **servers and CLIs** you invoke
directly, and **modules** the others import.

Run every command from the **repo root**, not from `tools/`:

```sh
cd /path/to/Lobotomy
python3 tools/serve.py
```

Almost everything here imports `config.py`, which reads `config.json` at the repo root and
exits immediately if it is missing or malformed. Copy `config.json.example` to
`config.json` first, even for the tools that never call an LLM.

**Just run them.** No `su`, no wrapper:

```sh
python3 tools/relink.py
python3 tools/promote_openers.py --dry-run
```

They write the same files the server does, and every write path keeps the tree's
ownership: an existing file keeps its own owner and mode, a new file takes its parent
directory's, and every directory level created along the way takes its parent's. So a tool
run as root inside a wiki owned by `www` leaves everything owned by `www`, and the server
can still write it. `tests/test_root_ownership.py` checks exactly this, and skips unless
it is actually running as root.

That was not always true — root-run tools used to leave root-owned files, and then
directories after the files were fixed. If your wiki was damaged before this, repair it
once and forget it:

```sh
chown -R www:www wiki raw      # substitute the user your server runs as
```

---

## Servers and clients

### `serve.py` — the web server
The primary interface: chat, wiki browsing, the reading list, page history, settings.

```sh
python3 tools/serve.py                 # http://127.0.0.1:8080 (host/port from config.json)
```

### `wiki.py` — command-line client
The same agent as the web UI, in a terminal. No Flask needed.

```sh
python3 tools/wiki.py                          # interactive REPL
python3 tools/wiki.py "ingest raw/article.md"  # one-shot
```

Picks its provider from the environment rather than from `llm.active`:

```sh
export WIKI_PROVIDER=gemini        # gemini | openai | ollama | openrouter
export WIKI_API_KEY=your-key       # not needed for ollama
export WIKI_MODEL=gemini-2.5-flash-lite   # optional override
export WIKI_API_BASE=...                  # optional override
```

`config.json` still has to exist — it is loaded on import even though these settings come
from the environment.

---

## Maintenance CLIs (no LLM, no API cost)

These read and repair the wiki directly. All of them are safe to run against a live
server — writes go through the same code path the server uses, so page history is recorded
and the autolinker's caches stay correct.

### `search.py` — full-text search
Keyword search across all wiki pages. Multiple keywords are AND-ed.

```sh
python3 tools/search.py monterey
python3 tools/search.py casa monterey                    # both words must appear
python3 tools/search.py in:entities monterey             # restrict to a subdirectory
python3 tools/search.py tag:nonprofit after:2026-01-01   # filter by tag and date
```

Set `LOBOTOMY_URL` and `LOBOTOMY_KEY` to search a running server instead of the local files.

### `relink.py` — add wiki links to bare mentions
**This is the one that puts links in.** A page is autolinked only while an ingest is
touching it, against the titles that existed at that moment — so a page written before its
subjects had pages keeps those mentions as plain text forever, and nothing revisits it.
This is the catch-up sweep.

```sh
python3 tools/relink.py                              # every page (minutes on a big wiki)
python3 tools/relink.py wiki/entities/pg-e.md        # just one page
python3 tools/relink.py wiki/concepts/*.md           # a subset
python3 tools/relink.py --dry-run                    # report, write nothing
python3 tools/relink.py --dry-run wiki/entities/pg-e.md
```

The web UI has the whole-wiki version on `/wiki/lint` ("Relink all pages"), which runs in
the background with a progress bar and a stop button. Use the CLI for a single page, a
subset, or a dry run.

Every page it changes gets a version-history entry first, so anything it gets wrong is
revertable from that page's History view.

### `rename_page.py` — rename a page, its title, and every link to it
Pages are titled by the LLM from whatever the source called the subject, and it sometimes
picks a fragment — an article mentioning "United" produced `entities/united.md` titled
"United", for the airline. A title that is a common word or part of a longer proper noun
then does real damage, because the autolinker matches it everywhere: that page turned
"United Nations" into `[United](../entities/united.md) Nations` across the wiki.

```sh
python3 tools/rename_page.py wiki/entities/united.md wiki/entities/united-airlines.md \
    --title "United Airlines" --dry-run
python3 tools/rename_page.py wiki/entities/united.md wiki/entities/united-airlines.md \
    --title "United Airlines"
python3 tools/relink.py                # then re-link the prose under the new title
```

Moves the page and its history, updates `title:` and the H1, and repoints every link that
pointed at it. Links whose display text was the *old* title are stripped to plain text
instead of repointed — that text is what the rename declares wrong, so
`[United](...) Nations` becomes `United Nations` rather than a link relabelled to an
airline. Running `relink.py` afterwards re-links prose that genuinely names the subject,
and leaves prose that never meant it alone.

### `repair_links.py` — fix broken internal links
Repairs links that already exist — it never creates one. (For turning bare text into links,
that's `relink.py` above.) Three passes: un-nest double-linked text left by an old
autolinker bug, correct relative paths with the wrong number of `../`, and unwrap
`about:reader?url=...` capture URLs into the real article URL. Scans `raw/` as well as
`wiki/`.

```sh
python3 tools/repair_links.py --dry-run   # report what would change
python3 tools/repair_links.py             # apply
```

### `unlink_headings.py` — take markdown links out of headings
`## [Atheism](../sources/atheism-wikipedia-2026.md)` → `## Atheism`. Every section tool
finds a section by its heading text, so a heading carrying link syntax is unaddressable:
asked for "Atheism" they do not find it, and `append_section` then creates a second
section beside it. The autolinker never does this — it skips heading lines — so these are
hand-written, and the write paths refuse them now. This repairs the pages damaged before
that.

```sh
python3 tools/unlink_headings.py --dry-run
python3 tools/unlink_headings.py
```

Nothing is lost: the link target is still reachable from the prose under the heading, and
`relink.py` re-links the subject there anyway. Every page changed goes through the normal
write path, so it is revertable from that page's History view.

### `promote_openers.py` — give pages the `## Overview` their template requires
`create_file` requires an opener — `## Overview` on an entity, `## Definition` on a
concept — and nothing adds one afterwards, so pages written before that check never
acquire one. `section_inventory.py` counts them; this fixes the ones that can be fixed
mechanically. Every ingest touching such a page pays a wasted round: the agent asks for
the section the schema promises, misses, and has to recover.

Most of those pages are not missing the prose, only the heading — a lead paragraph sits
under the H1 with nothing naming it, so no tool can address it and it does not show up in
a page outline. This inserts the heading above it.

```sh
python3 tools/promote_openers.py --dry-run
python3 tools/promote_openers.py
python3 tools/promote_openers.py --dry-run --all   # list every page, not the first 25
```

A page with no lead paragraph is left alone and listed: its first section is something
like "Background & Leadership", renaming which would misdescribe what it holds, and
writing an overview from scratch is authorship, not repair. Send those through a
regenerate, or leave them.

Report-only with `--dry-run`. Changes go through the normal write path, so each page is
revertable from its History view.

### `repair_frontmatter.py` — backfill missing frontmatter
Thin wrapper over `agent.heal_pages()`: fills in missing `created:`/`updated:` from the
file's own mtime, missing `tags:`/`sources:` as `[]`, and unwraps reader-mode URLs. Pages
missing `title:` or `type:` are reported, never guessed. The server already runs this at
startup and after every ingest — this is for repairing a wiki without starting the server.

```sh
python3 tools/repair_frontmatter.py --dry-run
python3 tools/repair_frontmatter.py
```

### `rebuild_sources.py` — regenerate `## Sources` sections and the index
The `## Sources` section on entity and concept pages is generated from `sources:`
frontmatter. This re-renders it when the two have drifted apart.

```sh
python3 tools/rebuild_sources.py                       # every entity and concept page
python3 tools/rebuild_sources.py wiki/entities/foo.md  # one page
python3 tools/rebuild_sources.py --index               # regenerate wiki/index.md instead
```

### `section_inventory.py` — what headings the wiki actually uses
`LOBOTOMY.md` gives a template per page type, but nothing enforces it outside source
pages, so what the wiki really contains has only been guessable. This counts it: the most
used headings per type, how many are one-offs, and four specific smells — a heading the
template does not list, a section named after its own page, one naming a date or single
event, and an entity with no Overview (or concept with no Definition).

Run it before deciding what to constrain: a heading on 400 pages is part of the
vocabulary whether the template names it or not; one on two pages is drift.

```sh
python3 tools/section_inventory.py            # summary
python3 tools/section_inventory.py --pages    # name the pages behind each smell
```

Report-only.

### `find_duplicate_pages.py` — find one subject split across several pages
Reports pages whose titles normalize to the same thing — "Pacific Gas and Electric
Company", "Pacific Gas & Electric Co." and "Pacific Gas & Electric" are one utility with
three pages, because the exact-title lookup that decides create-vs-update saw three
unrelated names.

Report-only: merging needs judgment it cannot have, since a parent holding company and its
operating subsidiary normalize together too and are correctly separate.

```sh
python3 tools/find_duplicate_pages.py
```

### `find_duplicate_sections.py` — find and merge repeated headings within a page
Reports pages carrying the same heading twice. The write paths refuse these now, but pages
damaged before that keep their duplicates.

`--fix` merges only what needs no judgment: one copy empty (what an echoed heading in
`update_section` content left behind), the copies identical, or one body containing all the
others. Two copies with genuinely divergent content are left alone and listed for you —
choosing what survives isn't mechanical. Repairs go through the normal write path, so every
page changed is revertable from its History view.

```sh
python3 tools/find_duplicate_sections.py               # report only
python3 tools/find_duplicate_sections.py --fix --dry-run
python3 tools/find_duplicate_sections.py --fix
```

### `lint.sh` — broken-link check, shell only
A dependency-free link checker: no Python, no `config.json`. `/wiki/lint` in the web UI
covers the same ground and more.

```sh
sh tools/lint.sh          # note: sh, not python3
```

---

## Modules (imported, not run directly)

| File | What it is |
|------|-----------|
| `agent.py` | The core. Every AI tool implementation (`read_file`, `update_file`, `create_file`, `lookup_titles`, `search_wiki`, `autolink`, `done`, …), the agentic loop, the LLM provider abstraction, page version history, and the whole-wiki maintenance passes. Both `serve.py` and `wiki.py` are thin layers over it. |
| `config.py` | Reads `config.json`. Use `cfg_get(section, key, default)` — configuration is never hardcoded. Re-reads on change, so most settings take effect without a restart. |
| `auth.py` | Single-user login, sessions, email verification and password reset. State is JSON files in `wiki/`, no database. |
| `job_queue.py` | Runs agent turns in a background daemon thread, on the assumption a job may take hours. Events are written to disk as `.ndjson`, so a client that navigates away or reconnects can resume the stream from the beginning. |
| `templates/` | Jinja2 templates for every page the web server serves. |

---

## Tests

The suite lives at the repo root, in `tests/` — it tests the whole project, not just what
is in here. See `tests/README.md`.

```sh
python3 tests/run_all.py     # everything; exits 0 on success, 1 on failure
python3 tests/mutate.py      # break each guard, check a test notices
```
