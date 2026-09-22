"""Regression: update_file's date-qualifier absorption once rewrote a check variable but
wrote the ORIGINAL, unpatched content to disk — the returned success message and the
in-memory check both agreed the heading had been fixed, while the file on disk still had
the bad heading in it. The bug was invisible to any test that only inspects the return
string, which is exactly why docs/test-plan.md says to always assert on disk.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class UpdateFileWritesTheBodyItValidated(TempWikiTestCase):

    def test_absorbed_heading_is_what_actually_lands_on_disk(self):
        self.w.page("entities/foo.md", title="Foo", type="entity",
                     body="## Overview\n\nFoo is a thing.\n")
        self.w.read("wiki/entities/foo.md")
        new_body = "## Overview\n\nFoo is a thing.\n\n## Fiscal Challenges (2026)\n\nY.\n"
        result = agent._update_file("wiki/entities/foo.md", new_body)
        self.assertFalse(result.startswith("Error:"), result)
        # The bug: the success message and the in-memory check agreed the heading was
        # fixed, but the bytes actually written to disk were the unpatched original. So
        # the assertion that matters is on disk, not on the return value above.
        disk = self.w.disk("entities/foo.md")
        self.assertIn("## Fiscal Challenges\n", disk)
        self.assertNotIn("(2026)", disk)


if __name__ == "__main__":
    unittest.main()
