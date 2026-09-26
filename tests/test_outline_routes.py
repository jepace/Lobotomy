"""The outline must name the route that fits the job — both of them, affirmatively.

Observed: asked to reorganize a 28,401-char page, the model read the outline, read one
section, and tried to rewrite it 81% shorter. Refused, correctly. It then moved to the next
section and did the same. It was never going to work — consolidating a page moves material
BETWEEN sections, so it is not a series of independent section rewrites, which is exactly
what the 40% guard refuses.

The reply was steering it there. It said "call read_section for the one you are changing",
then "Do NOT call read_file again with an offset", with the whole-page route mentioned only
as an exception to that prohibition. The dominant signal was "don't read the rest".

And the asymmetry, which is the real defect: a page OVER the rewrite threshold got an
explicit note telling it to use update_section, while a page UNDER it — the case where the
whole-page rewrite would have worked — got silence. The informative branch fired on the
failure case, so the quiet failure was the common one. Same shape as the template lesson.

A page in the 20,000–32,768 band is the one that matters: too large to quote, small enough
to rewrite.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


def _page_of(nchars):
    """A multi-section page of roughly nchars."""
    out, i = ["# AI\n"], 0
    while sum(len(x) for x in out) < nchars:
        out.append(f"\n## Section {i}\n\n" + ("Prose about the subject. " * 40) + "\n")
        i += 1
    return "".join(out)


class OutlineRouteTest(TempWikiTestCase):

    def _outline(self, nchars):
        self.w.page("concepts/ai.md", title="Artificial Intelligence", type="concept",
                    body=_page_of(nchars))
        total = len(self.w.disk("concepts/ai.md"))
        out = agent._read_file("wiki/concepts/ai.md")
        self.assertIn("[OUTLINE", out, "expected an outline for a page this size")
        return out, total

    def test_a_rewritable_page_is_told_so_and_told_how(self):
        """The branch that used to be silent."""
        out, total = self._outline(24_000)
        self.assertLess(total, (16384 * 4) // 2, "fixture is not in the rewritable band")
        self.assertIn("offset=0", out)
        self.assertIn("update_file", out)
        self.assertIn("small enough to rewrite in one call", out)

    def test_a_rewritable_page_is_not_told_it_is_too_large(self):
        out, _ = self._outline(24_000)
        self.assertNotIn("too large to rewrite", out, out[:400])

    def test_an_oversized_page_gets_the_section_by_section_route(self):
        out, total = self._outline(40_000)
        self.assertGreater(total, (16384 * 4) // 2)
        self.assertIn("too large to rewrite", out)
        self.assertIn("allow_shrink=true", out)
        self.assertIn("DESTINATION first", out)

    def test_both_sizes_name_the_reorganize_job_explicitly(self):
        """The model has to be able to recognise its own task in the reply."""
        for n in (24_000, 40_000):
            out, _ = self._outline(n)
            for word in ("REORGANIZING", "CONSOLIDATING", "DE-DUPLICATING"):
                self.assertIn(word, out, f"{word} missing at {n} chars")

    def test_the_per_section_route_is_still_offered(self):
        out, _ = self._outline(24_000)
        self.assertIn("read_section(path, section)", out)
        self.assertIn("update_section", out)

    def test_the_reply_says_why_section_by_section_cannot_reorganize(self):
        """Without the reason, "use the other route" is a rule to be forgotten."""
        out, _ = self._outline(24_000)
        self.assertIn("BETWEEN sections", out)
        self.assertIn("40%", out)

    def test_the_oversized_note_is_not_duplicated(self):
        """It used to be appended separately, contradicting route 1 in the same reply."""
        out, _ = self._outline(40_000)
        self.assertEqual(out.count("too large to rewrite"), 1, out[-600:])

    def test_a_small_page_is_quoted_and_gets_no_route_advice(self):
        self.w.page("concepts/s.md", title="Small", type="concept",
                    body="# Small\n\n## Overview\n\nShort.\n")
        out = agent._read_file("wiki/concepts/s.md")
        self.assertNotIn("[OUTLINE", out)
        self.assertNotIn("REORGANIZING", out)

    def test_the_outline_still_lists_every_section(self):
        out, _ = self._outline(24_000)
        self.assertIn("## Section 0", out)
        self.assertIn("## Section 1", out)


class PagingStillReachableTest(TempWikiTestCase):
    """Route A is only real if paging actually gets to full coverage and update_file then
    accepts the write. This is the assertion that proves the advice is followable."""

    def test_paging_through_reaches_full_coverage_and_update_file_succeeds(self):
        self.w.page("concepts/ai.md", title="Artificial Intelligence", type="concept",
                    body=_page_of(24_000))
        total = len(self.w.disk("concepts/ai.md"))

        # Step 1: the outline (credits nothing).
        agent._read_file("wiki/concepts/ai.md")
        out = agent._update_file("wiki/concepts/ai.md", "x")
        self.assertIn("only read the first 0", out, "outline must not credit coverage")

        # Step 2: follow the reply's own instruction — offset=0, then keep going until the
        # reply itself says it is done. Driven by the reply, not by the file's length on
        # disk: read_file strips system frontmatter fields, so its "total" is a little
        # smaller, and a test that paged to the disk length would never finish.
        import re as _re
        off, guard, reached_end = 0, 0, False
        while guard < 20:
            chunk = agent._read_file("wiki/concepts/ai.md", offset=off)
            if "END OF FILE" in chunk:
                reached_end = True
                break
            m = _re.search(r"offset=(\d+) to continue", chunk)
            self.assertIsNotNone(m, chunk[-300:])
            off = int(m.group(1))
            guard += 1
        self.assertTrue(reached_end, f"paging never reached the end (stopped at {off})")

        # Step 3: one whole-page update_file is now accepted. A real reorganization keeps
        # the material and moves it, so it is about as long as what it replaced.
        new = ('---\ntitle: "Artificial Intelligence"\ntype: concept\ntags: []\n'
               'updated: 2026-09-26\nsources: []\n---\n\n# Artificial Intelligence\n\n'
               '## Overview\n\n' + ("A reorganized synthesis of the material. " * 620) + "\n")
        res = agent._update_file("wiki/concepts/ai.md", new)
        self.assertNotIn("Error", res, res[:400])

    def test_route_a_still_cannot_quietly_gut_the_page(self):
        """update_file has its own shrink guard, so full read coverage is not a licence.

        A reorganization that comes back 80% shorter is the failure this project keeps
        meeting — a response that ran long and condensed instead of failing — so it is
        refused even with the whole page read, and the refusal names allow_shrink.
        """
        self.w.page("concepts/ai.md", title="Artificial Intelligence", type="concept",
                    body=_page_of(24_000))
        import re as _re
        off = 0
        for _ in range(20):
            chunk = agent._read_file("wiki/concepts/ai.md", offset=off)
            if "END OF FILE" in chunk:
                break
            off = int(_re.search(r"offset=(\d+) to continue", chunk).group(1))
        tiny = ('---\ntitle: "Artificial Intelligence"\ntype: concept\ntags: []\n'
                'updated: 2026-09-26\nsources: []\n---\n\n# Artificial Intelligence\n\n'
                '## Overview\n\nGutted.\n')
        res = agent._update_file("wiki/concepts/ai.md", tiny)
        self.assertIn("refused", res)
        self.assertIn("allow_shrink", res)
        # And the page on disk is untouched.
        self.assertGreater(len(self.w.disk("concepts/ai.md")), 20_000)


if __name__ == "__main__":
    unittest.main()
