#!/usr/bin/env python3
"""Entry point for the test suite. Runs unittest discovery over tests/, prints a
one-line summary per module and a final pass/fail, and exits non-zero on any failure.

    python3 tests/run_all.py                 # everything
    python3 tests/run_all.py test_timeline    # just tests/test_timeline.py
    python3 tests/run_all.py --failfast       # stop at the first failure

`--failfast` exists for mutate.py, which runs this whole suite once per mutation and
decides CAUGHT purely on the exit code. One red test is the entire verdict, so finishing
the other 500 buys nothing there. It is NOT a shortcut for a normal run: use it only when
a single failure is all the answer you need, never to "check the tests" before a commit —
the count it prints is then the count it got to, not the count that exists.

config.json must exist for agent.py to import at all (see harness.py's _ensure_config,
which runs before anything here imports agent) — this copies config.json.example if no
config.json is present, exactly as tools/README.md documents for every other tool here.
No test in this suite makes a real LLM call or reads config.json for anything but that
import-time existence check.
"""
import sys
import time
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

import harness  # noqa: F401 — importing this ensures config.json exists before discovery


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--failfast"]
    failfast = "--failfast" in sys.argv[1:]

    loader = unittest.TestLoader()
    if args:
        suite = loader.loadTestsFromName(args[0])
    else:
        suite = loader.discover(str(TESTS_DIR), pattern="test_*.py", top_level_dir=str(TESTS_DIR.parent))

    t0 = time.time()
    runner = unittest.TextTestRunner(verbosity=2, failfast=failfast)
    result = runner.run(suite)
    elapsed = time.time() - t0

    print(f"\n{'=' * 70}")
    # Say so when the run stopped early, or the number reads as the whole suite. A summary
    # that looks complete but is not is the same class of lie as a mutation runner that
    # reports CAUGHT for an anchor that matched nothing.
    _stopped = " (stopped at the first failure — this is NOT the whole suite)" \
        if failfast and not result.wasSuccessful() else ""
    print(f"Ran {result.testsRun} test(s) in {elapsed:.2f}s — "
          f"{len(result.failures)} failure(s), {len(result.errors)} error(s), "
          f"{len(result.skipped)} skipped{_stopped}")
    print("=" * 70)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
