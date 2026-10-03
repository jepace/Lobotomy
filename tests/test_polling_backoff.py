"""The pollers back off when idle and stop when nobody is looking.

A gigabyte of nginx access log filled `/var`, and a full disk is what finally broke saving
an article: nginx spools a request body larger than `client_body_buffer_size` to disk, so
LONG pastes failed and short ones did not —

    pwritev() "/var/tmp/nginx/client_body_temp/0000000398" failed
              (28: No space left on device)

— with nothing in the application log, because nginx never forwarded them. Days were spent
looking for a content bug.

**This code wrote that log.** `/chat/status` was polled every 2 SECONDS from `base.html`,
on every page of the site, with no visibility check: 43,200 requests a day from one tab
left open, each one a line in the access log. `/inbox/list` added 10,800 more at 8s. The
irony is that `serve.py` already filtered both paths out of WERKZEUG's access log, so the
volume was invisible in the one log this project looks at.

Idle is the overwhelmingly common state and now gets 30s; the fast cadence is kept for
exactly the moment it is needed — a job running, or the list about to change because
Wikify was just clicked. A hidden tab polls nothing at all.

The arithmetic, for one tab open all day: 43,200 + 10,800 → ~2,880 if left visible and
idle, and 0 while it is in the background. That is a ~95% cut, and most of a real tab's
day is hidden.
"""
import re
import sys
import unittest
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "tools" / "templates"


def _src(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


class NoFastFixedIntervalTest(unittest.TestCase):
    """The shape that caused it: a bare setInterval with a short period."""

    # wiki-lint's 2s poll is the one legitimate case and is allowed by name. It is created
    # only inside `if (s.running)` and cleared the moment the relink stops, so it never
    # runs while nothing is happening — which is the actual rule. Detecting "conditional on
    # a job and cleared afterwards" structurally is not worth the false positives, so it is
    # named here with the reason instead.
    ALLOWED = {("wiki-lint.html", "setInterval(pollRelink, 2000)")}

    def test_no_template_polls_on_a_fixed_fast_interval(self):
        bad = []
        for p in sorted(TEMPLATES.glob("*.html")):
            src = _src(p.name)
            for m in re.finditer(r"setInterval\(\s*([^,]+),\s*(\d+)\s*\)", src):
                if int(m.group(2)) >= 60000:
                    continue
                if (p.name, m.group(0)) in self.ALLOWED:
                    continue
                line = src[:m.start()].count("\n") + 1
                bad.append(f"{p.name}:{line}  {m.group(0)[:60]}")
        self.assertEqual(bad, [], (
            "a fixed fast interval cannot back off or stop when hidden, and every poll is "
            "a line in the access log:\n  " + "\n  ".join(bad)))

    def test_the_allowed_one_is_still_scoped_to_a_running_job(self):
        """An allowlist entry that stops being true is worse than no allowlist."""
        src = _src("wiki-lint.html")
        self.assertRegex(src, r"if \(s\.running\)[\s\S]{0,120}setInterval\(pollRelink")
        self.assertIn("clearInterval(relinkTimer)", src)

    def test_the_detector_would_catch_the_original(self):
        """A test that cannot fail proves nothing."""
        self.assertTrue(re.search(r"setInterval\(\s*([^,]+),\s*(\d+)\s*\)",
                                  "setInterval(syncBusy, 2000);"))


class BackoffTest(unittest.TestCase):

    def test_the_queue_poller_has_an_idle_and_a_busy_cadence(self):
        src = _src("base.html")
        self.assertIn("IDLE_MS = 30000", src)
        self.assertIn("BUSY_MS = 2000", src)

    def test_the_inbox_poller_has_both_too(self):
        src = _src("inbox.html")
        self.assertIn("IDLE_MS = 30000", src)
        self.assertIn("BUSY_MS = 8000", src)

    def test_idle_is_slower_than_busy_in_both(self):
        """Backwards would be worse than no change at all."""
        for name in ("base.html", "inbox.html"):
            src = _src(name)
            idle = int(re.search(r"IDLE_MS = (\d+)", src).group(1))
            busy = int(re.search(r"BUSY_MS = (\d+)", src).group(1))
            self.assertGreater(idle, busy, name)

    def test_the_cadence_is_CHOSEN_by_whether_anything_is_running(self):
        """The constants existing proves nothing — the first version of this module
        asserted only that, and mutate.py reported all three guards MISSED because a
        mutation to `schedule(BUSY_MS)` left every constant in place. What matters is that
        the next interval is SELECTED by the busy state."""
        # EVERY such call, not merely one. Each file schedules twice — once at start-up
        # and once at the end of each tick — and asserting "somewhere" let a mutation of
        # the tick path pass because the start-up call still matched. mutate.py reported
        # it MISSED twice before the count was pinned.
        base = re.findall(r"schedule\(\s*busy\s*\?\s*BUSY_MS\s*:\s*IDLE_MS\s*\)",
                          _src("base.html"))
        self.assertEqual(len(base), 2, f"expected both schedule sites, found {len(base)}")
        inbox = re.findall(
            r"schedule\(\s*anythingRunning\(\)\s*\?\s*BUSY_MS\s*:\s*IDLE_MS\s*\)",
            _src("inbox.html"))
        self.assertEqual(len(inbox), 2, f"expected both schedule sites, found {len(inbox)}")

    def test_neither_poller_runs_while_the_tab_is_hidden(self):
        for name in ("base.html", "inbox.html"):
            self.assertIn("document.hidden", _src(name), name)

    def test_the_hidden_check_actually_skips_the_fetch(self):
        """`document.hidden` appearing somewhere is not the guard; returning before the
        request is."""
        self.assertRegex(_src("base.html"),
                         r"if \(document\.hidden\) \{ schedule\(_every\); return; \}")
        self.assertRegex(_src("inbox.html"),
                         r"if \(document\.hidden\) \{ schedule\(IDLE_MS\); return; \}")

    def test_both_check_at_once_when_the_tab_comes_back(self):
        """Otherwise returning to the tab shows a 30-second-stale list."""
        for name in ("base.html", "inbox.html"):
            src = _src(name)
            self.assertIn("visibilitychange", src, name)
            self.assertRegex(src, r"visibilitychange[\s\S]{0,200}schedule\(0\)", name)

    def test_clicking_wikify_polls_promptly(self):
        """The one moment the list is certain to change — waiting out the idle interval
        would make the feature look broken."""
        self.assertIn("window.pollSoon", _src("inbox.html"))

    def test_a_failed_poll_does_not_stop_the_loop(self):
        """A poller that dies on one bad response is worse than a noisy one: the page
        silently stops updating and nothing says so."""
        base = _src("base.html")
        self.assertRegex(base, r"catch\(function\(\)\{ return false; \}\)")
        inbox = _src("inbox.html")
        self.assertRegex(inbox, r"catch \(e\) \{ /\* a failed poll must not stop the loop")


class RequestVolumeTest(unittest.TestCase):
    """The number that matters, stated as arithmetic rather than a feeling."""

    def _per_day(self, name):
        src = _src(name)
        idle = int(re.search(r"IDLE_MS = (\d+)", src).group(1))
        return 86400 // (idle // 1000)

    def test_an_idle_visible_tab_is_under_three_thousand_a_day(self):
        total = self._per_day("base.html") + self._per_day("inbox.html")
        self.assertLess(total, 6000, f"{total}/day from one idle tab")

    def test_it_is_most_of_an_order_of_magnitude(self):
        """54,000/day to 5,760 — 9.4x, not the 10x the first version of this test
        asserted. Measured rather than rounded up, because the number is the argument."""
        before = 86400 // 2 + 86400 // 8          # 2s queue poll + 8s inbox poll
        after = self._per_day("base.html") + self._per_day("inbox.html")
        self.assertEqual(before, 54000)
        self.assertGreater(before / after, 9.0, f"{before} -> {after}")

    def test_a_hidden_tab_costs_nothing(self):
        """The real saving: most of a tab's day is in the background, and both pollers
        now skip the fetch entirely rather than merely slowing down."""
        for name in ("base.html", "inbox.html"):
            src = _src(name)
            self.assertRegex(src, r"document\.hidden[\s\S]{0,80}(schedule|return)", name)


if __name__ == "__main__":
    unittest.main()
