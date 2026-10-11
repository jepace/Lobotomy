r"""A `\uXXXX` escaped twice reaches disk as six literal characters.

Reported as *"i get literal ‘ and ’ in titles"*.

**Nothing was malformed at any point**, which is why it sat there. A provider hands a tool
call's arguments over as a JSON string, so one `json.loads` is correct and gives back real
characters. A model that writes `\\u2019` — escaping the backslash too, a habit from
writing JSON inside JSON — produces *valid* JSON that parses to the six characters
`’`. The write succeeds, the page renders, `_build_title_map` indexes it, and the
title simply reads "Trump’s Plan" for ever.

Three decisions worth keeping:

* **Decoded at the parse site, not in `create_file`.** The escape reaches disk through
  whichever of the five write tools the model happened to call, plus `section`, `date` and
  `text` on the others. One answer in one place — and the two agent loops each had their
  own `json.loads` line, so a fix in one would have left the other broken, with *which loop
  serves a round* depending on whether a browser is watching.
* **Decoded to the character the escape names, not folded to an ASCII quote.** The
  autolinker already treats ’ and ' as one character for matching (`_esc_flex`), so a curly
  quote costs nothing — while flattening one would make a page's title disagree with the
  article it came from, and rewriting an author's punctuation is not this code's job.
* **Code is exempt.** A page explaining JSON escapes writes `` `’` `` on purpose, and
  a pass that rewrote it would destroy the one page in the wiki that is about this.

And the half that is easy to get wrong: `heal_pages` fixes the ~13,000 pages already
carrying them, because prevention alone freezes the damage — the lesson `_MANGLED_URL_RE`
and `_unlink_in_code` each paid for.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

B = chr(92)          # a real backslash, built rather than written, so no layer of this
                     # file's own escaping can be mistaken for the bug under test


def esc(code: str) -> str:
    r"""The six characters `\uXXXX`, as they arrive after one round of json.loads."""
    return B + "u" + code


class DecoderTest(unittest.TestCase):
    def test_the_reported_characters_decode(self):
        got, n = agent._decode_stray_escapes(
            "Trump" + esc("2019") + "s " + esc("2018") + "Plan" + esc("2019"))
        self.assertEqual(got, "Trump’s ‘Plan’")
        self.assertEqual(n, 3)

    def test_other_typographic_escapes_decode_too(self):
        """The report named two; the same mistake produces every character the model
        might escape, and a list of two would have to be extended on each new one."""
        got, _ = agent._decode_stray_escapes(
            esc("201c") + "quote" + esc("201d") + " " + esc("2014") + " " + esc("2026"))
        self.assertEqual(got, "“quote” — …")

    def test_text_with_no_escapes_is_untouched(self):
        for s in ("Plain text.", "A real ’ curly quote stays.",
                  "A path C:" + B + "temp" + B + "file", ""):
            with self.subTest(s=s):
                self.assertEqual(agent._decode_stray_escapes(s), (s, 0))

    def test_it_is_idempotent(self):
        once, _ = agent._decode_stray_escapes("Trump" + esc("2019") + "s")
        self.assertEqual(agent._decode_stray_escapes(once), (once, 0))


class DeclinedEscapesTest(unittest.TestCase):
    """Three shapes are deliberately left as visible text, and the count has to agree —
    see below for why the count is the part that bites."""

    def test_a_lone_surrogate_is_left_alone(self):
        """Decoding one turns six harmless characters into a byte that cannot be encoded.
        This is the exact input `_atomic_write`'s errors="replace" exists to absorb, and
        it already cost a user their pasted article once; a cosmetic repair must not be
        able to manufacture it."""
        s = "half an emoji " + esc("d83d") + " here"
        self.assertEqual(agent._decode_stray_escapes(s), (s, 0))

    def test_a_control_character_is_left_alone(self):
        """`\\u000a` inside a `title:` line would cut the frontmatter in half."""
        s = "bell " + esc("0007") + " newline " + esc("000a")
        self.assertEqual(agent._decode_stray_escapes(s), (s, 0))

    def test_a_non_breaking_space_is_left_alone(self):
        """The judgement call here. `\\u00a0` is a plausible mistake — but a non-breaking
        space in a title breaks autolink matching INVISIBLY, where the literal escape
        breaks it visibly and gets reported, as this bug was. Visible beats invisible."""
        s = "Trump" + esc("00a0") + "Plan"
        self.assertEqual(agent._decode_stray_escapes(s), (s, 0))

    def test_a_plain_space_still_decodes(self):
        got, n = agent._decode_stray_escapes("two" + esc("0020") + "words")
        self.assertEqual((got, n), ("two words", 1))

    def test_the_count_is_substitutions_made_not_matches_found(self):
        """**The bug this test exists for.** `subn` counts MATCHES, so a declined escape
        reported a repair that had not happened — and `heal_pages` runs at startup and
        after every ingest, so one page holding a lone surrogate would have written an
        identical revision for ever. Same idempotence requirement `normalize_timeline`
        carries, in a second place."""
        s = "kept " + esc("d83d") + " decoded " + esc("2019")
        got, n = agent._decode_stray_escapes(s)
        self.assertEqual(n, 1)
        self.assertIn(esc("d83d"), got)
        self.assertIn("’", got)


class CodeIsExemptTest(unittest.TestCase):
    def test_an_escape_in_a_code_span_is_kept(self):
        s = "write `" + esc("2019") + "` to mean an apostrophe"
        self.assertEqual(agent._decode_stray_escapes(s), (s, 0))

    def test_an_escape_in_a_fenced_block_is_kept(self):
        s = ("```json\n{" + '"a": "' + esc("2019") + '"}' + "\n```\n")
        self.assertEqual(agent._decode_stray_escapes(s), (s, 0))

    def test_prose_on_a_line_with_a_code_span_still_decodes(self):
        """The half a whole-line skip would lose: most lines are mostly prose."""
        got, n = agent._decode_stray_escapes(
            "Trump" + esc("2019") + "s `" + esc("2019") + "` plan")
        self.assertEqual(got, "Trump’s `" + esc("2019") + "` plan")
        self.assertEqual(n, 1)

    def test_prose_after_a_fenced_block_still_decodes(self):
        got, n = agent._decode_stray_escapes(
            "```\n" + esc("2019") + "\n```\nTrump" + esc("2019") + "s")
        self.assertEqual(n, 1)
        self.assertTrue(got.endswith("Trump’s"))

    def test_a_tilde_fence_inside_a_backtick_block_does_not_end_it(self):
        """A fence closes only on a run of its OWN character at least as long — the rule
        the autolinker's walk already carries, and the reason that walk is shared rather
        than copied."""
        s = "```\n~~~\n" + esc("2019") + "\n```\n"
        self.assertEqual(agent._decode_stray_escapes(s), (s, 0))

    def test_the_unlinker_still_works_through_the_shared_walk(self):
        """`_unlink_in_code` was rewritten onto `_map_in_code` for this. Its own module
        covers it properly; this is the smoke test that the refactor kept it wired."""
        out, n = agent._unlink_in_code("`[a](../entities/a.md)`")
        self.assertEqual((out, n), ("`a`", 1))


class ParseToolArgsTest(unittest.TestCase):
    def _tc(self, args: dict) -> dict:
        return {"function": {"name": "create_file", "arguments": json.dumps(args)}}

    def test_a_double_escaped_title_arrives_decoded(self):
        args = agent.parse_tool_args(self._tc({"title": "Trump" + esc("2019") + "s Plan"}))
        self.assertEqual(args["title"], "Trump’s Plan")

    def test_correctly_escaped_json_is_unaffected(self):
        """The common case, and the one a careless fix would break: a model that escapes
        properly sends `\\u2019` in the wire JSON, which json.loads already turns into the
        character. Nothing must be done to it twice."""
        raw = '{"title": "Trump' + B + 'u2019s Plan"}'
        args = agent.parse_tool_args({"function": {"arguments": raw}})
        self.assertEqual(args["title"], "Trump’s Plan")

    def test_it_reaches_nested_values(self):
        args = agent.parse_tool_args(self._tc({
            "tags": ["a" + esc("2019") + "b"],
            "sources": [{"t": esc("2018") + "x"}],
        }))
        self.assertEqual(args["tags"], ["a’b"])
        self.assertEqual(args["sources"], [{"t": "‘x"}])

    def test_non_string_values_survive(self):
        args = agent.parse_tool_args(self._tc({"offset": 0, "allow_shrink": True,
                                               "n": None}))
        self.assertEqual(args, {"offset": 0, "allow_shrink": True, "n": None})

    def test_empty_and_absent_arguments(self):
        self.assertEqual(agent.parse_tool_args({}), {})
        self.assertEqual(agent.parse_tool_args({"function": {"arguments": ""}}), {})

    def test_both_agent_loops_go_through_it(self):
        """They each had their own `json.loads` line. Which loop serves a round depends on
        whether a browser is watching, so a fix applied to one would have produced a bug
        that came and went with the UI."""
        src = (Path(agent.__file__).read_text(encoding="utf-8"))
        body = src[src.index("def stream_agent_turn"):]
        self.assertNotIn('json.loads((tc.get("function")', body,
                         "an agent loop still parses tool arguments on its own")
        self.assertGreaterEqual(body.count("parse_tool_args(tc)"), 2)


class ServeSharesTheParseTest(unittest.TestCase):
    """`serve.py`'s saved display log had its own `json.loads` too — the third place that
    builds a tool-call label, and the one that drifted last time. A `section` name can
    carry the escape, so the live view would read "§ Trump’s Record" while the saved record
    of the same turn read the raw escape.

    `from agent import …` binds the object, so `agent.foo` inside serve is a `NameError`
    and `save_history` swallows every exception — which is exactly how that mistake stays
    invisible. Hence the identity assertion rather than a behavioural one."""

    def test_serve_uses_the_same_function_object(self):
        import serve
        self.assertIs(serve.parse_tool_args, agent.parse_tool_args)

    def test_the_display_log_does_not_parse_on_its_own(self):
        src = (Path(__file__).resolve().parent.parent
               / "tools" / "serve.py").read_text(encoding="utf-8")
        self.assertNotIn('json.loads((tc.get("function")', src)


class CreateFileTest(unittest.TestCase):
    """Through the real tool, because the decode is only worth anything if the page on
    disk is clean — and the title is what was reported."""

    def test_the_page_is_written_with_real_characters(self):
        with TempWiki() as w:
            agent.init_session()
            tc = {"function": {"name": "create_file", "arguments": json.dumps({
                "path": "wiki/entities/trumps-plan.md",
                "title": "Trump" + esc("2019") + "s Plan",
                "type": "entity",
                "body": "## Overview\n\nA plan Trump" + esc("2019") + "s team wrote.\n",
            })}}
            args = agent.parse_tool_args(tc)
            out = agent.TOOL_FNS["create_file"](args)
            self.assertFalse(out.startswith("Error"), out)
            text = (w.wiki / "entities" / "trumps-plan.md").read_text()
            self.assertIn("Trump’s Plan", text)
            self.assertNotIn(esc("2019"), text)

    def test_the_autolinker_can_match_the_healed_title(self):
        """What the bug actually cost, beyond looking wrong: a title holding six literal
        characters can only ever match text spelled the same way, so the page was
        unlinkable from every article that mentioned it. The same permanent silent miss a
        backticked title had."""
        with TempWiki() as w:
            w.page("entities/trumps-plan.md", title="Trump’s Plan",
                   body="## Overview\n\nA plan.\n")
            w.page("entities/other.md", title="Other",
                   body="## Overview\n\nSee Trump‘s Plan for details.\n")
            titles = [t for t, _rel in agent._build_title_map()]
            self.assertIn("Trump’s Plan", titles)


class HealPagesTest(unittest.TestCase):
    """Prevention alone freezes the damage. ~13,000 pages were written before the parse
    site decoded anything, and the title on each is what feeds `_build_title_map`."""

    def test_a_title_on_disk_is_decoded(self):
        with TempWiki() as w:
            p = w.page("entities/x.md", title="Trump" + esc("2019") + "s Plan",
                       body="## Overview\n\nA plan.\n")
            r = agent.heal_pages()
            self.assertIn("Trump’s Plan", p.read_text())
            # Two, not one: the escape is on the page twice, in `title:` and in the H1
            # `ensure_h1` derives from it. The count is occurrences healed, not pages.
            self.assertEqual(r.get("escapes_decoded"), 2)
            self.assertNotIn(esc("2019"), p.read_text())

    def test_a_body_on_disk_is_decoded(self):
        with TempWiki() as w:
            p = w.page("entities/x.md", title="X",
                       body="## Overview\n\nHe said " + esc("201c") + "no"
                            + esc("201d") + ".\n")
            agent.heal_pages()
            self.assertIn("“no”", p.read_text())

    def test_healing_is_idempotent(self):
        """`heal_pages` runs at startup and after every ingest; a pass that rewrote on
        every run would fill the history store with identical revisions for ever."""
        with TempWiki() as w:
            p = w.page("entities/x.md", title="Trump" + esc("2019") + "s Plan",
                       body="# Trump’s Plan\n\n## Overview\n\nA plan.\n")
            agent.heal_pages()
            after = p.read_text()
            r2 = agent.heal_pages()
            self.assertEqual(r2.get("escapes_decoded", 0), 0)
            self.assertEqual(p.read_text(), after)

    def test_a_page_holding_only_a_declined_escape_is_not_rewritten(self):
        """The count bug, where it would actually have hurt: a page with a lone surrogate
        cannot be repaired, so it must not be written at all."""
        with TempWiki() as w:
            p = w.page("entities/x.md", title="X",
                       body="# X\n\n## Overview\n\nhalf " + esc("d83d") + " emoji.\n")
            agent.heal_pages()
            before = p.read_text()
            r2 = agent.heal_pages()
            self.assertEqual(r2.get("escapes_decoded", 0), 0)
            self.assertEqual(p.read_text(), before)
            self.assertIn(esc("d83d"), before)

    def test_a_documented_escape_in_a_code_block_survives_healing(self):
        with TempWiki() as w:
            p = w.page("concepts/json-escapes.md", title="JSON Escapes", type="concept",
                       body="## Definition\n\nWrite `" + esc("2019") + "` for an "
                            "apostrophe.\n")
            agent.heal_pages()
            self.assertIn(esc("2019"), p.read_text())

    def test_a_clean_page_is_untouched(self):
        with TempWiki() as w:
            p = w.page("entities/ok.md", title="OK",
                       body="# OK\n\n## Overview\n\nFine.\n")
            before = p.read_text()
            r = agent.heal_pages()
            self.assertEqual(r.get("escapes_decoded", 0), 0)
            self.assertEqual(p.read_text(), before)


if __name__ == "__main__":
    unittest.main()
