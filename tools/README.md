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

**Just run them.** No `su`, no wrapper:

```sh
python3 tools/relink.py
python3 tools/promote_openers.py --dry-run
```

They write the same files the server does, and every write path keeps the tree's
ownership: an existing file keeps its own owner and mode, a new file takes its parent
directory's, and every directory level created along the way takes its parent's. So a tool
run as root inside a wiki owned by `www` leaves everything owned by `www`, and the server
can still write it. `tests/test_root_ownership.py` checks exactly this, and skips unless
it is actually running as root.

That was not always true — root-run tools used to leave root-owned files, and then
directories after the files were fixed. If your wiki was damaged before this, repair it
once and forget it:

```sh
chown -R www:www wiki raw      # substitute the user your server runs as
```

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

### `add_story.py` — put an article into the reading list without the browser

The paste box is one route into `raw/`, and when it fails there was no other, so an
article you have in front of you could not be captured at all. This is the second route.
No network, no LLM, no cost.

```sh
python3 tools/add_story.py article.txt --url https://www.nytimes.com/2026/09/30/...
pbpaste | python3 tools/add_story.py --url https://www.nytimes.com/2026/09/30/...
python3 tools/add_story.py article.txt --title "How Meta Uses A.I. Data Centers"
```

It writes `raw/<slug>.md` with the same frontmatter the web capture writes, so the item
shows in the reading list and Wikify works on it exactly as if it had been pasted.

Three deliberate differences from the web route. Input is read as **bytes** and decoded
with `errors="replace"`, because a clipboard that has been through a browser can carry a
lone UTF-16 surrogate and that is precisely what used to lose a story. The title is the
**headline**, not the first line — a browser copy opens with the site name, then often a
byline and a reading time, and taking the first line titles the story "nytimes.com". And
an empty slug cannot produce a dotfile, which would save the story and then hide it from
the listing.

It refuses rather than overwriting when the filename already exists; pass `--name` to
choose another.

### `prune_history.py` — what `wiki/.history/` costs, and how to reclaim it

`_snapshot_version` keeps 50 revisions **per page**, pruned per page on write, with no
aggregate cap — at ~11,000 pages that is up to half a million full copies, and nothing
ever reported the total.

```sh
python3 tools/prune_history.py                 # report only; touches nothing
python3 tools/prune_history.py --keep 10       # what pruning to 10 would reclaim
python3 tools/prune_history.py --keep 10 --apply
python3 tools/prune_history.py --orphans       # history for pages that no longer exist
```

**A dry run is the default and `--apply` is required.** A revision is the only copy of
what a page said before a write, so keeping fewer is a real loss of recoverable past; the
report leads with what you would give up. The oldest revisions go first — they are the
ones `_snapshot_version` would evict next anyway, and a revert reaches for the recent ones.

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

### `rename_page.py` — rename a page, its title, and every link to it
Pages are titled by the LLM from whatever the source called the subject, and it sometimes
picks a fragment — an article mentioning "United" produced `entities/united.md` titled
"United", for the airline. A title that is a common word or part of a longer proper noun
then does real damage, because the autolinker matches it everywhere: that page turned
"United Nations" into `[United](../entities/united.md) Nations` across the wiki.

```sh
python3 tools/rename_page.py wiki/entities/united.md wiki/entities/united-airlines.md \
    --title "United Airlines" --dry-run
python3 tools/rename_page.py wiki/entities/united.md wiki/entities/united-airlines.md \
    --title "United Airlines"
python3 tools/relink.py                # then re-link the prose under the new title
```

Moves the page and its history, updates `title:` and the H1, and repoints every link that
pointed at it. Links whose display text was the *old* title are stripped to plain text
instead of repointed — that text is what the rename declares wrong, so
`[United](...) Nations` becomes `United Nations` rather than a link relabelled to an
airline. Running `relink.py` afterwards re-links prose that genuinely names the subject,
and leaves prose that never meant it alone.

### `repair_links.py` — fix broken internal links
Repairs links that already exist — it never creates one. (For turning bare text into links,
that's `relink.py` above.) Four passes: un-nest double-linked text left by an old
autolinker bug, correct relative paths with the wrong number of `../`, unwrap
`about:reader?url=...` capture URLs into the real article URL, and unwrap links to a page
that no longer exists anywhere. Scans `raw/` as well as `wiki/`.

```sh
python3 tools/repair_links.py --dry-run   # report what would change
python3 tools/repair_links.py             # apply
python3 tools/relink.py                   # re-link the prose that names a real page
```

Progress goes to stderr (`indexing…`, then every 1000 pages), so a long run is visibly
alive; the report itself is on stdout and still pipes cleanly.

That last pass is the one to reach for after deleting or renaming a page by hand: one
deleted page leaves a dead link on every page that mentioned it, and `/wiki/lint` will
list all of them. `[United](../entities/united.md)` becomes plain `United`. It is
unwrapped rather than repointed because there is nothing to point at, and guessing would
be worse than the dead link — the 135 links this was written for spanned an airline, the
UN, and the ordinary English word. Run `relink.py` afterwards and whatever genuinely names
a real page is linked again; whatever never meant it stays plain. A link whose target
still exists under some other path is *repaired* by pass 2, never unwrapped.

`rename_page.py` already does all of this for a rename, including the title and the
history — prefer it over deleting and repairing.

### `undo_pass.py` — put back everything one pass wrote
Every write stamps its reason into the revision filename, which makes a whole pass
addressable after the fact. Reverting one page from its History view answers one bad
repair; this answers "that pass touched 900 pages and I want them back".

```sh
python3 tools/undo_pass.py repair-links --dry-run   # what it changed
python3 tools/undo_pass.py repair-links --diff      # the full diff per page
python3 tools/undo_pass.py repair-links             # put it back
```

The reason is the one in the filename: `ingest`, `user-edit`, `relink`, `repair-links`,
`heal`, `merge`, `promote-opener`, `revert`.

A page is only reverted when that pass's write was the **last** thing to touch it —
otherwise reverting would discard the ingest that came after, so the page is listed under
"Left alone" instead. Several writes by the same pass are undone as a unit, back to the
state before the pass started. `index.md` and `log.md` keep no history and cannot be
restored from here; it says so. The revert is itself recorded, so it can be undone too.

### `overview_drift.py` — which summaries have stopped being summaries
An entity page's `## Overview` and a concept page's `## Definition` are supposed to say what
the subject IS. They are also the section the model reaches for when its material fits
nothing else on the page, so on a broad standing page they accumulate one unrelated dated
sentence per ingest. A live `florida.md` measured one paragraph, seven sentences, 1,723
characters and 18 links, of which only the FIRST sentence was about Florida — the other six
were six different articles.

```sh
python3 tools/overview_drift.py                  # the worst 40, by dated sentences
python3 tools/overview_drift.py --limit 200
python3 tools/overview_drift.py --show florida   # one page's summary in full
python3 tools/overview_drift.py --wall 1200 --sentences 8
python3 tools/overview_drift.py --csv > drift.csv
```

Three signals, reported separately because they call for different work. **DATED** is
sentences that OPEN by placing themselves in time — the signal that matters, because each
one is a news item that belongs in a named section, a Timeline, or on a page of its own.
**WALL** is one paragraph over the threshold, which is only a reflow. **LONG** is sentence
count, which mostly agrees with DATED. A page is listed on DATED alone, or on WALL and LONG
together — one long paragraph that is genuinely about its subject is not drift, and a report
that cries wolf gets ignored.

`agent._accreted_dated_sentences` refuses the write that causes this, so the wiki stops
acquiring it; this is for what is already on disk. **It reads and never writes, and offers
no repair deliberately** — where a dated sentence belongs is a judgement about what the page
is for, and where to break a wall is a judgement about what belongs together. A DATED page
wants the Regenerate Workflow or a human; a WALL-only page wants a blank line.

### `bleeding_titles.py` — which titles are common words
A page titled "Lost" links the word *lost* in "the hikers were lost for three days".
"Agency", "Power" and "Mission" do the same. The autolinker matches case-insensitively, and
the once-per-section rule makes it worse than a stray link: the wrong mention spends the
section's one link, so the genuine mention gets nothing.

```sh
python3 tools/bleeding_titles.py                 # the worst offenders
python3 tools/bleeding_titles.py --all           # every title with any lowercase use
python3 tools/bleeding_titles.py --min-lower 25  # raise the floor
python3 tools/bleeding_titles.py --words 2       # also two-word titles (slower)
```

**Read the page type first.** A `concept` page is *supposed* to catch the common
noun — "inflation" in prose is about inflation, and that link is what a concept wiki
is for — so concepts are listed only under `--concepts`, for review, never as a bug
list. An `entity` is a proper noun, so a lowercase use of its name is a different
word: Succession the series against succession the process, Visa the company against
a visa in a passport, Block, Notion, Coach, Vanguard, Girls, Survivor. Those are the
real bleeds and they are the default report. On a real wiki this was the difference
between 383 titles and about twenty.

No dictionary, and none is needed — the wiki reports on itself. A proper noun is written
capitalised wherever it appears and a common noun lowercase, so counting both per title says
which is which. Two numbers, two different decisions: **linked** is wrong links on disk
right now, **bare** is what the next relink sweep will link. Capitalised occurrences are the
denominator, and they over-count (a sentence can *start* "Lost in the woods…"), which makes
the report conservative rather than alarmist.

Excluded: headings, because the autolinker skips them; a page's own title on its own page;
and `index.md`/`log.md`. A link whose display text matches but which points at some other
page is not counted against this one. Aliases are checked too — they bleed identically, and
an alias is exactly what someone adds after a disambiguating rename leaves a page unlinked.

Read-only, no LLM, no API cost. It prints the `rename_page.py` command for each page it
flags.

`heal_pages` (run at server startup and after every ingest, no CLI needed) also now
alphabetizes a source page's `## Entities` / `## Concepts` lists. Those are lookup
tables and a source page is immutable to the LLM once written, so this is the only
route for one written before the rule existed. `## Claims` and `## Timeline` are left
alone — prose in bullet form, and chronological, respectively.

### `what_was_lost.py` — did a write remove material, or only shorten prose?
The history row says a write cut 2,461 words and added 479. It cannot say which of those
two things happened, and that is the only question you have: collapsing duplication you
asked to be removed looks identical, from the metadata, to discarding sourced detail.

```sh
python3 tools/what_was_lost.py wiki/concepts/artificial-intelligence.md          # the last write
python3 tools/what_was_lost.py wiki/concepts/artificial-intelligence.md --all    # every write, oldest first
python3 tools/what_was_lost.py wiki/concepts/artificial-intelligence.md --rev 20260926163407123456
```

Reports the disappearances that are not matters of taste — a **section**, a **dated
timeline entry**, a **link target** — plus the word and byte arithmetic. A heading is not
prose and does not get tightened away; a wiki link is a reference to another page, so a
target that appears nowhere afterwards is a reference that is gone. Link targets are
compared as a **set**, deliberately: the once-per-section rule legitimately deletes repeat
links to a page the text still discusses, and counting occurrences would report loss on
every relink sweep until nobody read the output.

Reads only `wiki/.history`. No LLM, no API cost, and it writes nothing at all.

### `unlink_headings.py` — take markdown links out of headings
`## [Atheism](../sources/atheism-wikipedia-2026.md)` → `## Atheism`. Every section tool
finds a section by its heading text, so a heading carrying link syntax is unaddressable:
asked for "Atheism" they do not find it, and `append_section` then creates a second
section beside it. The autolinker never does this — it skips heading lines — so these are
hand-written, and the write paths refuse them now. This repairs the pages damaged before
that.

```sh
python3 tools/unlink_headings.py --dry-run
python3 tools/unlink_headings.py
```

Nothing is lost: the link target is still reachable from the prose under the heading, and
`relink.py` re-links the subject there anyway. Every page changed goes through the normal
write path, so it is revertable from that page's History view.

### `merge_page.py` — fold one page into another and delete it
`find_duplicate_pages.py` finds one subject under two slugs — `gdp.md` and
`gross-domestic-product.md` — and is report-only, because deciding they are the same thing
needs judgment. Once you have decided, the rest is mechanical.

```sh
python3 tools/merge_page.py wiki/concepts/gdp.md \
    --into wiki/concepts/gross-domestic-product.md --dry-run
python3 tools/merge_page.py wiki/concepts/gdp.md \
    --into wiki/concepts/gross-domestic-product.md
python3 tools/relink.py      # re-link bare prose under the survivor's new aliases
```

Carries over what would otherwise be lost silently: **every link** to the old page,
repointed and re-relativized per referring directory; **`sources:`**, unioned into the
survivor, because provenance outlives the page; and **the old title plus its aliases**,
added as aliases of the survivor, so prose that said "GDP" still resolves after the page
called GDP is gone. `--alias NAME` adds more.

**Several losers in one command**, because `find_duplicate_pages.py` reports groups and
not all of them are pairs — Pacific Gas and Electric had five pages. Each is merged into
the survivor in turn and a refusal on one does not stop the rest.

```sh
python3 tools/merge_page.py wiki/entities/pge.md wiki/entities/pacific-gas-electric.md \
    --into wiki/entities/pacific-gas-and-electric-company.md --carry
```

**`--carry` is what makes a merge not be hand work.** Asked for directly — *"can you make
merges easier? editing deltas by hand is a real pain"* — over a run showing **35 groups, 73
pages**. Everything about a merge was already mechanical except one step: it refuses while
the old page still says anything the survivor does not, prints those lines, and leaves you
to move them. Across 35 groups that one step is the whole cost of the cleanup. `--carry`
appends each outstanding line to the survivor's section of the same name, or creates the
section and **reports that it did** — two pages naming one section differently ("Positions"
against "Political Stances") is a judgement no string comparison can make, so it is yours.

**The summary is carried too, and MARKED.** An `## Overview` or `## Definition` delta
lands under a visible `**TODO — merged from <page>, fold into the summary above:**` line;
the survivor gets `todo: "merge: summary needs rewriting"` in frontmatter **and the `_todo`
tag**, so nothing blocks on a summary rewrite and the pages waiting on one are listable:

```sh
python3 tools/search.py 'tag:_todo'     # or /wiki/tags/_todo in the browser
grep -rl '^todo:' wiki/entities wiki/concepts
```

**A leading underscore marks a tag as machinery rather than subject matter**, and the
namespace is the point — the next utility tag costs a name and no code. These tags are kept
out of the vocabulary `orientation_message` offers each ingest: a tag the model is told to
prefer is one it copies onto unrelated pages, which would fill the single listing the tag
exists to produce. The tag pages still show them.

This was the strict half until it was used in anger. Refusing a summary delta is right
about the accretion — but it made a 28-group cleanup into 28 separate summary rewrites,
each one blocking the merge behind it, which is the hand work `--carry` exists to remove.
What `_accreted_dated_sentences` refuses is a summary growing by a sentence per ingest with
**nothing recording that it happened**, so the page reads as though someone wrote it that
way and only `overview_drift.py` ever notices; a carried summary is declared in the body,
flagged in frontmatter, tagged, and listable in one command. **A guard against silent drift
is not a guard against drift that announces itself.**

`--strict-summary` restores the refusal, with the `update_section` call named, for when you
would rather do it properly page by page. `--force` skips the outstanding check entirely
and is the only mode that can lose text.

**The delta reported is the new SENTENCES, not the paragraph holding them.** A section's
prose is one line per paragraph, so an Overview that repeats the survivor's first sentence
and adds one clause used to come back whole and had to be diffed by eye. That was the
complaint, and fixing it exposed a **silent data-loss bug that had always been there**: the
old comparison asked whether either line contained the other, so a loser paragraph that
REPEATED the survivor's and then added a sentence counted as already-said, and the merge
deleted it — with no `--force` and nothing in the output. The one shape where a page
genuinely extends another was the one shape that was dropped.

**The entity/concept split is no longer trusted, and three tools stopped depending on it.**
`heal_pages` repairs `type:` from the **directory** at startup (the directory is
authoritative — every link to a page encodes it), `section_inventory.py` treats `Timeline`
as on-template for every type, and `bleeding_titles.py` decides "is this title a name?"
from the text — a capital in the MIDDLE of a sentence — falling back to `type:` only where
the text is silent. Pages whose opener no longer matches their healed type are listed at
startup rather than rewritten; `promote_openers.py` is the tool for those.

### `page_report.py` — everything mechanically wrong with one page
Written for `donald-trump.md`: 223,659 bytes, fourteen sections, 53.8s to autolink against
13,195 titles, and "pretty much a complete mess". The mess was **nine separate defects**,
most of them counting problems nobody can do by eye at that size.

```sh
python3 tools/page_report.py entities/donald-trump.md
python3 tools/page_report.py donald-trump                   # slug is enough
python3 tools/page_report.py donald-trump --only repeats,targets
python3 tools/page_report.py donald-trump --subsets --links  # the two noisier halves
```

Report-only, no LLM, and **no `--apply`** — same reason as `overview_drift.py`: every
repair here is a judgement about what the page is for. Re-run it after each pass and watch
the count drop.

| check | what it finds |
|---|---|
| `EMPTY` | a heading with no body — **marked** when the page lists sources for its subject, which means ingested text is not where the page's own map says it is |
| `HEADINGS` | what the write guards would refuse, plus the trap two of them make together: `(2025)` and `(2026)` each absorb to one name, so stripping the dates COLLIDES them into a duplicate |
| `DRIFT` | the summary measured with `overview_drift.py`'s own numbers |
| `PILE` | a section far larger than its siblings **and** full of dated news — both signals, since a page's main section is supposed to be its biggest |
| `REPEATS` | near-identical paragraphs and bullets anywhere on the page |
| `ECHOES` | one sentence appearing verbatim twice |
| `LINKS` | dead targets, `White [House](…)`, body links into `wiki/sources/` |
| `TARGETS` | one subject under several slugs — `war-with-iran` / `war-in-iran` / `iran-war` |
| `SOURCES` | one article captured twice (compared on TITLE, since duplicate slugs cannot collide), and unreadable slugs |

Two deliberate limits. **Common-word bleeding is not judged here** — a page titled
"Tariffs" *should* be linked from the word `tariffs` and one titled "Notes" should not, and
nothing on a single page distinguishes them; `bleeding_titles.py` answers it from the whole
wiki. And **a split name is only reported when the fuller name is a page the autolinker
knows**: three weaker tests all cried wolf on `Trump [Republicans](…)`, and asking whether
"White House" is itself a title settles it the autolinker's own way, since the title map is
sorted longest-first precisely so the longer name wins.

The old page's history under `wiki/.history/` is left in place: it is the only remaining
copy of what that page said.

### `rename_section.py` — collapse a cluster of synonymous headings into one name
Section names are the wiki's vocabulary, and vocabulary drifts: one idea ends up under
three names — "Claims & Positions" on 466 pages, "Positions" on 110, "Key Positions" on
25 — and then neither a reader nor a tool can find all of it. `section_inventory.py` shows
the clusters; this collapses one.

```sh
python3 tools/rename_section.py "Positions" \
    --from "Claims & Positions" --from "Key Positions" --dry-run
python3 tools/rename_section.py "Positions" \
    --from "Claims & Positions" --from "Key Positions"
```

Matching ignores case, `&` vs `and`, and trailing punctuation, so "Claims and Positions:"
is caught without being listed. Heading level is preserved.

**Renaming into a name a page already has is a merge**, and merges need judgment. It
applies the same ladder as `find_duplicate_sections.py` — one side empty, the two
identical, or one containing the other — and lists anything else for you rather than
guessing. Update the template in `LOBOTOMY.md` afterwards, or the next ingest writes the
old name back.

### `promote_openers.py` — give pages the `## Overview` their template requires
`create_file` requires an opener — `## Overview` on an entity, `## Definition` on a
concept — and nothing adds one afterwards, so pages written before that check never
acquire one. `section_inventory.py` counts them; this fixes the ones that can be fixed
mechanically. Every ingest touching such a page pays a wasted round: the agent asks for
the section the schema promises, misses, and has to recover.

Most of those pages are not missing the prose, only the heading — a lead paragraph sits
under the H1 with nothing naming it, so no tool can address it and it does not show up in
a page outline. This inserts the heading above it.

```sh
python3 tools/promote_openers.py --dry-run
python3 tools/promote_openers.py
python3 tools/promote_openers.py --dry-run --all   # list every page, not the first 25
```

A page with no lead paragraph is left alone and listed: its first section is something
like "Background & Leadership", renaming which would misdescribe what it holds, and
writing an overview from scratch is authorship, not repair. Send those through a
regenerate, or leave them.

Report-only with `--dry-run`. Changes go through the normal write path, so each page is
revertable from its History view.

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

### `section_inventory.py` — what headings the wiki actually uses
`LOBOTOMY.md` gives a template per page type, but nothing enforces it outside source
pages, so what the wiki really contains has only been guessable. This counts it: the most
used headings per type, how many are one-offs, and four specific smells — a heading the
template does not list, a section named after its own page, one naming a date or single
event, and an entity with no Overview (or concept with no Definition).

Run it before deciding what to constrain: a heading on 400 pages is part of the
vocabulary whether the template names it or not; one on two pages is drift.

```sh
python3 tools/section_inventory.py            # summary
python3 tools/section_inventory.py --pages    # name the pages behind each smell
```

Report-only.

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

The suite lives at the repo root, in `tests/` — it tests the whole project, not just what
is in here. See `tests/README.md`.

```sh
python3 tests/run_all.py     # everything; exits 0 on success, 1 on failure
python3 tests/mutate.py      # break each guard, check a test notices
```
