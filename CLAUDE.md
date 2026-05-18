# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Lobotomy is a personal knowledge-base server where LLMs synthesize knowledge at ingest time and write it permanently into a wiki — not retrieved at query time. Three layers: immutable raw sources (`raw/`), LLM-generated wiki pages (`wiki/`), and an operating schema (`LOBOTOMY.md`).

## Running the Server

```sh
pip install -r requirements.txt      # flask, markdown; add openai and resend as needed
cp config.json.example config.json   # then fill in llm.provider, llm.api_key, admin creds
python3 tools/serve.py               # web UI at http://127.0.0.1:8080
```

Alternative CLI (no Flask needed):
```sh
python3 tools/wiki.py                      # interactive REPL
python3 tools/wiki.py "ingest raw/file.md" # one-shot command
```

Utility CLIs (no LLM needed):
```sh
python3 tools/search.py "keyword"          # full-text search across wiki
python3 tools/tasks.py --due-today         # filter tasks.md
python3 tools/repair_links.py              # fix broken relative paths
sh tools/lint.sh                           # shell-based broken-link checker
```

## Architecture

### Core modules

**`tools/agent.py`** — the heart of the system. Contains all AI tool implementations (`_read_file`, `_write_file`, `_create_file`, `_autolink`, `_search_wiki`, `_fetch_url`, `_prepend_log`, `_done`, `_rebuild_index`, etc.) plus the agentic loop (`stream_agent_turn`, `run_agent_turn`) and LLM provider abstraction. Both `serve.py` and `wiki.py` import from here.

**`tools/serve.py`** — Flask web server. Routes for: `/chat` (streaming AI), `/wiki/*` (rendered markdown), `/tasks` (task manager UI), `/inbox` (read-it-later), `/blog`, auth, and settings. Imports `agent.py` for AI functionality and `job_queue.py` for background jobs.

**`tools/wiki.py`** — CLI wrapper around the same agent tools. An interactive REPL or one-shot runner; no Flask dependency.

**`tools/config.py`** — reads `config.json`. Use `cfg_get(section, key, default)` throughout. Config is never hardcoded.

**`tools/job_queue.py`** — background job queue used by `serve.py` for async inbox processing.

### The autolinker (common bug surface)

**`tools/agent.py:_autolink()`** — called automatically after every `create_file` or `write_file`. **This is the only way wiki links are ever created** — the LLM never writes raw markdown links itself. Uses a combined regex where group 1 protects existing links and group 2 matches titles bare or with a sub-span already linked (via `_title_alts()`). **All** bare occurrences of each title (and any `aliases:`) are linked (not just the first). When a partial match is found (e.g. `CASA of [Monterey County](url)`), the inner link is stripped and the whole phrase is replaced with the longer-title link.

The critical invariant: **never match inside existing markdown links**. Group 1 of the combined regex takes priority at each position, consuming existing links before group 2 can fire.

Pages can carry an `aliases:` frontmatter list (e.g. `aliases: ["gonzales", "uc davis"]`) for common short names that the autolinker should also match. The LLM is not instructed to set this field — it's a manual human override for when the formal page title differs from how the subject is typically referenced in prose.

### Wiki page lifecycle

1. `create_file` → writes frontmatter + body, calls `_autolink`, calls `_inject_sources_section`
2. `_autolink` → cross-links bare title occurrences to other wiki pages
3. `_inject_sources_section` → renders a `## Sources` section from `sources:` frontmatter
4. `_autolink_sources_if_entity` → if the written page is an entity, also re-autolinks all source pages that mention it
5. `done()` → server runs lint checks; results visible at `/wiki/lint`

### `system_prompt()` and `LOBOTOMY.md`

`agent.py:system_prompt()` reads `LOBOTOMY.md` as the LLM's operating schema and appends a tool quick-reference table. The LLM operating instructions (ingest workflow, query workflow, page format, naming conventions, etc.) all live in `LOBOTOMY.md`, not here.

## Key Conventions

- **`raw/` is immutable for the LLM** — code in `_write_file` blocks the LLM from writing outside `wiki/`. However, `serve.py` itself does move files within `raw/`: inbox items are renamed from `raw/inbox/` to `raw/sources/` when the user archives them (`serve.py:_mark_inbox_wikified`, `inbox_archive`). Don't assume files stay in `raw/inbox/` permanently.
- **`wiki/log.md` is append-only** — always use `prepend_log`, never `write_file` on the log.
- **No `[[wikilink]]` syntax** — standard relative markdown links only.
- **`create_file` over `write_file`** for new wiki pages — it auto-fills `created`/`updated` dates.
- Internal wiki links use paths relative to the page's location: `../entities/foo.md` from `wiki/sources/`.
- File names: `lowercase-hyphenated-slugs.md`. Source slugs encode `{author-or-org}-{year}-{short-title}`.
- The `## Sources` section in entity/concept pages is auto-generated from frontmatter — never write it manually.

## Config Structure

`config.json` (gitignored, copy from `config.json.example`):
```json
{
  "admin":  { "email": "...", "password": "..." },
  "server": { "host": "127.0.0.1", "port": 8080, "https": false, "base_url": "..." },
  "llm":    { "provider": "gemini|openai|openrouter|ollama|groq", "api_key": "...", "model": "..." },
  "email":  { "resend_api_key": "...", "from_address": "..." }
}
```

LLM providers use OpenAI-compatible APIs. The `agent.py:PROVIDERS` dict maps provider names to base URLs and default models. Provider config can also override `api_base` and `model` per-provider inside `config.json`.
