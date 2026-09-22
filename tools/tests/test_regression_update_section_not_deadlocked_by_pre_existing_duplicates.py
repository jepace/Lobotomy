"""Regression: an earlier duplicate-heading guard checked the WHOLE resulting page rather
than only what an edit introduced, and deadlocked 176 real pages — the only edits that
could have fixed a page with a pre-existing duplicate heading were the ones the guard
refused. Fixed by checking the delta: update_section (and every other update path) only
refuses a duplicate the edit itself introduces.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent


class UpdateSectionNotDeadlockedByPreExistingDuplicates(TempWikiTestCase):

    def test_page_with_preexisting_duplicate_heading_stays_editable(self):
        # A page damaged before the write-path guards existed — two 'Key Policies'
        # sections already on disk.
        self.w.page(
            "entities/foo.md", title="Foo", type="entity",
            body="## Overview\n\nFoo.\n\n## Key Policies\n\nFirst copy.\n\n"
                 "## Key Policies\n\nSecond copy.\n")
        self.w.read_section("wiki/entities/foo.md", "Overview")
        result = agent._update_section({
            "path": "wiki/entities/foo.md", "section": "Overview",
            "content": "Foo is a thing with an expanded overview.",
        })
        self.assertFalse(result.startswith("Error:"), result)
        disk = self.w.disk("entities/foo.md")
        self.assertIn("Foo is a thing with an expanded overview.", disk)

    def test_edit_that_would_add_a_new_duplicate_is_still_refused(self):
        # The delta rule cuts both ways: a page's existing damage is not an excuse to
        # introduce a THIRD copy or a duplicate of some other heading.
        self.w.page(
            "entities/foo.md", title="Foo", type="entity",
            body="## Overview\n\nFoo.\n\n## Key Policies\n\nFirst copy.\n\n"
                 "## Key Policies\n\nSecond copy.\n\n## Background\n\nB.\n")
        self.w.read_section("wiki/entities/foo.md", "Background")
        before = self.w.disk("entities/foo.md")
        result = agent._update_section({
            "path": "wiki/entities/foo.md", "section": "Background",
            "content": "New background.\n\n## Overview\n\nDuplicate overview.\n",
        })
        self.assertTrue(result.startswith("Error:"), result)
        self.assertEqual(self.w.disk("entities/foo.md"), before)


if __name__ == "__main__":
    unittest.main()
