# Lobotomy — Operating Schema

Read this file completely before doing anything else. It is the authoritative guide for every
operation in this wiki. If you are an LLM session that has just been pointed at this repository,
this file tells you everything you need to know to operate correctly.

## Git Branch Policy

Always push to **main**. Do not use feature branches unless the user explicitly requests one.

---

## 1. What This Wiki Is

This is a **personal knowledge base maintained by LLMs**. It is not a RAG system. Sources are not
retrieved at query time — knowledge is synthesized at ingest time and written permanently into wiki
pages. The wiki is a **compounding artifact**: every new source enriches the existing pages,
cross-references are maintained, and contradictions are flagged as they appear.

Three layers:

| Layer | Location | Who writes it |
|-------|----------|---------------|
| Raw sources | `raw/` | You (the human) — immutable |
| Wiki pages | `wiki/` | The LLM |
| This schema | `CLAUDE.md` | Defined once, evolved carefully |

Key invariants:
- **Raw sources are immutable.** The LLM reads `raw/` but never modifies or deletes anything there.
- **Every wiki claim has a source.** Pages cite which raw source supports each claim.
- **Contradictions are surfaced, not resolved.** The LLM flags disagreements; the human decides.
- **The log is append-only.** Every operation is recorded and never deleted.
- **Cold-start friendly.** A fresh LLM session can orient itself from this file alone.

---

## 2. Directory Structure

```
raw/                   Immutable source documents. Never modify anything here.
raw/inbox/             Drop articles, URLs, or notes here for later processing.
raw/assets/            Binary attachments (images, PDFs) referenced by raw sources.

wiki/                  All LLM-generated content lives here.
wiki/index.md          Master catalog. Every wiki page listed here exactly once.
wiki/log.md            Append-only operation log. Never delete entries.
wiki/overview.md       High-level synthesis. Updated after every ingest.
wiki/reading-list.md   Tracks read-it-later queue from raw/inbox/ to ingested.
wiki/tasks.md          Task manager. All tasks with priority, context, due date.
wiki/sources/          One summary page per ingested source document.
wiki/entities/         People, organizations, products, projects, codebases.
wiki/concepts/         Ideas, techniques, frameworks, algorithms, terms.
wiki/synthesis/        Cross-source analyses, comparisons, timelines, open questions.

tools/                 Helper scripts. Do not modify unless explicitly asked.
tools/search.py        Keyword search across wiki pages.
tools/tasks.py         Task filter/query CLI.
```

---

## 3. Page Format

Every wiki page (sources, entities, concepts, synthesis, overview) uses this structure:

```markdown
---
title: "Human Readable Title"
type: source | entity | concept | synthesis | overview | tasks | reading-list
tags: [tag1, tag2]
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: ["sources/source-slug.md", "sources/other-slug.md"]
---

# Human Readable Title

<!-- body content -->
```

### Frontmatter field rules

| Field | Type | Rules |
|-------|------|-------|
| `title` | string (quoted) | Title-case, human readable |
| `type` | enum | One of: `source`, `entity`, `concept`, `synthesis`, `overview`, `tasks`, `reading-list` |
| `tags` | list of strings | lowercase, hyphenated, no spaces |
| `created` | YYYY-MM-DD | Date first created. Never change. |
| `updated` | YYYY-MM-DD | Date of most recent edit. Update on every write. |
| `sources` | list of strings | Relative paths from `wiki/` to supporting source pages |

### Standard heading structures per page type

**Source page** (`wiki/sources/`):
- Summary
- Key Claims
- Key Entities
- Key Concepts
- Notable Quotes
- Limitations & Caveats
- Relation to Existing Wiki

**Entity page** (`wiki/entities/`):
- Overview
- Background
- Key Works / Products
- Claims & Positions
- Contradictions *(if any)*
- Sources

**Concept page** (`wiki/concepts/`):
- Definition
- How It Works
- Origins & History
- Applications
- Variants & Related Concepts
- Contradictions / Debates *(if any)*
- Sources

**Synthesis page** (`wiki/synthesis/`):
- Question / Thesis
- Evidence For
- Evidence Against
- Open Questions
- Sources

---

## 4. Naming Conventions

- File names use `lowercase-hyphenated-slugs.md` — all lowercase, words separated by hyphens, no
  spaces, no special characters except hyphens.
- Examples: `attention-mechanism.md`, `yann-lecun.md`, `openai-2023-gpt4-technical-report.md`
- Source slugs encode author/org and year when available:
  `{author-or-org}-{year}-{short-title}.md`
- Never use: uppercase, underscores, dots (other than `.md`), parentheses, slashes in filenames.
- Page title is the Title Case human-readable version of the slug.

---

## 5. Cross-References

Use **standard relative markdown links** for all internal links. Do not use `[[wikilinks]]`.

- From inside `wiki/sources/`: `[Yann LeCun](../entities/yann-lecun.md)`
- From inside `wiki/entities/`: `[Attention Mechanism](../concepts/attention-mechanism.md)`
- From `wiki/index.md` or `wiki/overview.md`: `[Attention Mechanism](concepts/attention-mechanism.md)`

When you mention an entity or concept that has (or should have) its own page, always link it.
After creating or updating a page, ask: "What other pages should link to this one?" and add
inbound links from those pages too (bidirectional linking strengthens the graph).

For external URLs use standard markdown: `[Title](https://example.com)`.

---

## 6. Ingest Workflow

**Trigger**: User says "ingest", "add this source", or points at a file in `raw/`.

Files in `raw/inbox/` must first be moved to `raw/` via the Inbox Workflow (Section 9).
Do not ingest directly from `raw/inbox/`.

Execute all steps in order. Do not skip any step.

### Step 1 — Verify source location
The file must be in `raw/` (not `raw/inbox/`). If the user gives pasted text, ask them to save it
to `raw/` first as a `.txt` or `.md` file.

### Step 2 — Read the source completely
Read the entire file before writing anything. If it is very long (>20,000 words), read it in
sections sequentially before proceeding.

### Step 3 — Create a source summary page
Create `wiki/sources/{source-slug}.md` using the standard page format with `type: source`.

Required sections:
- **Summary**: 3–5 paragraphs synthesizing the source's main content and contribution
- **Key Claims**: bulleted list of the most important factual or analytical claims
- **Key Entities**: bulleted list of significant people, orgs, products, projects (each linked)
- **Key Concepts**: bulleted list of important concepts and terms (each linked)
- **Notable Quotes**: 3–5 direct quotes with section references if available
- **Limitations & Caveats**: what this source does not cover; uncertainties it acknowledges
- **Relation to Existing Wiki**: how it relates to, extends, supports, or contradicts existing pages

### Step 4 — Identify affected existing pages
Read `wiki/index.md`. List every existing page that:
- Is mentioned in the new source
- Overlaps with entities or concepts in the source
- Could receive new citations or updated claims

List these explicitly before modifying any of them.

### Step 5 — Update or create entity pages
For each significant entity (person, organization, product, project) in the source:
- If a page exists in `wiki/entities/`, add new information and update claims.
- If the entity is new and significant, create `wiki/entities/{slug}.md`.
- Note any contradictions with existing claims in a `## Contradictions` section.

### Step 6 — Update or create concept pages
For each significant concept, technique, framework, or term:
- If a page exists in `wiki/concepts/`, add new information and cross-link the new source.
- If no page exists and the concept warrants one, create `wiki/concepts/{slug}.md`.

### Step 7 — Update synthesis pages
Determine whether the new source warrants:
- A new synthesis page in `wiki/synthesis/` (a comparison, timeline, or emerging pattern)
- Updates to an existing synthesis page

### Step 8 — Update `wiki/overview.md`
Update to reflect the new source. The overview must always represent the current state of the wiki
accurately. At minimum update: Current State, Domains Covered, Major Entities, Major Concepts.

### Step 9 — Update `wiki/index.md`
Add entries for all new pages. Update the `_Last updated:` line. Follow Section 11 protocol.

### Step 10 — Append to `wiki/log.md`
Prepend a new entry at the top. Follow Section 12 protocol.

### Step 11 — Self-check
Verify each item before reporting done:
- [ ] All new pages have complete YAML frontmatter (all required fields present)
- [ ] All new pages list the source page in their `sources:` frontmatter field
- [ ] The source page links to all entity and concept pages it spawned
- [ ] `wiki/index.md` has entries for every new page
- [ ] `wiki/log.md` has been updated
- [ ] No internal link points to a non-existent file
- [ ] `wiki/overview.md` reflects the new state

Report self-check results to the user. Note anything that could not be completed and why.

---

## 7. Query Workflow

**Trigger**: User asks a question, or says "query the wiki about X".

### Step 1 — Read `wiki/overview.md`
Get a high-level orientation. Note what domains and entities the wiki covers.

### Step 2 — Read `wiki/index.md`
Scan all sections. Identify every page relevant to the question. List them explicitly.

### Step 3 — Read relevant pages
Read every page identified. Follow links one level deep if relevant cross-references are found.
Do not follow links more than two levels deep unless specifically required.

### Step 4 — Synthesize an answer
Write a structured answer. For each claim:
- Cite the wiki page it comes from
- State well-supported multi-source claims confidently
- Attribute single-source claims to their source
- Flag contradictions explicitly
- State clearly where the wiki has no coverage ("the wiki does not cover X")

### Step 5 — Optionally file as a synthesis page
If the question and answer represent non-trivial synthesis worth preserving, offer to create a page
in `wiki/synthesis/`. If the user agrees, run Ingest Workflow Steps 8–10 to update index and log.

---

## 8. Lint Workflow

**Trigger**: User says "lint the wiki", "health check", or "find problems".

Run all checks and produce a structured report.

**Check 1 — Broken links**: Scan all `wiki/**/*.md` files for markdown links `[text](path)`.
Verify each relative path exists on disk. List every broken link with the file it appears in.

**Check 2 — Orphan pages**: List every file in `wiki/sources/`, `wiki/entities/`,
`wiki/concepts/`, `wiki/synthesis/` that does not appear in `wiki/index.md`.

**Check 3 — Missing frontmatter**: Check every wiki page for required fields (title, type, tags,
created, updated, sources). List any page missing any field.

**Check 4 — Stale pages**: Flag pages whose `updated` date is more than 90 days older than the
most recently ingested source (check `wiki/log.md` for the latest ingest date).

**Check 5 — Contradiction audit**: Scan all pages for `## Contradictions` sections. Summarize
every flagged contradiction and whether it remains unresolved.

**Check 6 — Underlinked pages**: Find pages with fewer than 2 incoming links from other wiki
pages. These may be disconnected from the main knowledge graph.

**Check 7 — Coverage gaps**: Review `wiki/overview.md` and synthesis pages. Identify topic areas
mentioned but lacking source documents. Suggest sources to look for.

**Lint report format**:
```markdown
# Wiki Lint Report — YYYY-MM-DD

## Summary
- Total pages: N
- Broken links: N
- Orphan pages: N
- Missing frontmatter: N
- Stale pages (>90 days): N
- Active contradictions: N
- Underlinked pages: N

## Broken Links
[list of broken links with file locations]

## Orphan Pages
[list]

## Missing Frontmatter
[list]

## Stale Pages
[list]

## Active Contradictions
[list]

## Underlinked Pages
[list]

## Coverage Gaps & Suggested Sources
[list of suggested topics and sources to find]
```

After the report, ask the user if they want to fix any identified issues.

---

## 9. Inbox Workflow (Read-It-Later)

**Trigger**: User drops a file into `raw/inbox/` and says "process inbox", or points at a
specific inbox file.

This is the Pocket-replacement workflow. The inbox is a holding area for articles, URLs, and notes
you want to process but have not gotten to yet.

### Supported inbox file formats
- `.md` or `.txt` file containing article text (saved from a browser or clipper tool)
- `.txt` or `.url` file containing a single URL (one URL per line)
- Any text file with pasted notes or excerpts

### Process inbox — step by step

1. **List inbox contents**: Read all files in `raw/inbox/`. Present the list to the user.
2. **Triage**: Ask which items to process now (or process all if user said "process inbox").
3. **For each item to process**:
   - Read the file. Determine if it is a URL, article text, or notes.
   - **If URL only**: Use `fetch_url` to retrieve the page content, then run the full Ingest
     Workflow on the fetched text. If fetch fails, note the URL and add to `wiki/reading-list.md`
     with status **Queued**.
   - **If article text or notes**: Assign a slug, move the file from `raw/inbox/` to `raw/`
     (rename: `raw/inbox/article.md` → `raw/article-slug.md`), then run the full Ingest Workflow
     (Section 6). After ingesting, add or update the entry in `wiki/reading-list.md` with status
     **Ingested**.
4. **Update `wiki/reading-list.md`** after each item is processed.
5. **Report** to user: items processed, items queued, any issues.

### `wiki/reading-list.md` table format

```markdown
| Title | File / URL | Added | Status | Notes |
|-------|-----------|-------|--------|-------|
| Article Title | [raw/article-slug.md](../raw/article-slug.md) | YYYY-MM-DD | Ingested | Brief note |
| Unread Article | [URL](https://example.com) | YYYY-MM-DD | Queued | |
```

Status progression: **Queued** → **Reading** → **Read** → **Ingested**

To update an item's status, edit the Status cell in the table.

---

## 10. Task Management Workflow

**Trigger**: User says "add a task", "show tasks", "complete a task", or asks about the task list.

Tasks live in `wiki/tasks.md`. All task management is done by editing that file.

### Task format

```markdown
- [ ] Task description #p:high #due:2026-05-01 #start:2026-04-25 #ctx:work #proj:project-name #s:next #rep:1w #len:30m #star
  Notes: optional notes on indented line
    - [ ] Subtask (double-indented)
    - [ ] Another subtask
```

All tags are optional. Only include what is relevant.

### Tag reference

| Tag | Example values | Meaning |
|-----|----------------|---------|
| `#p:` | `top`, `high`, `medium`, `low` | Priority (omit for none) |
| `#due:` | `2026-05-01` | Due date in YYYY-MM-DD format |
| `#start:` | `2026-04-25` | Hide task until this date |
| `#ctx:` | `home`, `work`, `computer`, `errands`, `calls` | GTD context |
| `#proj:` | `any-project-slug` | Project |
| `#s:` | `next`, `waiting`, `someday`, `hold` | Status (omit = active) |
| `#rep:` | `1d`, `7d`, `2w`, `1m`, `3m`, `1y` | Fixed recurrence period |
| `#rep:` (relative) | `7d+`, `1m+` | Recur N days/weeks/months after *completion* |
| `#len:` | `30m`, `2h` | Estimated duration |
| `#star` | (no value) | Starred / flagged |
| `#done:` | `2026-04-27` | Added automatically when marking complete |

### Recurrence behaviour

When a recurring task (`#rep:`) is marked complete:
- A new instance is automatically created immediately below it with the next due date.
- **Fixed** (`#rep:1w`): next due = current due date + period. Use for bills, meetings, scheduled events.
- **Relative** (`#rep:7d+`): next due = completion date + period. Use for habits, maintenance tasks.

### Task sections in `wiki/tasks.md`

```markdown
## Inbox
(uncategorized quick-capture tasks go here)

## [Project Name]
(add new sections as projects are created)
```

### Task operations

**Add a task**: Append to the appropriate project section. Quick-capture goes to Inbox. If no
matching section exists, create it.

**Complete a task**: Change `- [ ]` to `- [x]` and append `#done:YYYY-MM-DD`.

**Archive completed tasks**: Move all `- [x]` lines (and their indented subtasks/notes) from
`wiki/tasks.md` to `wiki/tasks-archive.md`, grouped by completion month. Create the archive file
if it does not exist. Update `wiki/log.md`.

**Prioritize**: Ask the LLM to review all open tasks and suggest an ordering by due date and
priority. The LLM presents the ordered list but does not modify the file unless asked.

**CLI filter** (no LLM needed):
```sh
python tools/tasks.py                    # all open tasks, sorted by due/priority
python tools/tasks.py --due-today        # due today or overdue
python tools/tasks.py --priority high    # high and top priority tasks
python tools/tasks.py --context work     # tasks with @work context
python tools/tasks.py --overdue          # past due date
```

---

## 11. `wiki/index.md` Protocol

The master catalog. Every wiki page appears here exactly once, under the correct section.

**Entry format**:
```markdown
- [Page Title](relative/path/to/page.md) — One-sentence description. *(updated: YYYY-MM-DD)*
```

**Section headers** (in this order):
```markdown
## Sources
## Entities
## Concepts
## Synthesis
```

**Rules**:
- Entries within each section are sorted **alphabetically by display title**.
- Insert new entries in alphabetical order — do not append to the end.
- Update the `_Last updated: YYYY-MM-DD_` line at the top on every modification.
- The files `wiki/overview.md`, `wiki/log.md`, `wiki/index.md`, `wiki/reading-list.md`, and
  `wiki/tasks.md` are **not** listed in the index (they are operational files, not knowledge pages).

---

## 12. `wiki/log.md` Protocol

Append-only operation log. Never delete or modify existing entries. Always prepend new entries at
the **top** (newest-first ordering).

**Entry format**:
```markdown
## [YYYY-MM-DD] {operation} | {title}

- **Operation**: ingest | query | lint | inbox | task | manual-edit
- **Target**: {filename, question text, "raw/inbox/", or "wiki/tasks.md"}
- **Pages created**: [Page Title](path.md), ...
- **Pages updated**: [Page Title](path.md), ...
- **Notes**: {brief description; any contradictions found; anything notable}
```

---

## 13. Handling Contradictions

When a new source contradicts an existing wiki page:

1. **Do not silently overwrite** the existing claim. Preserve both.
2. In the relevant entity or concept page, add or update a `## Contradictions` section:
   ```markdown
   ## Contradictions
   - **Claim**: [Source A](../sources/source-a.md) states X.
     [Source B](../sources/source-b.md) states Y. These contradict because Z.
     *Status: unresolved as of YYYY-MM-DD*
   ```
3. Note the contradiction in the new source page under "Relation to Existing Wiki".
4. Note it in `wiki/log.md` under Notes for the ingest entry.
5. **Do not resolve contradictions yourself** unless the user explicitly asks. Surface; do not
   adjudicate.
6. If a later ingest resolves a contradiction, update the entry:
   `*Status: resolved YYYY-MM-DD — [reason]*`

---

## 14. Handling Uncertainty

- Reflect hedged claims with appropriate language: "according to [Source](path)",
  "as of YYYY-MM-DD", "the author suggests but does not confirm"
- Do not present hedged claims as settled fact
- Mark uncertain passages: `<!-- TODO: verify this claim -->`
- Use tag `needs-verification` in frontmatter for pages with unverified claims
- The wiki reflects what sources say. It is not a ground-truth oracle. Answers should reflect this.

---

## 15. Cold-Start Checklist

If you are a fresh LLM session with no context beyond this file and the wiki directory:

1. Read this file (`CLAUDE.md`) completely — you have done so
2. Read `wiki/index.md` — understand what knowledge currently exists
3. Read the top 10 entries of `wiki/log.md` — understand recent operations
4. Read `wiki/overview.md` — understand the current synthesis
5. Ask the user what operation to perform: ingest / query / lint / process inbox / manage tasks

Do not modify any file until the user gives an explicit instruction.

---

## 16. Do Not Do These Things

- Do not modify, move, or delete anything in `raw/` — it is immutable
- Do not modify `CLAUDE.md` unless the user explicitly asks you to update the schema
- Do not resolve contradictions without user instruction
- Do not delete wiki pages — set `deprecated: true` in frontmatter instead, then note it in the log
- Do not ingest sources from outside `raw/`
- Do not invent sources — only cite documents actually present in `raw/`
- Do not use `[[wikilink]]` syntax — use standard relative markdown links
- Do not skip the self-check step (Step 11) after every ingest
- Do not break alphabetical ordering in `wiki/index.md`
- Do not modify existing `wiki/log.md` entries — only prepend new ones at the top
- Do not save important information only in chat — write it to a wiki page so it persists
