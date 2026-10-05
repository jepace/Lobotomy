"""A timeline entry whose source gives a span of days, not one day.

Reported from a page with a SINGLE source, so none of the multi-source paths were
involved. `create_file` wrote the body, and one of its three timeline bullets read:

    - 2026-10-01 to 2026-10-02: Darya Shipilova dies after being hospitalized…

No shape in `_TL_BULLET_RE` matched a date RANGE, so that bullet was filed as prose. The
documented consequence of an unrecognised bullet followed exactly: it was kept ABOVE the
list, out of chronological order, with a blank line after it, and exempt from
deduplication. The page shipped looking like this:

    - 2026-10-01 to 2026-10-02: Darya Shipilova dies after being hospitalized…

    - **2026-09-25** — A technician … breaks a test tube …
    - **2026-10-05** — International reporting … highlight contradictions …

A source that is unsure which of two days an event fell on says so, so this is ordinary
input rather than an exotic one.

**The span is preserved, not flattened to its first day.** "died on the 1st" and "died on
the 1st or 2nd" are different claims, and dropping the second date would have the tool
inventing a precision the source declined to give. Sorting keys on the START, which is
where a span belongs relative to the individual days it covers.

`add_timeline_entry` accepts a span too, and that is not scope creep: the normalizer now
writes one onto the page, so a tool that refused the shape would leave no way to edit what
every write path produces — principle 4, where the refusal names no move that works.

**Stated gap:** a span and a single date inside it are different date strings, so
`_tl_dedupe` does not treat `2026-10-01` and `2026-10-01 to 2026-10-02` as restatements of
one event. Widening "same date" to "overlapping span" would make `2026` overlap every entry
in that year, and there is no observed failure to justify the risk.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

# The page as it shipped, trimmed to the Timeline and its neighbours.
OBSERVED = """---
title: "Irkutsk Plague Outbreak"
type: entity
created: 2026-10-05
updated: 2026-10-05
---

# Irkutsk Plague Outbreak

## Overview

The Irkutsk Plague Outbreak refers to a suspected plague event in Siberia.

## Timeline

- 2026-10-01 to 2026-10-02: Darya Shipilova dies after being hospitalized with severe \
respiratory symptoms.

- **2026-09-25** - A technician at the Irkutsk Antiplague Research Institute reportedly \
breaks a test tube containing live pneumonic plague bacteria.
- **2026-10-05** - International reporting and commentary highlight contradictions in the \
Russian official response.

## Background

The event has been characterized by uncertainty.
"""


def _timeline_lines(content):
    out, inside = [], False
    for line in content.splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## Timeline"
            continue
        if inside and line.strip():
            out.append(line.strip())
    return out


class SpanParsingTest(unittest.TestCase):
    """Reading a bullet and writing one are different jobs; reading has to be generous."""

    def _parse(self, line):
        _prose, entries = agent._parse_timeline(line)
        return entries[0][:2] if entries else None

    def test_the_observed_bullet_parses(self):
        self.assertEqual(
            self._parse("- 2026-10-01 to 2026-10-02: Darya Shipilova dies."),
            ("2026-10-01 to 2026-10-02", "Darya Shipilova dies."))

    def test_every_range_word_is_accepted(self):
        for sep in ("to", "through", "until", "–", "—", "-"):
            with self.subTest(sep=sep):
                self.assertEqual(
                    self._parse(f"- 2026-10-01 {sep} 2026-10-02: x"),
                    ("2026-10-01 to 2026-10-02", "x"))

    def test_the_stored_span_is_one_shape_whatever_was_written(self):
        """Otherwise one event sits on the page under two different-looking dates."""
        shapes = {self._parse(f"- 2026-10-01 {s} 2026-10-02 — x")[0]
                  for s in ("to", "through", "until", "–", "—", "-")}
        self.assertEqual(shapes, {"2026-10-01 to 2026-10-02"})

    def test_a_partial_date_span_works(self):
        self.assertEqual(self._parse("- 2026-09 to 2026-10: x"),
                         ("2026-09 to 2026-10", "x"))

    def test_a_single_date_is_unchanged(self):
        self.assertEqual(self._parse("- **2026-09-25** — x"), ("2026-09-25", "x"))

    def test_a_hyphen_separator_is_still_a_separator(self):
        """The regression this had to avoid: the range alternative must not swallow the
        ordinary `- **date** - text` shape. It cannot, because a range requires a DATE
        after the hyphen."""
        self.assertEqual(self._parse("- **2026-09-25** - A technician breaks a tube."),
                         ("2026-09-25", "A technician breaks a tube."))

    def test_text_opening_with_a_year_range_is_still_not_a_span(self):
        """`- 1999 - 2001 saw a decline` has no separator after 2001, so it parses exactly
        as it did before — the behaviour an existing comment in agent.py protects."""
        self.assertEqual(self._parse("- 1999 - 2001 saw a decline"),
                         ("1999", "2001 saw a decline"))

    def test_a_span_with_no_text_stays_prose(self):
        self.assertIsNone(self._parse("- 2026-10-01 to 2026-10-02"))

    def test_a_span_sorts_by_its_start(self):
        self.assertEqual(agent._tl_sort_key("2026-10-01 to 2026-12-31"),
                         agent._tl_sort_key("2026-10-01"))

    def test_tl_start_reads_a_span_and_a_plain_date(self):
        self.assertEqual(agent._tl_start("2026-10-01 to 2026-10-02"), "2026-10-01")
        self.assertEqual(agent._tl_start("2026-10-01"), "2026-10-01")
        self.assertEqual(agent._tl_start("2026"), "2026")


class ObservedPageTest(unittest.TestCase):
    """The reported page, end to end."""

    def setUp(self):
        self.out = agent.normalize_timeline(OBSERVED)

    def test_the_timeline_is_one_list(self):
        """It was two: a prose line, a blank, then the bullets."""
        self.assertEqual(len(_timeline_lines(self.out)), 3)

    def test_every_entry_is_in_the_one_rendered_shape(self):
        for line in _timeline_lines(self.out):
            self.assertRegex(line, r"^- \*\*\d{4}(-\d{2}){0,2}( to \d{4}(-\d{2}){0,2})?"
                                   r"\*\* — \S")

    def test_the_entries_are_chronological(self):
        self.assertEqual(
            [l.split("**")[1] for l in _timeline_lines(self.out)],
            ["2026-09-25", "2026-10-01 to 2026-10-02", "2026-10-05"])

    def test_the_span_survives_rather_than_being_flattened(self):
        """Keeping only 2026-10-01 would assert a precision the source declined to give."""
        self.assertIn("**2026-10-01 to 2026-10-02** —", self.out)

    def test_no_blank_line_is_left_inside_the_list(self):
        body = self.out.split("## Timeline", 1)[1].split("## Background", 1)[0]
        self.assertNotIn("\n\n-", body.strip())

    def test_nothing_is_lost(self):
        for fragment in ("Darya Shipilova dies", "breaks a test tube",
                         "International reporting"):
            self.assertIn(fragment, self.out)

    def test_the_rest_of_the_page_is_untouched(self):
        self.assertIn("## Overview", self.out)
        self.assertIn("The event has been characterized by uncertainty.", self.out)
        self.assertTrue(self.out.startswith("---\ntitle:"))

    def test_it_is_idempotent(self):
        """heal_pages runs at startup and after every ingest; a normalizer that rewrote on
        every pass would fill page history with empty revisions forever."""
        self.assertEqual(agent.normalize_timeline(self.out), self.out)

    def test_a_span_is_deduplicated_against_itself(self):
        """The real cost of being unrecognised: prose is not merely unsorted, it is exempt
        from the duplicate check, so the same fact is written again underneath."""
        dup = OBSERVED.replace(
            "- **2026-09-25** -",
            "- 2026-10-01 to 2026-10-02: Darya Shipilova dies after being hospitalized "
            "with severe respiratory symptoms.\n- **2026-09-25** -")
        self.assertEqual(len(_timeline_lines(agent.normalize_timeline(dup))), 3)


class ToolTest(TempWikiTestCase):
    """`add_timeline_entry` has to be able to write what the normalizer stores."""

    def _page(self):
        self.w.page("entities/outbreak.md", title="Outbreak", type="entity",
                    body="## Overview\n\nAn outbreak.\n\n## Timeline\n\n"
                         "- **2026-09-25** — A tube is broken.\n")
        return "wiki/entities/outbreak.md"

    def _add(self, date, text):
        p = self._page()
        agent._read_file(p)
        out = agent._add_timeline_entry({"path": p, "date": date, "text": text})
        return out, (self.w.wiki / "entities" / "outbreak.md").read_text(encoding="utf-8")

    def test_a_span_is_accepted(self):
        out, content = self._add("2026-10-01 to 2026-10-02", "Shipilova dies.")
        self.assertNotIn("refused", out)
        self.assertIn("- **2026-10-01 to 2026-10-02** — Shipilova dies.", content)

    def test_a_span_lands_in_chronological_position(self):
        _out, content = self._add("2026-09-01 to 2026-09-02", "Earlier thing.")
        lines = _timeline_lines(content)
        self.assertEqual(lines[0],
                         "- **2026-09-01 to 2026-09-02** — Earlier thing.")

    def test_a_span_written_any_way_is_stored_the_one_way(self):
        _out, content = self._add("2026-10-01 - 2026-10-02", "Shipilova dies.")
        self.assertIn("**2026-10-01 to 2026-10-02**", content)

    def test_an_echoed_span_bullet_does_not_render_the_date_twice(self):
        _out, content = self._add(
            "2026-10-01 to 2026-10-02",
            "- **2026-10-01 to 2026-10-02** — Shipilova dies.")
        self.assertIn("- **2026-10-01 to 2026-10-02** — Shipilova dies.", content)
        self.assertNotIn("— - ", content)

    def test_a_span_ending_in_the_future_is_refused(self):
        """The END is the part that can be in the future, so both ends are checked."""
        out, _content = self._add("2026-10-01 to 2099-01-01", "Not yet.")
        self.assertIn("refused", out)
        self.assertIn("2099-01-01", out)

    def test_a_span_starting_in_the_future_is_refused(self):
        out, _content = self._add("2099-01-01 to 2099-01-02", "Not yet.")
        self.assertIn("refused", out)

    def test_a_non_date_is_still_refused(self):
        out, _content = self._add("sometime in October", "x")
        self.assertIn("refused", out)

    def test_the_refusal_names_the_span_form(self):
        """A refusal must name a move that works, and a span is now one of them."""
        out, _content = self._add("last October", "x")
        self.assertIn("to", out)
        self.assertRegex(out, r"YYYY-MM-DD to YYYY-MM-DD")

    def test_a_plain_date_still_works(self):
        out, content = self._add("2026-10-03", "A plain day.")
        self.assertNotIn("refused", out)
        self.assertIn("- **2026-10-03** — A plain day.", content)


class CreateFilePathTest(TempWikiTestCase):
    """Where the reported page came from: one call, timeline written by hand."""

    def test_create_file_canonicalizes_a_span(self):
        agent.init_session()
        r = agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/irkutsk-plague-outbreak.md",
            "title": "Irkutsk Plague Outbreak", "type": "entity",
            "body": ("## Overview\n\nA suspected plague event.\n\n## Timeline\n\n"
                     "- 2026-10-01 to 2026-10-02: Shipilova dies.\n\n"
                     "- **2026-09-25** - A tube is broken.\n"),
        })
        self.assertFalse(r.startswith("Error:"), r)
        lines = _timeline_lines(self.w.disk("entities/irkutsk-plague-outbreak.md"))
        self.assertEqual(lines, ["- **2026-09-25** — A tube is broken.",
                                 "- **2026-10-01 to 2026-10-02** — Shipilova dies."])


if __name__ == "__main__":
    unittest.main()
