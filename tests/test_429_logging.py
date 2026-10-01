"""A 429 logs one short line, not the provider's body.

Observed in the production log, several times a minute while a free-tier daily quota was
spent: a 1,200-character dump of nested JSON per occurrence —

    LLM 429 detail (payload was 154KB): [{
      "error": { "code": 429, "message": "You exceeded your current quota, please check
      ... 40 more lines of links, help URLs and quotaDimensions ...

None of which a reader acts on. The two lines around it already say everything:
`LLM HTTP 429: Too Many Requests`, then `model X rate limited — skipping it for 1800s
(daily quota)`, then `served by fallback model Y — primary X rate limited on a per-DAY
quota, retried in 1797s`. The body's ONE unique contribution is the quotaId, which is what
distinguishes a per-minute request limit (throttle with max_rpm) from a token-per-minute
limit (send smaller requests) from a per-day limit (nothing helps until it resets). So the
quotaId is extracted and the rest is dropped.

The second half of this module is the behaviour that must NOT change: the long-backoff
daily-quota path used to re-scan `raw_body` with its own regex, and now shares `_quota_id`.
"""
import io
import json
import logging
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

# A real Gemini free-tier daily-quota body, trimmed of nothing that matters.
_BODY = json.dumps([{
    "error": {
        "code": 429,
        "message": ("You exceeded your current quota, please check your plan and billing "
                    "details. For more information on this error, head to: "
                    "https://ai.google.dev/gemini-api/docs/rate-limits.\n"
                    "* Quota exceeded for metric: generativelanguage.googleapis.com/"
                    "generate_content_free_tier_requests, limit: 500, model: "
                    "gemini-3.5-flash-lite\nPlease retry in 48.496646864s."),
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {"@type": "type.googleapis.com/google.rpc.Help",
             "links": [{"description": "Learn more about Gemini API quotas",
                        "url": "https://ai.google.dev/gemini-api/docs/rate-limits"}]},
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{
                 "quotaMetric": ("generativelanguage.googleapis.com/"
                                 "generate_content_free_tier_requests"),
                 "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                 "quotaDimensions": {"model": "gemini-3.5-flash-lite",
                                     "location": "global"}}]},
        ],
    }
}])

_PER_MINUTE = json.dumps({"error": {"code": 429, "message": "Too many requests", "details": [
    {"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]}})


def _post_429(body: str):
    """Call _llm_post against a stubbed 429 and return (raised error, log records)."""
    err = urllib.error.HTTPError(
        "https://x/y", 429, "Too Many Requests", {},  # type: ignore[arg-type]
        io.BytesIO(body.encode("utf-8")))
    records = []

    class _Catch(logging.Handler):
        def emit(self, record):
            records.append(record)

    h = _Catch()
    # WARNING and above only. The DEBUG "LLM POST …" line is emitted on every request, and
    # whether it reaches a handler depends on the logger level some OTHER test module left
    # behind — so without this these tests pass alone and fail in the full suite.
    h.setLevel(logging.WARNING)
    agent.log.addHandler(h)
    try:
        with mock.patch.object(agent.urllib.request, "urlopen", side_effect=err):
            try:
                agent._llm_post("https://x/y", "k", {"model": "m", "messages": []})
            except agent._LLMError as e:
                return e, records
    finally:
        agent.log.removeHandler(h)
    raise AssertionError("_llm_post did not raise")


class QuotaIdTest(unittest.TestCase):

    def test_it_is_extracted(self):
        self.assertEqual(agent._quota_id(_BODY),
                         "GenerateRequestsPerDayPerProjectPerModel-FreeTier")

    def test_a_body_with_no_quota_id_is_empty_not_an_error(self):
        self.assertEqual(agent._quota_id('{"error": {"code": 429}}'), "")

    def test_an_empty_body_is_empty(self):
        self.assertEqual(agent._quota_id(""), "")
        self.assertEqual(agent._quota_id(None), "")

    def test_several_violations_are_all_named(self):
        """Order must not decide the retry interval — see the docstring on _quota_id."""
        body = ('{"violations": [{"quotaId": "RequestsPerMinute-FreeTier"},'
                ' {"quotaId": "RequestsPerDay-FreeTier"}]}')
        out = agent._quota_id(body)
        self.assertIn("RequestsPerMinute-FreeTier", out)
        self.assertIn("RequestsPerDay-FreeTier", out)

    def test_duplicates_are_collapsed(self):
        body = '[{"quotaId": "A"}, {"quotaId": "A"}, {"quotaId": "A"}]'
        self.assertEqual(agent._quota_id(body), "A")


class LogLineTest(unittest.TestCase):

    def _line(self, body):
        _e, records = _post_429(body)
        lines = [r.getMessage() for r in records]
        self.assertEqual(len(lines), 1, f"expected one log line, got {lines}")
        return lines[0]

    def test_the_body_is_not_dumped(self):
        line = self._line(_BODY)
        self.assertNotIn("quotaDimensions", line)
        self.assertNotIn("RESOURCE_EXHAUSTED", line)
        self.assertNotIn("docs/rate-limits", line)

    def test_it_is_one_short_line(self):
        line = self._line(_BODY)
        self.assertNotIn("\n", line)
        self.assertLess(len(line), 240, line)

    def test_it_still_names_the_quota(self):
        """The whole reason the old dump existed. Losing this makes the change a regression."""
        self.assertIn("GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                      self._line(_BODY))

    def test_it_says_so_when_the_body_names_no_quota(self):
        self.assertIn("quota unnamed", self._line('{"error": {"code": 429}}'))

    def test_a_provider_that_returns_a_long_message_is_capped(self):
        """Gemini's `msg` is short only because its body is a list. Others aren't."""
        body = json.dumps({"error": {"message": "x " * 400,
                                     "details": [{"quotaId": "Q-FreeTier"}]}})
        line = self._line(body)
        self.assertLess(len(line), 240, line)
        self.assertIn("Q-FreeTier", line)

    def test_a_multiline_message_is_reduced_to_its_first_line(self):
        body = json.dumps({"error": {"message": "Too many requests\nPlease retry in 48s.\n"}})
        line = self._line(body)
        self.assertIn("Too many requests", line)
        self.assertNotIn("Please retry", line)

    def test_other_codes_are_unchanged(self):
        err = urllib.error.HTTPError(
            "https://x/y", 503, "Service Unavailable", {},  # type: ignore[arg-type]
            io.BytesIO(b"{}"))
        records = []

        class _Catch(logging.Handler):
            def emit(self, record):
                records.append(record)

        h = _Catch()
        h.setLevel(logging.WARNING)
        agent.log.addHandler(h)
        try:
            with mock.patch.object(agent.urllib.request, "urlopen", side_effect=err):
                with self.assertRaises(agent._LLMError):
                    agent._llm_post("https://x/y", "k", {"model": "m", "messages": []})
        finally:
            agent.log.removeHandler(h)
        self.assertEqual([r.getMessage() for r in records],
                         ["LLM HTTP 503: Service Unavailable"])


class ClassificationIsUnchangedTest(unittest.TestCase):
    """The daily-quota detection now shares _quota_id — it must behave identically."""

    def test_a_per_day_quota_takes_the_long_backoff(self):
        e, _ = _post_429(_BODY)
        self.assertTrue(e.long_backoff)
        self.assertTrue(e.rate_limited)
        self.assertTrue(e.retryable)

    def test_a_per_minute_quota_does_not(self):
        e, _ = _post_429(_PER_MINUTE)
        self.assertFalse(e.long_backoff)
        self.assertTrue(e.rate_limited)

    def test_a_body_with_no_quota_id_does_not(self):
        e, _ = _post_429('{"error": {"code": 429}}')
        self.assertFalse(e.long_backoff)
        self.assertTrue(e.rate_limited)

    def test_per_day_wins_when_listed_second(self):
        """Taking only the first match would make the interval order-dependent."""
        body = ('{"violations": [{"quotaId": "RequestsPerMinute-FreeTier"},'
                ' {"quotaId": "RequestsPerDayPerProjectPerModel-FreeTier"}]}')
        e, _ = _post_429(body)
        self.assertTrue(e.long_backoff)


if __name__ == "__main__":
    unittest.main()
