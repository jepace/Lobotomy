# Lobotomy — Operating Schema

Read this file completely before doing anything else. It is the authoritative guide for every
operation in this knowledge base. If you are an LLM session that has just been oriented to this
repository, this file tells you everything you need to know to operate correctly.

> **Write plain text only.** Do not write any markdown links in page body text — not to other
> pages, not to URLs. External URLs belong only in `url:` frontmatter. The system handles
> all cross-referencing automatically. Any link you write will be stripped.

> **Always put a blank line before a bulleted or numbered list.** A list that follows a
> paragraph with no blank line between them does not render as a list — it renders as part
> of the paragraph. This applies to every `-` or `1.` list you write, in every section of
> every document. Get in the habit of adding the blank line as you type the list, not as
> an afterthought.

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
- **Cold-start friendly.** A fresh LLM session can orient itself from this file alone.

---

## 2. Directory Structure

```
raw/                   Immutable source documents. Never modify anything here.
raw/index.md           Auto-generated index of all raw sources and their state.
raw/assets/            Binary attachments (images, PDFs) referenced by raw sources.

wiki/                  All LLM-generated content lives here.
wiki/index.md          Master catalog of every page, for humans browsing the wiki.
                       Auto-generated on every write — never edit or read it. It is far
                       too large for context; use `lookup_titles` to ask what exists.
wiki/sources/          One summary document per ingested source.
wiki/entities/         Concrete things: people, organizations, products, projects,
                       codebases, places, events, works, vehicles, named phenomena —
                       anything with a proper name that isn't an idea.
wiki/concepts/         Ideas, techniques, frameworks, algorithms, terms.
wiki/synthesis/        Cross-source analyses, comparisons, timelines, open questions.
```

---

## 3. Document Format

Every document (sources, entities, concepts, synthesis) uses this structure:

```markdown
---
title: "Human Readable Title"
type: source | entity | concept | synthesis
tags: [tag1, tag2]
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: ["sources/source-slug.md", "sources/other-slug.md"]
url: "https://original-article-url"   # source documents only; omit on all others
---

# Human Readable Title

<!-- body content -->
```

**Every list needs a blank line above it.** Each required section below (Summary, Claims,
Entities, etc.) typically opens with a paragraph and then a bulleted list — put a blank
line between them. Example:

```markdown
## Claims

The article reports several developments in the ongoing negotiations.

- First claim, bare text, no link
- Second claim
```

Not:

```markdown
## Claims

The article reports several developments in the ongoing negotiations.
- First claim, bare text, no link
- Second claim
```

The second form renders as a single run-on paragraph with literal `-` characters — no
bullets, no line breaks. This is the single most common formatting mistake — check for it
before every `create_file` or `update_file` call.

### Frontmatter field rules

| Field | Type | Rules |
|-------|------|-------|
| `title` | string (quoted) | Title-case, human readable |
| `type` | enum | One of: `source`, `entity`, `concept`, `synthesis` |
| `tags` | list of strings | lowercase, hyphenated, no spaces. Prefer tags from the list in the orientation message; introduce new tags only when no existing tag fits. |
| `created` | YYYY-MM-DD | Date first created. **System-managed — never supply or modify.** |
| `updated` | YYYY-MM-DD | Date of most recent edit. Update on every write. |
| `sources` | list of strings | Paths from `wiki/` to supporting source documents. **System-managed — never supply or modify.** During an ingest you do not need to read these — the page body already synthesizes them. They are the reading list for the Regenerate Workflow (Section 6) only. |
| `url` | string (quoted) | Original article URL. Source documents only. **System-managed — never supply or modify.** |
| `raw_source` | string (quoted) | Repo-relative path to the raw inbox file. Source documents only. **System-managed — never supply or modify.** |
| `aliases` | list of strings | Extra names the autolinker should match and link to this page (e.g. common abbreviations or alternate spellings). Human-set only — do not supply during ingest. Example: `aliases: ["FBI", "bureau"]` |
| `no_autolink` | boolean | If `true`, this page's title and aliases are excluded from the autolinker — bare occurrences of the title in other pages will not be linked here. Use for concept titles that are also common nouns. Human-set only — do not supply during ingest. |
| `deprecated` | boolean | If `true`, the page is retired. Do not delete — set this flag. |

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
- Key Works / Products *(people, organizations, projects)*
- Claims & Positions *(anyone who holds positions)*
- Contradictions *(if any)*
- Sources *(auto-generated — do not write)*

These headings assume a person or organization. For an entity that is neither — a place,
an event, a signal, a vehicle, a work — keep Overview, Background, Contradictions and
Sources, and replace the middle sections with ones that actually fit the subject
(e.g. Location & Geography, Timeline, Technical Characteristics, Reception). Do not
force-fit a subject into headings built for someone else, and never drop a subject from
the wiki because the default template doesn't suit it.

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

### Look up once, in one call

Two different questions, two different tools. Confusing them wastes entire rounds:

- **"Does a page for X already exist?"** → `lookup_titles`. Pass **every** entity and
  concept name from the source in a **single** call and it tells you, for each one,
  whether a page exists and where. The answer is exact, covers the whole wiki, and
  includes aliases. **Do not use `search_wiki` for this**, and do not call
  `lookup_titles` once per name — batch them.
- **"Which pages mention X?"** → `search_wiki`. Full-text search cannot be answered by a
  title lookup, so this is what it is for.

The page catalog is *not* listed in "Current wiki state" — at several thousand pages it
is far too large to include. `lookup_titles` exists precisely so you never need it.

So: one `lookup_titles` call after Step 3, then act on the results. EXISTS → `read_file`
that path and `update_file` it. NO PAGE → `create_file`. Do not re-verify with a search;
the lookup is authoritative.

**During ingest, `search_wiki` has exactly one legitimate use**: a name so ambiguous the
lookup could not settle it. Even then, **two searches per term maximum — the server refuses
the third.** If two searches find nothing, it does not exist: create the page and move on.
Never use search to re-check a lookup result, and never use it to hunt down a page's older
sources — an existing page is updated from what you already read, not rebuilt.

**The lookup replaces the search, not the work.** Answering "does it exist?" cheaply does
not mean skipping a page. Every entity and concept in the source still gets a page written
or updated in Steps 5 and 6 — you just reach it faster. An ingest that produces only a
source page has failed, and the server will refuse your `done()` call.

### Step 1 — Verify source location
The file must be in `raw/`. If the user gives pasted text, ask them to save it
to `raw/` first as a `.txt` or `.md` file.

### Step 2 — Read the source completely
Read the entire file before writing anything. If `read_file` returns a `[TRUNCATED …]`
notice, keep calling it with the offset it names until you reach the end of the file.

### Step 3 — Create a source summary document
**One source page per ingest, exactly.** Do not create source pages for URLs or articles mentioned inside the raw file — only for the raw file itself. Do not call `create_file` with `type: source` more than once per session. **Source pages are immutable after creation — never call `update_file` on a `wiki/sources/` page.**

**You get one shot.** The source page cannot be edited after it is written. Before calling `create_file`, re-read the raw source, gather all quotes, claims, entities, and concepts you intend to include, and write the complete, thorough document in a single call. A thin or incomplete source page is permanent.

Call `create_file` with:
- `path`: `wiki/sources/{source-slug}.md` — always a wiki/ path, never a URL
- `type`: `source`
- `body`: the content below (do not write frontmatter manually — `create_file` fills in dates automatically)

Required sections:
- **Summary**: 3–5 paragraphs synthesizing the source's main content and contribution
- **Claims**: bulleted list of factual or analytical claims from the source
- **Entities**: bulleted list of named things — people, orgs, products, projects, places,
  events, works, vehicles, named phenomena. Bare names only —
  meaning plain text, not a markdown link. **Write each name exactly as a human would read
  it aloud: natural spacing and capitalization, never a filename slug.** Write
  "Film Criticism", not "film-criticism". This list is permanent — the page it's on cannot
  be edited afterward — and it becomes the title of a page in Step 5, so get the form right
  the first time.
  This list drives page creation in Step 5, so apply real judgement here — the bar is
  **central to the story**, not merely present in it.

  **What the source is ABOUT always goes on this list — no judgement call, no exceptions.**
  If the source is an encyclopedia entry, profile, or report on one subject, that subject
  is the single most important name here. This holds for any kind of subject, not just
  people: a radio signal, a company, a spacecraft, a piece of software, a place. Never
  omit the subject because it isn't a person and the wording below doesn't quite fit it.

  Beyond the subject, **include** an entity when the source makes substantive claims about
  it — what it did, what happened to it, what it is, its role in the events described.
  **Exclude**:
  - the article's byline or reporter — writing the piece is not the same as being a
    subject of it. A journalist who *is* the story's subject belongs on the list.
  - anyone quoted for reaction, explanation, or expert color, no matter how substantial
    the quote — a spokesperson explaining a decision, an analyst commenting on it, an
    official confirming a number. Being quoted well is not the same as being central.
  - names mentioned only in passing, with no claim attached
  - the publication or outlet, when it is only the venue this source appeared in

  The test for these secondary names is not "can I write a sentence about this" — nearly
  everyone quoted passes that. It is: **would the story be essentially the same article if
  this name were replaced with a generic stand-in** ("an official", "a spokesperson", "a
  nearby town")? If yes, leave it off; a later source that is actually about it will add
  it. The subject of the source itself never fails this test — it cannot be swapped for a
  generic stand-in without the article ceasing to be the article.
- **Concepts**: bulleted list of important concepts and terms. Bare names only — same rule
  as Entities: natural human-readable form, never a slug. Write "Sex in Cinema", not
  "sex-in-cinema"; "Horror Films", not "horror-films".

  **Every name here becomes a page in Step 6**, so list only concepts this source actually
  explains, argues about, or materially advances — not the general vocabulary it happens to
  use. In an article about a mesh-networking project, "LoRa" and "Mesh Network" are the
  subject matter; "Radio Frequency", "Open Source Software" and "Emergency Communications"
  are background terms any reader already understands and the article never explains. List
  the first kind, not the second.

  The test, for each candidate: **can you state what this specific source adds to the
  concept?** If you can — a definition it gives, a mechanism it explains, a claim it makes
  about how the concept works — list it. If the best you can say is "the article mentions
  it" or "it's related to the topic", leave it off. Judge each name on that question alone;
  there is no target number, and a source that genuinely explains eight concepts should
  list eight.

  If the source's subject is better understood as a concept than an entity, list it here
  instead — but list it somewhere; the subject never goes unlisted.
- **Quotes**: 3–5 direct quotes with section references if available
- **Context**: how it relates to, extends, supports, or contradicts existing documents

### Step 4 — Identify affected existing documents
Take the `## Entities` and `## Concepts` lists from the source page you created in Step 3 and
pass **all of those names together** to `lookup_titles` in one call. That single call tells you
which already have pages and which do not. Do not call `search_wiki` for this, and do not make
one lookup call per name.

The results are your worklist for Steps 5 and 6: every EXISTS entry gets updated, every
NO PAGE entry gets created.

### Step 5 — Update or create entity documents

**Mandatory. Work through the `## Entities` list of the source page you created in Step 3
and handle every name on it.** You already applied the page-worthiness test when you wrote
that list, so do not second-guess it here — every name on it gets a page created or
updated. Do not stop after one, and do not proceed to `done()` until this step and Step 6
are finished.

For each entity (person, organization, product, project) on that list:
- **Use the `lookup_titles` results from Step 4.** They already tell you whether this entity
  has a page. Do not call `search_wiki` to re-confirm them, and do not look the name up again.
- **Search only to resolve genuine ambiguity** the lookup could not settle. **HARD LIMIT: 2
  searches per entity — no exceptions.**
  Search 1: full name. Search 2: abbreviation or alternate name. If neither matches, stop
  immediately and treat as new. Do NOT search again. Do NOT try capitalization variants. Do NOT
  add `in:entities`, `in:concepts`, or `in:synthesis` modifiers as additional attempts. The server
  enforces this limit and will refuse the third call.
- **If a document exists**, fold the new source into it — do not rebuild it from scratch,
  and do not merely tack a paragraph onto the end. The page should read as one synthesis
  that happens to be informed by many sources, not as a pile of per-source additions.

  **Read the whole page, then write section by section:**
  1. `read_file` the page. Reading is cheap — it is writing that is limited — and you can
     only place new information well, or notice it is already covered somewhere else on the
     page, if you have seen all of it. If the page is long enough to come back
     `[TRUNCATED …]`, keep calling `read_file` with the offset it names until you reach the
     end.
  2. Decide where each piece of new information belongs.
  3. For each section that changes, send its full new text with `update_section` — the new
     information *merged into* the existing prose, extending a sentence, qualifying a claim,
     adding a detail where it fits. Preserve everything the source does not contradict.
     Most sources change one or two sections.

  Writing is what is bounded, not reading: `update_section` sends only one section, so it
  works at any page size. Use `read_section` instead of `read_file` only when a page is so
  large that reading it whole is wasteful — you lose the ability to spot duplication, so
  prefer the full read when it is practical.

  **Choosing which section.** Default to one that already exists — the templates in
  Section 3 cover most material, and merging into an existing section is what makes the page
  a synthesis instead of a pile. If `read_section` reports the section is absent, it lists
  the page's real sections; pick the closest fit from those before inventing one.

  Create a new section only when the material is a **recurring theme with enough substance
  to stand on its own** and fits none of the existing ones — "Legal Proceedings" on a
  much-litigated figure, "Technical Characteristics" on a device. **Never name a section
  after a source, a date, or a single event** — "2026 Tariff Announcement", "Wired Article
  Findings", "August Update". That is a changelog wearing a heading, and it fails for the
  same reason appending paragraphs does: the page stops being a synthesis. A one-off fact
  belongs *inside* a thematic section, not in a section of its own.

  `update_file` (whole page at once) is fine on a short page and is what the Regenerate
  Workflow uses, but on a large page it forces re-emitting every character and will be
  refused. `append_section` only adds, so reach for it only when creating a section that
  passes the test above — never as a way to avoid merging.

  Do not set `sources:` or `created:` — both are managed automatically.
  **Do not read the pages listed in `sources:`, and do not search for more sources.** Their
  content is already reflected in the page you just read. Re-deriving the page from all of its
  sources is the Regenerate Workflow (Section 6), which runs only when the user asks for it.
- **If the entity is new**, use `create_file` for `wiki/entities/{slug}.md`, written from this
  source. Do not set `sources:` — it is injected automatically. Do not search for older sources
  to backfill it; if the page later needs the fuller picture, the user can run a regenerate.
- Note any contradictions with existing claims in a `## Contradictions` section.
- Do not write a `## Sources` section — it is generated automatically from the `sources:` frontmatter.

### Step 6 — Update or create concept documents

**Mandatory. Work through the `## Concepts` list of the source page you created in Step 3
and handle every name on it.** As with entities, the list is already filtered — do not
re-apply the page-worthiness test here.

For each concept, technique, framework, or term on that list:
- **Use the `lookup_titles` results from Step 4.** They already tell you whether this concept
  has a page. Do not call `search_wiki` to re-confirm them, and do not look the name up again.
- **Search only to resolve genuine ambiguity** the lookup could not settle. **HARD LIMIT: 2
  searches per concept — no exceptions.**
  Search 1: full name. Search 2: abbreviation or alternate name. If neither matches, stop
  immediately and treat as new. Do NOT search again. Do NOT try capitalization variants. Do NOT
  add scope modifiers as additional attempts. The server enforces this limit and will refuse the
  third call.
- **If a document exists**, fold the new source into it — do not rebuild it from scratch,
  and do not merely tack a paragraph onto the end. The page should read as one synthesis
  that happens to be informed by many sources, not as a pile of per-source additions.

  **Read the whole page, then write section by section:**
  1. `read_file` the page. Reading is cheap — it is writing that is limited — and you can
     only place new information well, or notice it is already covered somewhere else on the
     page, if you have seen all of it. If the page is long enough to come back
     `[TRUNCATED …]`, keep calling `read_file` with the offset it names until you reach the
     end.
  2. Decide where each piece of new information belongs.
  3. For each section that changes, send its full new text with `update_section` — the new
     information *merged into* the existing prose, extending a sentence, qualifying a claim,
     adding a detail where it fits. Preserve everything the source does not contradict.
     Most sources change one or two sections.

  Writing is what is bounded, not reading: `update_section` sends only one section, so it
  works at any page size. Use `read_section` instead of `read_file` only when a page is so
  large that reading it whole is wasteful — you lose the ability to spot duplication, so
  prefer the full read when it is practical.

  **Choosing which section.** Default to one that already exists — the templates in
  Section 3 cover most material, and merging into an existing section is what makes the page
  a synthesis instead of a pile. If `read_section` reports the section is absent, it lists
  the page's real sections; pick the closest fit from those before inventing one.

  Create a new section only when the material is a **recurring theme with enough substance
  to stand on its own** and fits none of the existing ones — "Legal Proceedings" on a
  much-litigated figure, "Technical Characteristics" on a device. **Never name a section
  after a source, a date, or a single event** — "2026 Tariff Announcement", "Wired Article
  Findings", "August Update". That is a changelog wearing a heading, and it fails for the
  same reason appending paragraphs does: the page stops being a synthesis. A one-off fact
  belongs *inside* a thematic section, not in a section of its own.

  `update_file` (whole page at once) is fine on a short page and is what the Regenerate
  Workflow uses, but on a large page it forces re-emitting every character and will be
  refused. `append_section` only adds, so reach for it only when creating a section that
  passes the test above — never as a way to avoid merging.

  Do not set `sources:` or `created:` — both are managed automatically.
  **Do not read the pages listed in `sources:`, and do not search for more sources.** Their
  content is already reflected in the page you just read. Re-deriving the page from all of its
  sources is the Regenerate Workflow (Section 6), which runs only when the user asks for it.
- **If no document exists**, use `create_file` for
  `wiki/concepts/{slug}.md`, written from this source. Every name on the list gets a page —
  whether it "warrants" one was decided in Step 3, and re-deciding it here is what leaves
  concepts listed on the source page with no page behind them. Do not set `sources:` — it is injected
  automatically. Do not search for older sources to backfill it; if the page later needs the
  fuller picture, the user can run a regenerate.
- Do not write a `## Sources` section — it is generated automatically from the `sources:` frontmatter.

### Step 7 — Update synthesis documents
Determine whether the new source warrants:
- A new synthesis document in `wiki/synthesis/` (a comparison, timeline, or emerging pattern)
- Updates to an existing synthesis document

### Step 8 — Done
Before calling `done()`, confirm you actually completed Steps 5 and 6 — an ingest that
wrote only a source page is incomplete, and `done()` will be refused until at least one
entity or concept page has been created or updated.

Call `done()`. The server runs health checks — results are visible at `/wiki/lint`.

---

## 6. Regenerate Workflow

**Trigger**: User says "regenerate", "fix", "rewrite", or "redo" a wiki page (entity, concept, synthesis, etc.).

This workflow rewrites a wiki page from the synthesized source documents already in `wiki/sources/`. **Do not read `raw/` during a regenerate** — raw content has already been synthesized into `wiki/sources/` pages.

This is the *only* workflow that rebuilds a page from its full source list, and it runs only
when the user asks. Ingest (Section 5) deliberately does not do this: it folds each new source
into the existing page incrementally. Regenerate is how a page that drifted or was written
poorly gets repaired.

**You must read every source page before rewriting. Do not skip this.**

### Step 1 — Read the existing page
Call `read_file` on the target page. The `sources:` frontmatter field lists every source page that has informed this document — that is your reading list for Step 3.

### Step 2 — Discover additional source pages
Call `search_wiki` with query `"<page title> in:sources"` to find any source pages not already in the `sources:` frontmatter list. Add any new ones to your reading list.

### Step 3 — Read every source page
**Call `read_file` on every path in your reading list.** Do not skip any. Do not search for more sources — iterate the list. The rewrite is only as good as what you read here.

### Step 4 — Rewrite the page
Only after reading all source pages: call `update_file` with the full rewritten content synthesized from everything you read. Do not include `sources:`, `created:`, or `raw_source:` in the frontmatter — these are managed automatically by the system.

### Step 5 — Done
Call `done()`.

---

## 7. Handling Contradictions

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

## 8. Handling Uncertainty

- Reflect hedged claims with appropriate language: "according to [source name]",
  "as of YYYY-MM-DD", "the author suggests but does not confirm"
- Do not present hedged claims as settled fact
- Mark uncertain passages: `<!-- TODO: verify this claim -->`
- Use tag `needs-verification` in frontmatter for documents with unverified claims

---

## 9. Cold Start

If you are a fresh LLM session, reading this file is the whole orientation. The
orientation message supplies the rest: today's date, the wiki's size, and the list of
existing tags. Do not modify any file until the user gives an explicit instruction.

---

## 10. Do Not Do These Things

- Do not call `list_dir` to verify a file exists before reading it — call `read_file` directly
- Do not start a bullet list immediately after a prose paragraph without a blank line between them (see Section 3 — this is the most common formatting mistake)
- Do not modify, move, or delete anything in `raw/` — it is immutable
- Do not modify `LOBOTOMY.md` unless the user explicitly asks you to update the schema
- Do not edit `wiki/index.md`, and never `read_file` it — it is auto-generated on every page
  write and is thousands of lines long. Use `lookup_titles` to ask what exists.
- Do not call `search_wiki` to check whether a page exists — that is what `lookup_titles` is
  for. Search answers "which pages mention X", not "does a page for X exist"
- Do not call `lookup_titles` one name at a time — pass every name in a single call
- Do not write document frontmatter manually — always use `create_file` for new documents
- Do not write any markdown links in document body text — plain text only
- Do not resolve contradictions without user instruction
- Do not ingest sources from outside `raw/`
- Do not invent sources — only cite documents actually present in `raw/`
- Do not put URLs in document body text — they belong only in `url:` frontmatter on source documents
- Do not write workflow annotations like "(new)" or "(update)" in document content — these are planning notes only
- Do not save important information only in chat — write it to a document so it persists
