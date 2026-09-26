"""what_was_lost.py — did a write remove material, or only shorten prose?

The history row said a rewrite cut 2,461 words and added 479, and could not say which of
those two things it was. That is the only question the reader actually has, and "48% of
what it was" does not answer it: collapsing genuine duplication looks identical to
discarding sourced detail.

So the tool reports the disappearances that are not matters of taste — a section, a dated
timeline entry, a link target. A heading is not prose and does not get tightened away; a
wiki link is a reference to another page, so a target that no longer appears anywhere is a
reference that is gone.

The one subtlety worth a test of its own: link targets are compared as a SET. The
once-per-section rule legitimately deletes repeat links to a page the text still discusses,
so counting occurrences would report loss on every relink sweep and the tool would cry wolf
until nobody read it.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent
import what_was_lost as wwl


class UnitTest(unittest.TestCase):

    def test_sections_are_read_from_the_body(self):
        text = ('---\ntitle: "AI"\ntype: concept\n---\n\n# AI\n\n'
                '## Definition\n\nx\n\n## Impact\n\ny\n')
        self.assertEqual(wwl._sections(text), ["Definition", "Impact"])

    def test_the_page_title_h1_is_not_a_section(self):
        text = '---\ntitle: "AI"\ntype: concept\n---\n\n# AI\n\n## Definition\n\nx\n'
        self.assertNotIn("AI", wwl._sections(text))

    def test_only_internal_link_targets_count(self):
        text = ("body\n\n[a](../entities/a.md) [b](https://example.com/x) "
                "[c](#anchor) [d](../concepts/d.md#part)\n")
        self.assertEqual(wwl._link_targets(text),
                         {"../entities/a.md", "../concepts/d.md"})

    def test_timeline_dates_are_collected(self):
        text = ("body\n\n## Timeline\n\n- **2026-08** — A thing.\n"
                "- **2026-09-12** — Another.\n")
        self.assertEqual(wwl._timeline_dates(text), {"2026-08", "2026-09-12"})


class ReportTest(TempWikiTestCase):

    def _page(self, body, **kw):
        self.w.page("concepts/ai.md", title="Artificial Intelligence", type="concept",
                    body=body, **kw)
        return self.w.wiki / "concepts" / "ai.md"

    def _rewrite(self, new_body):
        p = self.w.wiki / "concepts" / "ai.md"
        agent.begin_write_scope()
        agent._atomic_write(
            p, '---\ntitle: "Artificial Intelligence"\ntype: concept\ntags: []\n'
               'created: 2026-01-01\nupdated: 2026-09-26\nsources: []\n---\n\n' + new_body)
        return p

    def _run(self, argv):
        import io
        import contextlib
        buf = io.StringIO()
        old = sys.argv
        sys.argv = ["what_was_lost.py"] + argv
        try:
            with contextlib.redirect_stdout(buf):
                rc = wwl.main()
        finally:
            sys.argv = old
        return rc, buf.getvalue()

    def test_a_dropped_section_is_named(self):
        self._page("# Artificial Intelligence\n\n## Definition\n\nx\n\n"
                   "## Impact & Public Sentiment\n\nConcern.\n\n"
                   "## Infrastructure & Backlash\n\nBacklash.\n")
        self._rewrite("# Artificial Intelligence\n\n## Definition\n\nx\n")
        rc, out = self._run(["wiki/concepts/ai.md"])
        self.assertEqual(rc, 0)
        self.assertIn("SECTIONS GONE (2)", out)
        self.assertIn("Impact & Public Sentiment", out)
        self.assertIn("Infrastructure & Backlash", out)

    def test_a_dropped_link_target_is_named(self):
        self._page("# Artificial Intelligence\n\n## Definition\n\n"
                   "Involves [FBI](../entities/fbi.md) and "
                   "[Kash Patel](../entities/kash-patel.md).\n")
        self._rewrite("# Artificial Intelligence\n\n## Definition\n\nA generic definition.\n")
        _, out = self._run(["wiki/concepts/ai.md"])
        self.assertIn("LINK TARGETS GONE (2)", out)
        self.assertIn("../entities/kash-patel.md", out)

    def test_a_repeat_link_removed_by_the_once_per_section_rule_is_not_loss(self):
        """The cry-wolf case. The page still discusses the subject and still links it."""
        self._page("# Artificial Intelligence\n\n## Definition\n\n"
                   "[FBI](../entities/fbi.md) did a thing, and the "
                   "[FBI](../entities/fbi.md) did another.\n")
        self._rewrite("# Artificial Intelligence\n\n## Definition\n\n"
                      "[FBI](../entities/fbi.md) did a thing, and the FBI did another.\n")
        _, out = self._run(["wiki/concepts/ai.md"])
        self.assertNotIn("LINK TARGETS GONE", out)
        self.assertIn("nothing structural lost", out)

    def test_a_dropped_timeline_date_is_named(self):
        self._page("# Artificial Intelligence\n\n## Overview\n\nx\n\n"
                   "## Timeline\n\n- **2026-08** — First.\n- **2026-09** — Second.\n")
        self._rewrite("# Artificial Intelligence\n\n## Overview\n\nx\n\n"
                      "## Timeline\n\n- **2026-08** — First.\n")
        _, out = self._run(["wiki/concepts/ai.md"])
        self.assertIn("TIMELINE DATES GONE (1)", out)
        self.assertIn("2026-09", out)

    def test_tightening_prose_reports_no_structural_loss(self):
        """A genuine copy-edit: shorter, same sections, same references."""
        self._page("# Artificial Intelligence\n\n## Definition\n\n"
                   "It is, in point of actual fact, the case that "
                   "[AI](../concepts/ml.md) is a thing.\n")
        self._rewrite("# Artificial Intelligence\n\n## Definition\n\n"
                      "[AI](../concepts/ml.md) is a thing.\n")
        _, out = self._run(["wiki/concepts/ai.md"])
        self.assertIn("nothing structural lost", out)

    def test_the_arithmetic_is_reported(self):
        self._page("# Artificial Intelligence\n\n## Definition\n\n" + ("word " * 400))
        self._rewrite("# Artificial Intelligence\n\n## Definition\n\n" + ("word " * 100))
        _, out = self._run(["wiki/concepts/ai.md"])
        self.assertIn("words", out)
        self.assertIn("of what it was", out)

    def test_an_added_section_is_reported_separately_from_loss(self):
        self._page("# Artificial Intelligence\n\n## Definition\n\nx\n")
        self._rewrite("# Artificial Intelligence\n\n## Definition\n\nx\n\n"
                      "## Applications and Capabilities\n\n- A bullet.\n")
        _, out = self._run(["wiki/concepts/ai.md"])
        self.assertIn("sections added (1): Applications and Capabilities", out)
        self.assertIn("nothing structural lost", out)

    def test_a_page_with_no_history_says_so(self):
        self._page("# Artificial Intelligence\n\n## Definition\n\nx\n")
        rc, out = self._run(["wiki/concepts/ai.md"])
        self.assertEqual(rc, 0)
        self.assertIn("no stored history", out)

    def test_all_walks_every_revision(self):
        self._page("# Artificial Intelligence\n\n## Definition\n\nx\n\n## Impact\n\ny\n")
        self._rewrite("# Artificial Intelligence\n\n## Definition\n\nx\n")
        self._rewrite("# Artificial Intelligence\n\n## Definition\n\nz\n")
        _, out = self._run(["wiki/concepts/ai.md", "--all"])
        # One report block per write. Counted on the arithmetic line, which appears exactly
        # once per block — "->" also appears inside it.
        self.assertEqual(out.count("  words "), 2, out)
        self.assertIn("-> current", out)
        self.assertIn("Impact", out)

    def test_it_writes_nothing(self):
        p = self._page("# Artificial Intelligence\n\n## Definition\n\nx\n\n## Impact\n\ny\n")
        self._rewrite("# Artificial Intelligence\n\n## Definition\n\nx\n")
        before = p.read_text()
        n_revs = len(list((agent.HISTORY_DIR / "concepts/ai.md").glob("*.md")))
        self._run(["wiki/concepts/ai.md"])
        self.assertEqual(p.read_text(), before)
        self.assertEqual(
            len(list((agent.HISTORY_DIR / "concepts/ai.md").glob("*.md"))), n_revs)

    def test_a_missing_page_is_reported_not_crashed(self):
        rc, out = self._run(["wiki/concepts/nope.md"])
        self.assertEqual(rc, 2)
        self.assertIn("No such page", out)


class ChangedSectionsNotTruncatedTest(TempWikiTestCase):
    """The other half of the same complaint: the history row capped its section list at
    four and said nothing about it, so a write that gutted a dozen sections read as a
    four-section edit. The data is now complete; the view caps it and says "+N more"."""

    def test_every_changed_section_is_returned(self):
        names = [f"Sec {i}" for i in range(9)]
        body = "# P\n\n" + "".join(f"## {n}\n\nOriginal prose here.\n\n" for n in names)
        self.w.page("concepts/p.md", title="P", type="concept", body=body)
        agent.begin_write_scope()
        agent._atomic_write(
            self.w.wiki / "concepts" / "p.md",
            '---\ntitle: "P"\ntype: concept\ntags: []\ncreated: 2026-01-01\n'
            'updated: 2026-09-26\nsources: []\n---\n\n# P\n\n## Sec 0\n\nKept.\n')
        rows = agent.page_history(self.w.wiki / "concepts" / "p.md")
        secs = rows[0]["sections"]
        self.assertGreater(len(secs), 4, f"still capped: {secs}")
        for n in names[1:]:
            self.assertIn(n, secs, f"{n} was deleted but not reported")


if __name__ == "__main__":
    unittest.main()
