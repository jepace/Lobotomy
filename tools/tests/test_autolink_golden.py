"""Golden-file test for the autolinker corpus.

Runs the existing 30-case corpus (autolink_cases.py) through _autolink in a throwaway
wiki and compares against the committed baseline (autolink_baseline.json). A change meant
to alter linking regenerates the baseline with run_autolink_cases.py; the diff is the
review artifact (see tools/README.md).
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki
from autolink_cases import CASES

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent

BASELINE = Path(__file__).parent / "autolink_baseline.json"


class AutolinkGoldenTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not BASELINE.exists():
            raise unittest.SkipTest(
                f"{BASELINE} missing — generate it with "
                f"'python3 tools/tests/run_autolink_cases.py tools/tests/autolink_baseline.json'")
        cls.baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    def _run_case(self, pages, target_subdir, body):
        # Mirrors run_autolink_cases.py's build() byte-for-byte (frontmatter shape and the
        # unconditional trailing "\n"), since the baseline was captured from that tool and
        # this must reproduce it exactly to be a meaningful diff rather than a format drift.
        with TempWiki() as w:
            for title, aliases, no_auto in pages:
                slug = agent._slug_for(title) or "untitled"
                fm = [f'title: "{title}"', "type: entity", "tags: []",
                      "created: 2026-01-01", "updated: 2026-01-01", "sources: []"]
                if aliases:
                    fm.append("aliases: " + json.dumps(aliases))
                if no_auto:
                    fm.append("no_autolink: true")
                (w.wiki / "entities" / f"{slug}.md").write_text(
                    "---\n" + "\n".join(fm) + "\n---\n\n# " + title + "\n\nStub.\n")
            target = w.wiki / target_subdir / "target-page.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                '---\ntitle: "Target Page"\ntype: entity\ntags: []\n'
                'created: 2026-01-01\nupdated: 2026-01-01\nsources: []\n---\n\n' + body + "\n")
            agent._title_map_cache = None
            agent._title_regex_cache.clear()
            rel = target.relative_to(w.wiki)
            msg = agent._autolink({"path": f"wiki/{rel.as_posix()}"})
            return msg, target.read_text(encoding="utf-8")

    def test_corpus_matches_baseline(self):
        mismatches = []
        for name, pages, subdir, body in CASES:
            with self.subTest(case=name):
                msg, content = self._run_case(pages, subdir, body)
                expected = self.baseline.get(name)
                if expected is None:
                    mismatches.append(f"{name}: no baseline entry")
                    continue
                # The baseline's message text embeds run_autolink_cases.py's sandbox dir
                # name ("alwiki/..."); this test's own temp wiki is just "wiki/...". Only
                # that literal differs, so normalize it away — the content comparison
                # below is the one that actually matters.
                norm_expected_msg = expected["message"].replace("alwiki/", "wiki/", 1)
                if msg != norm_expected_msg or content != expected["content"]:
                    mismatches.append(
                        f"{name}:\n  message: {msg!r}\n  expected: {norm_expected_msg!r}\n"
                        f"  content: {content!r}\n  expected: {expected['content']!r}")
        self.assertEqual(mismatches, [], "\n\n".join(mismatches))


if __name__ == "__main__":
    unittest.main()
