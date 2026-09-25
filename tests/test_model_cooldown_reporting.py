"""The log says which quota a model is cooling on, and for how long.

"served by fallback model X (primary Y unavailable)" is the same sentence for two
situations that call for opposite responses:

  * a per-MINUTE limit is exactly what running slowly protects you from
  * a per-DAY quota is a fixed count of requests, and throttling cannot preserve it —
    an hour at max_rpm 1 spends precisely as much of a daily budget as an hour flat out

An observed ingest ran every one of its rounds on the fallback model with the primary
"unavailable", and nothing in the log said whether the deliberate 1-request-per-minute
pacing was buying anything at all. `_model_cooldowns` has recorded both facts since
fallback was added; the warning simply never read them.

This is reporting, not policy. Nothing here changes when a model is skipped.
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

PRIMARY = "gemini-3.5-flash-lite"


class CooldownReportingTest(unittest.TestCase):

    def setUp(self):
        agent._model_cooldowns.clear()
        self.addCleanup(agent._model_cooldowns.clear)

    def test_a_daily_quota_is_reported_as_daily(self):
        agent._model_cooldowns[PRIMARY] = (time.monotonic() + 1800, True)
        left, daily = agent._model_cooldown_left(PRIMARY)
        self.assertTrue(daily)
        self.assertGreater(left, 1700)

    def test_a_per_minute_limit_is_reported_as_not_daily(self):
        agent._model_cooldowns[PRIMARY] = (time.monotonic() + 47, False)
        left, daily = agent._model_cooldown_left(PRIMARY)
        self.assertFalse(daily)
        self.assertLessEqual(left, 47)

    def test_a_model_that_is_not_cooling_reports_nothing(self):
        self.assertEqual(agent._model_cooldown_left(PRIMARY), (0, False))

    def test_an_expired_cooldown_reports_zero_rather_than_negative(self):
        # It is read at log time, which can be after the expiry the loop checked.
        agent._model_cooldowns[PRIMARY] = (time.monotonic() - 30, True)
        left, _ = agent._model_cooldown_left(PRIMARY)
        self.assertEqual(left, 0, "a lapsed cooldown reported a negative countdown")

    def test_reading_does_not_clear_the_cooldown(self):
        # _model_cooling() deletes a lapsed entry; this one must not, or logging would
        # change which models the next round is willing to try.
        agent._model_cooldowns[PRIMARY] = (time.monotonic() + 900, True)
        agent._model_cooldown_left(PRIMARY)
        self.assertIn(PRIMARY, agent._model_cooldowns)

    def test_both_facts_reach_the_warning(self):
        src = (Path(__file__).resolve().parent.parent / "tools" / "agent.py"
               ).read_text(encoding="utf-8")
        i = src.index("served by fallback model")
        line = src[i - 400:i + 400]
        self.assertIn("_model_cooldown_left", line,
                      "the warning stopped reading the cooldown it is describing")
        self.assertIn("per-DAY", line)


if __name__ == "__main__":
    unittest.main()
