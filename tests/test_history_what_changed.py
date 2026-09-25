"""Each history row says what the write touched, not only how many lines it moved.

The rows had a lot of empty middle and two numbers in them. Two things fill it, and they
are gathered in opposite ways on purpose:

**Which sections changed** — derived from the same unified diff that already produces the
+/- counts, by mapping each changed line back to the heading above it. Derived rather than
stored precisely so it works on the fifty revisions already on disk; a stored field would
have described only writes made after it shipped. Frontmatter is excluded, because
`updated:` changes on every single write and would put the same useless word on every row.

**Which source an ingest was folding in** — this one cannot be derived, so it is stored,
as a third `__` part of the revision filename. `_current_source_page` is already on the
session context at snapshot time, so nothing new has to be tracked. Old filenames have one
or two parts and still parse.

Neither is a diff. "ingest · from Mark Carney on Trump, tariffs and Canada · Positions" is
a sentence; `@@ -12,3 +12,5 @@` is not.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

BASE = ("# Mark Carney\n\n## Overview\n\nA banker.\n\n## Background\n\nBorn in 1965.\n\n"
        "## Positions\n\nOn trade policy.\n\n## Timeline\n\n- **2026-01** — Elected.\n")


class ChangedSectionsTest(TempWikiTestCase):

    def setUp(self):
        super().setUp()
        self.p = self.w.page("entities/mark-carney.md", title="Mark Carney",
                             type="entity", body=BASE)
        self.fm = self.p.read_text(encoding="utf-8").split("---\n")[1]

    def _write(self, body, why="ingest", source=""):
        agent.begin_write_scope()
        agent.init_session()
        agent._ctx()._current_source_page = source
        with agent.write_reason(why):
            agent._atomic_write(self.p, f"---\n{self.fm}---\n\n{body}")

    def _rows(self):
        return agent.page_history(self.p)

    def test_it_names_the_section_that_changed(self):
        self._write(BASE.replace("On trade policy.", "On trade policy and tariffs."))
        self.assertEqual(self._rows()[0]["sections"], ["Positions"])

    def test_it_names_several_sections_in_document_order(self):
        self._write(BASE.replace("A banker.", "A banker and politician.")
                        .replace("- **2026-01** — Elected.",
                                 "- **2026-01** — Elected.\n- **2026-09** — Interviewed."))
        self.assertEqual(self._rows()[0]["sections"], ["Overview", "Timeline"])

    def test_a_removed_line_counts_as_touching_its_section(self):
        self._write(BASE.replace("\n## Background\n\nBorn in 1965.\n", "\n## Background\n\n"))
        self.assertIn("Background", self._rows()[0]["sections"])

    def test_frontmatter_only_changes_name_no_section(self):
        # updated: moves on every write; reporting it would put the same word everywhere.
        agent.begin_write_scope()
        with agent.write_reason("heal"):
            agent._atomic_write(self.p, f"---\n{self.fm.replace('2026-01-01', '2026-09-24')}"
                                        f"---\n\n{BASE}")
        self.assertEqual(self._rows()[0]["sections"], [])

    def test_a_title_change_is_not_reported_as_a_section(self):
        # The H1 is not a section anywhere else either (_page_section_names skips it), so
        # a row must not claim the page's own name as a heading.
        self._write(BASE.replace("# Mark Carney", "# Mark J. Carney"))
        self.assertEqual(self._rows()[0]["sections"], [])

    def test_it_works_on_revisions_written_before_the_feature(self):
        # The reason this is derived and not stored. These revisions carry no section
        # data of their own; the diff is all there is, and it is enough.
        self._write(BASE.replace("On trade policy.", "On trade policy and tariffs."))
        self._write(BASE.replace("On trade policy.", "On trade policy and tariffs.")
                        .replace("A banker.", "A banker and politician."))
        rows = self._rows()
        self.assertEqual(rows[0]["sections"], ["Overview"])
        self.assertEqual(rows[1]["sections"], ["Positions"])

    def test_the_oldest_row_claims_no_sections(self):
        self._write(BASE.replace("A banker.", "A banker and politician."))
        self.assertEqual(self._rows()[-1]["sections"], [])

    def test_the_list_is_capped(self):
        body = BASE
        for old, new in (("A banker.", "A banker!"), ("Born in 1965.", "Born in 1966."),
                         ("On trade policy.", "On trade."), ("— Elected.", "— Won.")):
            body = body.replace(old, new)
        self._write(body)
        self.assertLessEqual(len(self._rows()[0]["sections"]), 4)


class IngestSourceTest(TempWikiTestCase):

    def setUp(self):
        super().setUp()
        self.p = self.w.page("entities/mark-carney.md", title="Mark Carney",
                             type="entity", body=BASE)
        self.fm = self.p.read_text(encoding="utf-8").split("---\n")[1]

    def _write(self, body, why="ingest", source=""):
        agent.begin_write_scope()
        agent.init_session()
        agent._ctx()._current_source_page = source
        with agent.write_reason(why):
            agent._atomic_write(self.p, f"---\n{self.fm}---\n\n{body}")

    def test_an_ingest_records_which_source_it_was_folding_in(self):
        self._write(BASE.replace("A banker.", "A banker and politician."),
                    source="sources/stevis-gridneff-2026-mark-carney-interview.md")
        self.assertEqual(agent.page_history(self.p)[0]["source"],
                         "stevis-gridneff-2026-mark-carney-interview")

    def test_a_non_ingest_write_records_no_source(self):
        # A relink sweep or a hand edit is not "from" an article, and claiming one would
        # be a lie the row states confidently.
        self._write(BASE.replace("A banker.", "A banker and politician."), why="relink",
                    source="sources/stevis-gridneff-2026-mark-carney-interview.md")
        self.assertEqual(agent.page_history(self.p)[0]["source"], "")

    def test_an_ingest_with_no_source_page_records_none(self):
        self._write(BASE.replace("A banker.", "A banker and politician."))
        self.assertEqual(agent.page_history(self.p)[0]["source"], "")

    def test_revision_filenames_from_before_this_still_parse(self):
        for stem, want in (
                ("20260924T120000000000", ("20260924T120000000000", "", "", "")),
                ("20260924T120000000000__ingest",
                 ("20260924T120000000000", "ingest", "", "")),
                ("20260924T120000000000__ingest__ap-2026-potash",
                 ("20260924T120000000000", "ingest", "ap-2026-potash", "")),
                ("20260924T120000000000__ingest__ap-2026-potash__update_file",
                 ("20260924T120000000000", "ingest", "ap-2026-potash", "update_file")),
                # "-" is the empty-slot placeholder. Every slot before a filled one has
                # to be written or the parts shift left — a write with a tool and no
                # reason produced "__-__update-file" and parsed as reason="-",
                # source="update-file", tool="".
                ("20260924T120000000000__user-edit__-__update_file",
                 ("20260924T120000000000", "user-edit", "", "update_file")),
                ("20260924T120000000000__-__-__update-file",
                 ("20260924T120000000000", "", "", "update-file")),
        ):
            with self.subTest(stem):
                self.assertEqual(agent._parse_revision_stem(stem), want)

    def test_the_reason_is_still_the_reason(self):
        # The source is a THIRD part; adding it must not turn the label into
        # "ingest__ap-2026-potash".
        self._write(BASE.replace("A banker.", "A banker and politician."),
                    source="sources/ap-2026-canada-potash-export.md")
        self.assertEqual(agent.page_history(self.p)[0]["why"], "ingest")

    def test_lexical_order_is_still_chronological(self):
        # The pruning and the history view both sort by filename; a longer suffix must not
        # disturb that, because the timestamp is still the fixed-width prefix.
        for i, src in enumerate(("sources/a-2026-one.md", "", "sources/b-2026-two.md")):
            self._write(BASE + f"\nline {i}\n", why="ingest" if src else "relink", source=src)
        names = sorted(f.name for f in
                       (agent.HISTORY_DIR / "entities" / "mark-carney.md").glob("*.md"))
        self.assertEqual(names, sorted(names, key=lambda n: n.split("__")[0]))


class WhichToolTest(TempWikiTestCase):
    """"ingest" says a source was folded in. It does not say whether that was a whole-page
    regenerate or a one-line fix, and those are very different things to find in a list of
    fifty rows.
    """

    def setUp(self):
        super().setUp()
        self.p = self.w.page("entities/mark-carney.md", title="Mark Carney",
                             type="entity", body=BASE)
        agent.init_session()
        agent.TOOL_FNS["read_file"]({"path": "wiki/entities/mark-carney.md"})

    def _why(self):
        return agent.page_history(self.p)[0]["tool"]

    def _dispatch(self, name, args):
        """Through the dispatcher both agent loops use, not around it.

        Calling write_tool() by hand here would test the label and not the wiring — and
        the wiring is what was wrong: the two loops each had their own copy of the scope
        and only one of them had it.
        """
        agent.begin_write_scope()
        return agent._call_tool(name, agent.TOOL_FNS.get(name), args)

    def test_a_whole_page_rewrite_is_labelled_rewrite(self):
        r = self._dispatch("update_file", {
            "path": "wiki/entities/mark-carney.md",
            "content": BASE.replace("A banker.", "An economist and politician.")})
        self.assertFalse(r.startswith("Error:"), r)
        self.assertEqual(self._why(), "rewrite")

    def test_a_section_edit_is_labelled_section(self):
        r = self._dispatch("update_section", {
            "path": "wiki/entities/mark-carney.md", "section": "Positions",
            "content": "On trade policy and tariffs.\n"})
        self.assertFalse(r.startswith("Error:"), r)
        self.assertEqual(self._why(), "section")

    def test_a_timeline_entry_is_labelled_timeline(self):
        r = self._dispatch("add_timeline_entry", {
            "path": "wiki/entities/mark-carney.md", "date": "2026-09",
            "text": "Gave an interview."})
        self.assertFalse(r.startswith("Error:"), r)
        self.assertEqual(self._why(), "timeline",
                         "the label was truncated or the name was not converted back")

    def test_a_read_only_tool_tags_nothing(self):
        # Only the writing tools are in _TOOL_LABELS; a read must not leave a label behind
        # for whatever writes next.
        self._dispatch("read_file", {"path": "wiki/entities/mark-carney.md"})
        self.assertEqual(agent.get_write_tool(), "")

    def test_an_unlabelled_write_says_nothing(self):
        # A maintenance pass writes through _atomic_write with no tool scope. It must not
        # invent one.
        agent.begin_write_scope()
        with agent.write_reason("relink"):
            agent._atomic_write(self.p, self.p.read_text(encoding="utf-8") + "\nMore.\n")
        self.assertEqual(self._why(), "")

    def test_the_tool_scope_does_not_leak_between_calls(self):
        self._dispatch("update_file", {
            "path": "wiki/entities/mark-carney.md",
            "content": BASE.replace("A banker.", "An economist.")})
        agent.begin_write_scope()
        with agent.write_reason("relink"):
            agent._atomic_write(self.p, self.p.read_text(encoding="utf-8") + "\nMore.\n")
        self.assertEqual(self._why(), "", "a tool label leaked into the next write")


class WordCountTest(TempWikiTestCase):
    """Counts are words, not lines.

    Wiki pages are written unwrapped, so one paragraph is one line. A whole paragraph
    replaced reported "+1 -1", which made a regenerate indistinguishable from a typo fix —
    the observation that started this.
    """

    def setUp(self):
        super().setUp()
        self.p = self.w.page("entities/mark-carney.md", title="Mark Carney",
                             type="entity", body=BASE)
        self.fm = self.p.read_text(encoding="utf-8").split("---\n")[1]

    def _write(self, body):
        agent.begin_write_scope()
        agent.init_session()
        with agent.write_reason("ingest"):
            agent._atomic_write(self.p, f"---\n{self.fm}---\n\n{body}")

    def _row(self):
        return agent.page_history(self.p)[0]

    def test_a_rewritten_paragraph_counts_its_words_not_its_line(self):
        self._write(BASE.replace(
            "On trade policy.",
            "On trade policy, tariffs, potash exports and the wider dispute."))
        r = self._row()
        self.assertGreater(r["added"], 5,
                           "a rewritten paragraph still reports as one line changed")

    def test_unchanged_words_in_a_touched_line_are_not_counted(self):
        # The point of diffing words rather than lines: only what actually changed.
        self._write(BASE.replace("Born in 1965.", "Born in 1966."))
        r = self._row()
        self.assertEqual((r["added"], r["removed"]), (1, 1), (r["added"], r["removed"]))

    def test_a_pure_addition_removes_nothing(self):
        self._write(BASE + "\n## Sources\n\n- one\n")
        r = self._row()
        self.assertEqual(r["removed"], 0, r)
        self.assertGreater(r["added"], 0)

    def test_a_hyphenated_word_counts_once(self):
        self._write(BASE.replace("A banker.", "A post-war banker."))
        self.assertEqual(self._row()["added"], 1, "'post-war' was counted as two words")


class SourceLinkMarkupTest(unittest.TestCase):
    """The source title on a history row is a link to that source page.

    It was wired correctly from the start and still did not read as one: styled in the
    muted body colour with a thin underline, it looked like plain text, and "make it a
    hyperlink" is what a working link that does not look like one earns. Accent colour is
    the affordance; the href was never the problem.

    Markup assertions are weak, so this file keeps only the two that would be silent
    failures: the anchor disappearing, and a source page that no longer exists being
    rendered as a link to nothing.
    """

    def setUp(self):
        self.html = (Path(__file__).resolve().parent.parent / "tools" / "templates"
                     / "wiki-history.html").read_text(encoding="utf-8")

    def test_the_source_title_is_an_anchor_to_the_source_page(self):
        self.assertIn('<a href="/wiki/{{ r.source.path }}">{{ r.source.title }}</a>',
                      self.html, "the source title stopped being a link")

    def test_a_missing_source_page_renders_as_plain_text(self):
        # serve.py sets path=None when the source page has been renamed or deleted; the
        # template must fall through to the bare title rather than link to nothing.
        self.assertIn("{%- else %} {{ r.source.title }}{% endif -%}", self.html)

    def test_the_link_is_accent_coloured(self):
        # The whole point of the change. A link in the body colour is a link nobody clicks.
        m = re.search(r"\.rev \.what \.src a \{[^}]*\}", self.html, re.DOTALL)
        self.assertIsNotNone(m, "the source link rule was removed")
        self.assertIn("var(--accent)", m.group(0), m.group(0))


class LongSourceSlugTest(TempWikiTestCase):
    """A capture's slug is long, and truncating it broke the link silently.

    A reading-list capture slugs to things like
    "https-www-nytimes-com-2026-09-23-world-canada-mark-carney-calls-trump-tariffs-a-rupture"
    — 87 characters. The stamp truncated at 72, so the stored slug named no file, serve.py
    fell through to its "source page is gone" branch, and the row rendered as plain text.
    Nothing failed; the link just was not there, which is why it read as "the text is there,
    make it a hyperlink".

    Two fixes, because one of them cannot reach what is already on disk: the cap is raised,
    AND serve.py resolves a stored slug that is a prefix of exactly one source page. Rows
    stamped before the cap changed keep working.
    """

    SLUG = ("https-www-nytimes-com-2026-09-23-world-canada-mark-carney-"
            "calls-trump-tariffs-a-rupture")

    def _ingest(self, source_rel):
        self.w.page("entities/mark-carney.md", title="Mark Carney", type="entity",
                    body="# Mark Carney\n\n## Overview\n\nA banker.\n")
        p = agent.WIKI_DIR / "entities" / "mark-carney.md"
        fm = p.read_text(encoding="utf-8").split("---\n")[1]
        agent.begin_write_scope()
        agent.init_session()
        agent._ctx()._current_source_page = source_rel
        with agent.write_reason("ingest"), agent.write_tool("update_section"):
            agent._atomic_write(p, f"---\n{fm}---\n\n# Mark Carney\n\n"
                                   f"## Overview\n\nA banker and politician.\n")
        return p

    def test_the_whole_slug_is_stored(self):
        self.assertGreater(len(self.SLUG), 72, "the fixture stopped exercising the cap")
        self.w.page(f"sources/{self.SLUG}.md", title="Carney calls tariffs a rupture",
                    type="source", body="# X\n\n## Summary\n\nX.\n")
        p = self._ingest(f"sources/{self.SLUG}.md")
        self.assertEqual(agent.page_history(p)[0]["source"], self.SLUG)

    def test_the_stored_slug_names_a_real_file(self):
        # The actual failure: a truncated slug is not a filename, so no link is possible.
        self.w.page(f"sources/{self.SLUG}.md", title="Carney calls tariffs a rupture",
                    type="source", body="# X\n\n## Summary\n\nX.\n")
        p = self._ingest(f"sources/{self.SLUG}.md")
        rec = agent.page_history(p)[0]["source"]
        self.assertTrue((agent.WIKI_DIR / "sources" / f"{rec}.md").is_file())

    def test_a_filename_stays_within_reach_of_any_filesystem(self):
        self.w.page(f"sources/{self.SLUG}.md", title="X", type="source",
                    body="# X\n\n## Summary\n\nX.\n")
        p = self._ingest(f"sources/{self.SLUG}.md")
        longest = max(len(f.name) for f in
                      (agent.HISTORY_DIR / "entities" / "mark-carney.md").glob("*.md"))
        self.assertLess(longest, 255, "a revision filename could exceed a filesystem limit")


if __name__ == "__main__":
    unittest.main()
