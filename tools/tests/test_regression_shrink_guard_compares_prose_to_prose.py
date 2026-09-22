"""Regression: the shrink guard on update_section/update_file used to compare the RAW
on-disk text (already autolinked, full of markdown link markup) against the plain text the
agent is required to send. At ~32 chars of link markup per occurrence, a heavily-linked
section can carry hundreds of characters the agent's version structurally cannot have, so a
faithful, lossless rewrite read as a large shrink and was wrongly refused. Fixed by
_unlinked_len, which reduces markdown links to their display words on both sides before
comparing — measuring prose against prose.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent


class ShrinkGuardComparesProseToProse(TempWikiTestCase):

    def test_unlinked_len_strips_markup_not_words(self):
        raw = "[Widget](../entities/widget.md) " * 115
        plain = "Widget " * 115
        self.assertGreater(len(raw), len(plain) * 3)
        self.assertEqual(agent._unlinked_len(raw), agent._unlinked_len(plain))

    def test_faithful_rewrite_of_heavily_linked_section_not_refused_as_shrink(self):
        heavily_linked = "[Widget](../entities/widget.md) " * 115  # unlinked ~805 chars
        self.w.page("entities/foo.md", title="Foo", type="entity",
                     body=f"## Overview\n\nFoo.\n\n## Background\n\n{heavily_linked}\n")
        self.assertGreaterEqual(agent._unlinked_len(heavily_linked), 800)
        self.w.read_section("wiki/entities/foo.md", "Background")

        # The agent must write plain text — same words, no links (the autolinker relinks
        # them). Measured raw against raw this looks like an ~75% cut; measured unlinked
        # against unlinked it is a no-op.
        plain_rewrite = "Widget " * 115
        result = agent._update_section({
            "path": "wiki/entities/foo.md", "section": "Background",
            "content": plain_rewrite,
        })
        self.assertFalse(result.startswith("Error:"), result)

    def test_genuine_shrink_of_plain_text_is_still_refused(self):
        # The fix must not have disabled the guard entirely — a real cut is still caught.
        plain_long = "This is a real sentence about widgets. " * 30
        self.assertGreaterEqual(agent._unlinked_len(plain_long), 800)
        self.w.page("entities/foo.md", title="Foo", type="entity",
                     body=f"## Overview\n\nFoo.\n\n## Background\n\n{plain_long}\n")
        self.w.read_section("wiki/entities/foo.md", "Background")
        result = agent._update_section({
            "path": "wiki/entities/foo.md", "section": "Background",
            "content": "One short sentence now.",
        })
        self.assertTrue(result.startswith("Error:"), result)


if __name__ == "__main__":
    unittest.main()
