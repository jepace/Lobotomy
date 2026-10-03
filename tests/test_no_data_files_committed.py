"""`raw/` and `wiki/` carry no committed data files.

Nine test stories — "headline-goes-here.md", "the-big-lie-goes-to-council.md",
"nytimes-com.md" and friends — were written into the repo's real `raw/` by a test run and
then swept into a commit by `git add -A`. The suite was green throughout, because nothing
checked either thing.

Two independent failures had to line up, and both are now closed:

  * **The write.** `tools/add_story.py` did `from agent import RAW_DIR`, which binds the
    VALUE at import, so the harness's rebind was invisible to it and it wrote to the real
    tree. `harness.TempWiki` now snapshots `RAW_DIR` and `WIKI_DIR` on entry and fails the
    test if either changed — which catches any module holding a captured path, not just
    this one.
  * **The commit.** Nothing noticed data files appearing under `raw/`. This module does.

It matters beyond tidiness: `deploy.sh --full` rsyncs `raw/` to the jail, so a committed
test story becomes a junk item in the reading list of a live server.

The allowlist is the structure the repo genuinely tracks: two directory markers and the
generated pages that give a fresh checkout a wiki to start from. Anything else under
`raw/` or `wiki/` is content, and content belongs on the server, not in the checkout.
(My first version of this list held only the `raw/` entries and failed on `wiki/log.md` —
written from what the bug had touched rather than from what the repo actually tracks.)
"""
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The repo's skeleton, not data: the two directory markers, and the generated pages that
# give a fresh checkout a wiki to start from. Everything else under raw/ or wiki/ is
# content, which belongs on the server.
ALLOWED = {
    "raw/assets/.gitkeep",
    "raw/inbox/.gitkeep",
    "raw/index.md",
    "wiki/index.md",
    "wiki/concepts/index.md",
    "wiki/entities/index.md",
    "wiki/sources/index.md",
    "wiki/synthesis/index.md",
    "wiki/log.md",
    "wiki/tasks.md",
}


def _tracked(*paths):
    out = subprocess.run(["git", "ls-files", "--", *paths],
                         cwd=REPO, capture_output=True, text=True)
    if out.returncode != 0:
        raise unittest.SkipTest("not a git checkout")
    return sorted(p for p in out.stdout.splitlines() if p.strip())


class NoDataFilesTest(unittest.TestCase):

    def test_raw_tracks_nothing_but_its_structure(self):
        stray = [p for p in _tracked("raw/") if p not in ALLOWED]
        self.assertEqual(stray, [], (
            "data files committed under raw/. A deploy with --full copies these to the "
            "jail, where they become junk items in the reading list:\n  "
            + "\n  ".join(stray)))

    def test_wiki_tracks_no_pages(self):
        stray = [p for p in _tracked("wiki/") if p not in ALLOWED]
        self.assertEqual(stray, [], (
            "wiki pages committed to the repo:\n  " + "\n  ".join(stray)))

    def test_the_allowlist_still_describes_the_repo(self):
        """An allowlist naming files that no longer exist stops being a check and starts
        being a comment."""
        for rel in ALLOWED:
            self.assertTrue((REPO / rel).exists(), f"{rel} is in the allowlist but gone")

    def test_the_check_is_looking_at_something(self):
        """If `git ls-files raw/` returned nothing at all this would pass vacuously."""
        self.assertTrue(_tracked("raw/"))


class HarnessIsolationTest(unittest.TestCase):
    """The other half: the write itself must fail the test that makes it."""

    def test_a_write_to_the_real_raw_fails_the_test_that_made_it(self):
        sys.path.insert(0, str(REPO / "tests"))
        sys.path.insert(0, str(REPO / "tools"))
        from harness import TempWiki
        import agent
        captured = agent.RAW_DIR                      # as an import-time binding would
        leaked = captured / "leaked-by-a-test.md"
        try:
            with self.assertRaises(AssertionError) as cm:
                with TempWiki():
                    leaked.write_text("junk\n", encoding="utf-8")
            self.assertIn("the REAL tree at", str(cm.exception))
            self.assertIn("leaked-by-a-test.md", str(cm.exception))
        finally:
            leaked.unlink(missing_ok=True)

    def test_an_ordinary_test_does_not_trip_it(self):
        sys.path.insert(0, str(REPO / "tests"))
        from harness import TempWiki
        with TempWiki() as w:
            w.page("entities/a.md", title="A", type="entity",
                   body="# A\n\n## Overview\n\nx\n")


if __name__ == "__main__":
    unittest.main()
