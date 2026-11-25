"""Every mutation in `mutate.py` still reaches the code it claims to break.

`mutate.py` is the one thing that proves this suite works: it disables a guard and
requires a test to go red. An anchor that no longer matches is reported STALE, and the
run exits non-zero — but **only if somebody runs it to the end**, which takes minutes and
gets slower with every test added. So the rot is invisible by default, and it accumulated:
twenty mutations had stopped matching, which is twenty guards that could have been deleted
with nothing objecting and nothing saying so.

That is this project's recurring shape, a fourth time over: the chromium launcher whose
breakage made 65 tests SKIP, the module that could not import and was dropped from
discovery, the zero-match pattern that printed `0 caught, 0 missed` and exited 0. **The
defect and its own symptom were the same thing**, so the fix is to make the silent case
loud — here, to check it in milliseconds on every ordinary run rather than in minutes on a
run nobody starts.

It asserts nothing about whether a guard is CAUGHT; that still needs the real runner. It
asserts only that each mutation can be applied at all, which is the precondition for the
verdict meaning anything.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
import mutate


class MutationAnchorsTest(unittest.TestCase):
    def test_every_anchor_matches_exactly_once(self):
        texts: dict = {}
        stale = []
        for name, find, _replace, *rest in mutate.MUTATIONS:
            rel = rest[0] if rest else "tools/agent.py"
            if rel not in texts:
                texts[rel] = (REPO / rel).read_text(encoding="utf-8")
            n = texts[rel].count(find)
            if n != 1:
                stale.append(f"  {n}x in {rel}: {name}")
        self.assertEqual(
            stale, [],
            "mutate.py anchors that no longer apply — the code moved and these guards "
            "are no longer being verified. 0x means the anchor is gone; 2x or more means "
            "it is ambiguous, and mutate.py refuses both.\n" + "\n".join(stale))

    def test_the_names_are_unique(self):
        """The pattern argument is a substring match, so a duplicate name runs two."""
        names = [m[0] for m in mutate.MUTATIONS]
        dupes = sorted({n for n in names if names.count(n) > 1})
        self.assertEqual(dupes, [], f"duplicate mutation names: {dupes}")

    def test_a_mutation_actually_changes_the_file(self):
        """A no-op mutation reports MISSED and means nothing.

        Observed: `return None or ("wiki/sources/…")` evaluates to the string, so the
        file changed and the behaviour did not — and the runner cannot tell that apart
        from an untested guard. Textual identity is the half that IS checkable here.
        """
        same = [m[0] for m in mutate.MUTATIONS if m[1] == m[2]]
        self.assertEqual(same, [], f"mutations that replace text with itself: {same}")


if __name__ == "__main__":
    unittest.main()
