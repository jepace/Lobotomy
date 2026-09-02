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

Utility CLIs (no LLM needed):
```sh
python3 tools/search.py "keyword"          # full-text search across wiki
python3 tools/repair_links.py              # fix broken relative paths, unwrap reader-mode URLs
python3 tools/repair_frontmatter.py        # backfill missing created:/updated: fields
sh tools/lint.sh                           # shell-based broken-link checker
```

## Architecture

### Core modules

**`tools/agent.py`** — the heart of the system. Contains all AI tool implementations (`_read_file`, `_update_file`, `_create_file`, `_lookup_titles`, `_search_wiki`, `_autolink`, `_fetch_url`, `_done`, `_rebuild_index`, etc.) plus the agentic loop (`stream_agent_turn`, `run_agent_turn`) and LLM provider abstraction. Both `serve.py` and `wiki.py` import from here.

**`tools/serve.py`** — Flask web server. Routes for: `/chat` (streaming AI), `/wiki/*` (rendered markdown), `/inbox` (read-it-later), auth, and settings. Imports `agent.py` for AI functionality and `job_queue.py` for background jobs.

**`tools/wiki.py`** — CLI wrapper around the same agent tools. An interactive REPL or one-shot runner; no Flask dependency.

**`tools/config.py`** — reads `config.json`. Use `cfg_get(section, key, default)` throughout. Config is never hardcoded.

**`tools/job_queue.py`** — background job queue used by `serve.py` for async inbox processing.

### The autolinker (common bug surface)

**`tools/agent.py:_autolink()`** — run over every page touched in a session by `_post_process_session()` when the agent calls `done()`. **This is the only way wiki links are ever created** — the LLM never writes raw markdown links itself. Uses a combined regex where group 1 protects existing links and group 2 matches titles bare or with a sub-span already linked (via `_title_alts()`). **All** bare occurrences of each title (and any `aliases:`) are linked (not just the first). When a partial match is found (e.g. `CASA of [Monterey County](url)`), the inner link is stripped and the whole phrase is replaced with the longer-title link.

The critical invariant: **never match inside existing markdown links**. Group 1 of the combined regex takes priority at each position, consuming existing links before group 2 can fire.

Performance: the title+alias map and the per-title compiled regexes are cached in memory (`_title_map_cache`, `_title_regex_cache`). `_atomic_write` invalidates them only when a write actually changes `title`/`aliases`/`no_autolink`; an mtime-scan backstop in `_build_title_map()` catches writes that bypass `_atomic_write` (other processes, manual edits), with per-file vetted mtimes so the backstop doesn't false-fire on the autolinker's own body-only writes. This invalidation logic has been a repeat bug source — change it with care and test all of: safe write keeps cache, title change invalidates, bypass write is detected, safe write doesn't mask a concurrent bypass.

Pages can carry an `aliases:` frontmatter list (e.g. `aliases: ["gonzales", "uc davis"]`) for common short names that the autolinker should also match. The LLM is not instructed to set this field — it's a manual human override for when the formal page title differs from how the subject is typically referenced in prose.

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

Served by `/wiki/<path>/history` (list), `/wiki/<path>/history/<rev>` (unified diff via
stdlib `difflib`), and `/api/wiki/<path>/revert/<rev>`. Revert goes through `_atomic_write`,
so it snapshots the current content first and is itself undoable.

### Wiki page lifecycle

1. `create_file` / `update_file` → write frontmatter + body; `sources:` is merged from disk plus the session's source page (never trusted from the LLM); `_inject_sources_section` renders the `## Sources` section
2. `done()` → `_post_process_session()` runs once: patches `sources:` on every touched page, autolinks them all, re-injects `## Sources`, rebuilds the index
3. Server lint checks run after `done()`; results visible at `/wiki/lint`

Guardrails enforced in code (not just in LOBOTOMY.md — instructions alone proved insufficient): search limited to 2 per term per session; `done()` refused if an ingest wrote no entity/concept pages or never established a source page; `update_file` refused until the session has read the page's full content (long pages are chunked at 20K chars); refusals hand back the needed file content in the same response to save a round-trip; `create_file` refuses all-lowercase titles and adopts an existing source page on re-ingest of the same raw file.

### `system_prompt()` and `LOBOTOMY.md`

`agent.py:system_prompt()` reads `LOBOTOMY.md` as the LLM's operating schema and appends a tool quick-reference table. The LLM operating instructions (ingest workflow, query workflow, page format, naming conventions, etc.) all live in `LOBOTOMY.md`, not here.

## Key Conventions

- **`raw/` is immutable for the LLM** — code in `_update_file` blocks the LLM from writing outside `wiki/`. Raw source files live flat in `raw/` (no subdirectories). `serve.py` manages their lifecycle via `_mark_inbox_wikified`.
- **`wiki/log.md` is append-only** — written by `_auto_write_log_entry` at `done()`; `update_file` refuses it.
- **No `[[wikilink]]` syntax** — standard relative markdown links only.
- **`create_file` for new pages, `update_file` for existing ones** — `create_file` auto-fills `created`/`updated`; `update_file` restores system-owned fields from disk.
- Internal wiki links use paths relative to the page's location: `../entities/foo.md` from `wiki/sources/`.
- File names: `lowercase-hyphenated-slugs.md`. Source slugs encode `{author-or-org}-{year}-{short-title}`.
- The `## Sources` section in entity/concept pages is auto-generated from frontmatter — never write it manually.

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
              "max_rpm": 15, "inter_request_delay": 5 },
  "email":  { "resend_api_key": "...", "from_address": "..." }
}
```

LLM providers use OpenAI-compatible APIs. The `agent.py:PROVIDERS` dict maps provider names to base URLs and default models. Provider config can also override `api_base` and `model` per-provider inside `config.json`.

**Model fallback on 429.** A provider block may list `fallback_models`. Because free-tier quotas are per-model, `_post_with_fallback()` treats a 429 as "this model is spent" rather than "the provider is down": it reissues the same request against the next model in the chain immediately, and only raises — handing control back to the existing two-phase backoff — once every model is rate limited. Only 429 walks the chain (`_LLMError.rate_limited`); 500s, timeouts and connection errors are provider-wide and would fail identically on every model, so they propagate at once. A rate-limited model goes into `_model_cooldowns` and is skipped until it lapses (`retry_after` or 60s; `daily_quota_poll_interval` when the body names a `PerDay` quota), which keeps later rounds from burning a wasted call on a model already known to be exhausted. Cooldowns are the only state — nothing is sticky, so the primary is retried first as soon as its window passes.
