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

## Usage

### Start a session

Open Claude Code in this directory. The LLM will read `CLAUDE.md` and orient itself automatically.

### Ingest a source

1. Save the document to `raw/` as a `.txt` or `.md` file
2. Say: `Ingest raw/your-document.md`

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

### Browse as a website

The wiki is served as a static site via [MkDocs](https://www.mkdocs.org/) with the
[Material theme](https://squidfunk.github.io/mkdocs-material/). Install once, build after
ingesting new sources, then serve with nginx.

**Install (FreeBSD):**

```sh
pkg install py311-mkdocs py311-mkdocs-material
```

**Build the static site:**

```sh
sh tools/build.sh
# Output: site/  (gitignored)
```

**Quick local preview:**

```sh
sh tools/build.sh --serve
# Opens at http://127.0.0.1:8000
```

**Deploy on a VPS jail:**

1. Build the site: `sh tools/build.sh`
2. Copy `site/` to your nginx document root:

```sh
cp -r site/ /usr/local/www/wiki/
```

3. Configure nginx to serve it (example — adjust paths and server_name for your setup):

```nginx
server {
    listen 80;
    server_name wiki.example.com;
    root /usr/local/www/wiki;
    index index.html;
    location / {
        try_files $uri $uri/ $uri.html =404;
    }
}
```

Rebuild and redeploy the site after each session where you ingest new sources.

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
  search.py             Keyword search CLI
  tasks.py              Task filter CLI
  build.sh              MkDocs build / serve wrapper
mkdocs.yml              MkDocs configuration
CLAUDE.md               LLM operating instructions (the schema)
```

## On FreeBSD

All CLI tools require only Python 3 (no additional packages):

```sh
pkg install python3
python3 tools/search.py "keyword"
python3 tools/tasks.py --due-today
```

For the web front end:

```sh
pkg install py311-mkdocs py311-mkdocs-material
sh tools/build.sh        # build once
sh tools/build.sh --serve  # or run a local dev server
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
