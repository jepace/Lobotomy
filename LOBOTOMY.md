# Lobotomy — Operating Schema

Read this file completely before doing anything else. It is the authoritative guide for every
operation in this wiki. If you are an LLM session that has just been pointed at this repository,
this file tells you everything you need to know to operate correctly.

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
| This schema | `LOBOTOMY.md` | Defined once, evolved carefully |

Key invariants:
- **Raw sources are immutable.** The LLM reads `raw/` but never modifies or deletes anything there.
- **Every wiki claim has a source.** Pages cite which raw source supports each claim.
- **Contradictions are surfaced, not resolved.** The LLM flags disagreements; the human decides.
- **The log is append-only.** Every operation is recorded and never deleted.
- **Cross-links are automatic.** Write bare entity/concept names in page body text — the autolinker adds wiki links after every page write. Never write internal links manually.
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
wiki/sources/          One summary page per ingested source document.
wiki/entities/         People, organizations, products, projects, codebases.
wiki/concepts/         Ideas, techniques, frameworks, algorithms, terms.
wiki/synthesis/        Cross-source analyses, comparisons, timelines, open questions.

```

---

## 3. Page Format

Every wiki page (sources, entities, concepts, synthesis, overview) uses this structure:

```markdown
---
title: "Human Readable Title"
type: source | entity | concept | synthesis | overview
tags: [tag1, tag2]
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: ["sources/source-slug.md", "sources/other-slug.md"]
url: "https://original-article-url"   # source pages only; omit on entity/concept/synthesis pages
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
| `sources` | list of strings | Relative paths from `wiki/` to supporting source pages |
| `url` | string (quoted) | Original article URL. Source pages only. Set automatically — do not supply. |
| `raw_source` | string (quoted) | Repo-relative path to the raw inbox file. Source pages only. Set automatically — do not supply. |

### Standard heading structures per page type

**Source page** (`wiki/sources/`):
- Summary
- Claims
- Entities
- Concepts
- Quotes
- Wiki Context

**Entity page** (`wiki/entities/`):
- Overview
- Background
- Key Works / Products
- Claims & Positions
- Contradictions *(if any)*
- Sources *(auto-generated — do not write)*

**Concept page** (`wiki/concepts/`):
- Definition
- How It Works
- Origins & History
- Applications
- Variants & Related Concepts
- Contradictions / Debates *(if any)*
- Sources *(auto-generated — do not write)*

**Synthesis page** (`wiki/synthesis/`):
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

## 5. Cross-References

Use **standard relative markdown links** for all internal links. Do not use `[[wikilinks]]`.

- From inside `wiki/sources/`: `[Yann LeCun](../entities/yann-lecun.md)`
- From inside `wiki/entities/`: `[Attention Mechanism](../concepts/attention-mechanism.md)`
- From `wiki/overview.md`: `[Attention Mechanism](concepts/attention-mechanism.md)`

When you mention an entity or concept that has (or should have) its own page, always link it with a relative wiki link — never with an external URL. Entity links always point to `wiki/entities/*.md`, never to `https://...`.

External URLs appear **only** in two places:
1. The `url:` frontmatter field on source pages (set automatically from the inbox item).
2. The auto-generated `## Sources` section (rendered from that frontmatter). Never write external links into page body text.

---

## 6. Ingest Workflow

**Trigger**: User says "ingest", "add this source", or points at a file in `raw/`.

All raw files live permanently in `raw/`. State (wikified, archived) is tracked in frontmatter — files never move.

Execute all steps in order. Do not skip any step.

### Step 1 — Verify source location
The file must be in `raw/`. If the user gives pasted text, ask them to save it
to `raw/` first as a `.txt` or `.md` file.

### Step 2 — Read the source completely
Read the entire file before writing anything. If it is very long (>20,000 words), read it in
sections sequentially before proceeding.

### Step 3 — Create a source summary page
Call `create_page` with:
- `path`: `wiki/sources/{source-slug}.md` — always a wiki/ path, never a URL
- `type`: `source`
- `body`: the content below (do not write frontmatter manually — `create_page` fills in dates automatically)

Required sections:
- **Summary**: 3–5 paragraphs synthesizing the source's main content and contribution
- **Claims**: bulleted list of factual or analytical claims from the source
- **Entities**: bulleted list of people, orgs, products, projects (each linked to its wiki page). Plain links only — no annotations like "(new)" or "(update)".
- **Concepts**: bulleted list of important concepts and terms (each linked to its wiki page). Plain links only — no annotations like "(new)" or "(update)".
- **Quotes**: 3–5 direct quotes with section references if available
- **Wiki Context**: how it relates to, extends, supports, or contradicts existing pages

### Step 4 — Identify affected existing pages
Call `search_wiki` for each significant entity and concept found in the source. Search uses AND
logic — all keywords must appear — so search the full name ("Colorado River Compact") rather than
splitting into individual words. List every existing page that:
- Is mentioned in the new source
- Overlaps with entities or concepts in the source
- Could receive new citations or updated claims

List these explicitly before modifying any of them.

### Step 5 — Update or create entity pages
For each significant entity (person, organization, product, project) in the source:
- **Always call `search_wiki` before calling `create_page`.** Do not create a page until you have
  confirmed no existing page covers this entity. Search by the entity's full name and any common
  abbreviations or alternate names.
- If a page exists and is correct, update it with `write_file`. When updating, read the existing
  page first and preserve its `sources:` frontmatter list, appending the new source if not already present.
- If the entity is new and significant, use `create_page` to create `wiki/entities/{slug}.md`.
  Pass `sources: ["sources/{source-slug}.md"]` so the Sources section is populated automatically.
- Note any contradictions with existing claims in a `## Contradictions` section.
- Do not write a `## Sources` section — it is generated automatically from the `sources:` frontmatter.

### Step 6 — Update or create concept pages
For each significant concept, technique, framework, or term:
- **Always call `search_wiki` before calling `create_page`.** Do not create a page until you have
  confirmed no existing page covers this concept. Search by the concept's full name and any common
  abbreviations or alternate names.
- If a page exists and is correct, update it with `write_file`. Preserve existing `sources:` and
  append the new source if not already present.
- If no page exists and the concept warrants one, use `create_page` for `wiki/concepts/{slug}.md`.
  Pass `sources: ["sources/{source-slug}.md"]` so the Sources section is populated automatically.
- Do not write a `## Sources` section — it is generated automatically from the `sources:` frontmatter.

### Step 7 — Update synthesis pages
Determine whether the new source warrants:
- A new synthesis page in `wiki/synthesis/` (a comparison, timeline, or emerging pattern)
- Updates to an existing synthesis page

### Step 8 — Update `wiki/overview.md`
Update to reflect the new source. The overview must always represent the current state of the wiki
accurately. At minimum update: Current State, Domains Covered, Major Entities, Major Concepts.

**Prose style**: Write in short, focused paragraphs — one idea per paragraph, 2–4 sentences each.
Never write a single long paragraph that runs multiple ideas together. Use flowing prose, not bullet
lists, for the narrative sections. Aim for something readable at a glance, not a wall of text.

### Step 9 — Append to `wiki/log.md`
Call `prepend_log` with the new entry text. Do NOT use `write_file` for the log — it would
overwrite and destroy existing entries. `prepend_log` inserts the entry at the top automatically.
Follow Section 8 for the entry format.

### Step 10 — Done
Call `done()`. The server runs health checks automatically (broken links, missing frontmatter,
index coverage) — results are visible at `/wiki/lint`.

---

## 7. Inbox Workflow (Read-It-Later)

**Trigger**: User drops a file into `raw/` and says "process inbox", or points at a
specific inbox file.

This is the Pocket-replacement workflow. The inbox is a holding area for articles, URLs, and notes
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
     Do NOT conclude the topic is already covered because a related page exists — a different
     source on the same topic is still a separate source that warrants its own page.
   - **If article text or notes**: Assign a slug, run the full Ingest Workflow (Section 6)
     reading the file from `raw/` in place. **Do NOT move or delete the inbox file.**
     The article stays in `raw/` permanently. The UI will show a "Wikified ✓" badge
     automatically once ingestion completes.
4. **Report** to user: items processed, items queued, any issues.

---

## 8. `wiki/log.md` Protocol

Append-only operation log. Never delete or modify existing entries. Always prepend new entries at
the **top** (newest-first ordering).

**Entry format**:
```markdown
## [2026-05-01] ingest | Some Article Title

- **Operation**: ingest
- **Target**: [raw/some-article-slug.txt](../raw/some-article-slug.txt)
- **Pages created**: [Some Article Title](sources/some-article-slug.md), [Jane Smith](entities/jane-smith.md)
- **Pages updated**: [Overview](overview.md)
```

Rules:
- **Target** must be a markdown link to the raw source file. Path is relative to `wiki/` so prefix with `../` — e.g. `[raw/foo.txt](../raw/foo.txt)`.
- Every entry in **Pages created** and **Pages updated** must be a markdown link — `[Title](relative/path.md)` — never plain text or a bare path.
- Paths in Pages created/updated are relative to `wiki/` — write `sources/slug.md`, not `wiki/sources/slug.md`.
- Use the actual page title as the link text, not the filename.
- Omit the **Notes** line entirely.

---

## 9. Handling Contradictions

When a new source contradicts an existing wiki page:

1. **Do not silently overwrite** the existing claim. Preserve both.
2. In the relevant entity or concept page, add or update a `## Contradictions` section:
   ```markdown
   ## Contradictions
   - **Claim**: [Source A](../sources/source-a.md) states X.
     [Source B](../sources/source-b.md) states Y. These contradict because Z.
     *Status: unresolved as of YYYY-MM-DD*
   ```
3. Note the contradiction in the new source page under "Wiki Context".
4. **Do not resolve contradictions yourself** unless the user explicitly asks. Surface; do not
   adjudicate.
5. If a later ingest resolves a contradiction, update the entry:
   `*Status: resolved YYYY-MM-DD — [reason]*`

---

## 10. Handling Uncertainty

- Reflect hedged claims with appropriate language: "according to [Source](path)",
  "as of YYYY-MM-DD", "the author suggests but does not confirm"
- Do not present hedged claims as settled fact
- Mark uncertain passages: `<!-- TODO: verify this claim -->`
- Use tag `needs-verification` in frontmatter for pages with unverified claims
- The wiki reflects what sources say. It is not a ground-truth oracle. Answers should reflect this.

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
- Do not write wiki page frontmatter manually — always use `create_page` for new pages
- Do not write internal wiki links manually — write bare text, cross-links are added automatically by the autolinker
- Do not resolve contradictions without user instruction
- Do not delete wiki pages — set `deprecated: true` in frontmatter instead, then note it in the log
- Do not ingest sources from outside `raw/`
- Do not invent sources — only cite documents actually present in `raw/`
- Do not use `[[wikilink]]` syntax — use standard relative markdown links
- Do not link entities or people to external URLs in page body text — always create a `wiki/entities/*.md` page and link to that instead
- Do not put external URLs in page body text at all — they belong only in `url:` frontmatter on source pages
- Do not write workflow annotations like "(new)" or "(update)" in page content — these are planning notes only
- Do not link to a page without first confirming the linked file exists and is about the correct subject
- Do not modify existing `wiki/log.md` entries — only prepend new ones at the top
- Do not save important information only in chat — write it to a wiki page so it persists
