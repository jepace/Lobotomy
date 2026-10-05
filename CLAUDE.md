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

**`tools/README.md` documents every tool in `tools/` with sample command lines — read it
before adding another one.** The maintenance CLIs need no LLM and no API cost: `search.py`,
`relink.py` (the catch-up sweep that adds links to pages written before their subjects
existed), `rename_page.py`, `unlink_headings.py`, `repair_links.py`,
`repair_frontmatter.py`, `rebuild_sources.py`, `section_inventory.py`,
`promote_openers.py`, `rename_section.py`, `merge_page.py`, `find_duplicate_pages.py`, `find_duplicate_sections.py`,
`undo_pass.py` (put back everything one named pass wrote, skipping pages something
wrote after it), `add_story.py` (put an article into the reading list without the browser,
for when the paste box will not take it), `prune_history.py` (what the history store costs,
and how to reclaim it), and `lint.sh`.

All of them go through `agent._atomic_write`, so their edits are recorded in page history
and keep the tree's ownership — an existing file keeps its own owner and mode, a new file
takes its parent's, and new directories go through `_mkdir_inheriting`. That is what makes
them safe to run as root beside a server running as another user, with no `su` ceremony;
**anything new that writes to the wiki must do the same.** `repair_links.py` did not for a
long time, and its repairs were neither revertable nor safe to run as root. Never call
`mkdir(parents=True)` directly under `wiki/`: a level created by root is root-owned inside
a tree the server has to keep writing, and it fails silently in the history path, which
deliberately never raises.

## Architecture

### Core modules

**`tools/agent.py`** — the heart of the system. All AI tool implementations (`_read_file`,
`_read_section`, `_update_file`, `_update_section`, `_append_section`, `_replace_text`,
`_add_timeline_entry`, `_create_file`, `_lookup_titles`, `_search_wiki`, `_search_raw`,
`_autolink`, `_fetch_url`, `_done`, `_rebuild_index`), the agentic loop
(`stream_agent_turn`, `run_agent_turn`), the LLM provider abstraction, page version
history, and the whole-wiki maintenance passes (`heal_pages`, `relink_all`,
`unlink_headings`). Both `serve.py` and `wiki.py` are thin layers over it.

Tools are registered in two places that must stay in sync: `TOOL_FNS` (name → callable)
and `TOOL_DEFS` (the JSON schema sent to the model). `system_prompt()` appends a
quick-reference table that also needs the new row.

**`tools/serve.py`** — Flask web server. Routes for: `/chat` (streaming AI), `/wiki/*` (rendered markdown), `/inbox` (read-it-later), auth, and settings. Imports `agent.py` for AI functionality and `job_queue.py` for background jobs.

`list_inbox()` is the one hot path in it, because **`/inbox` polls `/inbox/list` every 8
seconds while the tab is visible** and both go through it. So a per-item cost there is not
paid once on a page load, it is paid continuously against the same server that renders the
wiki and runs ingests. It was resolving "which `wiki/sources` page came from this raw file"
inside the per-item loop, globbing and frontmatter-parsing source pages until it hit the
match — and a miss parsed all of them. That map is identical for every item, so 90 reading-
list items rebuilt the same dictionary ~85 times: 1.03s → 0.03s once it was built once
(90 items, 1,500 source pages). Deliberately **not** cached across requests — the polling
exists to notice items that have just been wikified, so a cache would break the feature it
serves. `tests/test_inbox_listing.py` counts reads rather than timing them, because the
complexity is what regressed and wall-clock is flaky on someone else's disk.

**A reading-list row is identified by its filename, never by its position.** Clicking
Wikify on the second item started wikification on the first. Rows were addressed as
`item-<n>`/`wikify-<n>`/`progress-<n>`, numbered by Jinja's `loop.index` at render time —
and the poll above **prepends** newly-arrived rows, numbering them against the *fresh* list,
so a new arrival and the original first row both became `wikify-1`. `getElementById` returns
the first in document order, which is the prepended one, so what you see as item #2 drove
item #1. Mostly that was confusing rather than harmful, because every server call already
sends the filename: the right article was ingested, archived and deleted throughout.
**`saveEdit` was the exception and it lost data** — it read `edit-body-<n>`, resolved to
another row's textarea, and POSTed that content under the right filename, overwriting one
`raw/` file with a different row's box. `rowFor(name)`/`partFor(name, sel)` scope every
lookup to the row carrying that `data-name`, and the positional ids are gone so two of each
cannot coexist again; `dataset.name` is compared directly rather than through a CSS
attribute selector, because a filename can carry quotes and brackets that would need
escaping. `tests/test_inbox_row_identity.py` has both halves — a structural assertion that
no lookup is positional, and Playwright driving the real helpers (lifted out of the template
by regex, so they cannot drift from what ships) against a document holding the duplicate-row
condition. It launches whatever chromium is on disk by `executable_path`, since the pinned
playwright build and the installed browser build drift apart and the default launch then
tells you to download one.

**A `fetch` must get JSON back even when it is refused** (`_wants_json`,
`_auth_required_response`, `apiFetch` in `base.html`). Reported as *"I copied and pasted an
article and can't save it"*, with the message `Save failed: JSON.parse: unexpected
character at line 1 column 1 of the JSON data`. The session had expired, `require_login`
answered **302 to the login page**, and **`fetch` follows a redirect transparently** — so
the browser got the login page's HTML with status **200**, and `await resp.json()` died on
the `<` of `<!DOCTYPE`. The user's pasted article was still unsaved in the textarea, and
the only thing the message told them to do was nothing.

`require_login` now answers **401 JSON to a fetch and keeps redirecting a navigation**,
which is not a detail — drop the redirect and nobody can log in, so both sides are pinned
by tests. Three independent signals say "this is a fetch" (`X-Requested-With`, a JSON
body, an Accept header preferring JSON), because a request need only match one.

That fixed the auth case on all 52 protected routes at once: a parseable 401 reaches each
site's existing `data.error` branch, so "Archive failed: Unknown" became "Archive failed:
Your session has expired — sign in again."

**And then the same bug was reported a third time, from the wiki page editor, because
that reasoning was only half right.** The entry here used to claim the server fix had
covered the ~30 untouched `resp.json()` sites. It covered *authentication*. A reply can
fail to be JSON for reasons the server never sees — a proxy's HTML 502 or 504, a request
that never arrived, a response cut off mid-flight — and every one of those sites still
turned that into "unexpected character at line 1 column 1". **Leaving a known-broken
pattern in thirty places because the commonest cause is handled is how one bug gets
reported three times.** All 28 live sites now go through `apiFetch`, and
`tests/test_no_bare_json_parse.py` fails on a `.json()` call on any response in
`tools/templates/`, and on a plain `fetch` anywhere but the two chat STREAMS, whose bodies
are read with a reader and must not be buffered as text.

Two things that fell out of the sweep. `deleteItem` removed the row whatever the server
answered, so a refused delete looked like a successful one until the next poll put the row
back. And `tests/test_hovercards.py` lifts the card script out of `wiki.html` to run it —
once that script called `apiFetch`, the lift had to bring the helper too, or six tests fail
with a `ReferenceError` that has nothing to do with hovercards.

**The wiki page editor had no test at all** — 808 tests and `/api/wiki/<path>/save`, the
route behind the Edit button on every page, was never once called by the suite.
`tests/test_wiki_save_route.py` covers it now: the text reaches disk, the revision is
labelled `user-edit` and holds the content from BEFORE the edit, the result is autolinked,
a retitle is visible to the autolinker (which is why the route uses `_atomic_write` rather
than `p.write_text`), bad paths and logged-out saves are refused, and a failure answers in
JSON. Note the deliberate ordering it pins: `_atomic_write` runs BEFORE the autolink, so a
linking error leaves the page saved — losing the user's text to a link would be worse.

Two more defects fell out of looking at the save path, and the second is worse than the
reported one:

- **An unsaved edit lived only in the textarea**, and the commonest failure (an expired
  session) is fixed by LEAVING the page. `stashDraft`/`loadDraft`/`clearDraft` keep it in
  `localStorage` until the save lands; every access is wrapped, since storage can be absent
  or throw and a draft is a convenience — the file on disk is the record.
- **`editItem` swallowed its load error and left the textarea EMPTY**, which presents a
  failed load as an empty file. Saving that box would have written the article away to
  nothing. It closes the editor now, and `saveEdit` refuses a textarea that was never
  filled from disk.

`tests/test_api_auth_responses.py` has all three: the server's two answers, a structural
half over the templates, and Playwright running the real lifted `apiFetch` against the
exact responses — HTML-200, 401-JSON, 502-page, good JSON. It serves from a **real origin**,
not `set_content`, for the same reason the hovercard tests do: on `about:blank` a relative
`fetch('/api-test')` has no base and throws, and the first run of this module failed that
way for a reason unrelated to the code.

**A lone UTF-16 surrogate in pasted text lost the whole story** (`_atomic_write`'s
`errors="replace"`, and the same on serve.py's content reads and writes). This is the
answer to "the can't-add-the-story-content error, every 4-6 stories", and it was found by
**fuzzing `/inbox/add`** after reasoning from the symptom had failed three times.

A copy that truncates an emoji leaves half a surrogate pair. **JSON permits it and UTF-8
refuses it**, so the request parsed fine and then the write raised, which surfaced as a
bare 500 — and the pasted article was gone. Intermittent exactly as reported, because it
depends on the characters in what you paste, not on the route, the session or the page.

Two things worth keeping from how it was found:

- **Every hypothesis reasoned out from the symptom was wrong**, and each was disproved by
  measurement: a long autolink does NOT stall the server (5.7s of linking delayed another
  thread by 20ms, so the GIL is not the mechanism); the save routes answer 200 JSON for
  plain text, code spans, fenced blocks, unicode, no frontmatter, an empty body and a
  100KB page; and a days-old paywalled capture with `fetch_failed` accepts a long paste.
  Fuzzing the input found in one pass what three rounds of reading the code did not.
- **The same fuzz found two more**: a filename over 255 bytes raised `ENAMETOOLONG`, and a
  non-string `content` raised `AttributeError`. All three arrived as the same bare 500,
  which is why one report covered three bugs.

`errors="replace"` is a deliberate trade at the single write chokepoint: one `?` in place
of a character that was already garbage, against losing the save. Content reads take it
too, so one bad byte in a raw file cannot 500 the page that renders it. **Config, auth and
other state files stay strict** — a replacement character in a password hash or an API key
is corruption to be noticed, not smoothed over.

**An empty log is only evidence if every arriving request would have logged.** A failing
save showed *"the server log has the traceback"*, the log was empty, and that could not be
read either way: the request may never have arrived, or it may have arrived and nothing
logged it. Both halves were wrong and both are fixed.

- **`apiFetch` asserted what it could not know.** It gave the traceback message for ANY
  status >= 500, including an HTML body — which by definition did not come from here,
  since every route answers JSON and `_unhandled` turns even a crash into JSON. The user
  looked where the message said and found nothing. **An HTML body is the tell**, so that
  now picks the explanation: it names the status, quotes the page's `<title>` or the
  server it identifies as, says the request never reached Lobotomy, and points at the
  proxy's log — with 504 read timeout / 502 upstream closed / 413 body too large spelled
  out. Principle 4 again, in the UI: a message must name a move that works.
- **Werkzeug's access log never reached the file anyone reads.** It is its own logger and
  was going to stderr. It is the single line that proves a request arrived, so it now gets
  the same `FileHandler`, at INFO, with `propagate = False` so the terminal does not
  double-print. `after_request` logs method, path, status and duration for every non-GET
  and every 4xx/5xx (the 8-second `/inbox/list` poll excluded).

Together those make silence diagnostic: **nothing in the log now means the request never
got here**, which points at the proxy rather than at Flask.

**THE DISK WAS FULL, and this code filled it.** Long articles would not save; short notes
would. The cause, found in nginx's log after days of looking at the application:

    pwritev() "/var/tmp/nginx/client_body_temp/0000000398" failed
              (28: No space left on device)

nginx spools a request body larger than `client_body_buffer_size` (8–16KB) to disk, so a
full filesystem fails **exactly the long pastes and none of the short ones** — and nothing
reaches the application log, because the request is never forwarded. Size is not the
application's limit: both save routes answer 200 JSON at 15KB, 240KB, 1MB, 2MB and
**7.6MB**, and Flask sets no `MAX_CONTENT_LENGTH`.

What filled it was `lobotomy.access.log` at **1.0GB**, and the requests in it were ours.
`base.html` polled `/chat/status` **every 2 seconds on every page of the site with no
visibility check** — 43,200 requests a day from one tab left open, on wiki pages as much
as the reading list — and `/inbox/list` added 10,800 more at 8s. **The irony is that
`serve.py` already filtered both paths out of werkzeug's access log**, so the volume was
invisible in the one log this project reads while nginx recorded every line of it.

Both pollers now take a cadence rather than a fixed interval: 30s idle, the fast rate only
while a job is running or a Wikify has just been clicked, and **no request at all while the
tab is hidden**. 54,000/day → 5,760 for an idle visible tab, 9.4× (measured, not the "10×"
the first version of the test claimed), and zero in the background. `wiki-lint.py`'s 2s
relink poll is allowed by name: it is created only inside `if (s.running)` and cleared when
the sweep ends, which is the rule rather than an exception to it.

Two things to keep from this:

- **A poller is a log writer.** Every poll is a line in a log somewhere, and the one place
  this project looks is the one place they were filtered out of.
- **`tests/test_polling_backoff.py` asserted the constants existed**, which three mutations
  walked straight through: changing `schedule(busy ? BUSY_MS : IDLE_MS)` to
  `schedule(BUSY_MS)` leaves every constant in place. It now pins the conditional at BOTH
  schedule sites — asserting "somewhere" also failed, because each file schedules twice and
  the start-up call satisfied the regex while the tick path was mutated.

`tools/prune_history.py` reports what `wiki/.history/` costs and prunes it, since
`_snapshot_version` keeps 50 revisions PER PAGE with no aggregate cap and nothing ever
reported the total. It was NOT the culprit here — stated because this entry first claimed
it was — but at ~11,000 pages it is the next thing to fill a disk. Dry run by default;
`--apply` required, because a revision is the only copy of what a page said before a write.
`_warn_low_disk()` logs free space at startup and shouts under 500MB.

**`tools/wiki.py`** — CLI wrapper around the same agent tools. An interactive REPL or one-shot runner; no Flask dependency.

**`tools/config.py`** — reads `config.json`. Use `cfg_get(section, key, default)` throughout. Config is never hardcoded.

**`tools/job_queue.py`** — background job queue used by `serve.py` for async inbox processing.

**`submit(key=…)` makes a job unique while it is waiting or running.** Asked what happens
on a second Wikify click: `submit` minted a fresh `secrets.token_hex(8)` every call, so two
clicks queued two complete agent turns over the same raw file, and the only thing in the way
was `btn.disabled = true` in the browser — **lost on a reload, absent in a second tab, and
reset every time the 30-second poll re-renders the row.** At `max_rpm: 1` the duplicate is a
second ~40-minute run spending a per-day quota to re-derive pages the first run already
wrote, then folding the same source into them again.

A duplicate submit queues nothing and **returns the id of the job already in flight**, so
the caller attaches to it — principle 4 in the UI: clicking twice should show you the run
that is happening, not an error about a thing already underway. The key is
`ingest:<raw filename>`; a plain chat message carries no key and is never deduplicated,
because repeating yourself in a conversation is legitimate.

**The release is the risky half, not the guard** — leak a key and that article can never be
wikified again, which is worse than the duplicate. It happens on every exit path: the
worker's `finally` (after a clean run, a cancel, and a crash), and `drain()`, where the
worker's release never fires because the job never ran. It is deliberately **after**
`on_done`, which is what marks the article wikified — release first and there is a window
where the row still offers Wikify while the key is already free. `_release_key` checks
`_keys[k] == job_id`, so a late release from a finished job cannot unlock the key its
successor now holds.

Two call-site consequences. `inbox/process-all` must **advance to the next item** on a
duplicate: that job's `on_done` belongs to the click that started it, so ours never fires
and the batch would stop dead at that item. And `window.wikifying` in `inbox.html` is only
the local half — it stops a double click before the round trip, in a `finally` so the early
`return` on a failed ingest cannot leak it.

### Module state — read this before writing a test or a maintenance pass

`agent.py` keeps mutable state in three places, and code that ignores any of them will
appear to work while doing nothing:

- **Module globals** `REPO_ROOT`, `WIKI_DIR`, `RAW_DIR`, `HISTORY_DIR`. Never capture these
  as default argument values — a `def f(root=WIKI_DIR)` default binds at import, so every
  caller that omits it scans whatever `WIKI_DIR` pointed at *then*. `wiki_pages()` had this
  bug and silently scanned the real wiki under test. Resolve at call time.
- **A thread-local session context** (`_ctx()`, reset by `init_session()`): which pages
  this session has read, how much of each, which sections, which are stale, refusal
  counters. Guards depend on it, and it persists across calls on the same thread.
- **Autolinker caches**: `_title_map_cache`, `_title_regex_cache`, `_title_tokens_cache`,
  `_no_autolink_titles`. Keyed by title, not by wiki.

### The autolinker (common bug surface)

**`tools/agent.py:_autolink()`** — **the only way wiki links are ever created.** The LLM
never writes markdown links; `LOBOTOMY.md` tells it not to, and `_strip_broken_wiki_links`
removes what it writes anyway. It runs in three places: `_autolink_now(p)` after every
write tool, `_post_process_session()` over every touched page at `done()`, and
`relink.py`/`relink_all()` as the whole-wiki catch-up sweep.

Uses a combined regex where group 1 protects existing links and group 2 matches titles bare or with a sub-span already linked (via `_title_alts()`). When a partial match is found (e.g. `CASA of [Monterey County](url)`), the inner link is stripped and the whole phrase is replaced with the longer-title link.

**A title links once per section's prose, and on every list or table row.** This is
Wikipedia's MOS:REPEATLINK, which links once per article but relinks in infoboxes, tables,
captions, footnotes and lists — a reader arrives at those out of order. Two departures from
the letter of it, both forced by the shape of these pages:

- **Per section, not per page.** `donald-trump.md` is 136KB across twenty-odd sections; one
  link at the top leaves the rest with no navigation. A section here is about what an
  article is there. `_seen` resets on every heading line.
- **A lookup row never spends the section's prose mention** (`_is_lookup_row`). A source
  page's `## Entities` / `## Concepts` and a `## Timeline` are lookup tables, and a row
  whose link was spent in a paragraph above it is a dead row. **Not every bullet is one:**
  `## Claims` is a list of full sentences — prose that happens to carry hyphens, read top
  to bottom — and always-linking there put California in all nine claims. Length is the
  honest discriminator between a name and a sentence (60 chars, links flattened first);
  table rows and Timeline bullets are exempted explicitly, since a dated entry is a lookup
  row however long it runs.

**The once-per-section budget belongs to the PAGE, not to the name.** It was a fresh
`_seen` cell per title, and every alias is its own entry in the title map — so
`donald-trump.md` got one first mention as "Donald Trump" and another as "Trump", and a
real paragraph came out carrying both:

    President [Donald Trump](../entities/donald-trump.md)'s brand is deteriorating.
    Anderson contends that [Trump](../entities/donald-trump.md) and the party ...

Two links to one page in one section's prose, which is the repetition the rule exists to
stop; a reader does not care which of a page's names was used. The record is now keyed by
`(target page, section ordinal)` and shared across every pass. That fixes the unlinking
half for free: a repeat already on disk is met by the pass for the name it was written
under — the alias's own pass, whose token test matches it — and that pass now sees the
mention as spent, so it strips the link. The golden corpus had the bug recorded as correct
(`aliases` linked all three of PG&E / Pacific Gas and Electric Company / the utility in one
sentence); that case now expects one link, and `aliases_one_per_section` was added to keep
proving the aliases still MATCH, which one link no longer shows. Longest-first means the
fuller name takes the mention — **but if the alias appears earlier in the section than the
full name, the link lands on the later, fuller mention rather than the first one.**
Wikipedia links the first; fixing that means walking a section by position rather than by
title, which is a different loop.

The half that is easy to miss: the rule must also **unlink** repeats already on disk, or it
applies only to newly written text while ~9,000 pages keep every repeat. Unlinking is
scoped to links the autolinker itself would have written — same target page *and* display
text whose `\w+` tokens are exactly the title's — so a hand-written
`[the disease](../concepts/measles.md)` alias and every external link survive.

Two consequences worth holding on to. **Substitutions now remove link syntax as well as
adding it**, which breaks the old invariant that nothing could expose text for a later
title to match; the result stays deterministic only because `_build_title_map()` is sorted,
so that ordering is now load-bearing for output, not just for convergence. And **group 1
matches every existing link on the line for every candidate title**, so anything expensive
in that branch runs a million times on a large page: parsing the link with a regex there
cost 19× (1.1s → 20.9s on a 63KB page with 1,200 links) until a plain `basename not in`
substring test was put in front of it.

**A shorter title already linked inside a longer one gets its own pass.** Observed on a
source page's `## Entities` list: `[New York University](…) Langone Health`, with
`nyu-langone-health.md` sitting right there. The title map is sorted longest-first, so
when both pages exist the long title wins and this cannot happen — the failure needs a
*sequence*, which is the normal shape of an ingest: the short page exists, the list is
linked, and the long page is created twenty rounds later.

Nothing could then fix it, because group 1 wins at every position. An upgrade is only
reachable when the phrase has bare words BEFORE the linked part — in
`CASA of [Monterey County](url)` group 2 starts matching at "CASA", ahead of the `[`, so
it wins; when the linked span *starts* the phrase, group 1 matches there and returns it
unchanged, and group 2 is never tried. `_title_upgrade_re()` therefore runs the
already-linked forms alone, ahead of the combined regex. It matches **the title's words in
order with link syntax allowed around any of them**, not `_title_alts`'s single contiguous
linked sub-span, because the common shape has several:
`[Planned Parenthood](…) of [California](…)`, produced when a source page lists an entity
before its page exists. Making every piece of link syntax optional means the pattern also
matches bare text, so the replacer declines a match containing no `](` and leaves it to
group 2. **And it rejects a match whose `](` has no `[` of its own inside it** — without
that balance check the shorter title "Monterey County" matches the tail of
`[CASA of Monterey County](…)` and rewrites it to `[CASA of [Monterey County](…)](…)`,
manufacturing the very malformed shape `_LINK_G1` exists to contain. A pass that repairs
nesting must not be able to create it; the golden corpus caught that within the hour. Safe unprotected, because
every alternative carries the title's literal words *and* markdown link syntax: it cannot
match bare prose, and cannot match inside an unrelated link's display text.

Two traps in it. It must **not** touch `_seen` — the combined pass runs over the same line
next, meets the link the upgrade just wrote at group 1, and accounts for the mention
there; marking it in the upgrade made the combined pass read its own new link as a repeat
and unlink it, which the golden corpus caught as three cases losing their links entirely.
And it costs a regex per candidate title per line, so it is gated on `"](" in line` plus a
lowercase substring probe for the title's longest word, kept in step via `lines_lower`
(1.7× → 1.3× on a worst case where every line carries links).

**Group 1's link pattern tolerates one level of brackets in the display text**
(`_LINK_G1`), and that is load-bearing, not cosmetic. The plain `\[[^\]]*\]\([^)]*\)`
mis-parses a malformed `[[a](b)](c)`: it consumes `[[a](b)` and leaves `](c)` behind, so
the scanner meets that path as ordinary text, links the title inside it, and the link
gains a layer — **every pass, forever**. That is the engine that took one page to
twenty-eight layers of nesting, each layer being the previous one wrapped again:

    X0   = ../sources/backgammon-wikipedia.md
    Xk+1 = ../sources/[backgammon](Xk)-[wikipedia](../concepts/wikipedia.md).md

Two independent protections cover it now — `_BARE_PATH` stops a path being linked into at
all, and this stops group 1 handing an interior back to group 2 — and each was verified
with the other disabled. Whatever writes the malformed link in the first place (still
unknown), it can no longer compound. Recovery is a different matter: every layer appends a
real `-wikipedia.md`, so unwinding leaves those behind and **no pass can know they were
fabricated**. `_heal_mangled` recovers the innermost path, which is the most that is
knowable; the rest is a human edit. It takes the FIRST path match, not the last — every
outer `../sources/` is followed by `[`, so only the innermost matches a path pattern at
all, and taking the last returns a different page entirely.

The critical invariant: **never match inside existing markdown links — or inside a bare
URL**. Group 1 of the combined regex takes priority at each position, consuming both before
group 2 can fire. Heading lines are skipped entirely (`is_heading`), so a link inside a
heading was written by hand, not by this.

Bare URLs (`_BARE_URL`) were missing from group 1 for a long time, and the golden baseline
had the result recorded as correct: `https://example.com/meta/page` became
`https://example.com/[meta](../entities/meta.md)/page`. Two failures at once — the URL no
longer resolves, and the link points at a page the sentence was not about, because "meta"
there is a path segment. Prevention alone was not enough to fix it: group 1 protects
complete links, so a link already inside a URL is shielded by the very mechanism meant to
stop it. `_MANGLED_URL_RE` therefore unwraps them at the top of `_autolink`, before
anything else reads the text, and the wiki heals on the next write or relink with no
separate pass.

**A relative path is the same hazard without a scheme, and far commoner** — every page's
own links look like one, so prose that names a file gets mangled. `_BARE_PATH` covers
`../sources/foo.md`, `sources/foo.md` and a bare `foo.md`. Observed:
`Adapted from ../sources/backgammon-wikipedia.md` became
`../sources/[backgammon](…)-[wikipedia](…).md` — which **renders as an ordinary sentence
with two plausible links**, so it reads fine on the page and only `/wiki/lint` ever
notices. Do not conclude from a clean-looking page that link syntax is intact.

**Code is not prose, and none of it was protected** (`_CODE_SPAN`, `is_fenced`,
`_unlink_in_code`). Reported from a live page: `` `container.exe` `` came out as
`` `[container](../concepts/container.md).exe` ``, because a page titled "Container"
existed and `\b` is satisfied by the dot. `_BARE_PATH` covers `.md` only, and the general
rule it is a special case of was missing. Reproducing it showed the inline case was the
mild one — a fenced ```` ```sh ```` block came out as
`[docker](…) run --rm [python](…):3`, and **the reader copies that line into a terminal**.
`<code>` and `<pre>` too, since `render_md` passes HTML through.

Fenced blocks are line-ranged like `is_heading`; a fence closes only on a run of its OWN
character at least as long, or a `~~~` inside a ```` ``` ```` block would resume linking
mid-block. A `#` inside a fence is a shell comment, so it is cleared from `is_heading` —
it was advancing the section ordinal and resetting the once-per-section budget mid-section.

**Prevention alone freezes the damage** — the `_MANGLED_URL_RE` lesson exactly: once a link
is inside a span, group 1 protects it, so the mechanism that stops new ones is what keeps
the old ones forever. `_unlink_in_code` runs at the top of `_autolink`, scoped to links the
autolinker itself would have written (relative, ending `.md`), so a page documenting
markdown keeps `` `[label](https://example.com)` `` exactly as written.

Two traps. A span opened with N backticks closes on a run of N and may CONTAIN shorter
runs, so ``` ``docker ` ps`` ``` is one span — the first pattern used `` `[^`\n]*` `` for
every length, the two-tick span never matched, and **the golden corpus recorded the link
inside it as correct**, which is the baseline-enshrines-the-bug trap the corpus exists to
catch. And the alternation sits inside group 1 of the combined regex, so it must stay
capture-free or the title group renumbers; that is why the lengths are written out rather
than matched with a backreference.

**Known gap, stated rather than half-handled:** a 4-space *indented* code block is not
protected. Telling one from a lazy continuation under a bullet needs list state, and
getting that wrong would silently stop linking inside ordinary nested lists — a worse
failure than the one it fixes.

`repair_links._repair_nested` used to make this permanent. When it could recover no target
from inside the mangled URL it truncated at the first `[`, turning
`[Backgammon](../sources/[backgammon].md)` into `[Backgammon](../sources)` — a link to a
**directory**, which resolves, so lint fell silent while the real target was gone for good.
It now leaves what it cannot repair alone: visible damage that keeps getting reported beats
an invented link that hides it, and the dangling pass or `_MANGLED_URL_RE` handles the
shape properly on the next run.

Performance: the title+alias map and the per-title compiled regexes are cached in memory (`_title_map_cache`, `_title_regex_cache`). `_atomic_write` invalidates them only when a write actually changes `title`/`aliases`/`no_autolink`; an mtime-scan backstop in `_build_title_map()` catches writes that bypass `_atomic_write` (other processes, manual edits), with per-file vetted mtimes so the backstop doesn't false-fire on the autolinker's own body-only writes. This invalidation logic has been a repeat bug source — change it with care and test all of: safe write keeps cache, title change invalidates, bypass write is detected, safe write doesn't mask a concurrent bypass.

A per-title token prefilter (`_title_tokens_cache`) skips any title whose words are not all
present in the page — at ~9,000 titles this is what keeps a single autolink under a second.
**A per-LINE probe does the same job one level down**, and matters more since the repeat
rule landed: the body prefilter only says a title appears *somewhere*, so without it the
combined regex runs over every line for every candidate title. Profiling a 119KB page
against 9,600 titles showed **731,390 calls into the replacer**, and that one substring
test took 13.8s to 9.4s. The probe is the longest `\w+` **token**, not the longest
whitespace-word, because `noah's` is never found in a line spelling it `noah’s` — it would
reject the line before the flexible pattern could match.

**A comma in a title is optional in the text** (`_FLEX_CHARS[","]`), and the failure it
fixes is a WRONG link rather than a missing one. A wiki holding "University of California"
beside "University of California, Merced" linked prose saying *University of California
Merced* (no comma) to the parent: the long title did not match, so the shorter one did, and
a sentence about the campus pointed at the whole system. The link resolves, so lint is
silent and the page reads fine.

**Only that direction is safe.** A space in a title must NOT match a comma in the text, or
"attended by Smith, Johnson and Lee" links two people as one. A title carries the
punctuation of a formal name; prose carries the punctuation of a sentence, and only one of
those is safe to relax. Measured first: with both pages present and spelled alike, the
comma was never the problem — longest-first already gave each campus its own link.

Two limits that remain, stated so they are not mistaken for bugs: prose saying "UC Merced"
still needs an `aliases:` entry, and a campus with no page of its own
("University of California, Santa Cruz" where only the parent exists) links the parent and
stops mid-name — declining that needs the linker to know ", Santa Cruz" continues a proper
name, and "Monterey County, California" is the same shape with the opposite right answer.

**Typographic variants are the same character for matching** (`_esc_flex`). An article
writes "Noah’s Ark Scans" with a curly apostrophe, the page is created as "Noah's Ark
Scans" with a straight one, and the autolinker — matching the title literally — silently
skipped it while every other name in the same list linked. `_resolve_page` already
normalised, so no duplicate page was created and nothing reported it. Apostrophes, quotes
and dashes each match their whole family; the display text keeps whatever the page wrote,
since rewriting an author's punctuation is not the linker's job.

**The repeat-link rule is not free.** The same fixture runs 4.5s on the code before it and
9.9s after, because group 1 now inspects every existing link rather than returning it
untouched. That is the cost of unlinking repeats, and it is paid on every write to a large
page; the probe above is what keeps it from being 3×.

**A title that is also a common word bleeds, and the once-per-section rule makes that
worse than a stray link.** A page titled "Lost" links *lost* in "the hikers were lost for
three days"; "Agency", "Power" and "Mission" do the same. Matching is case-insensitive
(`re.IGNORECASE` on the combined regex), so there is no way for the linker to tell the
proper noun from the common one. Measured, after renaming the page to "Lost (TV series)"
and adding `aliases: ["Lost"]` to win the links back:

    The hikers were [lost](../entities/lost-tv-series.md) for three days,
    and the finale of Lost aired in 2010.

The alias took the ADJECTIVE, and that **spent the section's one mention**, so the genuine
subject went unlinked. An alias is strictly worse than no alias here. Until matching can be
case-sensitive for such a name, the honest settings are a disambiguated title (the
parenthetical never appears in prose, so the bleeding stops and `rename_page.py` strips the
wrong links back to plain text) plus `no_autolink: true`, which declares the intent and
keeps the page in `lookup_titles` so an ingest cannot create a duplicate later.

`tools/bleeding_titles.py` finds them, and needs no dictionary because **the wiki reports on
itself**: a proper noun is written capitalised wherever it appears and a common noun
lowercase, so counting both per title says which is which. It separates links already on
disk (damage done) from bare lowercase occurrences (what the next relink will link), and the
capitalised count over-counts sentence-initial use, which makes it conservative. Its whole
value is in not crying wolf — "Nvidia" and "Gavin Newsom" must never appear in the report —
so that is what its mutations protect.

`_build_title_map()` must be **deterministic**. It was not once (unsorted glob, no sort
tiebreak) and the resulting map order changed the output, so `relink` never converged: two
consecutive whole-wiki runs both reported hundreds of changes.

`merge_page()` has the same problem in miniature and solves it locally: `_claims`
compares the two bodies line by line, so two pages for one hospital each describing it by
their own title looked like two different sentences, and the merge was refused — blocking
the cleanup the naming mismatch had made necessary. Running the merge *is* the judgement
that these are one subject, so within that call every name either page has (titles and
aliases, longest first, word-bounded) folds to one placeholder before comparing. Note the
guard that is *not* there: a minimum name length looks like protection against folding
"NY" inside "company" and is not one, because the fold runs over both pages and mangles
them identically — `\b` is the real guard, and no mutation could catch the length cutoff
being removed.

**`_resolve_page()` is the one place that decides create-vs-update**, and `lookup_titles`,
`done()`'s listed-name check and the duplicate guards all share it. That sharing is a
feature — they cannot contradict each other — and it is also why a gap in it produces a
duplicate page rather than a stray round: every caller is wrong the same way at once.
Observed: an ingest listed "New York University Langone Health", created the page as
"NYU Langone Health", and then `done()` demanded the page, `lookup_titles` confirmed the
demand ("this answer is exact… do not double-check it"), and the model wrote a second page
for the same hospital. `_initialism_match()` closes that: it **declares** a match, unlike
`_norm_title_key()` which only suggests one, because every word outside the initialism has
to match exactly and both names must be consumed completely. A run of at least two
consecutive words is required, which keeps it off ordinary abbreviation — "MS Word" does
not match "Microsoft Word". A first-letter prefilter keeps the extra scan free: whether the
first words are the same word or one initialises the other, they share a first letter.

Pages can carry an `aliases:` frontmatter list (e.g. `aliases: ["gonzales", "uc davis"]`) for common short names that the autolinker should also match. The LLM is not instructed to set this field — it's a manual human override for when the formal page title differs from how the subject is typically referenced in prose. `no_autolink: true` excludes a page from *linking* but deliberately keeps it in the title map, because hiding it from `lookup_titles` made the agent create duplicate pages.

### Write-path guards

The densest logic in the file, and the reason the wiki survives an LLM editing it. Five
tools write pages — `create_file`, `update_file`, `update_section`, `append_section`,
`replace_text` — and each runs the same structural checks through shared helpers
(`_heading_dupes`, `_bad_headings`, `_absorb_date_qualifiers`) so they cannot disagree
about what is legal.

Three principles, each learned expensively:

1. **Absorb what is unambiguous; refuse only what needs a decision.** A refusal costs a
   full round trip (observed: 44 messages, 270KB, ~60s) and the model has to be able to act
   on it. An entity page opening with `## Definition` is one heading with the wrong name —
   renamed silently. `## Fiscal Challenges (2026)` is a standing heading with a date bolted
   on — the date is dropped. `## 2026 Outbreak` names the event itself, so there is nothing
   to strip and it is refused, pointing at `add_timeline_entry`.
2. **On update paths, check the delta, not the page.** Only violations the edit
   *introduces* are refused. An earlier duplicate-heading guard checked the whole resulting
   page and deadlocked 176 real pages: the only edits that could have fixed them were the
   ones it refused.
3. **A refusal names every problem, not the first one found.** `create_file` collects
   them and returns one numbered list. A call can be wrong in several independent ways at
   once, and each refusal costs a full round: an observed ingest spent three refusals and
   three minutes on one page, told about its missing `## Overview`, then — after fixing
   that — told it needed a source page first, which had been true from the start and said
   nothing about the body. Only the checks that make the rest meaningless return early:
   no path, outside `wiki/`, bad filename, page already exists (that reply carries the
   content), missing title or type.
4. **A refusal must name a move that works — the call, not the constraint.** The
   recurring failure here is a guard that is locally correct about its own question but
   answers a question the caller cannot act on — `lookup_titles` right that no title
   matched, `create_file` right that the path existed. Worst case: renaming one violation
   into another, so the refusal quotes a heading the model never wrote.
   The subtler version costs data rather than rounds. `update_section`'s duplicate-heading
   refusal used to end "Send the section's body only": true, and silent about where the
   material for that other section should go. An observed ingest resent the identical call
   twice, then complied by deleting the heading and leaving a second section's content
   inside `## Overview`. A guard that names no destination does not prevent the damage, it
   redirects it somewhere quieter. That refusal now names the call — `update_section(path,
   section='<the other one>', content=…)`, or `append_section` to create one — and says
   the text is not wasted. Where a refusal names a call, a test should follow its
   instructions and assert they succeed; that is the only version of the assertion that
   proves anything.

Also enforced: search limited to 2 per term per session; `done()` refused if an ingest
wrote no entity/concept pages or never established a source page; `update_file` refused
until the session has read the page's full content, and `update_section` until it has read
that section; all-lowercase titles refused — and, on a source page, a majority-lowercase
`## Entities` / `## Concepts` list, since each row becomes a page title in Step 5 and the
page is immutable afterwards, so it is permanent; the check is on the ratio because
"bell hooks" is a real name and a per-row rule would leave no way to write it;
`wiki/log.md` and `wiki/index.md` refused;
`wiki/sources/` pages immutable after creation.

**`done()` derives `ingested` rather than asking for it.** `serve.py`'s `on_done` marks
the reading-list article wikified only on `__ingested__:1`, and that flag used to be an
optional boolean the model had to remember twenty-odd rounds after the work — with nothing
in the reply to say it was forgotten, and the only consequence on a page the model never
sees. A 23-round ingest that updated fourteen pages and created its source page reported
`__AGENT_DONE__:0` and left the article unwikified; every round of it was served by the
fallback model, which is the variation that turns "usually remembers" into "did not".
`done()` already knows — there is an inbox path and a source page, and the refusals above
guarantee both — so it derives, keeps the argument as a backstop, and an explicit `true`
still wins. A raw file with `fetch_failed` still reports `0`, because no source page was
created for it and there is nothing to derive from. Same shape as `ensure_h1`.

**The template is a guess about the name, not a description of the page.** The model
reaches `update_section(path, "Overview")` without reading anything, because
`LOBOTOMY.md`'s entity template guarantees an `## Overview` and that guess is right nearly
always. The danger is the belief that comes with it. A page updated for a year grows
headings the template never mentions, and the failure was an **asymmetry**: a guess that
MISSES already got the full section list from the not-found reply and self-corrected,
while a guess that HITS got that one section and nothing else — so material belonging
under "Sanctions and the Oil Sector" was merged into Overview with nothing to notice. The
hit is the common case, so the quiet failure was the common one. Both replies now name the
page's other sections (names only; the body is already in the reply).

`append_section` had the same cause and a worse result: asked for the template's
"Positions" on a page calling it "Political Stances", it created a second section for one
subject and reported success — `_heading_dupes` cannot see that, because the *names*
differ. That one **reports rather than refuses**, because creating a section is legitimate
and a refusal would have no escape hatch: there is no "yes, really" argument, so the model
would loop or give up (principle 4).

**Four refusals hand the needed content back in the same response** — `update_section`
(unread section), `update_file` (unread page, and stale page), `create_file` (page
exists) — and handing it back is only half the job. Each must also say
`— do NOT call read_file first` (or `read_section`) and wrap the payload in
`<file path="…">` / `<section path="…" name="…">` delimiters. `update_section` was the one
missing both, and an observed ingest was handed the section text and called `read_section`
for it anyway: a full round, plus one of the inter-round pacing windows, to fetch what it
was already holding. A refusal that leaves the wasteful route open is one the model will
take. `tests/test_handback_refusals.py` asserts the contract across all four at once so
they cannot drift apart again.

**A tag is one string however the model wrapped it** (`norm_tag`, `parse_tags_line`,
`render_tags_line`). Observed: `tags: ["justice-department", `law-enforcement`,
`federal-agency`, "drug-policy"]` — backticks, because the model renders a tag name as
code, and that is not YAML quoting at all. The single bad line was not the bug; the bug is
that it **spread**, and doing so needed four separate places to disagree about what a tag
is. `update_file` passed the model's `tags:` line through verbatim — `type:` is sanitized
three lines away, from the `concept}EX_HEAT_CP` episode, and `tags:` never was — so it
reached disk; `_collect_tags` stripped `"` and `'` but not backticks, so it entered the
wiki's canonical tag list; `orientation_message()` hands that list to every later ingest as
*"Prefer tags from this list"*; the model copied it onto the next page. **One page's
markdown habit became the wiki's vocabulary, and repairing a page by hand could not stop
it** — every other page carrying the tag fed it straight back on the next round. That is
the shape to watch for: a malformed value that is read back as input becomes
self-reinforcing, and the fix has to break the *loop*, not clean the value. All four sites
now go through one canonicalizer, `heal_pages` repairs pages already on disk (one
mechanically-correct answer, so absorbed per principle 1), and `serve.py`'s two tag views
used it too — a backticked tag was splitting one subject's tag page in two. Case is folded
for the same reason, since the schema already says lowercase.

**One reading and one rendering of a frontmatter scalar** (`fm_scalar`, `fm_quote`), which
is the same lesson as the tags one, found by asking whether it had siblings. It did, two:

- **A backticked title was invisible to the autolinker.** There were nine copies of the
  title regex in two variants — `["\']?(.+?)["\']?` in seven places, `"?([^"\n]+)"?` in two
  — and not one stripped a backtick, so a title the model wrote as code entered
  `_build_title_map` *with* the backticks and could only match text spelled the same way.
  A permanent silent miss. `_resolve_page`'s filename fallback means it did **not** also
  produce a duplicate page — the shared resolver earning its keep. All nine now go through
  `_fm_title`.
- **Quoting without escaping produced invalid YAML, and the readers then disagreed.**
  `create_file` interpolated into `"{value}"`, so a title containing a quote was written
  `title: "The "Big Lie""`, which `_parse_title_fields` read as `The "Big Lie` —
  `.strip('"')` eats every quote at both ends — while the `["\']?` regex read
  `The "Big Lie"`. Two readers, two titles for one page, and the autolinker used the
  truncated one. **This is why "just force double quotes on everything" is the wrong
  instruction**: more quoting without escaping spreads that defect. `fm_scalar` strips
  MATCHED pairs only, one layer at a time, then removes backticks outright (an unmatched
  one is not a pair, and no frontmatter value legitimately contains one).

Deliberately **not** normalized: `type:` (a bare enum, sanitized separately), `created:` /
`updated:` (YAML dates — quoting makes them strings) and `no_autolink:` (a boolean, where
`"true"` is a string that works only by truthiness accident).

**`serve.py` imports with `from agent import (...)`, so `agent.foo(...)` inside it is a
`NameError`** — and `list_inbox` wraps its parse in a `try/except` that swallows
everything, so the symptom was not a traceback but every title quietly falling back to the
filename. The suite touched neither of serve's two tag readers, so the same mistake sat in
the `/wiki/tags` path undetected. Add the name to the import list, and when a helper is
shared across the two modules, test that `serve.x is agent.x`.

**How to reach a page you are about to write to is ONE answer, in `_write_route`.** Three
replies hand the agent that advice — `lookup_titles`' UPDATE group (which `create_file`'s
worklist handback goes through as well), `done()`'s refusal, and LOBOTOMY.md's Step 5 — and
each used to carry its own copy of the reasoning. **The copies disagreed**: on a page over
`_WIKI_READ_LIMIT`, `lookup_titles` said *go straight to `update_section`* while `done()`'s
refusal said *`read_section` first*. An agent told two things does one of them at random,
and the round it spends is real either way. Worse, `_page_shape` had no coverage case at
all, so `lookup_titles` sent the agent to `read_file` a page it was already holding — the
exact waste `done()`'s copy had been fixed to avoid.

The resolution is composition, not round count. Round count alone says go straight to
`update_section` — 2 rounds at any size — and that is wrong because it counts rounds and not
COMPOSITIONS. Reading first is the same two rounds (`update_section`'s refusal hands the
text back, so the blind route costs a round too) but the agent writes ONCE with the text in
front of it, instead of composing blind and then re-deciding while holding a draft aimed at
the section it guessed; an observed 58-page ingest did precisely that, resending Overview
after a refusal that had already named the alternatives. A sunk draft beats a list of names.
That argument does not change at the read limit — only the call does, since `read_file`
returns an outline there — which is why the large-page case is `read_section`, and why
`lookup_titles` listing section names is what makes it reachable. Three routes:

| condition | route |
|---|---|
| session has full read coverage | `update_section` directly, do not read again |
| ≤ `_WIKI_READ_LIMIT` | `read_file` first, then `update_section` |
| larger | `read_section` the named section, then `update_section` |

`ROUTE_LEGEND` is built by quoting the per-page phrases, so a marker cannot appear on a row
with nothing explaining it. The `read_file` outline is deliberately **not** folded in: it
answers a different question — per-section edit vs whole-page rewrite, keyed on the output
budget rather than the read limit — and merging two decisions into one helper is how a
shared answer starts being wrong for one of its callers.

**LOBOTOMY.md had the same advice twice because it had the whole procedure twice.** Step 5
(entities) and Step 6 (concepts) carried 100 identical lines, so the stale wording had to be
fixed in two places and only ever got fixed in one. Step 6 now points at Step 5.
`tests/test_schema_no_duplicate_blocks.py` fails on any repeated run of six-plus non-blank
lines in either LOBOTOMY.md or this file — the schema is prepended to every request of every
round, so a duplicated half-page is paid thousands of times a day, and two copies of a
procedure drift. A repeated bullet or sentence is fine and is not reported.

**A `fetch_failed` raw file deadlocked `done()`.** Nothing was fetched, so there was no
article text — and a fetch-failed ingest has exactly the shape the completeness guards look
for: no source page, no entity pages, no listed names. The agent called `done()` saying so
and was refused with *"this ingest created a source page but no entity or concept pages"* — a
source page it had not created and could not create. It called `done()` again, got the same
answer, and only the refusal cap released it. **No move satisfied the guard**, which is
principle 4's worst case: work demanded that cannot be performed.

`fetch_failed` was already recognised in two other places — `serve.py`'s `on_done` refuses
to mark such an article wikified, and `done()`'s `ingested` derivation reports 0 — so the
gap was only in the completeness checks. `_inbox_fetch_failed()` is checked before them now.
**Absence is not evidence:** an unreadable or missing raw file does NOT count as
fetch-failed, or a path bug would open the gate on every ingest.

The same refusal also claimed "created a source page" unconditionally and then pointed at
that page's lists for the next move, so an ingest that had written nothing was told about a
page it did not have. It now describes what the session actually did, and names
`create_file` when there is no source page yet.

**Creating a source page hands back the lookup for its own `## Entities` / `## Concepts`
lists.** Not merely to save the round — **a lookup taken before the source page exists is
over the wrong set by construction.** From a log: the agent called `lookup_titles` first
with SEVEN names and was told outright that "Patrick Lennox" had no page and not to
`read_file` it. Two rounds later it did. In between it wrote a source page listing TEN
names, so the lookup covered a guess; when the agent went back to work the list it worked
the page's list, which the earlier answer did not correspond to. The handback is over the
committed list, produced in the message that brings that list into existence, and it comes
from `_lookup_titles` itself so the two cannot disagree — read route included, which is
what makes the CREATE group say "there is no file there to read".

**`done()`'s refusal is where the agent re-plans, so it carries the routing too.** It used
to say "read_section the section you are changing" for every unhandled page at any size —
true when written, stale once `lookup_titles` started marking the route. Observed in a
20-round ingest: the agent followed the route for its first two pages, called `done()`
early, met this refusal, and then `read_section`'d all four remaining pages. It did what it
was told. One of those four it had already read IN FULL nine rounds earlier, with coverage
credited, so `update_section` would have gone straight through — the round was spent
fetching what it was holding. Its rows now come from `_write_route` like everyone else's.
**The pattern to watch: any reply that gives procedural advice has to track the tools
underneath it.** That is what produced the duplication above, and the remaining places where
it still applies independently are the `read_file` outline and the four handback refusals.

**A short page is handed back whole; a long one is handed back a section.** The
unread-section refusal used to return the guessed section plus the other sections' NAMES at
any page size, which made it stingier than the tool it stands in for: under
`_WIKI_READ_LIMIT` a plain `read_file` returns the entire page, so one session got two
different answers about how much to show. It now returns the whole page below that limit —
`## Sources` stripped, since it is rendered from the `sources:` frontmatter in the same
payload — and **credits full read coverage**.

Two reasons, and the second is the expensive one. It adds no context the agent could not
already have: the diligent path is `read_file` then `update_section`, which costs the same
bytes plus a round, so this makes the correct path free rather than the guess cheap. And
crediting coverage is what stops the SAME page being refused once per section — an observed
58-page ingest paid one refusal per page, and a page needing three sections paid three.
`update_file` becomes legitimately reachable on that page too, which is a real loosening of
a read-before-write guard, justified because the agent has genuinely seen all of it.

The threshold is `_WIKI_READ_LIMIT` rather than a new constant, so there is one rule instead
of two that can drift. **This superseded an explicit earlier decision** — a test named
`test_the_section_body_is_handed_back_not_the_whole_page` asserted the opposite — so the
replacement says so where that test used to be. Watch the branch split when changing either
path: `tests/test_handback_refusals.py` uses a short fixture and therefore exercises only
the whole-page branch, and the long-page branch's "do NOT re-read" clause lost its coverage
that way; `mutate.py` reported it MISSED, and `tests/test_unread_section_handback.py` now
covers both.

**Hover cards** (`/api/wiki/<path>/preview` + the IIFE in `wiki.html`). Hovering a wiki
link for 300ms shows title, type, blurb and — only above four — a section count. The blurb
comes from `first_desc_line`, which was **hoisted out of `_rebuild_index`** for this, so a
page's index entry and the card you get hovering a link to it cannot say different things.
Armed on keyboard focus as well as hover, skipped entirely under `(hover: none)`, and the
card is `pointer-events:none` so it can never swallow the click it describes. One fetch per
DISTINCT href, cached for the page's life, because a wiki page carries a hundred links to a
dozen targets. `tests/test_hovercards.py` drives the real script AND the real CSS, both
lifted from the template by regex; it serves the fixture from a **real origin** rather than
`set_content`, because on `about:blank` a relative `fetch('/api/…')` has no base, throws,
and every assertion then fails for a reason unrelated to the code.

**The reading view's blank lines came from `_clip_fetch`, not from the renderer**
(`_tidy_for_reading`). Asked to strip extra blank lines out of the reader, and markdown
turned out to be innocent: it already renders two blank lines and twenty as the same
paragraph break. The blank lines were real, in the stored text. `handle_data` appends the
whitespace **between tags**, so HTML indented like ordinary HTML yields
`First.\n\n  \n\n    \n\nSecond.` — lines that LOOK blank but hold spaces, which
`re.sub(r"\n{3,}", ...)` cannot match because the spaces sit between the newlines.
**Measured: three paragraphs of normally-indented HTML produced ELEVEN blank-looking
lines.**

Stripping trailing whitespace first turns each into a genuinely empty line and the existing
collapse then works. Fixed in both places on purpose: in **`_clip_fetch`** so new captures
are clean ON DISK — not cosmetic, since that text is what every ingest round re-sends to
the model — and in **`inbox_view`** for display only, because everything already captured
still carries them and a reading view must never rewrite the file it is reading.

**Trailing-only, never leading.** An indented line that has content is a code block, and
flattening those to tidy spacing would be a worse fault than the one being fixed.

Two presentational changes went with it: a 70ch measure on `.item-reader-html`, and the
"Open original ↗" link, which used to appear only when there was NOTHING to read — exactly
backwards, since "is this the whole article?" is the question you have *while* reading the
captured text.

**Search says it is working, and says so when it fails** (`.search-busy`, the `catch` in
`doSearch`). Reported as *"search is slow, so sometimes I can't tell if it is searching or
not working."* There was no busy state at all: between the keystroke and the results the
popup kept showing the PREVIOUS query's hits, which does not read as pending — it reads as
a **wrong answer**, and search greps the whole wiki so that can last seconds.

The spinner waits **180ms** before appearing. Showing it immediately makes every fast
search flicker, which is its own noise; under that threshold a wait does not register as
one, so a quick search goes straight from old results to new and only a slow one explains
itself.

The worse half was that **`doSearch` had no error handling**. `apiFetch` throws now, so a
failed search was an unhandled rejection — the popup kept the previous query's results and
the message went to the console, so a search that FAILED looked exactly like a search that
found those older things. The error text goes through a `searchEsc` local to that scope,
since the search IIFE had no escaper and an error message carries text the server chose, a
proxy's HTML page among them.

`tests/test_search_busy_state.py` drives the real script and the real CSS from a real
origin. **The delay belongs in the BROWSER, not in the route handler**: sleeping in a sync
playwright handler blocks the driver, so the test cannot look at the page while the request
is in flight, which is the entire thing being asserted — two tests passed that way by luck,
observing the DOM only once the sleep had ended. Wrapping `window.fetch` in the fixture
delays the transport without touching the driver. Give the observation a generous window
too: the spinner lives from debounce+180ms to debounce+delay, and a 400ms delay left ~200ms
to catch it, which fails on timing rather than on behaviour.

**A source page's `## Entities` / `## Concepts` are sorted** (`sort_lookup_lists`), in
`create_file` and in `heal_pages` — the latter because a source page is immutable to the
LLM once written, so an existing one can be ordered nowhere else. They are lookup tables,
which is what `_is_lookup_row` already treats them as. **`## Claims` and `## Timeline` are
deliberately not sorted**: the first is prose in bullet form that reads top to bottom, the
second is chronological and belongs to `normalize_timeline`. The key is the row's text with
links flattened and case folded, since on disk these rows are already autolinked and raw
text would sort by `[` and then by target path.

### Reading a large page

`_read_file` on a wiki page over `_WIKI_READ_LIMIT` (20,000 chars) returns an **outline** —
frontmatter, every section name, each section's size and its opening `_OUTLINE_PREVIEW`
chars — not the text. It used to return the first 20,000 chars and then instruct the model
not to work from them, so the ingest paid for that chunk on every subsequent round.

`offset` defaults to **-1, not 0**: "no offset given" and "offset 0" are different
requests. Without an offset you get the outline; with an explicit `offset=0` you get a
paged chunk. That distinction is load-bearing — the Regenerate Workflow rewrites a whole
page with `update_file`, which requires full read coverage, which requires paging to be
reachable.

### Reorganizing a page

`update_section` refuses a rewrite that cuts a section by more than 40% (`_old_cmp >= 800
and _new_cmp < _old_cmp * 0.6`), because that is what an ingest looks like when it runs
out of output budget and silently condenses a page instead of failing. But consolidating a
page *is* shrinkage — moving a duplicated point out of one section makes it smaller — so
`allow_shrink=true` is the deliberate opt-out, and both refusals name it. It exempts that
one check and nothing else: read-before-write and the heading rules still apply.

Without it there was no route at all on a large page: the section tools could not shrink,
and `update_file` is steered away above half the output budget. `LOBOTOMY.md` section 6b
is the workflow — read every section involved, plan the whole move, **write the
destination first and confirm it**, then shrink the source.

### Unfolding events and timelines

An event that arrives across several sources (an outbreak, an election, a trial) gets its
own entity page, with `## Overview` as the current state (replaced each visit) and
`## Timeline` as the record. `add_timeline_entry(path, date, text)` **inserts in
chronological position rather than appending**, because sources are not ingested in the
order events happened. It accepts partial dates (`2026-03`, `2026`), refuses future dates
and duplicates, and re-sorts the whole section on every insert, so a timeline that is
already out of order heals on the next write.

**Reading a timeline bullet and writing one are different jobs.** `_render_timeline`
emits exactly one shape; `_TL_BULLET_RE` accepts every shape a hand-written timeline
arrives in — bold date or bare, em dash, en dash, hyphen or colon — because anything it
does not match is filed as prose, and an unrecognised entry goes not merely unsorted but
*undeduplicated*. That is what produced a page carrying eight bullets for four events:
`create_file` writes a whole page in one call, so a page about an unfolding event is born
with a hand-written timeline before the tool has anything to add to; the tool did not
recognise those bullets, kept them above its own list, and wrote every one of the same
facts again underneath. `LOBOTOMY.md` does say the Timeline is never maintained by hand,
but a rule the model has no way to obey at `create_file` time is not worth a refusal —
principle 1 — so `normalize_timeline()` absorbs the hand-written form on every write path
and in `heal_pages`. The date is matched with `(?![-\d])` so the engine cannot backtrack
`2026-09-12` to `2026-09` and read `-12` as the separator.

Duplicates are decided by `_tl_dedupe`, shared by the tool and the normalizer so they
cannot disagree about what counts as a restatement. Exact-text comparison was not enough:
two sources wording one event differently produced two entries, and one page carried the
same death three times. The rule is deliberately narrow — same date, and every meaningful
word of one entry already present in the other — and the *longer* wording wins, in the
shorter one's position. Two entries that each contribute a word the other lacks are two
events on one day and both stay. Because a fuller wording *replaces* what it absorbs, the
list can stay the same length while the page changes, so "already there" is decided by
comparing what the section will say, never by counting entries.

`normalize_timeline` must be idempotent: `heal_pages` runs at startup and after every
ingest, and a normalizer that rewrote on every pass would fill page history with empty
revisions forever. A Timeline that is the page's last section is the case that breaks
this — watch the trailing blank line.

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

Each revision is stamped with **why** it was written: `<ts>__ingest.md`, `__user-edit`,
`__relink`, `__revert`, `__heal`, `__merge`. The reason goes after the fixed-width
timestamp so lexical order stays chronological, which the history view and the pruning
both depend on. `write_reason("…")` scopes it to a block and restores the previous value —
never `set_write_reason` bare inside a pass, because `heal_pages` runs at startup and after
every ingest and a leaked reason would mislabel every write after it. `init_session()`
clears it, so a reason cannot survive into the next job. Revisions written before this
have no suffix and render without a label.

**The +/- counts are words, not lines.** Pages are written with unwrapped lines, so one
paragraph is one line: a whole paragraph rewritten reported `+1 −1`, and a regenerate was
indistinguishable from a typo fix. `_delta` diffs word lists with `SequenceMatcher`, which
is also why the numbers on existing rows changed when this shipped.

`page_history(p)` builds what the view shows and lives in `agent.py` rather than
`serve.py` so it is reachable without flask. **A row is a version, not a change** — which
is what the Compare button already assumes, since it diffs that version's content against
the current page. The storage makes this easy to get backwards: revision file `R_i` holds
the content as it was BEFORE the write at `T_i`, so `R_i`'s stamped reason describes the
write that **destroyed** that content, and `R_i`'s content is what the write at `T_{i-1}`
produced. Attaching `R_i`'s reason to `R_i`'s own row labels a version with the cause of
its own deletion and puts the newest write's label one row too low. Every version
therefore takes its timestamp and reason from the revision **below** it, and the newest
write's reason lands on the current page. The oldest row is the earliest content still
kept; whatever produced it has been pruned, so it carries no timestamp, reason or counts
rather than a guessed one.

**`wiki/.history/` is inside `wiki/`, and every tool that walks the tree must exclude it.**
A revision is a record, never a link target, a page, or a search hit. Two ways this bites,
both found in `repair_links.py`: `rglob` matches **directories**, and a page deleted from
the wiki leaves `wiki/.history/entities/united.md/` behind — a directory carrying the
page's name — so a filename search "found" the deleted page and repaired every dead link
to point inside the history store. And at ~9,000 pages × 50 revisions the store holds a
few hundred thousand files, so a per-item walk over it turns a pass into a hang: a dry run
that takes 1.7s with `.history` excluded ran over two minutes without, on a tree a quarter
the size. `_wiki_pages()` in that file is the pattern — filter on `relative_to(HISTORY_DIR)`,
and match files, not paths.

**Each row also says what the write touched**, which is what the empty middle of the row
was for. Two halves, gathered in deliberately opposite ways:

- **Which sections changed** is *derived* from the same unified diff that produces the
  +/- counts, by mapping each changed line back to the heading above it. Derived rather
  than stored precisely so it works on the revisions already on disk — a stored field
  would only ever have described writes made after it shipped. Frontmatter is excluded:
  `updated:` moves on every write and would put the same useless word on every row, and
  the H1 is not a section, so a title fix reports nothing rather than the page's own name.
- **Which tool wrote it** is stored too, as a fourth part. "ingest" says a source was
  folded in; it does not say whether that was a whole-page regenerate (`update_file`,
  labelled **rewrite** and highlighted) or a one-line fix, and those are very different
  things to find in fifty rows. `_call_tool()` scopes it around the whole dispatch — both
  agent loops go through that one helper, because when each had its own copy only one of
  them was wrapped, and which loop served the request decided whether the row could say
  anything at all.
- **Which source an ingest folded in** cannot be derived, so it is *stored*, as a third
  `__` part of the revision filename. **Store the whole slug** — a reading-list capture
  slugs to `https-www-nytimes-com-2026-09-23-world-canada-mark-carney-calls-trump-tariffs-a-rupture`,
  87 characters, and an earlier 72-char cap truncated it into something that named no
  file. Nothing failed: `serve.py` fell through to its "the source page is gone" branch
  and the row rendered as plain text, so the symptom was a link that was simply absent.
  `serve.py` also resolves a stored slug that is a unique prefix of one source page, which
  is what repairs the rows stamped while the cap was in place. `_current_source_page` is
  already on the session context at snapshot time, so nothing new is tracked. `_parse_revision_stem()` handles
  all three shapes — bare `<ts>`, `<ts>__<reason>`, `<ts>__<reason>__<source>` — and the
  timestamp is still the fixed-width prefix, so lexical order stays chronological. Only
  ingests carry one; claiming a relink sweep came "from" an article would be a confident
  lie. **Every slot before a filled one is written, with `-` as the placeholder** — skip an
  empty one and the later parts shift left, so a write with a tool and no reason parsed as
  reason `-`, source `update-file`, tool nothing. `serve.py` resolves the slug to the source page's title for display, falling back
  to the de-slugged text if that page has since been renamed or deleted.

Served by `/wiki/<path>/history` (list), `/wiki/<path>/history/<rev>` (unified diff via
stdlib `difflib`), and `/api/wiki/<path>/revert/<rev>`. Revert goes through `_atomic_write`,
so it snapshots the current content first and is itself undoable.

### Wiki page lifecycle

1. `create_file` / `update_file` / the section tools → write frontmatter + body; `sources:` is merged from disk plus the session's source page (never trusted from the LLM); `_inject_sources_section` renders the `## Sources` section; `_autolink_now` links the page
2. `done()` → `_post_process_session()` runs once: patches `sources:` on every touched page, autolinks them all, re-injects `## Sources`, rebuilds the index
3. Server lint checks run after `done()`; results visible at `/wiki/lint`

### `system_prompt()` and `LOBOTOMY.md`

`agent.py:system_prompt()` reads `LOBOTOMY.md` as the LLM's operating schema and appends a tool quick-reference table. The LLM operating instructions (ingest workflow, query workflow, page format, naming conventions, etc.) all live in `LOBOTOMY.md`, not here.

**A guard added in code needs a matching line in `LOBOTOMY.md`.** Discovering a rule by
refusal costs a round; reading it in the schema costs nothing.

## Key Conventions

- **`raw/` is immutable for the LLM** — code in `_update_file` blocks the LLM from writing outside `wiki/`. Raw source files live flat in `raw/` (no subdirectories). `serve.py` manages their lifecycle via `_mark_inbox_wikified`.
- **`wiki/log.md` is append-only** — written by `_auto_write_log_entry` at `done()`; `update_file` refuses it.
- **No `[[wikilink]]` syntax** — standard relative markdown links only.
- **`create_file` for new pages, `update_file` for existing ones** — `create_file` auto-fills `created`/`updated`; `update_file` restores system-owned fields (`created`, `raw_source`, `type`) from disk.
- **Headings are plain text.** No links in them, no dates naming the section, no heading repeating the page's own title.
- **Every page opens with `# {title}`; sections are `##`.** `ensure_h1` adds the H1 in `create_file`, restores it in `update_file` and backfills it in `heal_pages` — it is derivable from `title:`, so it is filled in rather than demanded. The file carries it because the wiki must read correctly in any markdown viewer; `render_md` and `render_md_shareable` strip it because the web templates print the title themselves. Level-1 headings are skipped by `_page_section_names` and exempted by `_bad_headings`, so the H1 is not a section and never trips the title rule — but a *section* written as `# Overview` is then **invisible to every listing while still being readable and writable.** (This entry used to claim such a section "can never be read or edited by the section tools". Measured: `read_section` finds it and `update_section` rewrites it. The real failure is quieter and worse — it is missing from the `read_file` outline, from the "other sections on this page" hints and from `section_inventory.py`, and `_bad_headings` exempts it so nothing reports it, so the page holds content its own map denies.) `heal_pages` therefore demotes a stray level-1 heading to `##` via `demote_stray_h1s` — one right answer, so it is absorbed. It skips the first heading, which is the page's own H1, and skips fenced code blocks, where `#` is a shell comment and not a heading; an H1 repeating the page title is reported rather than demoted, since demoting it would manufacture a `## <page title>` the heading rules refuse. Observed on `artificial-intelligence.md`, which carried `# Applications` and `# Current Debates & Challenges`: asked to reorganize it, the model planned from an outline naming neither of the page's two largest sections.
- Internal wiki links use paths relative to the page's location: `../entities/foo.md` from `wiki/sources/`.
- File names: `lowercase-hyphenated-slugs.md`. Source slugs encode `{author-or-org}-{year}-{short-title}`.
- The `## Sources` section in entity/concept pages is auto-generated from frontmatter — never write it manually.

## Tests

```sh
python3 tests/run_all.py              # everything; exits non-zero on failure
python3 tests/run_all.py test_timeline  # one module
python3 tests/mutate.py               # break each guard, check a test notices
python3 tests/mutate.py read-before    # just the mutations whose name matches
```

`mutate.py` runs the entire suite once per mutation, so it takes minutes and gets slower
every time a test is added — each new test is paid ~78 times over. Start it in the
background and do something else; the pattern argument is there for when you only need the
one you just added.

`run_all.py` is stdlib `unittest` over `tests/` (repo root — the suite covers the
whole project, not just `tools/`), built on a `TempWiki` harness that
rebinds all four module globals, resets the thread-local session context, clears the
autolinker caches, and asserts its own isolation.

**That isolation claim was false, and a green suite was the proof of nothing.** The entry
check only proves `agent` SEES the temp tree; it cannot catch a module that captured a
path at import, because `from agent import RAW_DIR` binds the VALUE and the rebind is
invisible to it. `tools/add_story.py` did exactly that, wrote nine test stories into the
repo's real `raw/`, and a `git add -A` committed them. Nothing failed at any point.
`TempWiki` now snapshots `RAW_DIR` and `WIKI_DIR` on entry and raises on exit if either
changed, naming the files — which catches any module holding a captured path, not just
that one. It costs ~4ms across the whole suite, and it is restricted to those two globals:
`REPO_ROOT` is the whole checkout, whose `__pycache__` churns on every run, and
`HISTORY_DIR` is inside `WIKI_DIR` already.

`tests/test_no_data_files_committed.py` is the other half, because two independent failures
had to line up: the write, and nobody noticing the files in the commit. It allows only the
repo's skeleton — two `.gitkeep`s and the generated index/log/tasks pages — under `raw/`
and `wiki/`. **It matters beyond tidiness:** `deploy.sh --full` rsyncs `raw/`, so a
committed test story becomes a junk item in the reading list of a live server.

**Link-rewriting passes must skip `agent._GENERATED_PAGES`** (`index.md` anywhere,
`log.md`). `merge_page`, `rename_page.py` and `repair_links.py` share that one list so they
cannot drift. Two different reasons, and the log's is the serious one: patching `index.md`
is undone by `_rebuild_index` seconds later, but **repointing a link in `log.md` rewrites
what the log says happened** — the entry recording that an ingest created
`new-york-university-langone-health.md` would come to claim it created
`nyu-langone-health.md`, which it never did. A record edited to agree with the present is
not a record; the dead link is the true statement, and lint skips `log.md` so it costs
nothing. Neither file can be reverted either, since `_snapshot_version` skips both.
**The catch:** once a pass stops repointing `index.md` it *must* rebuild it, or it leaves
the dead link the repoint used to fix. `merge_page.py` already did; `rename_page.py` had to
start.

`run_all.py --failfast` stops at the first failure, and `mutate.py` passes it. That is
sound rather than a corner cut: the CAUGHT/MISSED verdict is the runner's exit code and
nothing else, so one red test is the entire answer and the other five hundred add no
information. A MISSED mutation still pays for the whole suite, because proving that nothing
objected means running everything. Measured on one mutation: 294 tests in 0.76s against
513 in 3.79s, same verdict. **Do not use `--failfast` for an ordinary run** — the count it
prints is the count it reached, not the count that exists, which is why the summary line
says so out loud when it stops early.

**`mutate.py` is the one that proves the suite works.** A green run says nothing on its
own: this disables one guard at a time and requires a test to go red for each. Every
mutation must report CAUGHT. Add one whenever you add a guard — if you cannot write a
mutation the suite catches, the guard is untested. It edits the file in place and restores
it, so do not run it anywhere near a live server. A mutation targets `agent.py` by default;
a fourth tuple element names another file (`"tools/repair_links.py"`), because the
maintenance tools carry guards too. **Every file a run touches is restored before the next
mutation is applied**, not just at the end — without that, mutations in different files
stack, and a guard whose removal nothing notices is reported CAUGHT on the strength of the
previous mutation's failures. That shipped briefly and inflated one result; a mutation
runner that lies about coverage is worse than not having one.

**Never run two `mutate.py` processes at once, and never edit a tool file while one is
running.** Each run snapshots every file it will touch at startup and restores from that
snapshot — so a second run, or your own edit, is silently reverted, and worse, one run's
restore can leave the *other* run's injected mutation applied. Observed: an interleaved
pair left `_WIKI_READ_LIMIT = 20_000_000` sitting in the working tree. Nothing failed
loudly; `read_file` simply stopped returning outlines and `update_file`'s coverage check
passed trivially, and the suite went from green to eleven failures with no edit to blame.
`git diff` on the tool files before committing is the check that catches it — the residue
is always a one-line change you did not write.

`rename_page.py` is the one maintenance tool whose logic is not in `agent.py`, so nothing
in-process can reach it — which is exactly how its copy of the generated-pages bug went
uncovered while `merge_page`'s was caught. `tests/test_rename_page_cli.py` runs the real
script against a `tools/` copied into a temp directory, since `REPO_ROOT` comes from the
script's own location. Worth moving into `agent.py` like the others.

**`deploy.sh` stamps `.version` into the jail** (`git log -1`, written after the rsync)
and `serve.py` logs it at startup — `Lobotomy starting — version: <sha> <date> <subject>`.
The repo's `.git` is not shipped, so that file is the only way the server can say what
code it is running. Check it before concluding a fix did not work: twice now, behaviour
the logs showed as unfixed was simply not deployed yet.

`deploy.sh` excludes `tests/` from what it ships — tests belong where the code is
changed, and `mutate.py` must never sit beside a running server. It does not run them
either: deploy copies files and nothing else. Run the suite yourself before deploying.

`tests/test_template_js.py` parses every inline `<script>` in `tools/templates/` with
`node --check`, skipping when node is absent. The templates carry real logic the Python
suite cannot reach, and a syntax error there kills the whole block silently — the search
popup's keyboard navigation would just stop working with nothing in any log.

`docs/test-plan.md` is the spec the suite was built from; read it before adding a module.

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
              "max_rpm": 15, "inter_request_delay": 5, "max_tokens": 16384 },
  "email":  { "resend_api_key": "...", "from_address": "..." }
}
```

`max_tokens` (default 16384) is also the model's output budget, and the write paths derive
a whole-page-rewrite threshold from it — a page whose rewrite would exceed roughly half the
budget is steered to the section tools, because the model silently condenses rather than
failing loudly when it runs out of room.

LLM providers use OpenAI-compatible APIs. The `agent.py:PROVIDERS` dict maps provider names to base URLs and default models. Provider config can also override `api_base` and `model` per-provider inside `config.json`.

**Model fallback on 429.** A provider block may list `fallback_models`. Because free-tier quotas are per-model, `_post_with_fallback()` treats a 429 as "this model is spent" rather than "the provider is down": it reissues the same request against the next model in the chain immediately, and only raises — handing control back to the existing two-phase backoff — once every model is rate limited. Only 429 walks the chain (`_LLMError.rate_limited`); 500s, timeouts and connection errors are provider-wide and would fail identically on every model, so they propagate at once. A rate-limited model goes into `_model_cooldowns` and is skipped until it lapses (`retry_after` or 60s; `daily_quota_poll_interval` when the body names a `PerDay` quota), which keeps later rounds from burning a wasted call on a model already known to be exhausted. Cooldowns are the only state — nothing is sticky, so the primary is retried first as soon as its window passes.

**The fallback warning names which quota and how long is left**, because "unavailable" is
one word for two situations that call for opposite responses. `max_rpm` throttles requests
per *minute*, so it is exactly what protects a per-minute limit — and it cannot preserve a
per-*day* quota at all, since that is a fixed count of requests: an hour at `max_rpm: 1`
spends precisely as much of a daily budget as an hour flat out. An observed ingest ran
every round on the fallback with the primary "unavailable" and nothing said whether the
deliberate one-request-per-minute pacing was buying anything. `_model_cooldowns` had
recorded both facts since fallback was added; the warning just never read them.

`max_rpm` is also the dominant term in ingest wall-clock, and that is a deliberate setting,
not a bug to fix: at `1` the gap between requests is a flat 60s against 3–5s of actual
work, so a 19-page ingest spends ~38 of its ~40 minutes asleep. Before proposing a change
there, check `_model_cooldown_left` in the log — if the primary is out on a per-day quota,
going faster costs nothing extra.
