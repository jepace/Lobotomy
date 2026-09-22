"""The opener requirement (create_file) and its absorption — docs/test-plan.md section 5.

Every entity page must open with '## Overview', every concept page with '## Definition'.
The two templates' openers leak into each other on the live wiki, so when a page carries
the OTHER type's opener and not its own, unambiguously, it is renamed rather than refused.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class OpenersTest(TempWikiTestCase):

    def test_entity_with_no_opener_refused(self):
        result = agent._create_file({
            "path": "wiki/entities/foo.md", "title": "Foo", "type": "entity",
            "body": "## Background\n\nSome background.\n",
        })
        self.assertTrue(result.startswith("Error:"), result)
        self.assertIn("Overview", result)
        self.assertFalse((self.w.wiki / "entities/foo.md").exists())

    def test_concept_with_no_opener_refused(self):
        result = agent._create_file({
            "path": "wiki/concepts/bar.md", "title": "Bar", "type": "concept",
            "body": "## Background\n\nSome background.\n",
        })
        self.assertTrue(result.startswith("Error:"), result)
        self.assertIn("Definition", result)
        self.assertFalse((self.w.wiki / "concepts/bar.md").exists())

    def test_entity_only_opener_is_definition_renamed_to_overview(self):
        result = agent._create_file({
            "path": "wiki/entities/foo.md", "title": "Foo", "type": "entity",
            "body": "## Definition\n\nFoo is a thing.\n",
        })
        self.assertFalse(result.startswith("Error:"), result)
        disk = self.w.disk("entities/foo.md")
        self.assertIn("## Overview", disk)
        self.assertNotIn("## Definition", disk)

    def test_concept_only_opener_is_overview_renamed_to_definition(self):
        result = agent._create_file({
            "path": "wiki/concepts/bar.md", "title": "Bar", "type": "concept",
            "body": "## Overview\n\nBar is a concept.\n",
        })
        self.assertFalse(result.startswith("Error:"), result)
        disk = self.w.disk("concepts/bar.md")
        self.assertIn("## Definition", disk)
        self.assertNotIn("## Overview", disk)

    def test_entity_with_both_openers_unchanged(self):
        result = agent._create_file({
            "path": "wiki/entities/foo.md", "title": "Foo", "type": "entity",
            "body": "## Overview\n\nFoo is a thing.\n\n## Definition\n\nA formal one.\n",
        })
        self.assertFalse(result.startswith("Error:"), result)
        disk = self.w.disk("entities/foo.md")
        self.assertIn("## Overview", disk)
        self.assertIn("## Definition", disk)

    def test_entity_two_definitions_no_overview_refused_ambiguous(self):
        # Two candidates to rename from — not one heading to rename, so this stays a
        # refusal rather than guessing which Definition is the real opener.
        result = agent._create_file({
            "path": "wiki/entities/foo.md", "title": "Foo", "type": "entity",
            "body": "## Definition\n\nFirst.\n\n## Definition:\n\nSecond.\n",
        })
        self.assertTrue(result.startswith("Error:"), result)
        self.assertFalse((self.w.wiki / "entities/foo.md").exists())

    def test_source_and_synthesis_pages_exempt(self):
        result = agent._create_file({
            "path": "wiki/synthesis/foo.md", "title": "Foo Synthesis", "type": "synthesis",
            "body": "## Background\n\nNo opener required here.\n",
        })
        self.assertFalse(result.startswith("Error:"), result)

        result2 = agent._create_file({
            "path": "wiki/sources/bar-2026-a.md", "title": "Bar Article", "type": "source",
            "body": ("## Summary\n\nS.\n\n## Claims\n\nC.\n\n"
                     "## Entities\n\n- Foo\n\n## Concepts\n\n- Something\n"),
        })
        self.assertFalse(result2.startswith("Error:"), result2)


if __name__ == "__main__":
    unittest.main()
