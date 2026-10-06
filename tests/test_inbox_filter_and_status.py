"""The reading list's status: showing it, reaching it, and filtering by it.

Three things reported together, and the second answers a question I had not been asked:

  * **"The Wikified tag is smaller than a button and sometimes I can't hit it."** It was
    `font-size: 10px` in `padding: 1px 6px` — roughly 14px tall against the ~44px a finger
    needs — and it was a LINK, so it was also the only way to reach the page the article
    became. A control you cannot reliably hit is not a control. The badge is a marker now
    and the clickable job moved to a real `.act-btn` in the action row, at the same size as
    every other control on the card.

  * **"The colours of the existing buttons are confusing, do they mean something?"**
    Partly, and the confusing part was real rather than a matter of taste: `--accent` blue
    was Read AND Wikify — the free instant one and the one that spends ~40 minutes and a
    per-day quota, identical to look at — and `--success` green was Archive (an action)
    AND the Wikified badge (a state). One meaning per colour now, with amber for the
    expensive action and green reserved for state.

  * **"How about a filter to show just non-wikified or just wikified?"** Added, and the
    trap is that SEARCH already wrote `style.display` directly: clearing the search box set
    every row back to visible, including the ones the status filter had hidden. Two things
    writing one property disagree, so `applyFilters()` is the only writer and reads both
    predicates.

Found while doing it, and worse than any of the three: **nothing disabled the Wikify button
on an article already wikified.** `item.wikified` rendered the badge and nothing else, so
one click on a finished item started another ~40-minute ingest at `max_rpm: 1`, re-folding
the same source into the same pages. The badge was the only thing saying it was done, and
that badge was the element too small to see.
"""
import re
import shutil
import sys
import unittest
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "tools" / "templates" / "inbox.html"
BASE = Path(__file__).resolve().parent.parent / "tools" / "templates" / "base.html"


class BadgeIsAMarkerTest(unittest.TestCase):
    """It says what the state is. It is not how you act on it."""

    @classmethod
    def setUpClass(cls):
        cls.src = TEMPLATE.read_text(encoding="utf-8")

    def test_the_badge_is_never_a_link(self):
        """In either renderer — the Jinja loop or the poll's prepend path."""
        self.assertEqual(re.findall(r"<a[^>]*wikified-badge", self.src), [])

    def test_the_badge_is_not_built_as_an_anchor_in_js(self):
        self.assertNotRegex(self.src,
                            r"createElement\('a'\)[\s\S]{0,120}?wikified-badge")

    def test_both_renderers_still_show_the_badge(self):
        self.assertIn('<span class="wikified-badge">Wikified ✓</span>', self.src)
        self.assertIn('`<span class="wikified-badge">Wikified ✓</span>`', self.src)

    def test_the_badge_is_big_enough_to_read(self):
        m = re.search(r"\.wikified-badge \{[^}]*\}", self.src)
        self.assertIsNotNone(m)
        size = int(re.search(r"font-size: (\d+)px", m.group(0)).group(1))
        self.assertGreaterEqual(size, 11, "the badge shrank again")


class ViewPageButtonTest(unittest.TestCase):
    """The clickable half, at the size of every other control."""

    @classmethod
    def setUpClass(cls):
        cls.src = TEMPLATE.read_text(encoding="utf-8")

    def test_the_server_template_renders_it(self):
        self.assertIn('<a class="act-btn wikipage" href="/wiki/{{ item.wiki_path }}">', self.src)

    def test_the_poll_renders_it_too(self):
        """A newly arrived row that is already wikified must not render with a live Wikify
        button and no way to reach its page."""
        self.assertIn('`<a class="act-btn wikipage" href="/wiki/${item.wiki_path}">', self.src)

    def test_it_is_a_real_act_btn(self):
        """So it inherits the same padding and hit area as Read, Archive and Delete."""
        self.assertRegex(self.src, r"\.act-btn\.wikipage \{")

    def test_wiki_path_is_read_from_the_button_now(self):
        """It used to be dug out of the badge's href. The badge has no href any more, so
        the one element carrying the path is the button."""
        self.assertIn("querySelector('a.act-btn.wikipage')?.getAttribute('href')", self.src)
        self.assertNotIn("querySelector('a.wikified-badge')", self.src)


class WikifyIsDisabledWhenDoneTest(unittest.TestCase):
    """The expensive accident: a second ingest of an article already in the wiki."""

    @classmethod
    def setUpClass(cls):
        cls.src = TEMPLATE.read_text(encoding="utf-8")

    def test_the_server_template_disables_it(self):
        self.assertRegex(self.src,
                         r'class="act-btn wikify" \{% if item\.wikified %\}disabled')

    def test_the_poll_render_disables_it(self):
        self.assertRegex(self.src,
                         r"class=\"act-btn wikify\" \$\{item\.wikified \? 'disabled")

    def test_the_disabled_button_says_why(self):
        self.assertIn("Already wikified", self.src)

    def test_one_helper_marks_a_row_wikified(self):
        """Two paths reach this state — the poll noticing someone else's job, and
        wikifyItem finishing its own — and each had its own copy of the badge code. The
        half missing from BOTH was disabling the button."""
        self.assertEqual(self.src.count("function markRowWikified("), 1)
        self.assertEqual(self.src.count("markRowWikified("), 3)   # one def, two callers

    def test_the_helper_disables_the_button(self):
        m = re.search(r"function markRowWikified\([\s\S]*?\n\}", self.src)
        self.assertIn("w.disabled = true", m.group(0))

    def test_replacing_the_text_removes_the_page_button(self):
        """saveEdit un-wikifies the row — the wiki page was built from the OLD text, so it
        is no longer this item's page."""
        self.assertIn("card?.querySelector('.act-btn.wikipage')?.remove();", self.src)


class ColourSchemeTest(unittest.TestCase):
    """One meaning per colour. They used to carry two each and so carried none."""

    @classmethod
    def setUpClass(cls):
        cls.src = TEMPLATE.read_text(encoding="utf-8")
        cls.base = BASE.read_text(encoding="utf-8")

    def _rule(self, cls_name):
        # Anchored at line start: `.item-edit-actions .act-btn.wikify` contains the same
        # substring, and an unanchored search found THAT rule instead — which is how the
        # first version of this test failed against correct CSS.
        m = re.search(rf"^  \.act-btn\.{cls_name}\s*\{{[^}}]*\}}", self.src, re.MULTILINE)
        self.assertIsNotNone(m, cls_name)
        return m.group(0)

    def test_read_is_the_accent(self):
        """The main thing you came to do, and it costs nothing."""
        self.assertIn("var(--accent)", self._rule("read"))

    def test_wikify_is_no_longer_the_same_colour_as_read(self):
        """The whole complaint: the free action and the ~40-minute one looked identical."""
        self.assertNotIn("var(--accent)", self._rule("wikify"))

    def test_wikify_is_the_warning_colour(self):
        self.assertIn("var(--warn)", self._rule("wikify"))

    def test_archive_is_no_longer_green(self):
        """Green is a state now. Archive is a reversible action — there is an Unarchive."""
        self.assertNotIn("var(--success)", self._rule("archive"))

    def test_delete_is_the_only_danger(self):
        self.assertIn("var(--danger)", self._rule("delete"))
        for other in ("read", "open", "archive", "wikify"):
            self.assertNotIn("var(--danger)", self._rule(other), other)

    def test_green_means_done_and_nothing_else(self):
        """The badge and the button that leads to what it produced."""
        greens = re.findall(r"^\s*(\.[\w.-]+) \{[^}]*var\(--success\)[^}]*\}",
                            self.src, re.MULTILINE)
        self.assertEqual(set(greens), {".wikified-badge", ".act-btn.wikipage"}, greens)

    def test_the_warn_colour_exists_in_every_theme(self):
        """Light, the prefers-color-scheme block, and the explicit dark theme — a button
        that vanishes in dark mode is worse than a confusing one."""
        self.assertEqual(self.base.count("--warn:"), 3)


class FilterTest(unittest.TestCase):
    """Status filter, and the search interaction that would have broken it."""

    @classmethod
    def setUpClass(cls):
        cls.src = TEMPLATE.read_text(encoding="utf-8")

    def test_the_three_choices_exist(self):
        for f in ("all", "todo", "done"):
            self.assertIn(f"""onclick="setStatusFilter('{f}')\"""", self.src)

    def test_only_one_function_writes_visibility(self):
        """The trap: search used to set el.style.display directly, so clearing the box
        revealed rows the status filter had hidden. applyFilters is the only writer."""
        writers = re.findall(r"\.style\.display = ", self.src)
        body = re.search(r"function applyFilters\(\)[\s\S]*?\n\}", self.src).group(0)
        self.assertEqual(len(writers), body.count(".style.display = "),
                         "something outside applyFilters writes row visibility")
        self.assertGreaterEqual(len(writers), 2)

    def test_applyfilters_reads_both_predicates(self):
        body = re.search(r"function applyFilters\(\)[\s\S]*?\n\}", self.src).group(0)
        self.assertIn("_searchMatches", body)
        self.assertIn("_statusFilter", body)

    def test_clearing_the_search_box_does_not_reset_the_status_filter(self):
        body = re.search(r"function filterItems\(q\)[\s\S]*?\n\}", self.src).group(0)
        self.assertIn("_searchMatches = null", body)
        self.assertIn("applyFilters()", body)
        self.assertNotIn(".style.display = ''", body)

    def test_the_filter_uses_the_same_signal_as_the_badge(self):
        """So what the filter calls 'done' and what the row shows cannot disagree."""
        body = re.search(r"function applyFilters\(\)[\s\S]*?\n\}", self.src).group(0)
        self.assertIn("querySelector('.wikified-badge')", body)

    def test_new_rows_from_the_poll_obey_the_filter(self):
        """The poll PREPENDS arrivals; without this they appear regardless of the filter,
        and they also used to overwrite the element applyFilters writes the count into."""
        poll = re.search(r"async function pollInbox\(\)[\s\S]*?\n\}", self.src).group(0)
        self.assertIn("applyFilters()", poll)
        self.assertNotIn("' saved'", poll)

    def test_a_row_that_becomes_wikified_is_re_filtered(self):
        m = re.search(r"function markRowWikified\([\s\S]*?\n\}", self.src)
        self.assertIn("applyFilters", m.group(0))

    def test_an_over_filtered_list_says_so(self):
        """An empty list must not read as an empty reading list."""
        self.assertIn('id="filter-empty"', self.src)
        self.assertIn("Nothing matches this filter", self.src)

    def test_the_choice_is_remembered_but_wrapped(self):
        """localStorage can be absent or throw, and a remembered filter is a convenience."""
        self.assertRegex(self.src, r"try \{ localStorage\.setItem\('inbox-filter'")
        self.assertRegex(self.src, r"try \{ saved = localStorage\.getItem\('inbox-filter'\); \} catch")


def _lift(name):
    """The real function, out of the shipped template, so the browser half cannot drift
    from what actually runs."""
    src = TEMPLATE.read_text(encoding="utf-8")
    m = re.search(rf"^function {name}\(.*?^\}}", src, re.MULTILINE | re.DOTALL)
    assert m, f"could not lift {name}"
    return m.group(0)


class BrowserTest(unittest.TestCase):
    """Drive the real applyFilters/setStatusFilter against a real document.

    The composition of two filters is exactly the thing a structural assertion cannot
    prove, because it is about what happens in sequence.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("playwright not installed")
        cls._pw = sync_playwright().start()
        candidates = [None] + sorted(
            str(p) for pat in ("chromium*/chrome-linux/chrome",
                               "chromium_headless_shell*/chrome-linux/headless_shell")
            for p in Path("/opt/pw-browsers").glob(pat))
        last = None
        for exe in candidates:
            try:
                cls._browser = cls._pw.chromium.launch(
                    **({"executable_path": exe} if exe else {}))
                break
            except Exception as e:
                last = e
        else:
            cls._pw.stop()
            raise unittest.SkipTest(f"no chromium could be launched: {last}")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_browser", None):
            cls._browser.close()
        if getattr(cls, "_pw", None):
            cls._pw.stop()

    def _page(self):
        page = self._browser.new_page()
        rows = "".join(
            f'<div class="reading-item" data-name="{n}.md">'
            f'<div class="item-meta">{n}'
            + ('<span class="wikified-badge">Wikified ✓</span>' if done else '')
            + '</div></div>'
            for n, done in (("alpha", True), ("beta", False), ("gamma", True),
                            ("delta", False)))
        page.set_content(
            f'<div class="seg">'
            f'<button class="seg-btn active" data-filter="all"></button>'
            f'<button class="seg-btn" data-filter="todo"></button>'
            f'<button class="seg-btn" data-filter="done"></button></div>'
            f'<span id="item-count">4</span><div id="filter-empty"></div>{rows}')
        page.add_script_tag(content=
            "let _searchMatches = null; let _statusFilter = 'all';\n"
            + _lift("applyFilters") + "\n" + _lift("setStatusFilter"))
        return page

    def _visible(self, page):
        return page.evaluate(
            "[...document.querySelectorAll('.reading-item')]"
            ".filter(e => e.style.display !== 'none').map(e => e.dataset.name)")

    def test_all_shows_everything(self):
        page = self._page()
        page.evaluate("applyFilters()")
        self.assertEqual(len(self._visible(page)), 4)
        page.close()

    def test_done_shows_only_wikified(self):
        page = self._page()
        page.evaluate("setStatusFilter('done')")
        self.assertEqual(self._visible(page), ["alpha.md", "gamma.md"])
        page.close()

    def test_todo_shows_only_unwikified(self):
        page = self._page()
        page.evaluate("setStatusFilter('todo')")
        self.assertEqual(self._visible(page), ["beta.md", "delta.md"])
        page.close()

    def test_search_and_status_compose(self):
        page = self._page()
        page.evaluate("setStatusFilter('done')")
        page.evaluate("_searchMatches = new Set(['gamma.md', 'beta.md']); applyFilters()")
        self.assertEqual(self._visible(page), ["gamma.md"])
        page.close()

    def test_clearing_the_search_keeps_the_status_filter(self):
        """The regression this design exists to prevent: search used to set display
        directly, so an empty box revealed every row including the filtered-out ones."""
        page = self._page()
        page.evaluate("setStatusFilter('done')")
        page.evaluate("_searchMatches = new Set(['beta.md']); applyFilters()")
        self.assertEqual(self._visible(page), [])
        page.evaluate("_searchMatches = null; applyFilters()")
        self.assertEqual(self._visible(page), ["alpha.md", "gamma.md"],
                         "clearing the search box escaped the status filter")
        page.close()

    def test_the_count_reports_the_filtered_total(self):
        page = self._page()
        page.evaluate("setStatusFilter('done')")
        self.assertEqual(page.inner_text("#item-count"), "2 of 4")
        page.evaluate("setStatusFilter('all')")
        self.assertEqual(page.inner_text("#item-count"), "4")
        page.close()

    def test_the_empty_notice_appears_only_when_everything_is_hidden(self):
        page = self._page()
        page.evaluate("setStatusFilter('done')")
        self.assertEqual(page.eval_on_selector("#filter-empty", "e => e.style.display"), "none")
        page.evaluate("_searchMatches = new Set(); applyFilters()")
        self.assertEqual(page.eval_on_selector("#filter-empty", "e => e.style.display"), "")
        page.close()

    def test_the_active_segment_follows_the_choice(self):
        page = self._page()
        page.evaluate("setStatusFilter('todo')")
        self.assertEqual(
            page.evaluate("document.querySelector('.seg-btn.active').dataset.filter"),
            "todo")
        page.close()


if __name__ == "__main__":
    unittest.main()
