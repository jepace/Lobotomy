"""A section too long to re-emit is outlined, not quoted — at BOTH doors.

Reported as *"he read it and then tried to nuke it?!?"* over this log:

    ⚙ read_section wiki/entities/donald-trump.md § Key Policies & Actions (2026)
    ✗ update_section … — this would cut 'Key Policies & Actions (2026)' by 100%
      (93637 chars of prose now, 895 in what you sent; links are discounted)

The model was not misbehaving and the guard was right. `_read_section` returned the
section with **no size cap at all** — 93,637 chars, ~23,000 tokens — and `max_tokens` is
16,384, so the output budget is ~65,536 chars. The section was 1.4× everything the model
can emit in one response. It read all of it, could not reproduce it, and sent the only
thing it could: a summary. There was no legal move.

Two costs. The loud one is ~23,000 tokens re-sent on every remaining round of that
ingest, which at `max_rpm: 1` is the most expensive thing in the run. The quiet one is
that the payload cannot be acted on as a rewrite at all, so those bytes buy a refusal
instead of a merge — the `read_file` outline lesson, in the one reader that never learned
it.

**Two doors hand a section back and both were uncapped**, which is why
`SharedByBothDoorsTest` is the module's centre of gravity: capping `read_section` alone
would route the model straight into `update_section`'s unread-section refusal, which
dumped the same text, and nothing would have changed.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import harness
import agent


def _huge_paragraphs(n: int = 9) -> str:
    """A section in the shape the report described: long prose blocks."""
    return "\n\n".join(f"Paragraph {i} opens the discussion. " + "word " * 900
                       for i in range(n))


def _many_bullets(n: int = 600) -> str:
    """The other shape, and the one that caught a bug in the fix: a section grown one
    short bullet per ingest. Bounding each block's preview bounds nothing here."""
    return "\n".join(
        f"- On 2026-{m % 12 + 1:02d}-{m % 27 + 1:02d} the administration announced a "
        f"policy change affecting trade." for m in range(n))


def _page(section_body: str, section: str = "Key Policies") -> str:
    return ("---\ntitle: Donald Trump\ntype: entity\ncreated: 2026-01-01\n---\n\n"
            "# Donald Trump\n\n## Overview\nHe is a politician.\n\n"
            f"## {section}\n{section_body}\n")


class RuleTest(unittest.TestCase):
    """The threshold is an existing rule, not a new constant."""

    def test_it_is_read_files_own_whole_page_rewrite_rule(self):
        """`_read_file` already decides whether a page can be rewritten in one call with
        `max_tokens * 4 // 2`. A section asks the same question, so it must not grow a
        fourth threshold that can drift from the other three."""
        budget = (agent.cfg_int("llm", "max_tokens", default=16384) * 4) // 2
        self.assertTrue(agent._section_rewritable(budget))
        self.assertFalse(agent._section_rewritable(budget + 1))

    def test_the_reported_section_is_over_the_line(self):
        self.assertFalse(agent._section_rewritable(93_637))


class HandbackTest(unittest.TestCase):
    def test_a_normal_section_is_quoted_verbatim(self):
        """The not-crying-wolf half. Almost every section in the wiki is small, and
        outlining those would remove the merge that makes the wiki coherent."""
        out = agent._section_handback("wiki/entities/x.md", "Overview", "## Overview",
                                      "He is a politician. He was born in 1946.")
        self.assertEqual(out, "## Overview\nHe is a politician. He was born in 1946.")

    def test_an_oversized_section_is_not_quoted(self):
        text = _huge_paragraphs()
        out = agent._section_handback("wiki/entities/x.md", "Key Policies",
                                      "## Key Policies", text)
        self.assertNotIn(text, out)
        self.assertLess(len(out), agent._unlinked_len(text) // 4)

    def test_the_outline_is_bounded_for_a_section_of_many_short_blocks(self):
        """The hole in the first version of this fix. Capping each block's preview is not
        a cap: 600 short bullets outlined to a payload LARGER than the text it stood in
        for, which makes the expensive case more expensive."""
        text = _many_bullets()
        out = agent._section_handback("wiki/entities/x.md", "Key Policies",
                                      "## Key Policies", text)
        self.assertLess(len(out), agent._unlinked_len(text),
                        "the outline is bigger than the text it replaces")
        self.assertIn("more block(s), not listed", out)

    def test_it_says_what_the_section_already_covers(self):
        """An outline that listed only sizes would stop the model noticing that its new
        material is already there, which is the one thing a merge was buying."""
        text = ("Paragraph about tariffs and trade. " + "word " * 1800 + "\n\n"
                + "Paragraph about judicial appointments. " + "word " * 1800)
        out = agent._section_handback("wiki/entities/x.md", "K", "## K", text)
        self.assertIn("tariffs and trade", out)
        self.assertIn("judicial appointments", out)

    def test_it_names_append_section_and_never_a_rewrite(self):
        """Principle 4: the reply has to name a call that works. update_section cannot
        succeed on this section at any length, so it must not be the move offered."""
        out = agent._section_handback("wiki/entities/donald-trump.md",
                                      "Key Policies & Actions (2026)",
                                      "## Key Policies & Actions (2026)",
                                      _huge_paragraphs())
        self.assertIn("append_section", out)
        self.assertIn("wiki/entities/donald-trump.md", out)
        self.assertIn("Key Policies & Actions (2026)", out)
        self.assertIn("will be refused", out)

    def test_it_offers_a_page_of_its_own(self):
        """The real repair for a section this size, and the one LOBOTOMY.md's own
        reasoning points at: a page is the only thing the autolinker can link."""
        out = agent._section_handback("wiki/entities/x.md", "K", "## K",
                                      _huge_paragraphs())
        self.assertIn("its own page", out)


class SharedByBothDoorsTest(unittest.TestCase):
    """The assertion that makes the fix a fix.

    `read_section` and `update_section`'s unread-section refusal both hand a section
    back. Capping one leaves the other dumping 93,637 chars, so the model reaches the
    uncapped door one round later and the whole change buys nothing.
    """

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self.text = _huge_paragraphs()
        p = self.w.wiki / "entities" / "donald-trump.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_page(self.text), encoding="utf-8")
        agent.init_session()

    def _assert_bounded(self, out, what):
        """Assert on the PROPERTY, never on the absence of an exact fixture string.

        The first version of these two asserted `assertNotIn(self.text, out)` — and
        `old_text` is `body[start:end].strip()` while the fixture's paragraphs end in a
        trailing space from `"word " * 900`, so that assertion was **trivially true
        whatever the code did**. `mutate.py` reported the second door MISSED and was
        right: reverting it to a raw dump still produced a 41,502-char reply and every
        test here passed. The project's oldest trap, in a new disguise — an assertion
        pinned to a fixture string stops reaching the code and says nothing.

        What actually matters is that the reply is bounded, so that is what is measured.
        """
        self.assertLess(len(out), agent._unlinked_len(self.text) // 4,
                        f"{what} is dumping the section: {len(out):,} chars for a "
                        f"{agent._unlinked_len(self.text):,}-char section")
        self.assertIn("append_section", out)

    def test_read_section_caps_it(self):
        out = agent._read_section({"path": "wiki/entities/donald-trump.md",
                                   "section": "Key Policies"})
        self._assert_bounded(out, "read_section")

    def test_the_unread_section_refusal_caps_it_too(self):
        """Reached without reading first, which is the common path — the model guesses a
        section name from the template and writes blind."""
        out = agent._update_section({"path": "wiki/entities/donald-trump.md",
                                     "section": "Key Policies",
                                     "content": "A short new paragraph about trade."})
        self.assertIn("refused", out)
        self._assert_bounded(out, "the unread-section refusal")

    def test_neither_door_tells_it_to_merge_into_text_it_cannot_see(self):
        """Principle 4's worst case: a refusal naming a move that cannot be made. The
        refusal's own prose has to follow the payload, not just the payload."""
        out = agent._update_section({"path": "wiki/entities/donald-trump.md",
                                     "section": "Key Policies",
                                     "content": "A short new paragraph about trade."})
        self.assertNotIn("MERGE, do not", out)

    def test_the_capped_refusal_rules_out_re_reading(self):
        """`read_section` now returns this same outline, so a model that goes and fetches
        it spends a round and a pacing window on what it is already holding — the exact
        waste the handback refusals were fixed for once already."""
        out = agent._update_section({"path": "wiki/entities/donald-trump.md",
                                     "section": "Key Policies",
                                     "content": "A short new paragraph about trade."})
        self.assertIn("do NOT call read_section", out)

    def test_a_small_section_still_gets_the_full_merge_treatment(self):
        """Both doors, on the ordinary case: the behaviour that makes the wiki coherent
        must be untouched, or this change is a regression dressed as an optimisation."""
        p = self.w.wiki / "entities" / "small.md"
        # A section name of its own: `_page` already writes an ## Overview, and reusing
        # that name makes a duplicate heading whose first copy `_find_section` returns —
        # so the assertion would be measuring the fixture rather than the code.
        p.write_text(_page("He was born in 1946. He ran for office.", "Early Life"),
                     encoding="utf-8")
        agent.init_session()
        read = agent._read_section({"path": "wiki/entities/small.md",
                                    "section": "Early Life"})
        self.assertIn("He was born in 1946.", read)
        agent.init_session()
        refusal = agent._update_section({"path": "wiki/entities/small.md",
                                         "section": "Early Life",
                                         "content": "Rewritten."})
        self.assertIn("He was born in 1946.", refusal)
        # A page this small takes the refusal's WHOLE-PAGE branch, which says REVISE.
        # The per-section branch — the one this change edits — is the next test.
        self.assertIn("REVISE", refusal)

    def test_a_small_section_on_a_LARGE_page_is_still_quoted_for_merging(self):
        """The branch this change actually edits, and the one a short fixture misses.

        `update_section`'s refusal only reaches its per-section branch above
        `_WIKI_READ_LIMIT`; below it the whole page is handed back instead. CLAUDE.md
        already records that a short fixture cost the long branch its coverage once
        before, and `mutate.py` reported it MISSED. So: a big page whose target section
        is small must still be quoted in full and still say MERGE.
        """
        p = self.w.wiki / "entities" / "big.md"
        p.write_text(
            "---\ntitle: Big\ntype: entity\ncreated: 2026-01-01\n---\n\n# Big\n\n"
            "## Key Policies\n" + _huge_paragraphs() + "\n\n"
            "## Early Life\nHe was born in 1946. He ran for office.\n",
            encoding="utf-8")
        agent.init_session()
        refusal = agent._update_section({"path": "wiki/entities/big.md",
                                         "section": "Early Life",
                                         "content": "Rewritten."})
        self.assertIn("He was born in 1946.", refusal)
        self.assertIn("MERGE, do not", refusal)


class RatchetReportTest(unittest.TestCase):
    """page_report's RATCHET finding: a section no tool can consolidate."""

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        import page_report
        self.pr = page_report

    def _analyze(self, body, section="Key Policies"):
        p = self.w.wiki / "entities" / "p.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_page(body, section), encoding="utf-8")
        return self.pr.analyze(p)

    def test_it_reports_an_unrewritable_section(self):
        r = self._analyze(_huge_paragraphs())
        self.assertEqual([x["name"] for x in r["ratchet"]], ["Key Policies"])

    def test_it_does_not_fire_on_a_healthy_page(self):
        """Every report in this tool lives or dies on not crying wolf: a long section
        that is still rewritable is a section that is working."""
        r = self._analyze("He is a politician. " + "word " * 1200)
        self.assertEqual(r["ratchet"], [])

    def test_it_uses_the_same_rule_as_the_handback(self):
        """One rule imported, not a second copy — or the report and the tool disagree
        about which sections are stuck."""
        self.assertIs(self.pr._section_rewritable, agent._section_rewritable)

    def test_the_generated_sources_section_is_never_reported(self):
        """## Sources is rendered from frontmatter on every write, so its size is not a
        defect and no human should be asked to split it."""
        r = self._analyze(_huge_paragraphs(), section="Sources")
        self.assertEqual(r["ratchet"], [])


if __name__ == "__main__":
    unittest.main()
