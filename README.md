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
| Operating schema | `LOBOTOMY.md` | Defined once, evolved carefully |

## Setup

### Install dependencies

```sh
pip install -r requirements.txt      # flask, markdown — that's all
```

On FreeBSD, both are available as packages (faster, no compiler needed):

```sh
pkg install py311-flask py311-markdown
```

No LLM-provider or email SDK is needed — all API calls use the Python standard library.

### Configure

Copy the example config and edit it:

```sh
cp config.json.example config.json
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
    "active": "gemini",
    "providers": {
      "gemini": {
        "api_key": "your-gemini-api-key",
        "model": "gemini-2.5-flash-lite"
      }
    }
  },
  "email": {
    "resend_api_key": "",
    "from_address": "wiki@yourdomain.com"
  }
}
```

`llm.active` names the provider to use; `llm.providers` holds one block per provider so you
can keep keys for several and switch between them (also switchable at runtime from the
Settings page). Provider options:

| Provider | Example `model` | Key needed |
|----------|-----------------|------------|
| `gemini` (free tier) | `gemini-2.5-flash-lite` | [aistudio.google.com](https://aistudio.google.com/apikey) |
| `openai` | `gpt-4o-mini` | platform.openai.com |
| `openrouter` (free models) | `google/gemini-2.0-flash-exp:free` | openrouter.ai |
| `groq` | `llama-3.3-70b-versatile` | console.groq.com |
| `ollama` (local) | `llama3.2` | none |

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

**FreeBSD service**: an rc.d script lives at `contrib/freebsd/rc.d/lobotomy`. Install it to
`/usr/local/etc/rc.d/`, then `sysrc lobotomy_enable=YES && service lobotomy start`.
`deploy.sh` handles the rsync + rc-script install for a bastille jail.

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

- **Chat** — talk to the AI: ingest sources, query the wiki, regenerate pages
- **Wiki** — browse all pages with rendered markdown and working links
- **Reading List** — save articles and URLs for later; wikify them with one tap
- **Settings** — theme, password, AI model switching, browser bookmarklet

### Reading list (Pocket replacement)

Three ways to save an article:

1. **Bookmarklet** — grab it from Settings, tap it on any page
2. **Push API** — `POST /api/push` from Shortcuts or any app (see `docs/api.md`)
3. **Paste** — add a URL or paste article text directly on the Reading List page

Saved items land in `raw/` (flat — no subdirectories). Tap **Wikify** on an item to ingest
it; the badge shows when it's been synthesized into the wiki.

### Ingest a source from chat

1. Save the document to `raw/` as a `.txt`, `.md`, or `.pdf` file
2. Say: `Ingest raw/your-document.pdf`

The LLM reads the source, creates a summary page, updates entity and concept pages, and
maintains the index and log.

### Query the wiki

Say: `What does the wiki say about [topic]?`

The LLM searches the wiki, reads the relevant pages, and synthesizes a cited answer. It will
tell you where the wiki has no coverage.

### Regenerate a page

Say: `Regenerate the [title] page` — rebuilds one page from every source that informed it.
Normal ingest folds new sources in incrementally; regenerate is the full re-synthesis.

### Search (no LLM needed)

```sh
python3 tools/search.py "keyword"
python3 tools/search.py transformer BERT GPT
```

### Health check

Visit `/wiki/lint` — checks for broken links, missing frontmatter, and pages missing from
the index. Lint checks also run automatically at the end of every ingest.

## File Structure

```
raw/                    Source documents, flat (never modified by the LLM)
raw/assets/             Images, PDFs, attachments
wiki/
  index.md              Master catalog of all wiki pages (auto-generated)
  log.md                Operation history (append-only)
  sources/              One page per ingested source
  entities/             People, orgs, products, projects
  concepts/             Ideas, techniques, frameworks
  synthesis/            Cross-source analyses and comparisons
tools/
  serve.py              Web server — primary interface
  agent.py              AI agent core: tools, agentic loop, provider abstraction
  wiki.py               CLI client (optional alternative to the web server)
  job_queue.py          Background job queue for async wikify
  config.py             config.json loader
  auth.py               Login, sessions, email verification
  search.py             Keyword search CLI (no LLM needed)
  repair_links.py       Fix broken relative links + reader-mode URLs (no LLM needed)
  repair_frontmatter.py Backfill missing created:/updated: frontmatter (no LLM needed)
  lint.sh               Shell-based broken-link checker
  templates/            HTML templates for the web server
contrib/freebsd/rc.d/   FreeBSD service script
docs/api.md             Push API documentation
LOBOTOMY.md             LLM operating instructions (the schema)
```

## Command line (optional)

```sh
python3 tools/wiki.py                      # interactive REPL
python3 tools/wiki.py "ingest raw/file.md" # one-shot
```

For reading the wiki in a terminal without the web front end:
- [`glow`](https://github.com/charmbracelet/glow): `pkg install glow`, then `glow wiki/index.md`
- [`mdcat`](https://github.com/swsnr/mdcat): terminal markdown renderer with image support

## Design Principles

- **Sources are immutable** — the LLM never modifies raw documents
- **Contradictions are surfaced, not resolved** — the LLM flags disagreements; humans decide
- **Every claim has provenance** — pages cite which source supports each claim
- **The log is append-only** — complete audit trail of all LLM operations
- **Cold-start friendly** — a fresh LLM session can fully orient from `LOBOTOMY.md` alone
- **No special tooling required** — all wiki content is standard markdown, readable everywhere
- **Viewer-agnostic** — works with any markdown renderer, no Obsidian required
