"""`page_report.py`'s REPEATS and ECHOES: the same material written twice on one page.

The expensive finding on `donald-trump.md`, and the one a human cannot get at by eye at
223KB. The page had grown two parallel dumping grounds for one pile — `## Second
Presidential Term` in prose and `## Key Policies & Actions (2026)` in bullets — so it said
everything twice: the Lake Ontario renaming six times across four sections, the press ban
three times, the arch four, the Sept 29 AI accord in four separate bullets, and one
Rattner sentence verbatim in two places.

**The duplicates DRIFT, which is why this is worse than redundancy.** Two copies of the
press-ban paragraph name different networks — one says MS NOW, the other MSNBC — so one of
them is now simply wrong, and nothing on the page says which.

Three decisions here cost something to get right and each has a test:

  * **Comparison sees through the autolinking.** Two copies of one paragraph are reliably
    linked DIFFERENTLY, because the once-per-section budget is keyed on (target page,
    section ordinal) — so the second copy in another section carries links the first does
    not. Comparing raw bytes misses every pair this exists to find.
  * **Containment, not Jaccard.** The common shape is a short bullet wholly absorbed into
    a long paragraph, and Jaccard scores exactly that pair low. The short one being fully
    contained IS the finding.
  * **A bullet's bold label comes off for comparison.** `- **AI**: Rattner noted that…`
    against the same sentence in prose. Found by running the real report on a fixture:
    the Rattner echo went unreported until the label was stripped, because it made the
    bullet one long sentence that matched nothing.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import page_report

# Long enough to clear _UNIT_MIN_CHARS, which exists so a shared phrase is not a finding.
LAKE = ("In August 2026, Trump signed an executive order renaming the United States "
        "portion of Lake Ontario as Lake America, a move that sparked widespread "
        "backlash and symbolic protests across the whole of the country.")
RATTNER = ("Rattner noted that the wildly generous tax benefits enacted in the first "
           "term and made permanent in the second have heavily subsidized capital "
           "expenditures for artificial intelligence companies throughout the year.")


def _report(w, body, **kw):
    w.page("entities/subject.md", title="Subject", body=body, **kw)
    return page_report.analyze(w.wiki / "entities" / "subject.md")


class RepeatsTest(unittest.TestCase):
    def test_the_same_paragraph_in_two_sections_is_one_pair(self):
        with TempWiki() as w:
            r = _report(w, f"## Overview\n\nShort.\n\n## Trade\n\n{LAKE}\n\n"
                           f"## Policies\n\n{LAKE}\n")
            self.assertEqual(len(r["repeats"]), 1)
            pair = r["repeats"][0]
            self.assertEqual({pair["a_section"], pair["b_section"]},
                             {"Trade", "Policies"})
            self.assertTrue(pair["identical"])

    def test_two_copies_linked_differently_are_still_found(self):
        """The decision that makes the check work at all. The autolinker's
        once-per-section budget guarantees the second copy is linked differently from the
        first, so a byte comparison would report nothing on a real page."""
        linked = LAKE.replace("Lake Ontario",
                              "[Lake Ontario](../entities/lake-ontario.md)")
        with TempWiki() as w:
            r = _report(w, f"## Overview\n\nShort.\n\n## Trade\n\n{linked}\n\n"
                           f"## Policies\n\n{LAKE}\n")
            self.assertEqual(len(r["repeats"]), 1)
            self.assertTrue(r["repeats"][0]["identical"],
                            "two copies differing only in link syntax are identical prose")

    def test_a_bullet_absorbed_into_a_longer_paragraph_is_found(self):
        """Containment rather than Jaccard. The page's real shape: a short labelled bullet
        whose every word is already in a long prose paragraph elsewhere."""
        with TempWiki() as w:
            r = _report(w, f"## Overview\n\nShort.\n\n## Trade\n\n{LAKE} "
                           f"Separately, many other unrelated things also happened in "
                           f"the same month and are described here at some length for "
                           f"the reader's benefit.\n\n"
                           f"## Policies\n\n- **Lake Ontario**: {LAKE}\n")
            self.assertEqual(len(r["repeats"]), 1)
            self.assertGreater(r["repeats"][0]["score"], 0.9)

    def test_a_bullets_bold_label_does_not_hide_the_duplicate(self):
        with TempWiki() as w:
            r = _report(w, f"## Overview\n\nShort.\n\n## Term\n\n{RATTNER}\n\n"
                           f"## Policies\n\n- **AI Policy** (September 2026): {RATTNER}\n")
            self.assertEqual(len(r["repeats"]), 1)
            self.assertTrue(r["repeats"][0]["identical"])

    def test_each_bullet_is_its_own_unit(self):
        """A 40-bullet list compared as one block is compared against nothing, and the
        duplication being hunted is precisely between a bullet and a paragraph."""
        with TempWiki() as w:
            r = _report(w, f"## Overview\n\nShort.\n\n## Term\n\n{LAKE}\n\n"
                           f"## Policies\n\n- **A**: {RATTNER}\n- **B**: {LAKE}\n")
            self.assertEqual(len(r["repeats"]), 1)
            self.assertEqual(r["repeats"][0]["b_section"], "Policies")

    def test_two_different_paragraphs_are_not_a_pair(self):
        with TempWiki() as w:
            r = _report(w, f"## Overview\n\nShort.\n\n## Trade\n\n{LAKE}\n\n"
                           f"## AI\n\n{RATTNER}\n")
            self.assertEqual(r["repeats"], [])

    def test_a_short_shared_phrase_is_not_a_pair(self):
        """Below `_UNIT_MIN_CHARS` a repeated block is a stock phrase, and reporting those
        buries the real pairs — the bleeding_titles lesson, which is that a report's whole
        value is in not crying wolf."""
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nShort.\n\n## A\n\nIn September 2026, "
                           "Trump said so.\n\n## B\n\nIn September 2026, Trump said "
                           "so.\n")
            self.assertEqual(r["repeats"], [])

    def test_the_generated_sources_section_is_never_compared(self):
        """Its rows are rendered from frontmatter and look alike by construction. A
        duplicate there is a duplicate source page, reported under SOURCES with a
        different repair."""
        with TempWiki() as w:
            rows = "\n".join(
                f"- [A very long article headline about the trade war, part {i}, as "
                f"published by a newspaper of record](../sources/s{i}-2026.md)"
                for i in range(6))
            r = _report(w, f"## Overview\n\nShort.\n\n## Sources\n\n{rows}\n")
            self.assertEqual(r["repeats"], [])

    def test_pairs_are_ordered_loudest_first(self):
        with TempWiki() as w:
            partial = LAKE.replace("widespread backlash and symbolic protests across "
                                   "the whole of the country",
                                   "a reaction that nobody involved had expected at all")
            r = _report(w, f"## Overview\n\nShort.\n\n## A\n\n{LAKE}\n\n"
                           f"## B\n\n{LAKE}\n\n## C\n\n{partial}\n")
            scores = [x["score"] for x in r["repeats"]]
            self.assertEqual(scores, sorted(scores, reverse=True))
            self.assertTrue(r["repeats"][0]["identical"])


class EchoesTest(unittest.TestCase):
    def test_one_sentence_appearing_twice_is_an_echo(self):
        with TempWiki() as w:
            r = _report(w, f"## Overview\n\nShort.\n\n## Term\n\nSome preamble here. "
                           f"{RATTNER}\n\n## Policies\n\n{RATTNER} And a tail.\n")
            self.assertEqual(len(r["echoes"]), 1)
            self.assertEqual(r["echoes"][0]["count"], 2)
            self.assertIn("Rattner noted", r["echoes"][0]["sentence"])

    def test_an_echo_names_every_section_it_appears_in(self):
        """Because the repair depends on where the copies are: two in one section is a
        botched edit, and one in each of two parallel piles is the structural defect."""
        with TempWiki() as w:
            r = _report(w, f"## Overview\n\nShort.\n\n## A\n\n{RATTNER}\n\n"
                           f"## B\n\n{RATTNER}\n\n## C\n\n{RATTNER}\n")
            self.assertEqual(r["echoes"][0]["count"], 3)
            self.assertEqual(sorted(r["echoes"][0]["sections"]), ["A", "B", "C"])

    def test_an_echo_is_found_through_differing_link_syntax(self):
        with TempWiki() as w:
            linked = RATTNER.replace("Rattner", "[Rattner](../entities/rattner.md)")
            r = _report(w, f"## Overview\n\nShort.\n\n## A\n\n{linked}\n\n"
                           f"## B\n\n{RATTNER}\n")
            self.assertEqual(len(r["echoes"]), 1)

    def test_a_short_sentence_repeated_is_not_an_echo(self):
        """`_ECHO_MIN_CHARS` is what makes this check have no false positives worth the
        name: nobody writes the same 80-character sentence twice by accident, and plenty
        of people write "He denied it." twice."""
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nShort.\n\n## A\n\nHe denied it.\n\n"
                           "## B\n\nHe denied it.\n")
            self.assertEqual(r["echoes"], [])

    def test_a_sentence_appearing_once_is_not_an_echo(self):
        with TempWiki() as w:
            r = _report(w, f"## Overview\n\nShort.\n\n## A\n\n{RATTNER}\n")
            self.assertEqual(r["echoes"], [])

    def test_an_abbreviation_does_not_split_one_sentence_into_two(self):
        """`_sentences` is shared with the accretion guard precisely so "U.S. Senate" is
        not two sentences. A splitter that got this wrong would manufacture echoes out of
        fragments like "Senate" appearing all over the page."""
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nShort.\n\n## A\n\nThe U.S. Senate did it.\n\n"
                           "## B\n\nThe U.S. Senate did it.\n")
            self.assertEqual(r["echoes"], [],
                             "fragments of one sentence must not become echoes")


if __name__ == "__main__":
    unittest.main()
