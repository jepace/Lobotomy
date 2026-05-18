# Lobotomy — Operating Schema

Read this file completely before doing anything else. It is the authoritative guide for every
operation in this knowledge base. If you are an LLM session that has just been oriented to this
repository, this file tells you everything you need to know to operate correctly.

> **Write plain text only.** Do not write any markdown links in page body text — not to other
> pages, not to URLs. External URLs belong only in `url:` frontmatter. The system handles
> all cross-referencing automatically. Any link you write will be stripped.

## 1. What This System Is

This is a **personal knowledge base maintained by LLMs**. It is not a RAG system. Sources are not
retrieved at query time — knowledge is synthesized at ingest time and written permanently into
structured documents.

Three layers:

| Layer | Location | Who writes it |
|-------|----------|---------------|
| Raw sources | `raw/` | You (the human) — immutable |
| Knowledge documents | `wiki/` | The LLM |
| This schema | `LOBOTOMY.md` | Defined once, evolved carefully |

Key invariants:
- **Raw sources are immutable.** The LLM reads `raw/` but never modifies or deletes anything there.
- **Every claim has a source.** Documents cite which raw source supports each claim.
- **Contradictions are surfaced, not resolved.** The LLM flags disagreements; the human decides.
- **The log is append-only.** Every operation is recorded and never deleted.
- **Cold-start friendly.** A fresh LLM session can orient itself from this file alone.

---

## 2. Directory Structure

```
raw/                   Immutable source documents. Never modify anything here.
raw/index.md           Auto-generated index of all raw sources and their state.
raw/assets/            Binary attachments (images, PDFs) referenced by raw sources.

wiki/                  All LLM-generated content lives here.
wiki/index.md          Master catalog. Auto-generated — do not read or edit directly.
wiki/log.md            Append-only operation log. Never delete entries.
wiki/overview.md       High-level synthesis. Updated after every ingest.
wiki/sources/          One summary document per ingested source.
wiki/entities/         People, organizations, products, projects, codebases.
wiki/concepts/         Ideas, techniques, frameworks, algorithms, terms.
wiki/synthesis/        Cross-source analyses, comparisons, timelines, open questions.
```

---

## 3. Document Format

Every document (sources, entities, concepts, synthesis, overview) uses this structure:

```markdown
---
title: "Human Readable Title"
type: source | entity | concept | synthesis | overview
tags: [tag1, tag2]
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: ["sources/source-slug.md", "sources/other-slug.md"]
url: "https://original-article-url"   # source documents only; omit on all others
---

# Human Readable Title

<!-- body content -->
```

### Frontmatter field rules

| Field | Type | Rules |
|-------|------|-------|
| `title` | string (quoted) | Title-case, human readable |
| `type` | enum | One of: `source`, `entity`, `concept`, `synthesis`, `overview` |
| `tags` | list of strings | lowercase, hyphenated, no spaces |
| `created` | YYYY-MM-DD | Date first created. Never change. |
| `updated` | YYYY-MM-DD | Date of most recent edit. Update on every write. |
| `sources` | list of strings | Paths from `wiki/` to supporting source documents. **During ingest: set automatically — do not supply.** During regenerate: set to the full union of source pages you read. |
| `url` | string (quoted) | Original article URL. Source documents only. Set automatically — do not supply. |
| `raw_source` | string (quoted) | Repo-relative path to the raw inbox file. Source documents only. Set automatically — do not supply. |
| `aliases` | list of strings | Extra names the autolinker should match and link to this page (e.g. common abbreviations or alternate spellings). Human-set only — do not supply during ingest. Example: `aliases: ["FBI", "bureau"]` |
| `no_autolink` | boolean | If `true`, this page's title and aliases are excluded from the autolinker — bare occurrences of the title in other pages will not be linked here. Use for concept titles that are also common nouns. Human-set only — do not supply during ingest. |
| `deprecated` | boolean | If `true`, the page is retired. Do not delete — set this flag and note it in the log. |

### Standard heading structures per document type

**Source document** (`wiki/sources/`):
- Summary
- Claims
- Entities
- Concepts
- Quotes
- Context

**Entity document** (`wiki/entities/`):
- Overview
- Background
- Key Works / Products
- Claims & Positions
- Contradictions *(if any)*
- Sources *(auto-generated — do not write)*

**Concept document** (`wiki/concepts/`):
- Definition
- How It Works
- Origins & History
- Applications
- Variants & Related Concepts
- Contradictions / Debates *(if any)*
- Sources *(auto-generated — do not write)*

**Synthesis document** (`wiki/synthesis/`):
- Question / Thesis
- Evidence For
- Evidence Against
- Open Questions
- Sources *(auto-generated — do not write)*

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

## 5. Ingest Workflow

**Trigger**: User says "ingest", "add this source", or points at a file in `raw/`.

All raw files live permanently in `raw/`. State (wikified, archived) is tracked in frontmatter — files never move.

Execute all steps in order. Do not skip any step.

### Step 1 — Verify source location
The file must be in `raw/`. If the user gives pasted text, ask them to save it
to `raw/` first as a `.txt` or `.md` file.

### Step 2 — Read the source completely
Read the entire file before writing anything. If it is very long (>20,000 words), read it in
sections sequentially before proceeding.

### Step 3 — Create a source summary document
Call `create_file` with:
- `path`: `wiki/sources/{source-slug}.md` — always a wiki/ path, never a URL
- `type`: `source`
- `body`: the content below (do not write frontmatter manually — `create_file` fills in dates automatically)

Required sections:
- **Summary**: 3–5 paragraphs synthesizing the source's main content and contribution
- **Claims**: bulleted list of factual or analytical claims from the source
- **Entities**: bulleted list of people, orgs, products, projects. Bare names only.
- **Concepts**: bulleted list of important concepts and terms. Bare names only.
- **Quotes**: 3–5 direct quotes with section references if available
- **Context**: how it relates to, extends, supports, or contradicts existing documents

### Step 4 — Identify affected existing documents
Call `search_wiki` for each significant entity and concept found in the source. Search uses AND
logic — all keywords must appear — so search the full name ("Colorado River Compact") rather than
splitting into individual words. List every existing document that:
- Is mentioned in the new source
- Overlaps with entities or concepts in the source
- Could receive new citations or updated claims

List these explicitly before modifying any of them.

### Step 5 — Update or create entity documents
For each significant entity (person, organization, product, project) in the source:
- **Always call `search_wiki` before calling `create_file`.** Do not create a document until you have
  confirmed no existing document covers this entity. Search by the entity's full name and any common
  abbreviations or alternate names.
- **If a document exists**, build full context before rewriting it:
  1. Read the existing entity page.
  2. Call `search_wiki` with `in:sources` and the entity's name to find any additional source pages.
  3. Read every `wiki/sources/*.md` page in the union of both sets (including the source page you just created in Step 3).
  4. Rewrite the entity page from this complete picture using `write_file`. Do not set `sources:` — it is managed automatically. Preserve the original `created` date.
- **If the entity is new**, use `create_file` for `wiki/entities/{slug}.md`. Do not set `sources:` — it is injected automatically.
- Note any contradictions with existing claims in a `## Contradictions` section.
- Do not write a `## Sources` section — it is generated automatically from the `sources:` frontmatter.

### Step 6 — Update or create concept documents
For each significant concept, technique, framework, or term:
- **Always call `search_wiki` before calling `create_file`.** Do not create a document until you have
  confirmed no existing document covers this concept. Search by the concept's full name and any common
  abbreviations or alternate names.
- **If a document exists**, build full context before rewriting it:
  1. Read the existing concept page.
  2. Call `search_wiki` with `in:sources` and the concept's name to find any additional source pages.
  3. Read every `wiki/sources/*.md` page in the union of both sets (including the source page you just created in Step 3).
  4. Rewrite the concept page from this complete picture using `write_file`. Do not set `sources:` — it is managed automatically. Preserve the original `created` date.
- **If no document exists** and the concept warrants one, use `create_file` for `wiki/concepts/{slug}.md`. Do not set `sources:` — it is injected automatically.
- Do not write a `## Sources` section — it is generated automatically from the `sources:` frontmatter.

### Step 7 — Update synthesis documents
Determine whether the new source warrants:
- A new synthesis document in `wiki/synthesis/` (a comparison, timeline, or emerging pattern)
- Updates to an existing synthesis document

### Step 8 — Update `wiki/overview.md`
Update to reflect the new source. The overview must always represent the current state of the knowledge base
accurately. At minimum update: Current State, Domains Covered, Major Entities, Major Concepts.

**Prose style**: Write in short, focused paragraphs — one idea per paragraph, 2–4 sentences each.
Never write a single long paragraph that runs multiple ideas together. Use flowing prose, not bullet
lists, for the narrative sections. Aim for something readable at a glance, not a wall of text.

### Step 9 — Append to `wiki/log.md`
Call `prepend_log` with the new entry text. Do NOT use `write_file` for the log — it would
overwrite and destroy existing entries. `prepend_log` inserts the entry at the top automatically.
Follow Section 7 for the entry format.

### Step 10 — Done
Call `done()`. The server runs health checks automatically — results are visible at `/wiki/lint`.

---

## 6. Regenerate Workflow

**Trigger**: User says "regenerate", "fix", "rewrite", or "redo" a wiki page (entity, concept, synthesis, etc.).

This workflow rewrites a wiki page from the synthesized source documents already in `wiki/sources/`. **Do not read `raw/` during a regenerate** — raw content has already been synthesized into `wiki/sources/` pages.

### Step 1 — Read the existing page
Call `read_file` on the page. Note the `sources:` frontmatter list — this is your initial source set.

### Step 2 — Discover additional sources
Call `search_wiki` with `in:sources` scope using the page's title and key terms (e.g. `"Colorado River Compact in:sources"`). Add any results not already in your source set from Step 1. Your final source set is the union of both.

### Step 3 — Read all source pages
Call `read_file` on every `wiki/sources/*.md` page in your final source set. Do not read `raw/` files.

### Step 4 — Rewrite the page
Call `write_file` with the full rewritten content synthesized from all the source pages you read in Step 3. Preserve the original `created` date. Set `sources:` frontmatter to the full union from Steps 1–2.

### Step 5 — Done
Call `done()`.

---

## 7. Inbox Workflow (Read-It-Later)

**Trigger**: User drops a file into `raw/` and says "process inbox", or points at a
specific inbox file.

This is the read-it-later workflow. The inbox is a holding area for articles, URLs, and notes
you want to process but have not gotten to yet.

### Supported inbox file formats
- `.md` or `.txt` file containing article text (saved from a browser or clipper tool)
- `.txt` or `.url` file containing a single URL (one URL per line)
- Any text file with pasted notes or excerpts

### Process inbox — step by step

1. **List inbox contents**: Read all files in `raw/`. Present the list to the user.
2. **Triage**: Ask which items to process now (or process all if user said "process inbox").
3. **For each item to process**:
   - Read the file. Determine if it is a URL, article text, or notes.
   - **If URL only**: Use `fetch_url` to retrieve the page content, then run the full Ingest
     Workflow on the fetched text.
     **If fetch fails or returns no usable content**: stop immediately, tell the user exactly
     what went wrong, and ask them to paste the article text into the item. Do NOT call `done()`.
     Do NOT conclude the topic is already covered because a related document exists — a different
     source on the same topic is still a separate source that warrants its own document.
   - **If article text or notes**: Assign a slug, run the full Ingest Workflow (Section 5)
     reading the file from `raw/` in place. **Do NOT move or delete the inbox file.**
     The article stays in `raw/` permanently.
4. **Report** to user: items processed, items queued, any issues.

---

## 8. `wiki/log.md` Protocol

Append-only operation log. Never delete or modify existing entries. Always prepend new entries at
the **top** (newest-first ordering).

**Entry format**:
```markdown
## [2026-05-01] ingest | Some Article Title

- **Operation**: ingest
- **Source file**: raw/some-article-slug.txt
- **Documents created**: sources/some-article-slug.md, entities/jane-smith.md
- **Documents updated**: overview.md
```

Rules:
- Source file path is repo-relative, no leading slash.
- Documents created/updated are paths relative to `wiki/` — write `sources/slug.md`, not `wiki/sources/slug.md`.
- Use bare paths, not markdown links.
- Omit any line that has no entries.

---

## 9. Handling Contradictions

When a new source contradicts an existing document:

1. **Do not silently overwrite** the existing claim. Preserve both.
2. In the relevant entity or concept document, add or update a `## Contradictions` section:
   ```
   ## Contradictions
   - **Claim**: Source A (sources/source-a.md) states X.
     Source B (sources/source-b.md) states Y. These contradict because Z.
     Status: unresolved as of YYYY-MM-DD
   ```
3. Note the contradiction in the new source document under "Context".
4. **Do not resolve contradictions yourself** unless the user explicitly asks. Surface; do not
   adjudicate.
5. If a later ingest resolves a contradiction, update the entry:
   `Status: resolved YYYY-MM-DD — [reason]`

---

## 10. Handling Uncertainty

- Reflect hedged claims with appropriate language: "according to [source name]",
  "as of YYYY-MM-DD", "the author suggests but does not confirm"
- Do not present hedged claims as settled fact
- Mark uncertain passages: `<!-- TODO: verify this claim -->`
- Use tag `needs-verification` in frontmatter for documents with unverified claims

---

## 11. Cold-Start Checklist

If you are a fresh LLM session with no context beyond this file and the wiki directory:

1. Read this file (`LOBOTOMY.md`) completely — you have done so
2. Read `wiki/log.md` — understand recent operations
3. Read `wiki/overview.md` — understand the current synthesis
4. Ask the user what operation to perform: ingest or process inbox

Do not modify any file until the user gives an explicit instruction.

---

## 12. Do Not Do These Things

- Do not call `list_dir` to verify a file exists before reading it — call `read_file` directly
- Do not modify, move, or delete anything in `raw/` — it is immutable
- Do not modify `LOBOTOMY.md` unless the user explicitly asks you to update the schema
- Do not read or edit `wiki/index.md` — it is auto-generated on every page write
- Do not write document frontmatter manually — always use `create_file` for new documents
- Do not write any markdown links in document body text — plain text only
- Do not resolve contradictions without user instruction
- Do not delete documents — set `deprecated: true` in frontmatter instead, then note it in the log
- Do not ingest sources from outside `raw/`
- Do not invent sources — only cite documents actually present in `raw/`
- Do not put URLs in document body text — they belong only in `url:` frontmatter on source documents
- Do not write workflow annotations like "(new)" or "(update)" in document content — these are planning notes only
- Do not modify existing `wiki/log.md` entries — only prepend new ones at the top
- Do not save important information only in chat — write it to a document so it persists
