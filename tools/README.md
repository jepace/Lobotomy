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

**Run the maintenance tools as the user the server runs as.** They write the same files
the server does. Writes preserve each file's existing owner and mode, and anything newly
created inherits its parent directory's owner, so running one as root no longer locks the
server out — but running as the server's own user is still the safe habit, and it is the
only thing that gets the ownership right on a wiki that has already been damaged:

```sh
su -m www -c 'python3 tools/relink.py'         # FreeBSD jail, server running as www
```

If a tool was run as root before this behavior existed, fix it once with
`chown -R www:www wiki raw` (substituting the server's user).

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

### `tests/run_autolink_cases.py` — autolinker characterization harness
The autolinker is the most bug-prone code in the project: its matching rules are subtle
(link every occurrence, never inside an existing link, upgrade a partial link to the longer
title, skip headings) and easy to break by accident. This runs a corpus of tricky inputs
through it in a throwaway wiki and dumps the results as JSON.

Capture a baseline before changing anything, compare after — the output should be
byte-identical unless the change is meant to alter behavior.

```sh
python3 tools/tests/run_autolink_cases.py before.json
# ...make your change...
python3 tools/tests/run_autolink_cases.py after.json
diff before.json after.json
```

The corpus itself is `tests/autolink_cases.py`; add a case there when you find an edge the
30 existing ones miss.
