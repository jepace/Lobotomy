# Test suite plan

A plan for a real test suite for Lobotomy, written to be implemented in one pass.

Today `tools/tests/` holds two files, both about the autolinker.
(The suite was built from this and now lives at `tests/` in the repo root.) Everything else —
the write-path guards, the section tools, the timeline, the read path, the repair
passes — has no automated coverage at all. This document specifies what to build.

Read `CLAUDE.md` first for the architecture, then this.

---

## 1. Goals

1. **Every guard in the write paths has a test that fails if the guard stops working.**
   The guards are the product: they are what keeps an LLM from quietly destroying pages.
2. **Every bug we have already fixed has a regression test**, named after the bug.
   This codebase has a documented history of the same defect returning in a new place.
3. **One command runs everything and exits non-zero on failure.** Today verification is
   nine scripts run by hand and a JSON diff eyeballed by a human.
4. **Correct before quick.** There is no runtime budget. If a case needs a 9,000-page
   wiki to be meaningful, build one. Do not weaken a test, share fixtures between tests,
   or skip a slow case to save seconds — a suite that is fast because it checks less is
   the failure mode this is meant to replace. If it ends up slow enough to discourage
   running, say so and we will split it into a fast tier and a full tier; do not solve
   that by cutting coverage.

### Non-goals

- No coverage target. Chasing a percentage here would mean testing the LLM plumbing,
  which is the part tests cannot meaningfully check.
- No tests that call an LLM, open a socket, or read `config.json` for anything real.
- No test touches the actual `wiki/` or `raw/` directories in the repo. Ever.

---

## 2. Constraints

| Constraint | Why |
|---|---|
| **Standard library only** — `unittest`, not pytest | `requirements.txt` is `flask` and `markdown`. The project's stated principle is no tooling requirement; a test suite that needs a `pip install` is one people skip. `unittest` has discovery, fixtures, and assertions, and is already there. |
| **No network, no LLM, no clock dependence** | Tests must pass on a plane and in CI. Anything reading "today" must be given a fixed date or tolerate the real one deliberately. |
| **Each test builds its own throwaway wiki** | Shared mutable state between tests is how a suite starts lying. |
| **`config.json` must exist** | `agent.py` imports `config.py`, which exits if the file is missing. The runner copies `config.json.example` to a temp location if no `config.json` is present, or the harness sets it up. Document this in `tools/README.md`. |

---

## 3. Layout

**Prefer many small files over few large ones.** One file per behavior area, named after
the behavior. A reader who wants to know how dated headings are handled should find
`test_date_qualifiers.py` and read the whole thing in one screen; if a file grows past
roughly 200 lines or starts covering two unrelated rules, split it again. Discovery finds
them all either way, and a failure names the file, so the filename is the first line of
the diagnosis.

```
tests/
  __init__.py
  harness.py                        the shared fixture — read section 4 carefully
  run_all.py                        entry point

  autolink_cases.py                 EXISTING — the 30-case corpus, unchanged
  autolink_baseline.json            NEW — committed expected output
  test_autolink_golden.py           corpus vs baseline
  test_autolink_caches.py           map determinism, cache invalidation, no_autolink

  test_heading_titles.py            a section may not repeat the page title
  test_heading_dates.py             a section may not be named after a date
  test_heading_links.py             a heading may not contain a markdown link
  test_heading_duplicates.py        no heading twice on a page
  test_heading_delta.py             update paths refuse only what the edit introduces

  test_openers_required.py          entity needs Overview, concept needs Definition
  test_openers_absorbed.py          the Definition <-> Overview swap is renamed, not refused

  test_date_qualifiers_strip.py     trailing dates dropped
  test_date_qualifiers_refuse.py    leading dates, collisions, page-title collisions

  test_timeline_ordering.py         chronological insertion, partial dates, ties
  test_timeline_validation.py       future dates, bad dates, duplicates, echoed bullets
  test_timeline_placement.py        section created before Sources, prose preserved

  test_read_outline.py              over-limit wiki pages return an outline
  test_read_paging.py               the offset sentinel and full read coverage
  test_read_coverage.py             read-before-write bookkeeping

  test_repair_unlink_headings.py
  test_repair_heal_pages.py
  test_repair_links.py

  test_regression_*.py              one file per fixed bug — see section 5
```

`run_autolink_cases.py` stays as a **developer tool** for inspecting what the autolinker
does to the corpus (it dumps JSON for eyeballing). `test_autolink_golden.py` is the
automated check. Do not delete the former; it is how you regenerate the baseline.

The tables in section 5 are grouped by area rather than by file; split each group across
the files above as the names suggest.

---

## 4. The harness — the part that matters most

**This is where a test suite for this codebase succeeds or fails.** `agent.py` keeps
state in three places that a naive test will get wrong, and getting it wrong produces
tests that *pass while testing nothing*. That has already happened twice.

`harness.py` provides one context manager / `unittest` mixin, `TempWiki`, that:

1. **Creates a temp repo** with `wiki/{sources,entities,concepts,synthesis}` and `raw/`.
2. **Rebinds the module globals** on `agent`:
   `REPO_ROOT`, `WIKI_DIR`, `RAW_DIR`, `HISTORY_DIR`.
   Save the originals and restore them in teardown, even when the test raises.
3. **Resets thread-local session state** by calling `agent.init_session()`.
   Session state (`_session_read_pages`, `_session_read_coverage`,
   `_session_read_sections`, `_session_stale_pages`, `_current_source_page`, …) lives in
   a thread-local and **persists between tests in the same process**. A test that forgets
   this inherits the previous test's "already read" credit and passes for the wrong reason.
4. **Clears the autolinker caches**: `_title_map_cache = None`, `_title_regex_cache = {}`,
   `_title_tokens_cache = {}`, `_no_autolink_titles = set()`. These are keyed by title, not
   by wiki, so a title from a previous test's throwaway wiki will otherwise still be live.
5. **Provides page builders** so tests read as intent, not as frontmatter plumbing:

```python
with TempWiki() as w:
    w.page("entities/jd-vance.md", title="JD Vance", type="entity",
           body="## Overview\n\nA politician.\n")
    w.page("concepts/ai.md", title="AI", type="concept", body="## Definition\n\nX.\n",
           sources=["sources/a.md"], mtime_days_ago=60)
    w.read("wiki/entities/jd-vance.md")     # satisfies the read-before-write guard
```

`w.read(path)` calls `agent._read_file` so the read-coverage bookkeeping is real rather
than simulated. Several guards refuse until a page has been read; a test that fakes that
credit by writing to `_session_read_pages` directly is testing its own fake.

### Three pitfalls to encode in the harness, with comments explaining why

- **Default arguments bind at import.** `wiki_pages()` used to take `root=WIKI_DIR` as a
  default value, so every caller that omitted it scanned the *real* wiki even after the
  harness rebound the global — silently, reporting nothing. That is fixed, but the shape
  recurs. The harness should assert in setup that `agent.WIKI_DIR` is the temp path **and**
  that `list(agent.wiki_pages())` returns only files under it.
- **mtime matters.** `heal_pages` fills dates from mtime and the stub-event smell keys off
  it. `w.page(..., mtime_days_ago=N)` must set it via `os.utime`.
- **Ordering matters.** `_build_title_map` was nondeterministic once because of an
  unsorted glob; the autolinker's output depends on map order. Tests that create several
  pages must not depend on filesystem order.

---

## 5. What to test

Each row is one test method. **Assert on the file on disk, not only on the returned
string.** The `update_file` date-qualifier bug returned a success message while writing
unpatched content — a test checking only the return value would have passed.

Where a row says "refused", assert both that the result starts with `Error:` **and** that
the file on disk is unchanged.

### `test_heading_rules.py` — `_bad_headings` across all five write paths

The rules: no heading repeating the page title, no heading naming a date, no heading
containing a markdown link. The five paths: `create_file`, `update_file`,
`update_section`, `append_section`, `replace_text`.

| Case | Expected |
|---|---|
| `## Cybersecurity` on cybersecurity.md | refused (title) |
| `# Cybersecurity` (the page's own H1) on cybersecurity.md | accepted |
| `## 2026 Outbreak` | refused (date) |
| `## [Atheism](../sources/a.md)` | refused (link) |
| `## What It May Mean`, `## March of the Penguins`, `## Origins & History`, `## Key Works / Products` | accepted — month-name false positives |
| Each rule, once per write path | refused in all five |
| **Delta:** page already has `## Legacy Notes (2025)`; edit changes prose elsewhere | accepted, heading untouched |
| **Delta:** same page, edit that removes the bad heading | accepted |
| **Delta:** same page, edit that introduces a *second* bad heading | refused, naming only the new one |

The delta rule is the one with a scar: an earlier version checked the whole resulting page
and deadlocked 176 real pages, because the only edits that could fix them were refused.

### `test_openers.py` — the opener requirement and its absorption

| Case | Expected |
|---|---|
| entity page with no `## Overview` and no `## Definition` | refused |
| concept page with no `## Definition` and no `## Overview` | refused |
| entity page whose only opener is `## Definition` | **accepted**, heading on disk is `## Overview` |
| concept page whose only opener is `## Overview` | **accepted**, heading on disk is `## Definition` |
| entity page with *both* `## Overview` and `## Definition` | accepted, both unchanged |
| entity page with two `## Definition` headings and no Overview | refused (ambiguous — not one heading to rename) |
| `source` / `synthesis` page with neither | accepted (rule applies only to entity/concept) |

### `test_date_qualifiers.py` — `_absorb_date_qualifiers`

| Case | Expected |
|---|---|
| `## Fiscal Challenges (2026)` | → `## Fiscal Challenges` |
| `## Role and Context in 2026` | → `## Role and Context` |
| `## Status of the Primary Race (As of June 2026)` | → `## Status of the Primary Race` |
| `## Political Discourse (2017–2026)` | → `## Political Discourse` (en dash) |
| `## 2026 Outbreak`, `## 1976 Chowchilla Kidnapping`, `## 2026 Forecast` | refused — leading date, nothing to strip |
| `## Context (2026)` on a page that already has `## Context` | refused, message names `Context` to merge into |
| `## Cybersecurity in 2026` on cybersecurity.md | refused, message quotes the heading **as sent** |
| `## Threat Landscape in 2026` on cybersecurity.md | → `## Threat Landscape` (proves the above is the title case, not the shape) |
| Absorption in each of the five write paths | rewritten on disk in all five |
| Pre-existing dated heading + unrelated edit | left alone |

### `test_timeline.py` — `add_timeline_entry`

| Case | Expected |
|---|---|
| Five entries added in scrambled date order | file reads chronologically |
| Two entries sharing a date | insertion order preserved between them |
| `2026-03` and `2026` | sort before the precise dates they contain |
| Entry identical to one present | refused as duplicate, file unchanged |
| Duplicate where the existing entry has been autolinked | still detected (compare with links stripped) |
| Date in the future | refused |
| `"last Tuesday"` | refused, message names the accepted formats |
| `text` sent as a whole `- **2026-03-14** — …` bullet | accepted, date rendered once |
| Timeline already out of order | re-sorted on the next insert |
| Non-bullet prose inside the Timeline section | preserved, above the bullets |
| Target is a `wiki/sources/` page | refused (immutable) |
| No `## Timeline` section yet | created, placed before `## Sources` |

### `test_read_path.py` — `read_file` outline and the offset sentinel

| Case | Expected |
|---|---|
| Wiki page under the limit | full text, no `[OUTLINE` |
| Wiki page over the limit, no offset | outline; contains every section name **including ones past the old 20k cut**; much smaller than the page |
| Same page, `offset=0` | a text chunk with `[TRUNCATED`, **not** an outline |
| Paging with explicit offsets to EOF, then `update_file` | accepted — this is the Regenerate path, and it broke once |
| After an outline read, `update_file` | refused (nothing was quoted, so nothing is credited) |
| Raw file over its limit | `[TRUNCATED` paging, never an outline |
| Truncation emits a log line | assert via `assertLogs` |

That third row is the sentinel: `offset` defaults to `-1`, because "no offset given" and
"offset 0" are different requests and `int(x or 0)` collapsed them.

### `test_repairs.py` — the non-LLM passes

| Function | Cases |
|---|---|
| `unlink_headings` | link stripped from heading, display text kept; prose links untouched; history entry recorded; dry run writes nothing |
| `heal_pages` | `type: concept}EX_HEAT_CP` → `concept`; `type: wibble` → reported in `manual`, not guessed; missing `created:` filled from mtime; missing `title:` reported, never invented |
| `_update_file` type restore | caller sends a corrupted `type:`; disk value wins |
| `repair_links` | wrong relative path corrected; `about:reader?url=` unwrapped; a version-history entry is recorded (it bypassed `_atomic_write` until recently) |

### `test_autolink.py` — golden file

Run the existing corpus, compare to `autolink_baseline.json`, fail with a readable diff.
Generate the baseline once with `run_autolink_cases.py` and commit it. Document in
`tools/README.md`: when a change is *meant* to alter linking, regenerate the baseline and
the diff is the review artifact.

### Regressions — one **file** per bug already fixed

`test_regression_<short_name>.py`, each containing the one test plus a docstring saying
what broke, how it was found, and what it cost. These are the highest-value files here:
every one of them is a bug that shipped, and several are the same class of defect
reappearing somewhere new. A failure should explain itself without anyone digging through
git history.

- `test_update_file_writes_the_body_it_validated` — it patched the check variable, not the
  content it wrote.
- `test_paging_remains_reachable_for_regenerate` — the outline change made full read
  coverage unreachable.
- `test_wiki_pages_honours_rebound_wiki_dir` — default argument bound at import.
- `test_update_section_not_deadlocked_by_pre_existing_duplicates` — the 176-page deadlock.
- `test_shrink_guard_compares_prose_to_prose` — markup vs plain text made faithful
  rewrites look like 49% cuts.
- `test_no_autolink_page_still_found_by_lookup_titles` — hiding it caused duplicate pages.
- `test_title_map_is_deterministic` — build it twice over the same wiki, assert identical;
  nondeterminism made `relink` never converge.

---

## 6. Entry point

`tests/run_all.py`:

- Runs `unittest` discovery over `tests/`.
- Prints a one-line summary per module and a final pass/fail.
- **Exits 0 on success, 1 on any failure.**
- Takes an optional module name to run one file.

```sh
python3 tests/run_all.py
python3 tests/run_all.py test_timeline
```

Add a `## Tests` section to `tools/README.md` documenting the command, the
`config.json` requirement, and how to regenerate the autolink baseline.

---

## 7. Conventions

- **One behavior per test.** A test asserting five things reports the first failure and
  hides the rest.
- **Name the behavior, not the function**: `test_dated_heading_refused_in_append_section`,
  not `test_append_section_2`.
- **Assert on disk.** Return strings are a convenience, not the contract.
- **Refusals assert two things**: the error, and that nothing changed.
- **No `sleep`, no real dates** unless the test is about dates, in which case compute
  relative to `date.today()` rather than hardcoding.
- **Tests must fail if the guard is removed.** Before finishing, comment out each guard in
  turn and confirm the matching test goes red. A guard test that passes with the guard
  deleted is worse than no test — it is a false assurance, which is the exact failure mode
  this suite exists to prevent.

---

## 8. Definition of done

1. `python3 tests/run_all.py` exits 0 on a clean checkout.
2. Every table row in section 5 has a corresponding test.
3. Each guard, when disabled, turns at least one test red (section 7, last bullet).
4. No test reads or writes the repo's own `wiki/` or `raw/`.
5. `tools/README.md` documents how to run it and how to regenerate the baseline.
6. The run prints its own wall-clock time, so the cost is visible rather than guessed at.

## 9. Out of scope

The agent loop, provider fallback and retry ladder, the Flask routes, the job queue, and
anything requiring an API key. Worth testing eventually; they need a different approach
(fakes for the HTTP layer) and would triple the size of this task.
