"""The post-ingest chain walked the whole wiki after every single article.

Reported as *"navigating while it is ingesting an article has gotten painful"*, then
*"clicking Read page after successful ingestion is taking like 18 seconds"* and
*"hitting the back button took 46 seconds to return to the reading list"*.

Measured at 13,000 pages, run after EVERY wikify, with the server blocked throughout:

    _validate_ingest   7.3s      whole wiki
    heal_pages         2.6s      whole wiki
    _rebuild_index     0.87s     whole wiki   (has to be: it builds the index)
    _fix_wiki_links    0.2s      whole wiki

An ingest touches ~20 pages. Three of those four read all 13,000 regardless, so almost
all of it was re-reading pages the ingest never looked at. Scoped: heal 2240ms → 4ms,
validate 483ms → 1ms.

**The sweep is not gone, it moved.** `heal_pages()` with no `only` still walks everything
and still runs at startup, so a defect from anywhere is still repaired — just not
re-repaired thirty times a day. `/wiki/lint` remains the whole-wiki link check. That
split is what makes the scoping safe, and it is what these tests pin.

`_validate_ingest`'s own docstring already said it checked "all new/touched pages" while
the code read `wiki_pages()` — the code and its description had drifted, and the
expensive one won. Its comment read *"full wiki scan is safe — files are small"*, which
was true when it was written.

**Honest limit, stated because three attempts failed:** none of this reproduces the
reported 18s or 46s on the development machine, where the same operations run in tens of
milliseconds. The gap is almost certainly cold-cache I/O on the live jail rather than
CPU. These measurements are of work genuinely removed; they are not a claim to have
explained those numbers.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import harness
import agent
import serve


def _page(d: Path, slug: str, *, title=None, fm_extra="", body="Text."):
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{slug}.md"
    p.write_text(
        f'---\ntitle: "{title or slug.title()}"\ntype: entity\ntags: []\n'
        f'created: 2026-01-01\nupdated: 2026-01-01\nsources: []\n{fm_extra}---\n\n'
        f'# {title or slug.title()}\n\n## Overview\n\n{body}\n', encoding="utf-8")
    return p


class HealScopeTest(harness.TempWikiTestCase if hasattr(harness, "TempWikiTestCase")
                    else unittest.TestCase):
    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self.ents = self.w.wiki / "entities"

    def _broken(self, slug):
        """A page heal_pages is known to repair: type: disagreeing with the directory."""
        p = _page(self.ents, slug)
        p.write_text(p.read_text(encoding="utf-8").replace("type: entity", "type: concept"),
                     encoding="utf-8")
        return p

    def test_scoped_heal_repairs_the_page_it_is_given(self):
        p = self._broken("alpha")
        agent.heal_pages(only=[p])
        self.assertIn("type: entity", p.read_text(encoding="utf-8"))

    def test_scoped_heal_leaves_the_rest_of_the_wiki_alone(self):
        """The whole point: an ingest that touched one page must not read 13,000."""
        touched, untouched = self._broken("alpha"), self._broken("beta")
        agent.heal_pages(only=[touched])
        self.assertIn("type: entity", touched.read_text(encoding="utf-8"))
        self.assertIn("type: concept", untouched.read_text(encoding="utf-8"),
                      "a scoped heal repaired a page outside its scope")

    def test_the_whole_wiki_sweep_still_exists(self):
        """What makes the scoping safe. Startup still calls this with no `only`, so
        damage from anywhere is still repaired — just not after every article."""
        a, b = self._broken("alpha"), self._broken("beta")
        agent.heal_pages()
        for p in (a, b):
            self.assertIn("type: entity", p.read_text(encoding="utf-8"))

    def test_an_empty_scope_is_not_the_same_as_scoping_to_nothing(self):
        """`only=None` means sweep. The call site passes `_touched or None`, so a
        session that recorded nothing gets the sweep rather than silently checking
        nothing — wrong in the cheap direction."""
        p = self._broken("alpha")
        agent.heal_pages(only=None)
        self.assertIn("type: entity", p.read_text(encoding="utf-8"))


class ValidateScopeTest(unittest.TestCase):
    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self.ents = self.w.wiki / "entities"
        agent.init_session()

    def _missing_fields(self, slug):
        p = self.ents / f"{slug}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f'---\ntitle: "{slug}"\n---\n\n# {slug}\n\nText.\n', encoding="utf-8")
        return p

    def test_scoped_validation_reports_the_page_it_was_given(self):
        p = self._missing_fields("alpha")
        self.assertIn("alpha", agent._validate_ingest({"paths": [p]}))

    def test_scoped_validation_is_silent_about_pages_outside_the_scope(self):
        good = _page(self.ents, "alpha")
        self._missing_fields("beta")
        self.assertNotIn("beta", agent._validate_ingest({"paths": [good]}),
                         "a scoped validation reported a page the ingest never touched")

    def test_the_whole_wiki_scan_is_still_reachable(self):
        """`/wiki/lint` and anything else that wants the sweep asks for it by name."""
        self._missing_fields("beta")
        self.assertIn("beta", agent._validate_ingest({"full": True}))


class SessionTouchedPagesTest(unittest.TestCase):
    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        agent.init_session()

    def test_it_reports_what_the_session_wrote(self):
        _page(self.w.wiki / "entities", "alpha")
        ctx = agent._ctx()
        ctx._session_entity_pages.append("entities/alpha.md")
        self.assertEqual([p.name for p in agent.session_touched_pages()], ["alpha.md"])

    def test_the_source_page_counts_as_touched(self):
        """It is written by the ingest and carries the provenance every other page's
        frontmatter was keyed off, so leaving it out would skip the one page most likely
        to be wrong."""
        _page(self.w.wiki / "sources", "src-2026-x")
        agent._ctx()._current_source_page = "sources/src-2026-x.md"
        self.assertEqual([p.name for p in agent.session_touched_pages()], ["src-2026-x.md"])

    def test_a_page_written_and_then_deleted_is_not_reported(self):
        agent._ctx()._session_entity_pages.append("entities/gone.md")
        self.assertEqual(agent.session_touched_pages(), [])

    def test_no_session_reports_nothing_rather_than_raising(self):
        """The caller turns an empty list into a full sweep; raising here would take the
        whole post-ingest chain down with it."""
        self.assertEqual(agent.session_touched_pages(), [])

    def test_serve_uses_the_same_function(self):
        """serve.py imports NAMES from agent, so `agent.foo` there is a NameError —
        CLAUDE.md's own warning. Assert the import, not a lookalike."""
        self.assertIs(serve.agent_session_touched_pages, agent.session_touched_pages)


class SectionTabsTest(unittest.TestCase):
    """`wiki_sections()` runs on EVERY page view and built a list of every .md in all
    four directories to answer "is this one non-empty" — 19ms at 13,000 pages, about 40%
    of the cost of rendering a page. `next` stops at the first entry."""

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self._saved = serve.WIKI_DIR
        serve.WIKI_DIR = self.w.wiki
        self.addCleanup(lambda: setattr(serve, "WIKI_DIR", self._saved))

    def _labels(self):
        return [s["label"] for s in serve.wiki_sections()]

    def test_a_directory_with_pages_gets_its_tab(self):
        _page(self.w.wiki / "entities", "alpha")
        self.assertIn("Entities", self._labels())

    def test_a_directory_holding_only_the_stub_index_does_not(self):
        """The behaviour the discarded list was computing, kept exactly."""
        d = self.w.wiki / "concepts"
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.md").write_text("# Concepts\n", encoding="utf-8")
        self.assertNotIn("Concepts", self._labels())

    def test_an_empty_directory_does_not(self):
        (self.w.wiki / "synthesis").mkdir(parents=True, exist_ok=True)
        self.assertNotIn("Synthesis", self._labels())

    def test_it_stops_at_the_first_page_rather_than_listing_them_all(self):
        """Counted, not grepped. The first version of this asserted the old list
        comprehension's text was absent — so a mutation that rewrote it as
        `if [f for f in dpath.glob(...)]:` walked straight through, same waste, different
        spelling. What matters is how many entries are CONSUMED, so count them."""
        d = self.w.wiki / "entities"
        d.mkdir(parents=True, exist_ok=True)
        for i in range(50):
            _page(d, f"p{i:03d}")

        consumed = {"n": 0}
        real_glob = Path.glob

        def counting_glob(self_path, pattern):
            for entry in real_glob(self_path, pattern):
                consumed["n"] += 1
                yield entry

        Path.glob = counting_glob
        try:
            serve.wiki_sections()
        finally:
            Path.glob = real_glob
        self.assertLessEqual(consumed["n"], 5,
                             f"listed {consumed['n']} entries to answer 'is it non-empty'")


if __name__ == "__main__":
    unittest.main()


class PostIngestChainIsScopedTest(unittest.TestCase):
    """The CALL SITE, not just the capability.

    `mutate.py` reported MISSED when `_mark_inbox_wikified` was reverted to a whole-wiki
    `heal_pages()`: every test still passed, because the result is identical and only the
    cost differs. A scoping fix whose call site is untested is a fix that silently
    un-applies itself, so this drives the real post-ingest chain and asserts a page the
    ingest never touched is left alone.
    """

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        # REPO_ROOT too: _link_raw_source_to_wiki does relative_to(REPO_ROOT), so
        # leaving it pointing at the real checkout makes the whole chain raise into the
        # try/except that swallows it — which looked exactly like the heal not running.
        self._saved = (serve.RAW_DIR, serve.WIKI_DIR, serve.REPO_ROOT,
                       agent.RAW_DIR, agent.WIKI_DIR, agent.REPO_ROOT)
        serve.RAW_DIR, serve.WIKI_DIR, serve.REPO_ROOT = self.w.raw, self.w.wiki, self.w.root
        agent.RAW_DIR, agent.WIKI_DIR, agent.REPO_ROOT = self.w.raw, self.w.wiki, self.w.root
        self.addCleanup(lambda: (setattr(serve, "RAW_DIR", self._saved[0]),
                                 setattr(serve, "WIKI_DIR", self._saved[1]),
                                 setattr(serve, "REPO_ROOT", self._saved[2]),
                                 setattr(agent, "RAW_DIR", self._saved[3]),
                                 setattr(agent, "WIKI_DIR", self._saved[4]),
                                 setattr(agent, "REPO_ROOT", self._saved[5])))
        agent.init_session()

    def _broken(self, slug):
        p = _page(self.w.wiki / "entities", slug)
        p.write_text(p.read_text(encoding="utf-8").replace("type: entity", "type: concept"),
                     encoding="utf-8")
        return p

    def test_an_ingest_does_not_heal_pages_it_never_touched(self):
        touched, untouched = self._broken("alpha"), self._broken("beta")
        agent._ctx()._session_entity_pages.append("entities/alpha.md")

        raw = self.w.raw / "story.md"
        raw.write_text('---\ntitle: "Story"\nurl: https://example.com/s\n'
                       'saved: 2026-01-01\nwikified: false\n---\n\nBody.\n', encoding="utf-8")
        serve._mark_inbox_wikified("story.md")

        self.assertIn("type: entity", touched.read_text(encoding="utf-8"),
                      "the ingest did not heal the page it wrote")
        self.assertIn("type: concept", untouched.read_text(encoding="utf-8"),
                      "the post-ingest chain healed the whole wiki again")


class GilSwitchIntervalTest(unittest.TestCase):
    """A page view was not slowed by an ingest, it was HELD by one.

    From the live log, once slow GETs were logged at all:

        15:49:52  Tool call: update_section … barack-obama.md § Overview
        15:50:08  (the page request arrives)
        15:50:31  autolink: linked 62 title(s) (39.1s, 16702 titles checked)
        15:50:32  GET /wiki/sources/… -> 200 in 23491ms  << SLOW

    Arrived 16s into a 39s autolink, finished one second after it ended.

    It is not one long hold: a monitor thread through a 53-second autolink at 16,700
    titles saw a max stall of 19ms and none over 100ms. It is the MEDIAN — a thread
    asking for 1ms waited 6.35ms to get the interpreter back, every time, and a request
    needing hundreds of those pays seconds. Measured at 133x, and trimming the request's
    own syscalls moved it only 3213ms -> 2786ms, which is what says the multiplier is
    structural rather than anything in the handler.

    `sys.setswitchinterval` IS that round trip: 5ms -> 2496ms, 1ms -> 558ms,
    0.2ms -> 157ms, with the autolink itself no slower (56.5 / 36.7 / 38.5s).
    """

    def setUp(self):
        self._iv = sys.getswitchinterval()
        self.addCleanup(sys.setswitchinterval, self._iv)
        self._cfg = serve.cfg_get
        self.addCleanup(lambda: setattr(serve, "cfg_get", self._cfg))

    def _with(self, value):
        serve.cfg_get = lambda s, k, d=None: (
            value if (s, k) == ("server", "gil_switch_interval") else self._cfg(s, k, d))

    def test_it_shortens_the_interval(self):
        sys.setswitchinterval(0.005)
        self._with(0.0002)
        serve._tune_gil()
        self.assertAlmostEqual(sys.getswitchinterval(), 0.0002, places=6)

    def test_the_default_is_shorter_than_the_interpreter_default(self):
        """The whole point. 5ms is the stock value and the one that measured 2496ms."""
        sys.setswitchinterval(0.005)
        serve._tune_gil()
        self.assertLess(sys.getswitchinterval(), 0.005)

    def test_a_non_numeric_setting_leaves_the_interval_alone(self):
        """Config is hand-edited. Crashing the server at startup over a typo in a
        performance knob would be worse than the slowness it tunes."""
        sys.setswitchinterval(0.005)
        self._with("nonsense")
        serve._tune_gil()
        self.assertEqual(sys.getswitchinterval(), 0.005)

    def test_an_out_of_range_setting_leaves_the_interval_alone(self):
        """0 spins the CPU and a large value recreates exactly the stall this removes,
        so a nonsensical number is refused rather than applied."""
        for bad in (0.0, 99.0, -1.0):
            sys.setswitchinterval(0.005)
            self._with(bad)
            serve._tune_gil()
            self.assertEqual(sys.getswitchinterval(), 0.005, bad)

    def test_startup_actually_calls_it(self):
        """A tuning function nothing calls is a comment. The call site is in main()."""
        src = Path(serve.__file__).read_text(encoding="utf-8")
        self.assertIn("    _tune_gil()", src)
