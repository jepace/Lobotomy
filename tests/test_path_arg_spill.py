"""A tool call whose `path` swallowed the next argument looped until the quota was gone.

Observed in production, over and over in one reading-list ingest:

    ✗ create_file wiki/entities/berger-montague.md,tags:[ — filename
      'berger-montague.md,tags:[' is not a valid slug. Use lowercase-hyphenated-words.md…
    ⏳ AI busy — retrying in 1800s (attempt 1/60)

**Nothing was malformed at any point, which is why nothing upstream caught it.** The
provider hands a tool call's arguments over as a JSON string and that string was valid
JSON — the model simply ran two fields together, so one string came back where two were
meant: `path` carried the opening of `tags`, and `tags` was absent. `parse_tool_args`
did exactly the right thing with exactly the wrong input.

Three defects stacked, and the cheap one is the loop:

1. **The refusal was locally correct and unactionable.** The model resent the identical
   call every round, so nothing terminated it — `done()` has a refusal cap and this had
   none. At `max_rpm: 1`, with the primary out on a per-day quota, each round of that is
   a 1800s wait.
2. **It named the constraint, not the call** (principle 4). "Use
   lowercase-hyphenated-words.md" is true and leaves the model to apply it; the corrected
   path is computable right there, so it is now given.
3. **"AI busy" is the wrong words for a spent daily quota.** 1800s is
   `daily_quota_poll_interval`, so that line meant ~30 hours of phase 1 — not a hiccup.

The repair is principle 1: nothing after `.md` is ever part of a filename this project
accepts, so the intended path is not a guess. **It is accepted only when the truncated
path is one the validator would have taken anyway**, so it can never admit something a
refusal would have caught.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import harness  # noqa: F401
import agent


def _call(path, **kw):
    """A tool call as the provider delivers one: arguments are a JSON STRING."""
    import json
    args = {"path": path}
    args.update(kw)
    return {"function": {"name": "create_file", "arguments": json.dumps(args)}}


class RepairTest(unittest.TestCase):
    REPORTED = "wiki/entities/berger-montague.md,tags:["

    def test_the_reported_path_is_repaired(self):
        self.assertEqual(agent._repair_path_spill(self.REPORTED),
                         "wiki/entities/berger-montague.md")

    def test_a_quoted_spill_is_repaired_too(self):
        """The same fault with the model's quoting intact rather than dropped."""
        self.assertEqual(
            agent._repair_path_spill('wiki/entities/berger-montague.md","tags":["law"]'),
            "wiki/entities/berger-montague.md")

    def test_a_clean_path_is_untouched(self):
        for p in ("wiki/entities/berger-montague.md", "wiki/sources/reuters-2026-x.md"):
            self.assertEqual(agent._repair_path_spill(p), p)

    def test_an_empty_path_is_untouched(self):
        self.assertEqual(agent._repair_path_spill(""), "")

    # ---- the half that keeps the repair honest --------------------------------

    def test_it_never_admits_what_the_refusal_would_have_caught(self):
        """The whole safety argument. Truncating has to leave a path the validator
        would have accepted on its own, or this becomes a way to smuggle a bad slug
        past the guard — a bad filename is permanent and shows up in every link."""
        for bad in ("wiki/entities/Berger Montague.md,tags:[",
                    "wiki/entities/under_score.md,tags:[",
                    "wiki/entities/Caps.md,tags:[",
                    "wiki/entities/dots.in.name.md,tags:["):
            self.assertEqual(agent._repair_path_spill(bad), bad,
                             f"{bad!r} was repaired into something still invalid")

    def test_a_directory_named_md_is_not_a_truncation_point(self):
        """Truncating at the FIRST `.md` anywhere would cut a path off at a directory."""
        p = "wiki/entities/foo.md/bar.md"
        self.assertEqual(agent._repair_path_spill(p), p)


class ParseSiteTest(unittest.TestCase):
    """At the parse site, for `_decode_args`' reason: the spill arrives through whichever
    tool the model called, and every one of them takes a `path`."""

    def test_parse_tool_args_repairs_the_path(self):
        args = agent.parse_tool_args(_call(RepairTest.REPORTED, title="Berger Montague"))
        self.assertEqual(args["path"], "wiki/entities/berger-montague.md")

    def test_the_other_arguments_survive(self):
        args = agent.parse_tool_args(
            _call(RepairTest.REPORTED, title="Berger Montague", type="entity"))
        self.assertEqual(args["title"], "Berger Montague")
        self.assertEqual(args["type"], "entity")

    def test_a_non_path_argument_is_never_truncated(self):
        """Only `path` is repaired. A body legitimately mentions a filename, and cutting
        one off at `.md` would silently delete the rest of the page."""
        body = "Adapted from wiki/sources/x.md, which says more."
        args = agent.parse_tool_args(_call("wiki/entities/ok.md", body=body))
        self.assertEqual(args["body"], body)

    def test_a_call_with_no_path_still_parses(self):
        import json
        args = agent.parse_tool_args(
            {"function": {"name": "done", "arguments": json.dumps({"ingested": True})}})
        self.assertEqual(args, {"ingested": True})


class CreateFileEndToEndTest(unittest.TestCase):
    """The round this cost. The spilled call has to WRITE, not be refused again."""

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        agent.init_session()

    def test_the_spilled_call_creates_the_page(self):
        args = agent.parse_tool_args(_call(
            RepairTest.REPORTED, title="Berger Montague", type="entity",
            body="## Overview\n\nA law firm.\n"))
        out = agent._create_file(args)
        self.assertNotIn("Error", out, out)
        self.assertTrue((self.w.wiki / "entities" / "berger-montague.md").exists(),
                        "the repaired call still did not write the page")

    def test_a_genuinely_bad_slug_is_still_refused(self):
        out = agent._create_file({"path": "wiki/entities/Berger Montague.md",
                                  "title": "Berger Montague", "type": "entity",
                                  "body": "## Overview\n\nx\n"})
        self.assertIn("not a valid slug", out)

    def test_the_refusal_names_the_path_to_use(self):
        """Principle 4: name the call, not the constraint. The model resent the identical
        path every round against a refusal that described the convention and never said
        what to type."""
        out = agent._create_file({"path": "wiki/entities/Berger Montague.md",
                                  "title": "Berger Montague", "type": "entity",
                                  "body": "## Overview\n\nx\n"})
        self.assertIn("wiki/entities/berger-montague.md", out)

    def test_following_the_refusal_works(self):
        """The only version of this assertion that proves anything: do what the refusal
        says and assert it succeeds."""
        out = agent._create_file({"path": "wiki/entities/Berger Montague.md",
                                  "title": "Berger Montague", "type": "entity",
                                  "body": "## Overview\n\nx\n"})
        import re
        m = re.search(r'path="([^"]+)"', out)
        self.assertIsNotNone(m, out)
        out2 = agent._create_file({"path": m.group(1), "title": "Berger Montague",
                                   "type": "entity", "body": "## Overview\n\nx\n"})
        self.assertNotIn("Error", out2, out2)

    def test_the_refusal_keeps_its_subdirectory(self):
        """A source page refused into wiki/entities/ would be a refusal renaming one
        violation into another — principle 4's documented worst case."""
        out = agent._create_file({"path": "wiki/sources/Reuters 2026 X.md",
                                  "title": "Reuters 2026 X", "type": "source",
                                  "body": "## Claims\n\nx\n"})
        self.assertIn("wiki/sources/", out)
        self.assertNotIn("wiki/entities/reuters", out)


class QuotaMessageTest(unittest.TestCase):
    """'AI busy — retrying in 1800s' is the wrong words for a spent daily quota: it reads
    as a hiccup and means ~30 hours. The two call for opposite responses, which is why
    the fallback log warning already names the quota."""

    def test_a_daily_quota_retry_says_so(self):
        """Driving the real builder. The first version of this grepped the source for
        `long_backoff` near the yield and matched the COMMENT explaining the bug, so
        mutate.py deleted the branch and it still passed."""
        exc = agent._LLMError("429 quota")
        exc.long_backoff = True
        evt = agent._retry_event(1, 1800, 60, exc)
        self.assertIn("msg", evt, "a spent daily quota is still reported as 'AI busy'")
        self.assertIn("quota", evt["msg"].lower())
        self.assertIn("1800", evt["msg"])

    def test_an_ordinary_retry_keeps_the_plain_wording(self):
        """The busy-server case must NOT claim the quota is gone — the crying-wolf
        constraint. A 60s blip is exactly what 'AI busy' is for."""
        evt = agent._retry_event(1, 60, 60, RuntimeError("boom"))
        self.assertNotIn("msg", evt)
        self.assertEqual(evt["delay"], 60)

    def test_the_renderer_prefers_that_message(self):
        """One renderer, server-chosen words: base.html falls back to 'AI busy' only
        when no msg was sent."""
        base = (Path(__file__).resolve().parent.parent / "tools" / "templates"
                / "base.html").read_text(encoding="utf-8")
        self.assertIn("ev.msg ||", base)


if __name__ == "__main__":
    unittest.main()
