"""One canonical reading and one canonical rendering of a frontmatter scalar.

Two defects, found by asking whether the tags bug had siblings.

**A backticked title is invisible to the autolinker.** There were nine copies of the
title regex in two variants — `["\\']?(.+?)["\\']?` in seven places, `"?([^"\\n]+)"?` in
two — and not one stripped a backtick. So a title the model wrote as code entered
_build_title_map WITH the backticks and could only ever match text spelled the same way:
a permanent silent miss, the Noah's-Ark symptom class. _resolve_page's filename fallback
means it does NOT also produce a duplicate page, which is the shared resolver earning its
keep.

**Quoting without escaping produced invalid YAML, and the readers then disagreed.**
create_file interpolated into '"{value}"', so a title containing a quote was written

    title: "The "Big Lie""

which _parse_title_fields read as 'The "Big Lie' — .strip('"') eats every quote at both
ends — while the ["\\']? regex read 'The "Big Lie"'. Two readers, two titles for one page,
and the autolinker used the truncated one. This is why "just force double quotes on
everything" was the wrong instruction: more quoting without escaping spreads it.

Not normalized, deliberately: type: (a bare enum, sanitized elsewhere), created:/updated:
(YAML dates — quoting makes them strings) and no_autolink: (a boolean, where "true" is a
string that only works by truthiness accident).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent
import serve


class FmScalarTest(unittest.TestCase):

    def test_matched_wrapper_pairs_are_stripped(self):
        for raw in ('"Measles"', "'Measles'", "`Measles`", "  Measles  ", '"`Measles`"'):
            self.assertEqual(agent.fm_scalar(raw), "Measles", raw)

    def test_an_unmatched_backtick_is_removed_too(self):
        # Not a pair, so pair-stripping alone would leave it — and the model writes it.
        self.assertEqual(agent.fm_scalar("`Noah's Ark Scans"), "Noah's Ark Scans")
        self.assertEqual(agent.fm_scalar('"`Noah\'s Ark`"'), "Noah's Ark")

    def test_an_inner_quote_is_kept_not_eaten(self):
        """The truncation. .strip('"') removed the closing quote as well as the wrapper."""
        self.assertEqual(agent.fm_scalar('"The "Big Lie""'), 'The "Big Lie"')
        self.assertEqual('"The "Big Lie""'.strip('"'), 'The "Big Lie')  # the old behaviour

    def test_an_apostrophe_is_not_a_wrapper(self):
        self.assertEqual(agent.fm_scalar('"Noah\'s Ark"'), "Noah's Ark")

    def test_a_legitimately_quoted_phrase_survives(self):
        self.assertEqual(agent.fm_scalar('"\\"Big Lie\\" claims"'), '"Big Lie" claims')

    def test_quote_then_read_is_a_round_trip(self):
        for v in ('The "Big Lie"', "Noah's Ark", "a\\b", 'both " and \\ here',
                  "Measles", "C:\\path"):
            self.assertEqual(agent.fm_scalar(agent.fm_quote(v)), v, v)

    def test_quoting_produces_an_escaped_scalar(self):
        self.assertEqual(agent.fm_quote('The "Big Lie"'), '"The \\"Big Lie\\""')

    def test_reading_is_idempotent(self):
        once = agent.fm_scalar('"`Measles`"')
        self.assertEqual(agent.fm_scalar(once), once)


class OneReaderTest(TempWikiTestCase):
    """_fm_title is now the only way a title is read, so no two callers can disagree."""

    def _write(self, title_line):
        p = self.w.wiki / "entities" / "x.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\n{title_line}\ntype: entity\ntags: []\ncreated: 2026-01-01\n"
                     f"updated: 2026-01-01\nsources: []\n---\n\n# X\n\n## Overview\n\nx.\n")
        agent._title_map_cache = None
        return p

    def test_the_two_readers_agree_on_a_title_with_an_inner_quote(self):
        """They used to return 'The "Big Lie"' and 'The "Big Lie' for the same line."""
        text = self._write('title: "The "Big Lie""').read_text()
        self.assertEqual(agent._fm_title(text), agent._parse_title_fields(text)[0])

    def test_a_backticked_title_is_read_without_the_backticks(self):
        text = self._write("title: `Noah's Ark Scans`").read_text()
        self.assertEqual(agent._fm_title(text), "Noah's Ark Scans")
        self.assertEqual(agent._parse_title_fields(text)[0], "Noah's Ark Scans")

    def test_serve_agrees_with_agent(self):
        text = self._write('title: "The "Big Lie""').read_text()
        self.assertEqual(serve._parse_frontmatter(text)[0]["title"],
                         agent._fm_title(text))


class AutolinkPayoffTest(TempWikiTestCase):
    """The reason this matters: the title feeds the autolinker."""

    def _target(self, title_line):
        p = self.w.wiki / "entities" / "noah.md"
        p.write_text(f"---\n{title_line}\ntype: entity\ntags: []\ncreated: 2026-01-01\n"
                     f"updated: 2026-01-01\nsources: []\n---\n\n# N\n\n## Overview\n\nx.\n")
        agent._title_map_cache = None

    def test_a_backticked_title_still_enters_the_map_cleanly(self):
        self._target("title: `Noah's Ark Scans`")
        self.assertEqual([t for t, _ in agent._build_title_map()], ["Noah's Ark Scans"])

    def test_and_prose_naming_it_now_links(self):
        self._target("title: `Noah's Ark Scans`")
        self.w.page("sources/s.md", title="S", type="source",
                    body="# S\n\n## Entities\n\n- Noah's Ark Scans\n")
        agent._autolink({"path": "wiki/sources/s.md"})
        self.assertIn("](../entities/noah.md)", self.w.disk("sources/s.md"))

    def test_a_page_is_not_duplicated_by_it(self):
        """_resolve_page's filename fallback covered this even while the map was wrong."""
        self._target("title: `Noah's Ark Scans`")
        by_key = {agent._norm_title_key(t): v for t, v in agent._build_title_map()}
        self.assertEqual(agent._resolve_page("Noah's Ark Scans", by_key), "entities/noah.md")


class WritePathTest(TempWikiTestCase):

    def test_create_file_escapes_a_quote_in_the_title(self):
        out = agent._create_file({"path": "wiki/concepts/bl.md", "title": 'The "Big Lie"',
                                  "type": "concept", "tags": ["politics"],
                                  "body": "## Definition\n\nA claim.\n"})
        self.assertNotIn("Error", out, out)
        disk = self.w.disk("concepts/bl.md")
        self.assertIn('title: "The \\"Big Lie\\""', disk)
        self.assertEqual(agent._fm_title(disk), 'The "Big Lie"')
        self.assertEqual(agent._parse_title_fields(disk)[0], 'The "Big Lie"')

    def test_create_file_strips_a_backtick_from_the_title(self):
        agent._create_file({"path": "wiki/entities/n.md", "title": "`Noah's Ark Scans`",
                            "type": "entity", "tags": ["archaeology"],
                            "body": "## Overview\n\nA thing.\n"})
        self.assertEqual(agent._fm_title(self.w.disk("entities/n.md")),
                         "Noah's Ark Scans")

    def test_a_quote_in_a_url_is_escaped(self):
        agent._create_file({"path": "wiki/sources/s.md", "title": "S", "type": "source",
                            "tags": ["news"], "url": 'https://e.com/a"b',
                            "body": "## Summary\n\nx.\n\n## Claims\n\n- A claim.\n"
                                    "\n## Entities\n\n- Measles\n"
                                    "\n## Concepts\n\n- Vaccination\n"})
        disk = self.w.disk("sources/s.md")
        self.assertIn('url: "https://e.com/a\\"b"', disk)
        self.assertEqual(serve._parse_frontmatter(disk)[0]["url"], 'https://e.com/a"b')

    def test_an_ordinary_title_is_written_plainly(self):
        agent._create_file({"path": "wiki/entities/m.md", "title": "Measles Outbreak",
                            "type": "entity", "tags": ["health"],
                            "body": "## Overview\n\nx.\n"})
        self.assertIn('title: "Measles Outbreak"', self.w.disk("entities/m.md"))


class HealTest(TempWikiTestCase):

    def _damaged(self, title_line, h1="X"):
        p = self.w.wiki / "entities" / "x.md"
        p.write_text(f"---\n{title_line}\ntype: entity\ntags: []\ncreated: 2026-01-01\n"
                     f"updated: 2026-01-01\nsources: []\n---\n\n# {h1}\n\n"
                     f"## Overview\n\nx.\n")
        return p

    def test_a_backticked_title_is_repaired(self):
        p = self._damaged("title: `Noah's Ark Scans`")
        agent.heal_pages()
        self.assertIn("""title: "Noah's Ark Scans\"""", p.read_text())

    def test_an_unescaped_inner_quote_is_repaired(self):
        p = self._damaged('title: "The "Big Lie""')
        agent.heal_pages()
        self.assertIn('title: "The \\"Big Lie\\""', p.read_text())
        self.assertEqual(agent._fm_title(p.read_text()), 'The "Big Lie"')

    def test_healing_is_idempotent(self):
        """heal_pages runs at startup and after every ingest — a normalizer that rewrote
        on every pass would fill page history with empty revisions forever."""
        p = self._damaged('title: "The "Big Lie""')
        agent.heal_pages()
        once = p.read_text()
        agent.heal_pages()
        self.assertEqual(p.read_text(), once)

    def test_a_clean_title_is_not_rewritten(self):
        p = self._damaged('title: "Measles Outbreak"', h1="Measles Outbreak")
        before = p.read_text()
        agent.heal_pages()
        self.assertEqual(p.read_text(), before)


class FieldsLeftAloneTest(TempWikiTestCase):
    """"Force everything to double quotes" would have been wrong for these three."""

    def test_type_dates_and_booleans_stay_bare(self):
        agent._create_file({"path": "wiki/entities/m.md", "title": "M", "type": "entity",
                            "tags": ["health"], "body": "## Overview\n\nx.\n"})
        disk = self.w.disk("entities/m.md")
        self.assertIn("type: entity", disk)
        self.assertNotIn('type: "entity"', disk)
        self.assertRegex(disk, r"created: \d{4}-\d{2}-\d{2}\n")
        self.assertNotRegex(disk, r'created: "')

    def test_no_autolink_is_still_read_as_a_boolean(self):
        self.w.page("entities/q.md", title="Q", type="entity", no_autolink=True,
                    body="# Q\n\n## Overview\n\nx.\n")
        text = self.w.disk("entities/q.md")
        self.assertTrue(agent._parse_title_fields(text)[2])
        self.assertIs(serve._parse_frontmatter(text)[0]["no_autolink"], True)


class ServeTagViewsTest(TempWikiTestCase):
    """The NameError this refactor surfaced.

    serve.py imports from agent with `from agent import (...)`, so an `agent.fm_scalar(...)`
    call inside it raises NameError — and list_inbox wraps its parse in a try/except that
    swallows everything, so the symptom was not a traceback but titles quietly falling back
    to the filename. Nothing in the suite touched serve's two tag readers at all, so the
    same mistake sat in the /wiki/tags path undetected.
    """

    def test_the_helpers_are_bound_in_serve(self):
        self.assertIs(serve.fm_scalar, agent.fm_scalar)
        self.assertIs(serve.parse_tags_line, agent.parse_tags_line)

    def test_a_backticked_tag_is_one_tag_in_the_frontmatter_parse(self):
        self.w.page("entities/a.md", title="A", type="entity", body="# A\n\nx.\n")
        p = self.w.wiki / "entities" / "a.md"
        p.write_text(p.read_text().replace("tags: []", 'tags: ["law", `law`]'))
        # The generic reader canonicalizes each entry; it does not dedupe arbitrary
        # lists, because sources: must keep exactly what it has.
        self.assertEqual(serve._parse_frontmatter(p.read_text())[0]["tags"],
                         ["law", "law"])
        # Deduping is the tag readers' job, and they share parse_tags_line.
        self.assertEqual(serve.parse_tags_line('["law", `law`]'), ["law"])


if __name__ == "__main__":
    unittest.main()
