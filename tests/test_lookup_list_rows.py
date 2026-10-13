"""A source page's lookup lists are read one way, whatever shape the model wrote them in.

Reported as *"the concept list generated at ingest is lower case again"* — and **the guard
was there, it just could not see the list.** Three places read `## Entities` / `## Concepts`
and each had its own idea of what a row is, all three matching `-`, `*` or `+` and nothing
else:

    create_file's lowercase check   the refusal the report is about
    _listed_names                   what done() demands pages for, and the progress counter
    sort_lookup_lists               the alphabetizing

Measured on the four shapes a model actually writes, before changing anything:

    bullet list            refused=True
    numbered list          refused=False      <-- invisible
    bare lines, no marker  refused=False      <-- invisible
    table rows             refused=False      <-- invisible

So a list written `1. climate policy` was checked by nothing, demanded pages from nothing,
and sorted by nothing — and a source page is immutable once written, so the lowercase list
is permanent. **This is `_TL_BULLET_RE`'s lesson in the other half of the file**: reading a
list and writing one are different jobs, so the reader has to accept every shape the list
arrives in, because anything it does not match is filed as prose and goes not merely
unsorted but unchecked.

The other half is not crying wolf, which is what `NotCryingWolfTest` is for: a properly
capitalised list in any shape must go straight through, an introductory sentence above the
bullets must not be counted as a row, and "bell hooks" must still be allowed to be
lowercase.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

HEAD = "## Summary\n\nThe article.\n\n## Claims\n\n- A claim.\n\n"
LOWER = ["climate policy", "carbon tax", "emissions trading", "net zero"]
PROPER = ["Climate Policy", "Carbon Tax", "Emissions Trading", "Net Zero"]


def bullets(names):
    return "".join(f"- {n}\n" for n in names)


def numbered(names):
    return "".join(f"{i + 1}. {n}\n" for i, n in enumerate(names))


def table(names):
    return "| Name | Why |\n|---|---|\n" + "".join(f"| {n} | it appears |\n" for n in names)


def bare(names):
    return "".join(f"{n}\n" for n in names)


def page(entities, concepts="- Another Concept\n"):
    return HEAD + "## Entities\n\n" + entities + "\n## Concepts\n\n" + concepts


class RowReaderTest(unittest.TestCase):
    def test_bullets(self):
        self.assertEqual(agent.lookup_list_rows(page(bullets(PROPER), "")),
                         PROPER)

    def test_asterisk_and_plus_markers(self):
        body = page("* Climate Policy\n+ Carbon Tax\n", "")
        self.assertEqual(agent.lookup_list_rows(body), ["Climate Policy", "Carbon Tax"])

    def test_a_numbered_list(self):
        self.assertEqual(agent.lookup_list_rows(page(numbered(PROPER), "")), PROPER)

    def test_a_numbered_list_with_parentheses(self):
        body = page("1) Climate Policy\n2) Carbon Tax\n", "")
        self.assertEqual(agent.lookup_list_rows(body), ["Climate Policy", "Carbon Tax"])

    def test_a_table_takes_the_first_cell(self):
        self.assertEqual(agent.lookup_list_rows(page(table(PROPER), "")), PROPER)

    def test_a_tables_header_and_separator_are_not_rows(self):
        rows = agent.lookup_list_rows(page(table(PROPER), ""))
        self.assertNotIn("Name", rows)
        self.assertFalse([r for r in rows if set(r) <= set("-: ")])

    def test_bare_lines_when_nothing_is_marked(self):
        self.assertEqual(agent.lookup_list_rows(page(bare(PROPER), "")), PROPER)

    def test_both_sections_are_read(self):
        rows = agent.lookup_list_rows(page(bullets(["Alpha"]), "- Beta\n"))
        self.assertEqual(rows, ["Alpha", "Beta"])

    def test_a_link_is_flattened_and_bold_is_stripped(self):
        """A re-ingested source page is already autolinked, and the model likes a bold
        label — both would otherwise read as part of the name."""
        body = page("- [Climate Policy](../concepts/climate-policy.md)\n"
                    "- **Carbon Tax**\n", "")
        self.assertEqual(agent.lookup_list_rows(body), ["Climate Policy", "Carbon Tax"])

    def test_an_empty_or_absent_section_is_no_rows(self):
        self.assertEqual(agent.lookup_list_rows("## Summary\n\nx\n"), [])
        self.assertEqual(agent.lookup_list_rows(page("", "")), [])

    def test_rows_stop_at_the_next_heading(self):
        body = (HEAD + "## Entities\n\n- Climate Policy\n\n## Quotes\n\n- Not an entity\n")
        self.assertEqual(agent.lookup_list_rows(body), ["Climate Policy"])

    def test_a_bare_line_is_ignored_when_the_section_has_marked_rows(self):
        """With bullets present an unmarked line is an introduction or a continuation.
        Counting it is what would make the lowercase check cry wolf on
        "the following entities appear:"."""
        body = page("the following entities appear in this article:\n\n"
                    + bullets(PROPER), "")
        self.assertEqual(agent.lookup_list_rows(body), PROPER)

    def test_a_long_bare_line_is_prose(self):
        """The length cap is `_is_lookup_row`'s discriminator: a name is short, a sentence
        is not. Without it a section written as a paragraph reports its sentences as
        names."""
        long_line = ("This article discusses a number of climate-related policy "
                     "instruments and their effects on emissions over time.")
        self.assertGreater(len(long_line), 80)
        self.assertEqual(agent.lookup_list_rows(page(long_line + "\n", "")), [])


class OneReaderTest(unittest.TestCase):
    """The point of the change: the rows `create_file` refuses over are exactly the rows
    `done()` will demand pages for. Three independent patterns is how the write path could
    be satisfied by a list the completeness check could not see."""

    def test_the_sites_share_the_function(self):
        src = Path(agent.__file__).read_text(encoding="utf-8")
        # Only the sorter may still carry its own row pattern, and its docstring says so.
        self.assertEqual(src.count("lookup_list_rows("), 3,
                         "a reader of these lists is not going through the shared one")

    def test_listed_names_sees_a_numbered_list(self):
        with TempWiki() as w:
            w.page("sources/x-2026-y.md", title="A Source 2026", type="source",
                   body=page(numbered(PROPER), "1. Another Concept\n"))
            agent.init_session(inbox_path="raw/x.md")
            agent._ctx()._current_source_page = "sources/x-2026-y.md"
            self.assertEqual(agent._listed_names(agent._ctx()),
                             PROPER + ["Another Concept"])

    def test_listed_names_sees_a_table(self):
        with TempWiki() as w:
            w.page("sources/x-2026-y.md", title="A Source 2026", type="source",
                   body=page(table(PROPER), "- Another Concept\n"))
            agent.init_session(inbox_path="raw/x.md")
            agent._ctx()._current_source_page = "sources/x-2026-y.md"
            self.assertEqual(agent._listed_names(agent._ctx()),
                             PROPER + ["Another Concept"])

    def test_listed_names_still_dedupes(self):
        with TempWiki() as w:
            w.page("sources/x-2026-y.md", title="A Source 2026", type="source",
                   body=page("- Climate Policy\n- climate policy\n", "- Climate Policy\n"))
            agent.init_session(inbox_path="raw/x.md")
            agent._ctx()._current_source_page = "sources/x-2026-y.md"
            self.assertEqual(agent._listed_names(agent._ctx()), ["Climate Policy"])


class CreateFileRefusesTest(unittest.TestCase):
    """Through the real tool, in every shape — the reported bug is a refusal that did not
    happen, so the only assertion that proves anything is the refusal itself."""

    def _create(self, body):
        with TempWiki():
            agent.init_session(inbox_path="raw/x.md")
            return agent.TOOL_FNS["create_file"]({
                "path": "wiki/sources/x-2026-y.md", "title": "A Source 2026",
                "type": "source", "body": body,
            })

    def test_a_lowercase_bullet_list_is_refused(self):
        out = self._create(page(bullets(LOWER), "- another concept\n"))
        self.assertTrue(out.startswith("Error"), out)
        self.assertIn("lowercase", out)

    def test_a_lowercase_numbered_list_is_refused(self):
        out = self._create(page(numbered(LOWER), "1. another concept\n"))
        self.assertTrue(out.startswith("Error"), out)
        self.assertIn("lowercase", out)

    def test_a_lowercase_table_is_refused(self):
        out = self._create(page(table(LOWER), "| Name | Why |\n|---|---|\n"
                                              "| another concept | x |\n"))
        self.assertTrue(out.startswith("Error"), out)
        self.assertIn("lowercase", out)

    def test_lowercase_bare_lines_are_refused(self):
        out = self._create(page(bare(LOWER), "another concept\n"))
        self.assertTrue(out.startswith("Error"), out)
        self.assertIn("lowercase", out)

    def test_the_refusal_names_a_move_that_works(self):
        """Principle 4, and the only version of this assertion worth having: follow the
        refusal's instruction and assert the page is accepted."""
        out = self._create(page(numbered(LOWER), "1. another concept\n"))
        self.assertIn("as a human would read it aloud", out)
        ok = self._create(page(numbered(PROPER), "1. Another Concept\n"))
        self.assertFalse(ok.startswith("Error"), ok)

    def test_the_page_is_not_written(self):
        """A source page cannot be edited afterwards, so a refusal that still wrote the
        file would be worse than no refusal."""
        with TempWiki() as w:
            agent.init_session(inbox_path="raw/x.md")
            agent.TOOL_FNS["create_file"]({
                "path": "wiki/sources/x-2026-y.md", "title": "A Source 2026",
                "type": "source", "body": page(numbered(LOWER), "1. another concept\n"),
            })
            self.assertFalse((w.wiki / "sources" / "x-2026-y.md").exists())


class NotCryingWolfTest(unittest.TestCase):
    """Widening what counts as a row widens what can be falsely refused, and a refusal
    costs a full round at `max_rpm: 1`. Each of these must go straight through."""

    def _create(self, body):
        with TempWiki():
            agent.init_session(inbox_path="raw/x.md")
            return agent.TOOL_FNS["create_file"]({
                "path": "wiki/sources/x-2026-y.md", "title": "A Source 2026",
                "type": "source", "body": body,
            })

    def test_a_proper_list_in_every_shape_is_accepted(self):
        for label, rows in (("bullets", bullets(PROPER)),
                            ("numbered", numbered(PROPER)),
                            ("table", table(PROPER)),
                            ("bare", bare(PROPER))):
            with self.subTest(shape=label):
                out = self._create(page(rows, "- Another Concept\n"))
                self.assertFalse(out.startswith("Error"), f"{label}: {out}")

    def test_an_introductory_sentence_is_not_a_row(self):
        out = self._create(page("the following entities appear in this article:\n\n"
                                + bullets(PROPER), "- Another Concept\n"))
        self.assertFalse(out.startswith("Error"), out)

    def test_a_genuinely_lowercase_name_is_still_allowed(self):
        out = self._create(page("- bell hooks\n- Harvard University\n"
                                "- Gloria Watkins\n- Yale University\n",
                                "- Feminist Theory\n"))
        self.assertFalse(out.startswith("Error"), out)

    def test_a_table_header_does_not_count_toward_the_ratio(self):
        """"Name" is capitalised, so counting the header would make a lowercase table
        look like half-and-half and slip under the majority test."""
        rows = agent.lookup_list_rows(page(table(LOWER), ""))
        self.assertEqual(rows, LOWER)


class SorterGapIsStatedTest(unittest.TestCase):
    """The sorter is deliberately left bullet-only — sorting `1.` rows means renumbering
    and a table has a header that must stay on top. A stated gap, not a silent one."""

    def test_bullets_are_still_sorted(self):
        body = page("- Carbon Tax\n- Alpha Policy\n", "")
        out = agent.sort_lookup_lists(body)
        self.assertLess(out.index("Alpha Policy"), out.index("Carbon Tax"))

    def test_a_numbered_list_is_left_alone(self):
        body = page("1. Carbon Tax\n2. Alpha Policy\n", "")
        self.assertEqual(agent.sort_lookup_lists(body), body)

    def test_the_gap_is_documented(self):
        self.assertIn("Stated gap", agent.sort_lookup_lists.__doc__)

    def test_the_schema_asks_for_bullets(self):
        text = (Path(__file__).resolve().parent.parent / "LOBOTOMY.md").read_text()
        self.assertIn("one name per row", text)


if __name__ == "__main__":
    unittest.main()
