"""A curly apostrophe and a straight one are the same character for matching.

Observed: an ingest created entities/noahs-ark-scans.md titled "Noah's Ark Scans" with a
straight apostrophe, while the source page's ## Entities list — written from an article
that uses typographic quotes — said "Noah’s Ark Scans". Every other name in that list
linked. That one silently did not, and nothing reported it:

    - [Lauren Witzke](../entities/lauren-witzke.md)
    - [Durupinar formation](../entities/durupinar-formation.md)
    - Noah’s Ark Scans                       <- the page exists
    - [Gopher](../entities/gopher.md)

_resolve_page already handled this — lookup_titles finds the page either way, so no
duplicate was created. Only the autolinker matched the title literally.

Two halves had to change together. The pattern treats each typographic character as its
whole family, and the per-line probe uses the longest \\w+ TOKEN rather than the longest
whitespace-word: "noah's" would never be found in a line spelling it "noah’s", so the probe
would have rejected the line before the flexible pattern ever ran.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

CURLY = "’"
STRAIGHT = "'"


class TypographicVariantTest(TempWikiTestCase):

    def _run(self, title, text):
        self.w.page("entities/noahs-ark-scans.md", title=title, type="entity",
                    body=f"# {title}\n\n## Overview\n\nScans.\n")
        self.w.page("sources/s.md", title="S", type="source",
                    body=f"# S\n\n## Entities\n\n- {text}\n")
        agent._autolink({"path": "wiki/sources/s.md"})
        return next(l for l in self.w.disk("sources/s.md").splitlines()
                    if l.startswith("- "))

    def test_the_observed_case_links(self):
        row = self._run(f"Noah{STRAIGHT}s Ark Scans", f"Noah{CURLY}s Ark Scans")
        self.assertIn("](../entities/noahs-ark-scans.md)", row, row)

    def test_it_works_in_the_other_direction_too(self):
        row = self._run(f"Noah{CURLY}s Ark Scans", f"Noah{STRAIGHT}s Ark Scans")
        self.assertIn("](../entities/noahs-ark-scans.md)", row, row)

    def test_the_pages_own_spelling_is_kept_in_the_display_text(self):
        # The link text is what the page said, not what the title says: this is
        # presentation, and rewriting an author's punctuation is not the linker's job.
        row = self._run(f"Noah{STRAIGHT}s Ark Scans", f"Noah{CURLY}s Ark Scans")
        self.assertIn(f"[Noah{CURLY}s Ark Scans]", row, row)

    def test_curly_quotes_and_dashes_too(self):
        """One wiki, several pages — never setUp() again inside a test.

        Doing that leaves the previous TempWiki un-torn-down with REPO_ROOT still bound to
        it, and the next module to call system_prompt() fails looking for CLAUDE.md in a
        temp directory. That is the module-state trap in CLAUDE.md, and this file walked
        into it on the first draft, exactly as tests/test_handback_refusals.py did.

        Straight DOUBLE quotes against curly double quotes. Pairing a straight SINGLE
        quote with a curly double one, as the first draft also did, asks for a match
        between genuinely different characters and correctly did not get one.
        """
        cases = [("ark", 'The "Ark" Project', "The \u201cArk\u201d Project"),
                 ("rel", "Canada-US Relations", "Canada\u2013US Relations")]
        rows = []
        for slug, title, _text in cases:
            self.w.page(f"entities/{slug}.md", title=title, type="entity",
                        body=f"# {title}\n\n## Overview\n\nX.\n")
        self.w.page("sources/s.md", title="S", type="source",
                    body="# S\n\n## Entities\n\n"
                         + "".join(f"- {t}\n" for _s, _ti, t in cases))
        agent._autolink({"path": "wiki/sources/s.md"})
        rows = [l for l in self.w.disk("sources/s.md").splitlines()
                if l.startswith("- ")]
        for (slug, _title, _text), row in zip(cases, rows):
            with self.subTest(slug):
                self.assertIn(f"](../entities/{slug}.md)", row, row)

    def test_the_probe_does_not_reject_the_line_first(self):
        # The half that is easy to miss. The per-line probe is a plain substring test, so
        # a word carrying the other spelling would never be found and the line would be
        # skipped before the flexible pattern was consulted.
        self.assertEqual(
            max(__import__("re").findall(r"\w+", f"Noah{STRAIGHT}s Ark Scans".lower()),
                key=len),
            "scans", "the probe is no longer punctuation-free")

    def test_an_unrelated_name_still_does_not_match(self):
        row = self._run(f"Noah{STRAIGHT}s Ark Scans", "Noah’s Ark Expedition")
        self.assertNotIn("](", row, row)


if __name__ == "__main__":
    unittest.main()
