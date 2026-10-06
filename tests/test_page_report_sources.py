"""`page_report.py`'s SOURCES check: one article captured twice, and unreadable slugs.

From `donald-trump.md`'s 300-odd `sources:` entries, nine pairs of which are two captures
of one article. The detection has to be on the source pages' TITLES, not their slugs,
because the slugs can never collide — a duplicate capture gets a different filename by
construction:

    nytimes-2026-trump-name-removed-kennedy-center.md
    nytimes-2026-nytimes-com-live-updates-trump-s-name-must-be-removed-from.md

Same headline, same article, two ~40-minute ingests at `max_rpm: 1`, and both now feed the
same pages — so every claim either one makes is double-counted by anything reading
provenance.

The slug smells are milder and separate: no author/year prefix, a raw URL used as a slug,
a slug truncated mid-word. None of them breaks anything today. One has already been
observed defeating the history view's source link, which is why they are reported at all
and why they are reported quietly.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import page_report


def _report(w, slugs, body="## Overview\n\nx\n"):
    w.page("entities/subject.md", title="Subject", body=body, sources=slugs)
    return page_report.analyze(w.wiki / "entities" / "subject.md")["sources"]


class DuplicateSourceTest(unittest.TestCase):
    def test_two_source_pages_with_one_title_are_reported(self):
        with TempWiki() as w:
            for slug in ("nytimes-2026-kennedy-center.md",
                         "nytimes-2026-live-updates-name-must-be-removed-from.md"):
                w.page(f"sources/{slug}",
                       title="Live Updates: Trump's Name Must Be Removed From Kennedy "
                             "Center, Judge Rules",
                       type="source", body="## Summary\n\nx\n")
            s = _report(w, ["sources/nytimes-2026-kennedy-center.md",
                            "sources/nytimes-2026-live-updates-name-must-be-removed-"
                            "from.md"])
            self.assertEqual(len(s["dupes"]), 1)
            self.assertEqual(len(s["dupes"][0]["slugs"]), 2)

    def test_the_comparison_ignores_case_and_spacing(self):
        """Because two captures of one headline differ by exactly that: a site's own
        markup, the capture path and the model's transcription."""
        with TempWiki() as w:
            w.page("sources/a-2026-vegas.md",
                   title="Las Vegas hotel deals aren't bringing visitors back",
                   type="source", body="## Summary\n\nx\n")
            w.page("sources/b-2026-vegas.md",
                   title="Las  Vegas Hotel Deals Aren't Bringing Visitors Back",
                   type="source", body="## Summary\n\nx\n")
            s = _report(w, ["sources/a-2026-vegas.md", "sources/b-2026-vegas.md"])
            self.assertEqual(len(s["dupes"]), 1)

    def test_two_different_articles_are_not_a_duplicate(self):
        with TempWiki() as w:
            w.page("sources/a-2026-x.md", title="Trump Signs Order on Lake Ontario",
                   type="source", body="## Summary\n\nx\n")
            w.page("sources/b-2026-y.md", title="Carney Responds to Tariff Threat",
                   type="source", body="## Summary\n\nx\n")
            s = _report(w, ["sources/a-2026-x.md", "sources/b-2026-y.md"])
            self.assertEqual(s["dupes"], [])

    def test_a_source_listed_in_frontmatter_but_not_on_disk_is_reported(self):
        """`## Sources` renders from this list, so a missing one is a row that cannot
        link anywhere — and the page's provenance is quietly short by one."""
        with TempWiki() as w:
            s = _report(w, ["sources/nytimes-2026-gone.md"])
            self.assertEqual(s["missing"], ["nytimes-2026-gone"])

    def test_the_count_is_what_the_page_actually_lists(self):
        with TempWiki() as w:
            w.page("sources/a-2026-x.md", title="A", type="source",
                   body="## Summary\n\nx\n")
            s = _report(w, ["sources/a-2026-x.md", "sources/b-2026-y.md"])
            self.assertEqual(s["total"], 2)


class SlugSmellTest(unittest.TestCase):
    def test_a_raw_url_used_as_a_slug_is_reported(self):
        with TempWiki() as w:
            slug = "https-www-nytimes-com-2026-09-22-opinion-trump-china-ai-html"
            w.page(f"sources/{slug}.md", title="It's Time to Cry Wolf Over A.I.",
                   type="source", body="## Summary\n\nx\n")
            s = _report(w, [f"sources/{slug}.md"])
            self.assertIn("a URL, not a slug",
                          [x["why"] for x in s["smells"]][0])

    def test_a_slug_with_no_year_is_reported(self):
        """`{author-or-org}-{year}-{short-title}` is the documented shape, and the year is
        the part the history view's source resolution leans on."""
        with TempWiki() as w:
            slug = "canadian-council-considers-changing-street-named-after-trump"
            w.page(f"sources/{slug}.md", title="Street Renamed", type="source",
                   body="## Summary\n\nx\n")
            s = _report(w, [f"sources/{slug}.md"])
            self.assertIn("no year", " ".join(x["why"] for x in s["smells"]))

    def test_a_slug_truncated_mid_word_is_reported(self):
        with TempWiki() as w:
            slug = "pete-hegseth-2026-confronts-house-with-a-lame-duck-impeachment-di"
            w.page(f"sources/{slug}.md", title="Impeachment Dilemma", type="source",
                   body="## Summary\n\nx\n")
            s = _report(w, [f"sources/{slug}.md"])
            self.assertIn("looks truncated", " ".join(x["why"] for x in s["smells"]))

    def test_a_well_formed_slug_is_not_a_smell(self):
        """The check has to stay quiet on the thousands of correct slugs, or the handful
        of broken ones are unfindable."""
        with TempWiki() as w:
            for slug in ("stevis-gridneff-2026-canada-us-trade-war-escalates",
                         "nytimes-2026-trump-canada-tariffs",
                         "wikipedia-2026-barack-obama"):
                w.page(f"sources/{slug}.md", title=slug, type="source",
                       body="## Summary\n\nx\n")
            s = _report(w, [f"sources/{x}.md" for x in
                            ("stevis-gridneff-2026-canada-us-trade-war-escalates",
                             "nytimes-2026-trump-canada-tariffs",
                             "wikipedia-2026-barack-obama")])
            self.assertEqual(s["smells"], [], s["smells"])

    def test_a_page_with_no_sources_reports_nothing(self):
        with TempWiki() as w:
            s = _report(w, [])
            self.assertEqual((s["dupes"], s["missing"], s["smells"], s["total"]),
                             ([], [], [], 0))


if __name__ == "__main__":
    unittest.main()
