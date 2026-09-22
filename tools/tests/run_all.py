#!/usr/bin/env python3
"""Entry point for the test suite. Runs unittest discovery over tools/tests/, prints a
one-line summary per module and a final pass/fail, and exits non-zero on any failure.

    python3 tools/tests/run_all.py                 # everything
    python3 tools/tests/run_all.py test_timeline    # just tools/tests/test_timeline.py

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
    loader = unittest.TestLoader()
    if len(sys.argv) > 1:
        module_name = sys.argv[1]
        suite = loader.loadTestsFromName(module_name)
    else:
        suite = loader.discover(str(TESTS_DIR), pattern="test_*.py", top_level_dir=str(TESTS_DIR.parent))

    t0 = time.time()
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    elapsed = time.time() - t0

    print(f"\n{'=' * 70}")
    print(f"Ran {result.testsRun} test(s) in {elapsed:.2f}s — "
          f"{len(result.failures)} failure(s), {len(result.errors)} error(s), "
          f"{len(result.skipped)} skipped")
    print("=" * 70)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
