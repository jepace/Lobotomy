# Lobotomy

A personal knowledge base maintained by LLMs. Not a RAG system — a **compounding wiki**.

## The Idea

Most LLM memory systems work by retrieving text chunks at query time (RAG). This wiki works
differently: when a new source is added, the LLM reads it, synthesizes the key information, and
**writes it permanently into the wiki** — updating entity pages, concept pages, noting
contradictions, and maintaining cross-references. By the time you ask a question, the synthesis is
already done.

The wiki is a compounding artifact. Every ingested source makes every subsequent query more
accurate, because the cross-references, comparisons, and contradiction flags are already there.

## Three Layers

| Layer | Location | Who writes it |
|-------|----------|---------------|
| Raw sources | `raw/` | You (human) — immutable |
| Wiki pages | `wiki/` | The LLM |
| Operating schema | `CLAUDE.md` | Defined once, evolved carefully |

## Setup

### Install dependencies

```sh
pip install -r requirements.txt
```

On FreeBSD, flask and markdown are available as packages (faster, no compiler needed):

```sh
pkg install py311-flask py311-markdown
pip install openai resend
```

### Configure

Copy the example config and edit it:

```sh
cp config.example.json config.json
$EDITOR config.json
```

`config.json` is gitignored. All settings in one place:

```json
{
  "admin": {
    "email": "you@example.com",
    "password": "your-login-password"
  },
  "server": {
    "host": "127.0.0.1",
    "port": 8080,
    "https": false,
    "base_url": "https://wiki.example.com"
  },
  "llm": {
    "provider": "gemini",
    "api_key": "your-gemini-api-key",
    "model": "gemini-2.5-flash-lite"
  },
  "email": {
    "resend_api_key": "",
    "from_address": "wiki@yourdomain.com"
  }
}
```

**`llm.provider` / `llm.model`** options:

| Provider | `provider` | Example `model` | Key needed |
|----------|-----------|-----------------|------------|
| Gemini (free tier) | `gemini` | `gemini-2.5-flash-lite` | [aistudio.google.com](https://aistudio.google.com/apikey) |
| OpenAI | `openai` | `gpt-4o-mini` | platform.openai.com |
| OpenRouter (free models) | `openrouter` | `google/gemini-2.0-flash-exp:free` | openrouter.ai |
| Ollama (local) | `ollama` | `llama3.2` | none |

**Email verification** (optional): fill in `email.resend_api_key` and `email.from_address`
with your [Resend](https://resend.com) credentials. Without it, accounts are auto-verified.

**Behind HTTPS?** Set `"https": true` and `"base_url"` to your public URL so email links work.

The admin password is hashed with scrypt on first run; the plaintext in `config.json` is only
read once and never stored directly.

### Start the web server

```sh
python3 tools/serve.py
```

Open `http://your-vps-ip:8080` in any browser — including your iPhone.

**VPS jail setup** — bind to all interfaces: set `"host": "0.0.0.0"` in `config.json`.

For a reverse proxy via nginx (recommended — handles TLS):

```nginx
server {
    listen 443 ssl;
    server_name wiki.example.com;
    # ... ssl config ...
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_buffering off;        # required for streaming chat
    }
}
```

## Usage

### Web interface (primary)

Browse to the server URL. Four tabs:

- **Chat** — talk to the AI: ingest sources, query the wiki, lint, manage tasks via AI
- **Wiki** — browse all pages with rendered markdown and working links
- **Tasks** — view, add, and check off tasks without needing the AI
- **Inbox** — paste articles or URLs to save for later; tap "Process with AI" to ingest them

### Command line (optional)

```sh
python3 tools/wiki.py                      # interactive REPL
python3 tools/wiki.py "ingest raw/file.md" # one-shot
```

### Ingest a source

1. Save the document to `raw/` as a `.txt`, `.md`, or `.pdf` file
2. Say: `Ingest raw/your-document.pdf`

The LLM will read the source, create a summary page, update entity and concept pages, and maintain
the index and log.

### Read-it-later (Pocket replacement)

1. Save an article as `.md`/`.txt`, or save a URL as a single-line `.txt` file, into `raw/inbox/`
2. Say: `Process inbox` — the LLM will triage, ingest, and update the reading list

### Query the wiki

Say: `What does the wiki say about [topic]?`

The LLM reads `wiki/index.md`, finds relevant pages, reads them, and synthesizes a cited answer.
It will tell you where the wiki has no coverage.

### Manage tasks (Toodledo replacement)

Tasks live in `wiki/tasks.md` with inline tags for priority, due date, context, and project.

```
- [ ] Task description #p:high #due:2026-05-01 #ctx:work #proj:project-name
```

Operations you can ask the LLM:
- `Add a task: [description] #p:high #due:2026-05-01 #ctx:work`
- `Show open tasks due this week`
- `Complete task: [description]`
- `Archive completed tasks`
- `Prioritize my task list`

### Search (no LLM needed)

```sh
python3 tools/search.py "keyword"
python3 tools/search.py transformer BERT GPT
```

### Filter tasks (no LLM needed)

```sh
python3 tools/tasks.py                    # all open tasks, sorted by due date / priority
python3 tools/tasks.py --due-today        # due today or overdue
python3 tools/tasks.py --priority high    # high and top priority tasks
python3 tools/tasks.py --context work     # tasks with @work context
python3 tools/tasks.py --overdue          # past their due date
python3 tools/tasks.py --project name     # tasks in a specific project
```

### Health check

Say: `Lint the wiki` — checks for broken links, orphan pages, stale content, contradictions, and
coverage gaps. Suggests sources to look for.

## File Structure

```
raw/                    Drop source documents here (never modified by LLM)
raw/inbox/              Drop articles/URLs for read-it-later processing
raw/assets/             Images, PDFs, attachments
wiki/
  index.md              Master catalog of all wiki pages
  log.md                Operation history (append-only)
  overview.md           High-level synthesis, always kept current
  reading-list.md       Read-it-later queue tracker
  tasks.md              Task manager
  sources/              One page per ingested source
  entities/             People, orgs, products, projects
  concepts/             Ideas, techniques, frameworks
  synthesis/            Cross-source analyses and comparisons
tools/
  serve.py              Web server — primary interface (chat, wiki, tasks, inbox)
  agent.py              Shared AI agent logic (provider config, tools, streaming)
  wiki.py               CLI client (optional alternative to the web server)
  search.py             Keyword search CLI (no LLM needed)
  tasks.py              Task filter CLI (no LLM needed)
  templates/            HTML templates for the web server
CLAUDE.md               LLM operating instructions (the schema)
```

## On FreeBSD

All CLI tools require only Python 3 (no additional packages):

```sh
pkg install python3
python3 tools/search.py "keyword"
python3 tools/tasks.py --due-today
```

For reading the wiki in a terminal without the web front end:
- [`glow`](https://github.com/charmbracelet/glow): `pkg install glow`, then `glow wiki/overview.md`
- [`mdcat`](https://github.com/swsnr/mdcat): terminal markdown renderer with image support

## Design Principles

- **Sources are immutable** — the LLM never modifies raw documents
- **Contradictions are surfaced, not resolved** — the LLM flags disagreements; humans decide
- **Every claim has provenance** — pages cite which source supports each claim
- **The log is append-only** — complete audit trail of all LLM operations
- **Cold-start friendly** — a fresh LLM session can fully orient from `CLAUDE.md` alone
- **No special tooling required** — all wiki content is standard markdown, readable everywhere
- **Viewer-agnostic** — works with any markdown renderer, no Obsidian required
