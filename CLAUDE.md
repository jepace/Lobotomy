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
`overview_drift.py` (which summary sections have turned into piles of dated headlines),
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

**`/inbox/add` must never destroy an existing item** (`_raw_items`, `_find_existing_capture`,
`_dupe_url_key`, `_unique_raw_name`). Asked *"does the reading list notice if I try to add a
duplicate item?"* — it did not, and the reason that never showed up as duplicate ROWS is
worse than duplicate rows would have been. The destination filename is derived
deterministically (from the URL, or from the first 60 characters of pasted text), so a
second add resolved to the same path and `_atomic_write` **silently overwrote it**. All
three measured, all three answering `{"ok": true}`:

- **Re-adding a URL reset `wikified: true` to `false`.** An article already folded into the
  wiki came back as unread, and wikifying it again is another ~40-minute ingest at
  `max_rpm: 1` of a source already ingested.
- **Re-adding a URL whose text you had pasted in by hand replaced that text** with an empty
  `fetch_failed: true` placeholder. The paste was gone — the same class as the save-failure
  bugs above, from the opposite direction.
- **Two genuinely DIFFERENT articles pasted from one site collapsed into one file.** The
  slug is the first 60 characters, and a news site's chrome ("Skip to content Skip to site
  index Sections Search Subscribe for $1 a week Log in Today's Paper World U.S. Politics…")
  runs to **151** before the headline. The first article was destroyed, silently.

The rule the three share: **editing is a separate route, so the add path has no business
overwriting anything.** A story already here is either a duplicate — reported, never
rewritten — or a different story whose slug collided, which gets `-2`. One deliberate
exception, and it destroys nothing: a capture that fetched NOTHING and has no body is what
re-adding the URL is *for*, so that retries the fetch.

**`_normalize_capture_url` does not do URL normalization in the sense you would assume** —
it unwraps Firefox's `about:reader?url=…` wrapper and nothing else. A comment here claimed
it handled tracking links and the two tests asserting that failed. `_dupe_url_key` is the
comparison key: scheme, `www.`, host case, trailing slash, fragment and known tracking
parameters come off. **Compare normalized, store verbatim** — the same rule as `_norm_prose`,
because the Original-article link has to go where the user actually saved. The tracking list
is fixed and short on purpose: plenty of sites put the article's identity in a parameter
(`?id=`, `?story=`), and merging two articles loses the second exactly as the overwrite did.

`_raw_items()` is deliberately separate from `list_inbox()` and is **not** on the 8-second
poll — the per-file frontmatter parse is paid once when you save a story. `add_story.py`
already refused to overwrite (`if dest.exists(): return 1`); the web route, the one anybody
actually uses, had no check at all.

**The reading list's status markers, colours and filter** (`.wikified-badge`,
`.act-btn.wikipage`, `markRowWikified`, `applyFilters`). Three complaints at once, and the
most expensive thing was none of them.

- **"The Wikified tag is smaller than a button and sometimes I can't hit it."** It was
  `font-size: 10px` in `padding: 1px 6px` — about 14px tall against the ~44px a finger
  needs — **and it was a link**, so the element too small to hit was also the only way to
  reach the page the article became. It is a marker now; the clickable job moved to a real
  `.act-btn` in the action row at the same size as every other control.
- **"The colours are confusing, do they mean something?"** Partly, and the confusing part
  was real rather than taste: `--accent` blue was Read AND Wikify — the free instant one
  and the one that spends ~40 minutes and a per-day quota, identical to look at — while
  `--success` green was Archive (an action) AND the Wikified badge (a state). One meaning
  per colour now: blue the free primary action, grey neutral and reversible, **amber the
  expensive one**, red destructive, **green a state and never an action**. `--warn` had to
  be added to all three theme blocks — a button that vanishes in dark mode is worse than a
  confusing one.
- **A status filter** (All / To read / Wikified). The trap is that search already wrote
  `style.display` directly, so clearing the search box set every row visible including the
  ones the filter had hidden. **`applyFilters()` is the only writer** and reads both
  predicates — the same "one answer in one place" shape as `_write_route`. It also owns the
  count, because the poll used to overwrite that whole element and would have deleted the
  span it writes into.

**Found while doing it, and worse than any of the three: nothing disabled Wikify on an
article already wikified.** `item.wikified` rendered the badge and nothing else, so one
click on a finished item started another ~40-minute ingest re-folding the same source into
the same pages — and the badge was the only thing saying it was done. Marking a row
wikified is now one helper (`markRowWikified`) called by both paths that reach that state,
the 30-second poll and `wikifyItem`'s own completion; each had its own copy of the badge
code, and the half missing from BOTH was the disable. `saveEdit` removes the View page
button along with the badge, since that page was built from the text the edit replaced.

**Both renderers, always.** Every row exists twice — the Jinja loop and the poll's prepend
path — so a change to one is a bug in the other, and the tests assert the pair.

**One label for a tool call, in every view that shows one** (`tool_arg_preview`,
`window.agentEventLine`). Asked whether the chat log and the reading-list log differ, then
narrowed: *"it's the chat log for AFTER an ingest is complete that does not display
sections."* Fifty lines of `⚙ update_section wiki/entities/alberta.md` against a handful of
pages, with no way to tell which section any of them touched.

**The live view was never wrong; the record of it was.** Three places built this label.
`agent.py`'s `/chat/events` stream and its debug log both appended `§ <section>` and always
had; `serve.py`'s saved display log took `args["path"]` and nothing else, so a turn that
read `update_section …/alberta.md § Overview` while running redrew from history without the
section the moment it finished. `tool_arg_preview()` is the only builder now, and `serve.py`
imports the NAME — `agent.foo` there is a `NameError`, and `save_history` wraps its parse in
a `try/except` that swallows everything, which is exactly how such a mistake stays invisible.

Two more drifts fell out of the same comparison:

- **The gear was a bug, not a style difference.** `chat.html` rendered the same `tools`
  array with two different map expressions — one prefixing `⚙ `, one not — and both ran
  over the whole array, so whichever event arrived LAST decided how every earlier line was
  drawn. **A single retry stripped the gear off every tool line above it**, which is why
  the pasted chat log had none while the reading list's had one per line. The icon lives in
  the TEXT now and one `renderTools()` draws the list.
- **The retry wording differed and the reading list had the worse half**: `"Retrying in
  60s…"` against `"AI busy — retrying in 60s (attempt 1/60)"`. Seven of the first in a row
  say nothing about whether anything is happening; seven of the second count 1/60 to 7/60
  and name the cap. The better one was on the page nobody watches during a wikify.

`inbox.html` also went through `innerHTML` **without escaping**, on a string containing a
path the model chose; `chat.html` had escaped since it was written. Both escape now.

**The path in each line is a link, to the SECTION where there is one** (`toolLineHtml`).
The logs name the file every write touched and reading one meant copying the path into the
URL bar by hand. The line already says which section was edited, so that is where it lands:
`update_section wiki/entities/alberta.md § Overview` links to
`/wiki/entities/alberta.md#overview`. python-markdown's `toc` extension gives every heading
that id, and `_headingSlug` reproduces its rule — drop everything that is not a word
character, whitespace or hyphen, lowercase, collapse runs to one hyphen. JS `\w` is
ASCII-only where Python's is not, so an accented heading can slug differently; that costs
nothing, because a wrong anchor lands at the top of the right page, which is where no anchor
would have landed.

**Escape first, then build the anchor from the parsed pieces.** Every string here — the
path, the section name — is one the MODEL chose. The display text goes through `_esc` and
the href is assembled from a restricted character class, so `wiki/entities/<script>…</script>.md`
matches no path, is never linked, and renders as escaped text.

Three views show these lines and one function links them: the chat page's live stream, the
reading list's progress block, and the chat page's COMPLETED turns — which come from Jinja
rather than the event stream and need their own pass, scoped to `.tool-list.static` so it
can never re-process a list the live renderer owns. That third one is the same view that was
missing the sections to begin with.

**A tool line is written whether the call SUCCEEDED or was refused, and linking it turned
that ambiguity into a false claim.** Reported as a link that 404s:

    ⚙ create_file wiki/entities/2026-r-a-f-fairford-bombing-plot.md

The page did not exist and never had. The agent called `create_file` for the event page
BEFORE the source page existed, `create_file` refused — *"create the source page first"* —
and the line appeared anyway, because the event is emitted whatever the tool returned. That
was merely vague until the path became a link; **a link asserts the page exists**, so
making these clickable turned a vague line into a wrong one. My regression, from the commit
before.

The outcome was already known at the yield and already recorded in `_session_tool_calls`;
it just never reached the view. The event carries `ok` and `why` now, a refused line is
marked `✗` and says why, and `toolLineHtml` refuses to link it — **a 404 is worse than
plain text, because it claims otherwise**. The saved display log gets the same treatment by
pairing each call with its `{"role": "tool", "tool_call_id": …}` result, already in the
transcript; a missing id counts as success, so a provider that omits one gets today's
behaviour rather than every line marked failed.

**Two of the four mutations for this were MISSED on the first run, and both were the tests'
fault rather than the code's** — the same shape twice, worth keeping:

- *A fixture that cannot reach the code it names proves nothing.* The no-link test used
  `"✗ create_file  wiki/entities/x.md  — refused"`. The linking regex is anchored at
  end-of-string, so a trailing reason means it never matched **at all** — the `✗` guard was
  never exercised and removing it changed nothing. The line has to END in the path, and the
  test now also asserts the unmarked form IS linked, which is what proves the fixture
  reaches the guard.
- *Testing the renderer is not testing the producer.* Every other test here built the event
  dict by hand. Setting `"ok": True` in `agent.py` broke nothing, because no test had ever
  driven the real loop. `EventContractTest` scripts the model's replies through
  `_post_with_fallback` and reads what `stream_agent_turn` emits. It neutralizes exactly one
  config key — `inter_request_delay: 5`, two requests a test, four tests, **41 seconds** —
  because `mutate.py` pays for the whole suite once per mutation.

**A test that greps for a string the code and a COMMENT both contain proves nothing.** The
first assertion here checked that `"attempt "` appears in `base.html` — it does, in the
comment explaining this bug — so `mutate.py` deleted the attempt counter from the code and
the test still passed. It runs the lifted function under `node` now and reads the output.

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

**A scan could not be told from the owner, and it buried the log.** Asked *"im being
probed — are we cool?"* over thirty `GET /admin.php -> 404` lines interleaved with a
running ingest. Cool on that scan — every path is a `.php` file, this is Flask, so no
interpreter is in the request path and probing cannot put one there; `debug=False`, so the
Werkzeug console (the one real RCE in a Flask app) is off; both `send_file` handlers are
`@require_login` and resolve-then-contain; and the fifteen unauthenticated routes are the
login/share flows plus `/api/*`, which all go through `_api_auth()` — **which fails closed**,
answering 501 when no key is set rather than letting `"" == ""` authenticate everyone.

But the log could not say any of that, for two separate reasons.

- **It could not say WHO.** Everything arrives through nginx, so `request.remote_addr` is
  the PROXY on every line — the same `192.168.x.x` for the scan and for the owner loading
  `/reading-list`, which is how you can tell it is the proxy. The real address existed only
  in nginx's log. `_client_ip()` reads `X-Forwarded-For` **only when the immediate peer is
  private or loopback**, because that header is attacker-controlled: reached directly it
  returns the real peer rather than believing it. The **rightmost** entry is the one our
  proxy observed; anything to its left the client supplied and could have invented.
- **And it drowned the thing being asked about.** This project has already had its disk
  filled once by its own log volume, so a line per probe is not a safe default — but
  dropping them silently is worse, because then the next "am I being probed?" has no answer.
  The first is logged in full, the rest counted, a summary per window: **32 probes → 2
  lines.** The discriminator is `request.url_rule is None`, which Flask sets only when
  NOTHING matched; a 404 from a route that exists is a real answer to a real request and
  keeps its own line.

`_api_auth` also compares with `hmac.compare_digest` now — a plain `!=` leaks the key a
character at a time to anyone who can measure the reply, on a route that needs no login.

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
where the row still offers Wikify while the key is already free. `_job_keys.pop` is what
makes a double release safe, and it is the only thing that needs to — an added
`_keys[k] == job_id` test looked like belt-and-braces for exactly that and was unreachable,
which `mutate.py` reported as a MISSED guard.

Two call-site consequences. `inbox/process-all` must **advance to the next item** on a
duplicate: that job's `on_done` belongs to the click that started it, so ours never fires
and the batch would stop dead at that item. And `window.wikifying` in `inbox.html` is only
the local half — it stops a double click before the round trip, in a `finally` so the early
`return` on a failed ingest cannot leak it.

**Wikify All never touched the queue, and the batch lived in the browser.** Reported as
*"the wikify all button doesn't seem to tie into the work queue? The number didn't go up in
the q"* — and the number was right. Two independent defects, in the template and in the
route, each sufficient on its own:

- **`wikifyAll()` looped in the BROWSER**: `for (const nm of names) await wikifyItem(nm)`,
  one article at a time, each `await` waiting out a ~40-minute ingest before submitting the
  next. So one job ran, the queue behind it was empty, and `pending` — which
  `JobQueue.status()` reports as `qsize()` and the badge in `base.html` renders as "N
  articles queued" — was **0 for the entire batch**. It never called
  `/inbox/process-all` at all; that route was unreachable from the UI.

  **The badge was the symptom; the real defect is that the browser WAS the batch.** Close
  the tab, follow a link, or let the laptop sleep after article three of thirty, and the
  remaining twenty-seven were never submitted, with nothing anywhere recording that they
  were meant to be.
- **And `/inbox/process-all` was chained the same way**, server-side: `_submit_item` queued
  item 0 and only item 0, submitting the next from that job's `on_done` — then **answered
  `{"queued": len(unprocessed)}`**, claiming to have queued thirty articles having queued
  one. The chain bought nothing, because the queue has a single worker and N queued jobs run
  in the same order as N chained ones. What it cost: the backlog was invisible; a
  **cancelled** job killed the rest of the batch, since `_worker` calls `on_done` only
  `if on_done and not cancelled`; and `_batch_running` was cleared at the END of the chain,
  so one cancel left it True forever and every later click answered `409 A batch is already
  running` until the server was restarted.

`_batch_running` is **gone rather than fixed**. The per-item dedupe key already prevents
double submission — a second click finds every item in flight and queues nothing — so the
flag was a second mechanism answering a question `submit(key=…)` had already settled, with
a failure mode of its own. A second click now reports what is already running instead of
refusing, the same choice `submit` makes.

The split that remains is deliberate: **`wikifyItem` is what a single row's button does**
and still streams that job's tool calls live, because watching one ingest is useful and
watching thirty is not. Per-row progress during a batch arrives through the existing poll,
which calls `markRowWikified` as each one lands.

One trap in the rewrite, avoided deliberately: thirty closures built in one loop need their
per-item values bound as **defaults** (`def _setup(_p=inbox_path_str, …)`). Late binding
would hand every job the LAST article's session, which nothing would report — you would
find it by reading the pages the ingest wrote.

**Whether an article is being ingested is the SERVER's answer** (`JobQueue.in_flight`,
`list_inbox`'s `ingest` field). Asked *"will clear queue remove those?"* about a batch the
new Wikify All had just queued. It does — `drain()` drops every waiting job, releases its
dedupe key and leaves the running one to finish — **and the reading list then went on
saying "Queued" for all of them until a page reload.**

That was a regression from the commit before: Wikify All now disables every row it queues,
so where one stale row had been possible there were thirty. But looking at it found two
older faults of the same shape, and all three have one cause — **the browser was inferring
in-flight state from what it had itself started:**

- **Clear Queue left every row disabled** against a server that had released the keys and
  would have taken every one of them again.
- **A reload mid-batch showed every queued row as ready to Wikify.** Nothing was
  double-ingested, because the dedupe key refused the click — but the page was wrong about
  thirty articles and invited a click that did nothing visible.
- **Nothing said WHICH article was being written.** The badge gave a count and the nav went
  busy; the row in flight looked like the other twenty-nine.

`_keys` has held the answer since the dedupe guard was added and nothing could ask for it.
`in_flight()` returns `{key: "running"|"queued"}`, `list_inbox` asks **once per listing**
rather than once per item (the rule the source-page map here learned the hard way, on a
path polled every 8 seconds), and every item carries `ingest: "" | "queued" | "running"` —
so a page load, a poll and a drain cannot disagree. A queue that cannot answer costs the
column and never the page.

**The asymmetry is the part worth remembering.** Every other branch of `patchItemStatus`
patches a one-way transition — content arrives, a page becomes wikified, neither ever goes
back — so the function's shape quietly assumed state only moves forwards. In-flight state
moves both ways, and **the direction nobody wrote is the one a drain needs.** When adding a
field to that function, ask what clears it.

**The queue was in-memory only, and nothing said so** (`_save_pending`, `resume_pending`,
`serve._resume_ingest`). Reported as *"reloading or navigating away resets the reading list
so I can't see what's in the ingest queue / wrong data is displayed."* Reloading the PAGE
was already fixed by the entry above; what was left explains the same symptom and is worse.
**Queue thirty articles, restart the server — a deploy IS a restart — and all thirty were
gone.** The articles stayed in the reading list, so nothing was lost permanently, but ~20
hours of queued work at `max_rpm: 1` silently became nothing and the list then correctly
showed every one as idle, which reads exactly like the display being wrong. `_recover`'s own
docstring says "the work itself is not lost either way" — true of the article, false of the
queue.

Three decisions:

- **Only the KEYS are persisted.** An ingest's messages are derivable: the key is
  `ingest:<raw filename>` and `serve._ingest_job` rebuilds the history, setup and `on_done`
  from that file. Writing the messages would store every queued article's full text plus
  the orientation message, then replay a prompt built against a wiki that has moved on.
  That construction was **hoisted out of the route** so a resumed job and a freshly queued
  one cannot be built two different ways — the resume path is the one nobody watches.
- **Only the WAITING ones, never the one that was running.** If that article is what killed
  the server, re-queueing it is a crash loop that survives restarts. It is in the reading
  list and one click re-runs it. A resume builder may also DECLINE — the article may have
  been wikified or deleted while the server was down — and the mirror is cleared **before**
  any attempt, so one that cannot be built is not retried on every restart forever.
- **A chat turn is never recorded.** No key, messages derivable from nothing, and an
  interactive turn whose browser has gone is not worth resuming.

**The bug this found was in the mirroring, and only a REAL queue showed it.** Two threads
write that file — the submitting thread records a job joining, the WORKER records one
leaving — and the first version shared one `pending.tmp`. Both wrote it, both called
`replace`, the loser got `ENOENT`, and the file that survived was two interleaved writes
(`["ingest:c.md"] "ingest:c.md"]`) which `_load_pending` then refused as malformed. **So a
restart silently resumed nothing — the exact failure the feature exists to prevent.** With
a single-threaded fake it looked perfect. The save now holds the lock and uses a
pid-and-thread temp name.

**`/queue` is the page for it** (`JobQueue.listing`, `drop`, `queue.html`), asked for in the
same message: *"maybe a way to view and manage the queue is needed."* Until then the only
thing that reported the queue at all was the count in the badge — and a count cannot say
which article is in flight, which are behind it, or let you drop one without dropping all
of them. The badge's label is now the link to it, so the thing that tells you there is a
queue is the way into it.

`listing()` reads the Queue's deque under its mutex, because there is no non-destructive
public way to look at a `queue.Queue` — getting that wrong would EAT the batch, which is
what its test exists for. `drop(job_id)` removes one waiting job and **releases its key**,
for the reason `drain` does: a dropped job never runs, so the worker's release never fires,
and a leaked key makes that article permanently un-wikifiable. It refuses to touch the
RUNNING job, which makes dropping always safe — cancelling is the separate, louder call,
and its confirm says what it costs (a part-written article, pages already changed left in
place, the source not marked wikified so a re-run folds the whole thing in again).

**Three test modules were each duplicating `JobQueue.__init__`'s attribute list**, so the
two attributes this added broke all of them at once — fourteen errors in one run, none
about the thing being tested. `harness.parked_job_queue` is the one fixture now.

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

**That line-by-line comparison was also losing text, silently, and `--carry` is what found
it.** Asked *"can you make merges easier? editing deltas by hand is a real pain"*, over a
`find_duplicate_pages.py` run showing **35 groups, 73 pages**. Everything in a merge was
mechanical except one step — it refuses while the loser still says anything the survivor
does not, prints those lines, and leaves you to move them — and across 35 groups that one
step is the whole cost of the cleanup.

Making the report usable meant reporting the delta rather than the paragraph holding it: a
claim is a LINE, a section's prose is one line per paragraph, so an Overview repeating the
survivor's first sentence and adding one clause came back whole and had to be diffed by
eye. Splitting it into sentences to report it exposed the bug. **The old test asked whether
either line contained the other** — so a loser paragraph that repeated the survivor's and
then APPENDED a sentence satisfied `survivor_line in loser_line`, counted as already-said,
and the merge went through and deleted the page with that sentence in it. No `--force`, no
refusal, nothing in the output. The one shape where a page genuinely extends another is the
one shape that was dropped, and the symmetric test that caused it looks obviously correct.
Comparison is per sentence now; a line the splitter cannot divide still gets the symmetric
test, because a one-sentence paragraph merely reflowed is not new material and treating it
as such would make every merge refuse.

`--carry` then appends each non-summary line to the survivor's section of the same name.
Three things it had to get right:

- **A summary delta is carried and MARKED, and refusing it was the wrong call.** The first
  version refused, on the grounds that appending to an `## Overview` is precisely the
  accretion `_accreted_dated_sentences` refuses and `overview_drift.py` reports on 11,000
  pages. That reasoning is sound about the accretion and was wrong about the cost: across
  28 duplicate groups it meant 28 separate summary rewrites, **each one blocking the merge
  behind it**, which is exactly the hand work `--carry` exists to remove — *"could you just
  carry into the overview / definition so I don't have to do this one at a time. Mark the
  page with TODO or something, but just do it?"*

  **The marker is the whole difference.** What the write guards refuse is a summary growing
  by a sentence per ingest with **nothing recording that it happened** — the page then reads
  as though somebody wrote it that way, and only a drift report ever notices. A carried
  summary gets a visible `**TODO — merged from <page>, fold into the summary above:**` line
  AND `todo: "merge: summary needs rewriting"` in frontmatter, so it is a declared,
  listable, temporary state: `grep -rl '^todo:' wiki/entities wiki/concepts`. Both halves
  are needed and each has its own mutation — a marker only a grep can see is one nobody
  acts on, and a marker only a reader can see cannot be listed. A clean merge is **not**
  flagged, or the listing is every page ever merged and says nothing.

  The generalisable part: **a guard against silent drift is not a guard against drift that
  announces itself.** Where the objection is "this will be forgotten", a marker answers it
  and a refusal just moves the work. `--strict-summary` keeps the refusal for anyone who
  would rather do it page by page.
- **A differently-named section is created and REPORTED, not mapped.** The first version of
  this claimed it would land `## Political Stances` in the survivor's `## Positions`, and
  said so in its docstring and its test. It cannot: deciding those two headings name one
  subject is a judgement, not a string comparison, and a tool that guessed would merge a
  section about policy into one about appointments. So it does what `append_section` does —
  create it and say so — because creating a section is legitimate and a refusal would have
  no escape hatch.
- **The refusal has to name the SURVIVOR's summary section.** Reported from a live run a
  day after this shipped, merging `concepts/war-in-iran.md` and `concepts/iran-war.md` into
  `entities/us-iran-war.md`: the refusal said `update_section(section='Definition')`, which
  is the LOSERS' heading, because they are concept pages. The survivor is an entity, so its
  summary is `## Overview` — following that instruction is either refused for a section
  that does not exist or, through `append_section`, puts a `## Definition` onto an entity
  page. **Principle 4's documented worst case, shipped**: a refusal that renames one
  violation into another. Nineteen tests passed over it, because every one of them merged an
  entity into an entity, so the two headings were the same word. `_survivor_summary` reads
  the survivor's own heading where it has one and falls back to `_OPENER` for its type,
  naming `append_section` rather than `update_section` when the section is not there yet.
- **A carried list has to stay a list, and that took three fixes.** From the same run. The
  losers' `## Contradictions` is `- **Claim**: …` followed by its indented `Status:` line.
  `"\n\n".join` looked obviously right and made three one-item lists out of one structure;
  joining tightly was not enough, because the carry used the sentence-filtered text and
  every sentence had been `.strip()`ed, so `  Status:` arrived with no indent and stopped
  being a continuation whatever it was joined with. A line with nothing dropped is carried
  **verbatim** now, and a partial carry keeps its own leading marker. The general lesson:
  **a tool that rebuilds a line from its parts has to put the markup back, and markdown's
  markup is whitespace.**
- **And `_fold` was not stripping the list marker.** `_claims` strips a line's marker before
  comparing, so a survivor bullet is stored as bare prose — while a loser *sentence* kept
  its `- `, never matched anything, and the bullet was carried whole. **Every list item
  whose first sentence the survivor already had was duplicated on merge.** Found by a test
  written for the indent fix, which is the second time this week a bug surfaced from a test
  aimed at something else.
- **The carried text has to reach disk on the refusing path.** `_merge_page_impl` writes the
  survivor once, near the bottom, after the aliases and sources are folded in — and a
  summary delta `return`s before that point, so a carry was computed, reported in
  `result["carried"]`, and dropped. **Every unit test passed**, because they all asserted on
  the result dict or on a merge that succeeded; running the real CLI against an
  AIPAC-shaped fixture showed two sections missing from the file. The lesson is the one this
  file keeps relearning: a test that reads the return value is not a test that the write
  happened.

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

**A leading national qualifier is the same subject** (`_jurisdiction_match`,
`_JURISDICTION_PREFIXES`), and the duplicate it produced was made by the LAST call of an
ingest: the run updated `entities/commission-of-fine-arts.md` early on and twenty rounds
later created `entities/u-s-commission-of-fine-arts.md`. `_initialism_match` cannot see it —
`U.S.` is not an initialism OF anything in the other name, it is a word added in front of
it — so every check answered NO PAGE, and `find_duplicate_pages.py` will not report the
pair either, because the two titles have different keys. It runs as a **separate loop**
from the initialism one, which is gated on a shared first letter: sound for an initialism
and wrong here by construction, since the qualifier is what changes the first letter.

**The ≥2-word remainder is the whole guard.** "US Steel" is not "Steel", "US Open" is not
"Open", "U.S. Bank" is not "Bank"; at two words and up the shape is an institution's name
and the qualifier is a formality prose adds and drops freely. **"federal" and "national"
are deliberately not in the list**, and the asymmetry is why: a false match here does not
create a duplicate page, it sends the write to the WRONG page. "U.S." is a place and never
part of what a body does, while "National" and "Federal" sit inside formal names whose
remainder is often a subject of its own — a "National Gallery" is not a "Gallery". The
observed pair was geographic, so the list is geographic; add one when a real pair is
observed, not because it looks similar.

**And `create_file` had no title-level duplicate check at all** — only the path. That is
the call that actually wrote the second page: `lookup_titles` and `done()`'s worklist both
share `_resolve_page` and both would now route this name to UPDATE, but neither is on the
write path, so nothing stood between the model and the file. It refuses on the resolver
now, which is safe because `_resolve_page` only ever **declares** a match (exact title or
alias, the page's own slug, a spelled-out initialism, a leading qualifier) — the same
answer `lookup_titles` gives for that name in that session, so the refusal cannot
contradict the routing the model was handed.

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

**A summary section is rewritten, never appended to** (`_accreted_dated_sentences`,
`_summary_accretion_refusal`). Reported by showing a live `florida.md` and asking *"does
this read like an Overview to you?"*:

    Florida is a U.S. state located in the southeastern region. In August 2026, housing
    market data showed typical home values at $375,470… In 2026, amid nationwide
    redistricting battles… As of October 2026, the state is also battling a significant
    dengue outbreak… In October 2026, state officials announced that Florida would
    discontinue the use of Flock Safety…

Measured: one paragraph, seven sentences, 1,723 characters, 18 links — and **only the first
sentence is about Florida.** The other six are six different articles, each appended by the
ingest that read it, four opening with a date.

**Nothing was malformed and no guard fired.** Every individual write was a small, legal
addition to a section that permits additions. This is the exact mirror of the shrink guard
— which refuses a section that CONDENSES — and that guard's own comment already named this
failure as "the pile the wiki is not supposed to become". Growing by a sentence at a time
was never checked. It is also not a formatting problem: reflowing it into paragraphs gives
five tidy paragraphs about housing, redistricting, primaries, dengue and surveillance, none
of which is an overview of Florida.

Two conditions, and both are needed or the check is useless or a nuisance. **The old text
must survive verbatim** — the accretion signature, since a genuine revision rewrites the
summary to account for the new material. And **the added text must open a sentence with a
date**, which is what separates a news item from a summary line. It deliberately does NOT
fire on a sentence that merely contains a year ("The 2026 primary season saw…"); the value
of a check like this is in not crying wolf, the `bleeding_titles` lesson. Compared with
links flattened and whitespace collapsed, because the page is autolinked and the agent
writes plain text, so a byte comparison reports "changed" for a sentence nobody touched.

Both write paths go through the one helper — guard `update_section` alone and the damage
simply arrives via `append_section`, where appending to a summary is accretion by
construction. Scoped to the ADDED text only, so a page that is already a pile stays
editable and the write that repairs it is the one to make (principle 2; an earlier
whole-page heading guard deadlocked 176 pages by forgetting this).

The refusal names four moves, because which one fits depends on something only the model
knows: a new named section, `add_timeline_entry`, a page of its own, or the summary resent
rewritten. It quotes the offending sentences, since a refusal that names the text it is
objecting to is one the model can act on.

**Its first live ingest found two defects in that refusal, both in one log.** The agent met
it on `pete-ricketts.md` and **resent the identical call twice more** — three refusals for
one sentence, ~60s of pacing each. Four named moves were not enough on their own, because
nothing in the reply CHANGED between attempts, and a model reading a refusal as "that did
not go through" tries again. A per-`(page, section)` counter on the session context now
escalates: the second and later refusals say outright that this exact call has already been
refused, and **drop the menu** for the one call that always works. A menu is what it just
failed to act on.

And option 1 **steered it into another guard** — told to name a section for the subject it
chose `'2026 Senate Campaign'`, which the date-in-heading rule refuses, costing another
round. That is principle 4's documented worst case, renaming one violation into another, and
here it is near-certain rather than unlucky: this material is dated by definition, so the
obvious name carries its year. Both refusals now say the name must carry no date, with the
example. The lesson generalises — **a refusal that tells the model to write something new
has to respect the rules the OTHER guards will apply to it.**

**Then a whole ingest was measured and the menu turned out to be ranked backwards.** Asked
*"happy with this run?"* over a production log; counted: **82 tool calls, 36 refused
(44%)** — 20 read-before-write, 8 summary accretion, 3 shrink, 2 page-exists — 39 pages
touched, 11 refused twice or more, and **11 NEW sections created on existing pages** from
one article: Leadership, Legal Challenges, Political Treatment ×2, Election Reporting,
Political Advertising Role, State Legislative Initiatives, Recent Developments,
Contemporary Political Proposals, Recent Controversies, 2026 Midterms Context.

Option 1 was *"a standing feature of the subject → append_section"*, and it is the cheapest
of the four moves to take: no date to extract, no judgement about whether the event is a
subject, no rewrite of existing prose. So it was taken reflexively, and **a page with a
section per news item is the same pile this guard exists to prevent, in a different
shape** — a heading per headline instead of a sentence per headline. The menu is ordered
timeline → page of its own → rewrite the summary → new section, with the last marked as a
last resort and the eleven named out loud; LOBOTOMY.md section 5 lost its *"the answer is a
new section, not Overview"* line for the same reason, and the test asserting that wording
was replaced rather than deleted so the reversal is on the record.

**And three of those eleven headings were changelogs, which nothing refused**
(`_RECENCY_HEADING_RE`). "Recent Developments", "Recent Controversies" — the dated-heading
mistake with the date left implicit, and **implicit is worse**: "## 2026 Senate Campaign"
at least records which year it froze at, while "## Recent Developments" claims to be
current forever, nothing revises it, and the next ingest adds its own news underneath. The
argument for refusing a dated heading applies word for word.

**A recency qualifier is required, and a bare noun is never refused.** `## Controversies`
is a legitimate standing section on a person's page and `## Events` on a festival's;
refusing those would be the crying-wolf failure every report here is built around. It is
the "Recent"/"Latest"/"Current"/"Ongoing" in front of the noun that makes the changelog,
and a mutation that loosens `match` to `search` is what keeps that honest. The date rule
wins where both match ("Recent 2026 Developments"), because it is the more specific finding
and its refusal is the one that explains the rename — so the order inside `_bad_headings`
is load-bearing. Both accretion refusals name this rule too, since a model told to name a
section for dated material reaches for exactly this heading: principle 4's worst case again,
one violation renamed into another.

**And the read-before-write refusal says outright that the draft was written blind.** It
was 20 of the 82 calls, with 11 pages refused twice or more — the handback removed the
round trip and did nothing about what the model does with the text, and a model reading a
refusal as "that did not go through" resends the same draft. Both branches now say, before
the payload, that the draft was composed without the page in front of it and that
**resending it unchanged is the one response that wastes what it is being handed** — REVISE
on the whole-page branch, MERGE on the section one. Placement is asserted: a model that
stops reading at the handed-back text never reaches an instruction underneath it.

**The guard decides WHERE material goes; it says nothing about what deserves recording,
and the first thing it steered produced an under-recorded page.** The same ingest put a
224-character paragraph on `los-angeles.md` and a 222-character one on `san-diego.md`,
because a president had said on the record that enemies should be allowed to destroy both
cities. This entry first called that "a passing rhetorical mention" and asked whether it
should have touched those pages at all. **That was wrong, and wrong in a way worth naming:
it inferred the importance of an event from the number of bytes the model wrote about it.**
The raw file was `trump-on-war-in-iran-let-em-take-out-los-angeles-let-em-take.md` — the
remark was not mentioned in the article, it WAS the article.

The real defect is the inverse. The source page's `## Entities` list named Donald Trump,
Los Angeles, San Diego and Iran — **the participants, and not the story** — so the account
of what happened had nowhere to live and was split into a sentence per participant. The
guard was right that it did not belong in Overview; option 3 (a page of its own) was the
right move and the model did not take it, because every example the refusal and the schema
gave was a SLOW-BURNING event — an outbreak, an election, a trial, an investigation — so a
single day's remark read as not qualifying.

**A page is the only thing this wiki can link**, and that is the mechanical argument rather
than an editorial one: the autolinker matches page titles, so a subject with a page is
linked from every page that mentions it in every future ingest, forever, while a subject
with only a section is remembered in the one place somebody happened to put it. "Findable
everywhere it is relevant" is precisely what a page buys and a section cannot. LOBOTOMY.md
Step 3 now says the event itself belongs on the entity list beside the people and places in
it, the event template covers a single incident as well as an unfolding one, and option 3
names the discriminator the model actually needed: **is this page the subject of what
happened, or a participant in it?**

One trap closed with it: a model freshly refused for the dated HEADING `## 2026 Senate
Campaign` will over-generalize and decline to name the event at all, so the schema says
outright that a page TITLE may carry a date — "2026 Irkutsk Plague Outbreak" is fine. The
heading rule exists because a section is revised in place forever; a page is about one thing
that happened once.

**The sentence splitter was the fiddly part.** "U.S. Senate" split in two and the fragment
then opened with a capital, so no heuristic about the FOLLOWING text could repair it — the
test has to be on what precedes the dot. A fixed-width two-character lookbehind rejects a
dot preceded by a single capital (U.S., J.D.), and a short abbreviation list covers
"Sen."/"Dr."/"Aug.", where the dot follows a lowercase letter.

`tools/overview_drift.py` is the other half, for the ~11,400 pages already written: the
guard stops the wiki acquiring more, and nothing else could say how many already have it.
It reads, never writes, and **offers no repair on purpose** — where a dated sentence
belongs is a judgement about what the page is for.

**`tools/page_report.py` is the whole-page version of that question.** Asked *"if I gave
you the text of the current donald-trump.md page, what could you do with it? It's pretty
much a complete mess."* — 223,659 bytes, fourteen sections, 53.8s to autolink against
13,195 titles. **The mess was nine separate defects**, and most of them are counting
problems no human can do by eye at that size: five empty sections (three of them with
sources for their subject in the page's own frontmatter, so ingested text was missing);
two parallel dumping grounds holding the same pile in prose and in bullets, saying the Lake
Ontario renaming six times and the Sept 29 AI accord in four separate bullets; and one
sentence verbatim twice. Report-only, no LLM, no `--apply`, for `overview_drift.py`'s
reason.

Three of its checks are worth naming here because each one is a filter rather than a
finding, and the filter is the hard part:

- **A `PILE` needs size AND dated news.** Size alone reports the section that is working —
  a page's principal section is supposed to be its biggest. Dated openers alone report the
  Timeline of every event page in the wiki.
- **The duplicate comparison must see through the autolinking, and must use containment.**
  Two copies of one paragraph are reliably linked DIFFERENTLY, because the once-per-section
  budget is keyed on `(target page, section ordinal)` — so a byte comparison finds nothing
  on a real page. And the common shape is a short bullet wholly absorbed into a long
  paragraph, which Jaccard scores low exactly when it matters; the short one being fully
  contained IS the finding. A bullet's bold label (`- **AI**: …`) comes off first, or the
  commonest duplicate shape there is — one sentence written once as prose and once as a
  labelled bullet — matches nothing.
- **A split proper name (`White [House](…)`) is only reported when the FULLER name is a
  page the autolinker knows.** Three weaker tests each cried wolf: a capitalised word
  before a capitalised one-word link flags `Trump [Republicans](…)`; adding an honorific
  list fixes `President [Trump](…)` and not that; and a word ending in a dot made
  *"at the White. [House](…) members objected"* into the name "White House", because `.` has
  to be in the word pattern for "U.S." and `_norm_name_key` strips it back out
  (`_ABBREV_TAIL_RE` is agent.py's own answer to that and is shared). Asking whether "White
  House" is itself a title settles it **the autolinker's own way** — the title map is sorted
  longest-first precisely so the longer name wins, which makes every hit a race the linker
  is designed to win and lost, and therefore something `_title_upgrade_re` should be able to
  repair.

**And one thing it deliberately refuses to answer.** Common-word bleeding — `[notes]`,
`[standing]`, `[power]`, dozens of them on that page — is listed and never flagged. A page
titled "Tariffs" is SUPPOSED to be linked from the word `tariffs` and one titled "Notes" is
not, and **nothing on a single page distinguishes them**: the evidence is wiki-wide, which
is what `bleeding_titles.py` reads. Duplicating a worse version of that answer would put
dozens of confident wrong findings in front of the real ones, which is the same
not-crying-wolf constraint every report here is built around.

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

**A leading underscore marks a tag as machinery, not subject matter** (`is_utility_tag`,
`_UTILITY_TAG_PREFIX`, `_TODO_TAG`). Asked for in two steps — *"add a Tag 'todo' for todo
items, so I can dig them out quickly"*, then *"maybe special case '_todo' or other
underscores so we can build some utility for the future"*, which is the better shape: a
namespace, so the next utility tag costs a name and no code. `merge_page --carry` sets
`_todo` on any page whose summary it folded, and `tag:_todo` or `/wiki/tags/_todo` lists
them.

**The trap is the backticked-tag loop exactly.** `_collect_tags` scans every page and
`orientation_message` hands that list to every ingest as *"Prefer tags from this list"* — so
adding `_todo` naively tells the model to prefer it, the model tags unrelated pages with it,
and the one listing the tag exists to produce fills with noise. The filter is therefore in
`orientation_message` and **deliberately not** in `_collect_tags`: the tag pages and
`/wiki/tags/_todo` must still see it, and filtering at the source would have removed the
feature while fixing the bug. A prefix rather than a list, because a list is a second place
to forget. `LOBOTOMY.md` tells the model never to invent, copy or **remove** one — it is
somebody's worklist — and the schema needs that line precisely because the tag is absent
from the vocabulary, so the only way the model meets one is on a page it is already editing.

**The entity/concept split cannot be trusted, so three things stopped depending on it**
(`_DIR_PAGE_TYPE`, `section_inventory.TEMPLATE`, `bleeding_titles._is_bleed`). Asked after
working out what the split actually costs: *"I can't trust that the distinction between the
2 types has been honored, so let's do the best we can."* The evidence it was not honoured:
the live wiki has `concepts/tim-wu.md` and `concepts/jesse-watters.md` (people),
`concepts/environmental-protection-agency.md` (an organisation), and `concepts/ebola.md` (a
proper noun) — and `rename_page.py` moved pages between directories for months **without
carrying `type:` along**, so a correctly-filed page could still claim the other type.

Worth knowing first what the split does and does not reach, because it is lower-stakes than
it looks. **Linking does not care** — `_build_title_map` globs all four subdirectories — and
**duplicate prevention does not care**, because `_resolve_page` searches `entities/`,
`concepts/` and `synthesis/` by slug, so a misfile never produces a duplicate page later.
What reads the field is `_OPENER`, `section_inventory.py`, `bleeding_titles.py`, and the
relative path in every inbound link.

- **`heal_pages` repairs the field from the DIRECTORY.** Every link to a page encodes its
  directory, `_resolve_page` searches by directory, and the page's own relative links are
  computed from it; the field is read by tools that cannot move a file. So where the two
  disagree, the one that cannot be wrong without the page being unreachable wins, and the
  repair has one answer — absorbed, not reported (principle 1). The **opener is deliberately
  left alone**: renaming `## Definition` to `## Overview` is a content edit,
  `promote_openers.py` is the tool for it, and doing it inside a startup sweep over 13,000
  pages buries a real change in a metadata pass. Those pages are counted and listed instead.
  The check had to be sequenced after the corrupt-type repair rather than `elif`'d onto it,
  or `type: concept}EX_HEAT_CP` in `entities/` healed over two passes instead of one.
- **`Timeline` is on-template for every type.** It was entity-only because the
  unfolding-event template is an entity template, and three things already disagreed:
  `add_timeline_entry` has never checked a page's type, the stub-event smell recognises an
  event page by the SECTION rather than the type, and the split itself is untrustworthy. A
  disease page under `concepts/` that tracks outbreaks was reporting as drift for carrying
  exactly the section the tool maintaining it writes.
- **`bleeding_titles` asks the TEXT first and the field only as a tiebreak.** The filter was
  `type != "concept"`, on the sound reasoning that a page titled "Tariffs" is supposed to be
  linked from the word `tariffs`. The reasoning is right and the field carrying it is not.
  **A capital in the MIDDLE of a sentence is proof the title is a name** — "the finale of
  Lost aired" is evidence, "Tariffs are a tax" at a sentence start is not, which is why the
  raw `cap` count was useless and `cap_mid` is not. Measured on a fixture: that gains a
  proper noun misfiled as a concept, which the old filter hid completely, and loses nothing.

  **The first version of that change went too far**, treating `cap_mid == 0` as proof of a
  common noun, and it dropped three existing cases at once: a page titled "Succession" whose
  name the wiki only ever writes lowercase has no mid-sentence capital, and the lowercase
  links to it are still wrong. **Zero is absence of evidence, not evidence of absence.** The
  field stays as the fallback — demoted from the decision to a tiebreak, which is the most
  it has earned. The three tests that caught the overreach were the ones already there.

**Search's two full-page substitutions ran before any keyword was tested** (`_prefilter_ok`,
`lit_groups` in `search_wiki_core`). Reported as *"search is pretty slow; are there cheap
optimizations we can be doing?"* Measured at 2,000 pages / 17.8 MB: **423ms for a query
matching nothing**, because every page paid `_SYS_FIELDS.sub` and then the link-URL rewrite
— two fresh copies of every page in the wiki, per query, to establish that none of them
matched. This is the autolinker's token-prefilter lesson for the third time, in the one
place that had never learned it.

Both substitutions only DELETE text, so a keyword absent from the raw bytes is absent from
the rewritten text: the raw test can only admit pages that still need the full check. That
alone gave 3.9× on a miss and **10% SLOWER** on a query matching every page, because there
the allocation is pure overhead — so the matching moved onto the lowered copy too. The
substitutions run on it, the exact check is `in`, the score is `str.count`, and every
`re.IGNORECASE` match and `findall` list-build is gone. One allocation now does three jobs
instead of being charged on top of them: **3.6× / 2.7× / 1.4×** at 0%, 16% and ~100% hit
rates, faster everywhere.

**The gate is the part that is easy to get wrong.** The link rewrite turns `](URL)` into
`]()`, so a deletion can forge an adjacency the original never had — a keyword containing
`]`, `(` or `)` can match the rewritten text while being absent from the raw bytes, and a
naive prefilter would skip a page that really matches. Dropping a `sources:` line joins two
lines the same way, but a keyword cannot contain a newline because `query.split()` built it.
So the fast path requires every keyword to be free of bracket characters; anything else
takes the old route and is merely slow.

**The benchmark was wrong before it was right, and that is worth keeping.** The first corpus
drew from a 17-word vocabulary, so every page matched every query and the prefilter looked
worthless — the measurement, not the code, was the thing being tested. A Zipfian vocabulary
of 6,000 words gives realistic hit rates, and only then does the shape of the win appear.
`tests/test_search_prefilter.py` carries a transcription of the ORIGINAL algorithm as an
oracle and asserts the two agree on paths, scores **and order** across eighteen query
shapes, because a faster search that returns something subtly different is not a faster
search.

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

**A `\uXXXX` escaped TWICE reaches disk as six literal characters** (`parse_tool_args`,
`_decode_stray_escapes`, `_STRAY_ESCAPE_RE`). Reported as *"i get literal ‘ and
’ in titles"*, and **nothing was malformed at any point**, which is why it sat there.
A provider hands a tool call's arguments over as a JSON string, so one `json.loads` is
correct and returns real characters; a model that writes `\\u2019` — escaping the backslash
as well, a habit from writing JSON inside JSON — produces *valid* JSON that parses to the
six characters `’`. The write succeeds, the page renders, `_build_title_map` indexes
it, and the title reads "Trump’s Plan" for ever. **The cost is not only cosmetic**: a
title holding those six characters can only match text spelled the same way, so the page is
unlinkable from every article that mentions it — the same permanent silent miss a backticked
title had.

Four decisions:

- **Decoded at the parse site**, because the escape reaches disk through whichever of the
  five write tools the model called, plus `section`, `date` and `text` on the others. The
  two agent loops each had their own `json.loads` line, so a fix in one would have left the
  other broken — and **which loop serves a round depends on whether a browser is watching**,
  so the bug would have come and gone with the UI and been blamed on the model.
- **Decoded to the character the escape names, not folded to an ASCII quote.** `_esc_flex`
  already treats ’ and ' as one character for matching, so a curly quote costs nothing,
  while flattening one would make a page's title disagree with the article it came from.
- **Code is exempt**, through the autolinker's own fence walk — factored out as
  `_map_in_code`/`_map_outside_code` so the two passes cannot disagree about where a fence
  ends. A page explaining JSON escapes writes `` `’` `` on purpose, and rewriting it
  would destroy the one page in the wiki that is about this.
- **Three shapes are declined and left as visible text.** A lone surrogate is the exact
  input `_atomic_write`'s `errors="replace"` exists to absorb, and decoding one turns six
  harmless characters into a byte that cannot be encoded. A control character was never
  typed, and `\u000a` inside a `title:` line would cut the frontmatter in half. And
  ` ` is declined on a judgement call: a non-breaking space in a title breaks autolink
  matching **invisibly**, where the literal escape breaks it visibly and gets reported, as
  this one was.

`heal_pages` decodes the ~13,000 pages already carrying them, because prevention alone
freezes the damage — the lesson `_MANGLED_URL_RE` and `_unlink_in_code` each paid for.
Absorbed rather than reported and with no `LOBOTOMY.md` line, because the escape names
exactly one character and there is nothing for the model to decide (principle 1); a schema
line is what a *refusal* needs.

**And then: *"the heal didn't affect the articles in my reading list with ’ or
whatevers."*** Correct, for two reasons rather than one. A reading-list item is a file in
`raw/`, which `heal_pages` does not walk — and **`raw/` is immutable**, so a listing must
not repair it even if it could. The split is the one `_tidy_for_reading` already made for
the blank-line collapse: **the capture path cleans new stories on disk, the display cleans
every story already there.**

**And this half was not the model's doing at all.** A site that renders its copy out of
embedded JSON leaves `’` in the HTML as six literal characters, and `_clip_fetch`'s
`handle_data` decodes HTML *entities*, not JavaScript escapes. `_fetch_and_patch` then takes
the capture's FIRST LINE as the reading-list title and `list_inbox` three lines as the
excerpt — so one site's habit surfaces as a title, an excerpt and the article you sit and
read. Three doors into the reading list and all three now decode on the way in:
`_clip_fetch` (a fetched URL), `/inbox/add` (a paste, which has no fetch to clean it, and
decoded *before* the slug is derived so the filename comes from the words), and
`add_story.py`. `list_inbox` and `inbox_view` decode for display only — the listing is on
the 8-second poll, which the `"\\u" not in text` early return makes free for a clean item,
and it scans a 100-character title and a 200-character excerpt rather than the article.

**Stated limit:** the raw files already on disk keep their escapes, so an ingest of one
still sends them to the model — harmless, because anything the model writes back comes
through `parse_tool_args` and is decoded, so the wiki pages come out clean. Rewriting
`raw/` to fix the bytes would trade a cosmetic flaw for the one invariant this project
does not break.

**Two bugs in the repair itself, and the tests that caught them are the ones worth
copying.** `fm_quote` escapes a backslash, so a title holding this bug sits on disk as
`title: "Trump\\u2019s Plan"` — a pattern matching ONE backslash ate the second, left the
first, and `fm_quote` re-escaped it on the next write, so **`heal_pages` rewrote the page on
every startup for ever**: a cosmetic repair turned into a drift loop, which is strictly
worse than the bug. And `subn` counts MATCHES rather than substitutions, so a *declined*
escape reported a repair that had not happened and a page holding one lone surrogate wrote
an identical revision on every pass. Both were found by the idempotence test, which is the
same requirement `normalize_timeline` carries — **any pass `heal_pages` runs needs one.**

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

**Hover cards** (`/api/wiki/<path>/preview` + the IIFE in `base.html`). Hovering a wiki
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

**It lives in `base.html` because the chat log and the reading list grew wiki links too**
— asked for after the tool-call lines became links. Moved rather than copied: a second card
is a second set of dwell, cache and placement rules to drift. Two things blocked the reuse
and each would have failed SILENTLY, producing no card rather than a wrong one:

- It was scoped to `.wiki-content`, which neither of those pages has. The delegation
  already fell back to `document`, so only the placement was wrong.
- `isPreviewable` rejected any href containing `#` — and a log link names the exact section
  a write touched, so **most** of them carry one. Fragments are allowed now and stripped
  for the lookup.

A wiki page's own links are written WITHOUT `.md` (`_resolve_wiki_href` strips it, the
route adds it back) while a log link carries it, so `previewUrl` normalizes both rather than
appending unconditionally — which produced `/api/wiki/entities/alberta.md.md/preview`. The
cache is keyed on the PAGE rather than the href, so one page listed once per section is one
fetch. Delegation means links the live stream adds afterwards are covered with no rebinding.

The existing test's lifts had to follow the code to `base.html`; CLAUDE.md's warning about
that test is exactly this hazard.

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

**A span of days is one of those shapes, and it was missing.** Reported on a page with a
SINGLE source, so none of the multi-source paths were involved: `create_file` wrote
`- 2026-10-01 to 2026-10-02: Darya Shipilova dies…`, no shape matched a date RANGE, and the
documented consequence followed exactly — filed as prose, kept ABOVE the list, out of
chronological order, with a blank line after it, and exempt from deduplication. A source
that will not say which of two days an event fell on says so, so this is ordinary input.

**The span is preserved, not flattened to its first day.** "died on the 1st" and "died on
the 1st or 2nd" are different claims, and dropping the second date would be the tool
inventing a precision the source declined to give. `_tl_start` is what sorting, the
future check and the duplicate check all key on, so a span cannot sort or validate by its
tail; `_tl_date_text` folds to/through/until/–/—/- to one stored form, or one event sits on
the page under two different-looking dates. A plain hyphen is safe as a range separator
even though it is also the text separator, because the range alternative only matches when
a DATE follows it AND a separator follows that date — so `- **2026-09-25** - A technician…`
still takes the hyphen as its separator, and `- 1999 - 2001 saw a decline` still stays
prose.

`add_timeline_entry` accepts a span too, and that is not scope creep: the normalizer writes
one onto the page, so a tool refusing the shape would leave no way to edit what every write
path produces (principle 4). Its refusal names the span form, and **both ends are checked
against today** — the end is the half that can be in the future.

**Stated gap:** a span and a single date inside it are different date strings, so
`_tl_dedupe` does not treat `2026-10-01` and `2026-10-01 to 2026-10-02` as one event.
Widening "same date" to "overlapping span" would make `2026` overlap every entry in that
year, and there is no observed failure to justify that.

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
write's reason lands on the current page.

**The bottom row is one of two different things, and calling both "Earliest kept version —
origin unknown" was wrong.** Reported on a page one day old: *"why doesn't it know the
original story's origin? 'Earliest kept version' sounds fishy, when I know that was the
creation."* It was. That wording is only true once pruning has thrown older revisions away,
and the page had ONE revision on a store that keeps fifty. Below `_HISTORY_KEEP` nothing has
ever been pruned, so the oldest stored revision IS the content the page was created with,
and the page says when in its own `created:` — read from THAT revision's text, never from
the live page, or a later edit dates the row.

It can usually say what made it, too, which looks like a guess and is not. **A brand-new
page leaves no revision for its own creation** — `_snapshot_version` copies the content
being replaced and there is none — so the first revision on disk is the snapshot taken by
the autolink pass that runs inside the same `create_file` call and is stamped with it. When
that revision's tool is `create_file`, the write that destroyed the original content was
part of the call that wrote it, so its source and reason describe the creation as well. Any
other tool there says nothing about what came before it, and the row then carries its date
and nothing else.

`pruned` is deliberately the conservative half: `_HISTORY_KEEP` could have been raised after
a page was pruned at a lower cap, and nothing on disk can distinguish that — so the claim is
only made where the count is unambiguous. A genuinely pruned bottom row still says
`origin unknown` and carries no timestamp or counts rather than a guessed one.

**A second hole fell out of looking at it.** A page whose autolink pass finds nothing to
link has NO revisions at all — one version, ever — and that single row carried no date and
no origin whatsoever. It gets the `created:` date now; what wrote it was never recorded.

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
