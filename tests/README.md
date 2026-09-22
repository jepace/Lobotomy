# tests/

The test suite for the whole project. Stdlib `unittest` — no pytest, no `pip install`:
`requirements.txt` is flask and markdown, and a suite that needs another dependency is one
people skip. Nothing here touches the repo's own `wiki/` or `raw/`, calls an LLM, or opens
a socket.

Run everything from the **repo root**:

```sh
python3 tests/run_all.py                 # everything; exits 0 on success, 1 on failure
python3 tests/run_all.py test_timeline    # one module
python3 tests/mutate.py                   # prove the suite would catch a regression
```

`config.json` does not have to exist — the harness copies `config.json.example` if it is
missing, since `agent.py` imports `config.py`, which exits at import time without it.

`docs/test-plan.md` is the spec this was built from. Read it before adding a module.

**Not deployed.** `deploy.sh` excludes `tests/` and does not run it: deploy copies files
into the jail and nothing else, so running the suite before you deploy is on you.
`mutate.py` in particular edits `agent.py` in place, which is the last thing that should
exist beside a running server.

---

### `tests/run_all.py` — the test suite
Real automated coverage for the write-path guards, the section tools, the timeline, the
read path, and the non-LLM repair passes — everything `docs/test-plan.md` specifies.
Standard-library `unittest` only; no network, no LLM, no clock dependence, and no test
here ever reads or writes the repo's own `wiki/` or `raw/` — every test builds a throwaway
wiki via `tests/harness.py`'s `TempWiki`.

```sh
python3 tests/run_all.py                 # everything; exits 0 on success, 1 on failure
python3 tests/run_all.py test_timeline    # just tests/test_timeline.py
```

`config.json` still has to exist for `agent.py` to import at all — `run_all.py` copies
`config.json.example` automatically if no `config.json` is present, same as every other
tool here. No test makes a real LLM call.

`tests/harness.py` is the part that matters most if you're adding a test: `agent.py` hides
state in three places (module globals, a thread-local session, and the autolinker's
title-map caches), and a test that misses one of them can pass while testing nothing. Read
its docstring before writing a new test file, and build every fixture through its `TempWiki`
class rather than touching `agent.py`'s globals directly.

**The suite is not deployed.** `deploy.sh` excludes `tests/`: tests belong where the
code is changed, not next to a running server — and `mutate.py` below edits `agent.py` in
place, which is the last thing that should exist on a production box. `deploy.sh` copies
files and does not run anything, so running the suite before you deploy is on you.

### `tests/mutate.py` — does the suite actually catch anything?
A green suite proves nothing on its own. This breaks one guard in `agent.py` at a time,
runs the suite, and reports whether any test noticed. **Every mutation must be CAUGHT.**

```sh
python3 tests/mutate.py              # all mutations
python3 tests/mutate.py timeline     # just the ones matching a name
```

A `MISSED` line names a guard that could be deleted tomorrow without a single test
objecting — which is how the duplicate-heading guard and the `read_file` dispatch layer
were found uncovered after the suite was first written, both passing 90 tests while
protecting nothing. A `STALE` line means an anchor no longer matches, so the code moved
and that mutation is no longer testing what it claims.

`agent.py` is restored afterwards, including on Ctrl-C. Add a mutation whenever you add a
guard; if you cannot write one the suite catches, the guard is untested.

### `tests/run_autolink_cases.py` — autolinker characterization harness
The autolinker is the most bug-prone code in the project: its matching rules are subtle
(link every occurrence, never inside an existing link, upgrade a partial link to the longer
title, skip headings) and easy to break by accident. This runs a corpus of tricky inputs
through it in a throwaway wiki and dumps the results as JSON.

Capture a baseline before changing anything, compare after — the output should be
byte-identical unless the change is meant to alter behavior.

```sh
python3 tests/run_autolink_cases.py before.json
# ...make your change...
python3 tests/run_autolink_cases.py after.json
diff before.json after.json
```

The corpus itself is `tests/autolink_cases.py`; add a case there when you find an edge the
30 existing ones miss.

`tests/test_autolink_golden.py` is the automated version of this same check — it runs the
corpus in-process and fails with a readable diff against the committed
`tests/autolink_baseline.json`. When a change is *meant* to alter linking, regenerate the
baseline and commit it alongside the change:

```sh
python3 tests/run_autolink_cases.py tests/autolink_baseline.json
```
