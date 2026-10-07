"""rename_page.py, run as the script it is, against a throwaway repository.

Every other maintenance pass keeps its logic in agent.py — merge_page, unlink_headings,
promote_lead_to_opener, rename_section — with a thin CLI in front. rename_page.py is the
outlier: the whole rename is top-level script code, so there is no function to call and
the rest of the suite cannot reach it. That is exactly how its copy of the
generated-pages bug went uncovered while merge_page's was caught.

REPO_ROOT is derived from the script's own location (agent.py: Path(__file__).parent.parent),
so copying tools/ into a temp directory beside a throwaway wiki/ gives the real script a
real repository to work on, with no risk to this one. Slower than an in-process test, and
worth it for the one tool that cannot have one.

What it must do, and what the mutation runner checks it still does:

  * repoint (or strip) links on content pages
  * leave wiki/log.md alone — repointing a link there rewrites what the log says happened
  * leave wiki/index.md alone, and rebuild it instead, so the rename does not leave the
    dead link the repoint used to fix
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _page(w: Path, rel: str, title: str, body: str) -> None:
    (w / rel).write_text(
        f'---\ntitle: "{title}"\ntype: entity\ntags: []\ncreated: 2026-01-01\n'
        f'updated: 2026-01-01\nsources: []\n---\n\n{body}\n', encoding="utf-8")


class RenamePageCliTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not (REPO / "config.json.example").exists():
            raise unittest.SkipTest("config.json.example missing — cannot build a temp repo")

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="lobotomy-rename-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        shutil.copytree(REPO / "tools", self.root / "tools")
        shutil.copy(REPO / "config.json.example", self.root / "config.json")
        (self.root / "raw").mkdir()
        self.w = self.root / "wiki"
        (self.w / "entities").mkdir(parents=True)
        _page(self.w, "entities/united.md", "United",
              "# United\n\n## Overview\n\nAn airline.")
        _page(self.w, "entities/american-airlines.md", "American Airlines",
              "# American Airlines\n\n## Overview\n\nA rival of [United](united.md).")
        (self.w / "log.md").write_text(
            "# Log\n\n- 2026-09-01 created [United](entities/united.md)\n", encoding="utf-8")
        (self.w / "index.md").write_text(
            "# Wiki Index\n\n---\n\n## Entities\n\n- [United](entities/united.md)\n",
            encoding="utf-8")

    def _rename(self, *extra):
        r = subprocess.run(
            [sys.executable, str(self.root / "tools" / "rename_page.py"),
             str(self.w / "entities" / "united.md"),
             str(self.w / "entities" / "united-airlines.md"),
             "--title", "United Airlines", *extra],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        return r.stdout

    def _move_dir(self, *extra, src="concepts/ebola.md", dst="entities/ebola.md"):
        """A cross-DIRECTORY move, which is how a misfiled page gets corrected."""
        (self.w / "concepts").mkdir(exist_ok=True)
        (self.w / "concepts" / "ebola.md").write_text(
            '---\ntitle: "Ebola"\ntype: concept\ntags: []\ncreated: 2026-01-01\n'
            'updated: 2026-01-01\nsources: []\n---\n\n# Ebola\n\n## Definition\n\n'
            'A viral haemorrhagic fever.\n', encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(self.root / "tools" / "rename_page.py"),
             str(self.w / src), str(self.w / dst), *extra],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        return r.stdout

    def test_a_cross_directory_move_carries_the_type_field(self):
        """**The half-move.** This script moved `concepts/ebola.md` to
        `entities/ebola.md` and left `type: concept` behind — and `_OPENER`,
        `section_inventory.py` and `bleeding_titles.py` all read the FIELD, not the
        directory. The result is worse than the misfile it was correcting: the page sits
        where an entity lives while claiming to be a concept, so every tool that asks gets
        the stale answer. Nine tests passed over this, all of them renames inside one
        directory."""
        self._move_dir()
        text = (self.w / "entities" / "ebola.md").read_text()
        self.assertIn("type: entity", text)
        self.assertNotIn("type: concept", text)

    def test_the_type_change_is_reported(self):
        """Silently rewriting a frontmatter field the operator did not ask about is how a
        tool becomes something you cannot reason about."""
        out = self._move_dir()
        self.assertIn("type: concept  ->  entity", out)

    def test_a_move_within_one_directory_leaves_the_type_alone(self):
        """The common case. A plain rename must not touch it — and a `synthesis/` page
        moved inside `synthesis/` has a type the directory map would not change either."""
        self._rename()
        self.assertIn("type: entity",
                      (self.w / "entities" / "united-airlines.md").read_text())

    def test_the_stale_opener_is_reported_rather_than_rewritten(self):
        """An entity page wants `## Overview`. Renaming the heading is a content edit with
        a tool of its own (`rename_section.py`), and doing it silently inside a file move
        would hide it; saying nothing leaves a page off-template with nothing pointing at
        it. So it reports and names the call — principle 4."""
        out = self._move_dir()
        self.assertIn("## Definition", out)
        self.assertIn("Overview", out)
        self.assertIn("rename_section.py", out)
        # and the heading itself is untouched
        self.assertIn("## Definition", (self.w / "entities" / "ebola.md").read_text())

    def test_the_history_directory_is_created_through_the_inheriting_helper(self):
        """`mkdir(parents=True)` under `wiki/` is the one thing CLAUDE.md forbids outright,
        and this file was the last place still doing it. Run as root beside a server
        running as another user — which is how this tool is normally run — a root-owned
        level inside `wiki/.history/` stops the server writing revisions for that page, and
        `_snapshot_version` deliberately never raises, so the page just stops accumulating
        history with nothing to say so.

        Asserted structurally because ownership cannot be reproduced in a test that is not
        root: what is checkable is that the call goes through the helper that exists for
        this, which is also what `mutate.py` can break."""
        src = (self.root / "tools" / "rename_page.py").read_text()
        self.assertIn("_mkdir_inheriting(hist_dst.parent)", src)
        self.assertNotIn("hist_dst.parent.mkdir(parents=True", src)

    def test_history_moves_with_the_page(self):
        """The behaviour the mkdir was there for, so the fix cannot have broken it."""
        hist = self.w / ".history" / "concepts" / "ebola.md"
        hist.mkdir(parents=True)
        (hist / "20260101000000000000__ingest.md").write_text("old", encoding="utf-8")
        self._move_dir()
        moved = self.w / ".history" / "entities" / "ebola.md"
        self.assertTrue(moved.is_dir(), "the page's revisions did not follow it")
        self.assertEqual([f.name for f in moved.iterdir()],
                         ["20260101000000000000__ingest.md"])

    def test_the_page_is_renamed(self):
        self._rename()
        self.assertTrue((self.w / "entities" / "united-airlines.md").is_file())
        self.assertFalse((self.w / "entities" / "united.md").exists())

    def test_the_log_still_says_what_actually_happened(self):
        # The one this exists for. Repointing here would make the log claim the ingest
        # created united-airlines.md, which it never did.
        self._rename()
        self.assertIn("[United](entities/united.md)",
                      (self.w / "log.md").read_text(encoding="utf-8"),
                      "the audit trail was rewritten to agree with the present")

    def test_the_index_is_rebuilt_rather_than_patched(self):
        # Not repointing index.md is only safe because the generator runs afterwards.
        self._rename()
        idx = (self.w / "index.md").read_text(encoding="utf-8")
        self.assertIn("united-airlines.md", idx, idx)
        self.assertNotIn("(entities/united.md)", idx, idx)

    def test_a_content_page_is_still_handled(self):
        # Display text that was the old title is stripped, not relabelled — the rename
        # declares that text wrong. relink.py re-links it under the new title.
        self._rename()
        body = (self.w / "entities" / "american-airlines.md").read_text(encoding="utf-8")
        self.assertIn("A rival of United.", body, body)
        self.assertNotIn("](united.md)", body)


    # -- refusals and warnings must name a move that works (principle 4) ----------

    def _raw(self, dest, *extra):
        return subprocess.run(
            [sys.executable, str(self.root / "tools" / "rename_page.py"),
             str(self.w / "entities" / "united.md"), str(self.w / "entities" / dest),
             *extra],
            capture_output=True, text=True)

    def test_an_invalid_slug_is_refused_with_the_corrected_one(self):
        """Observed: `wiki/concepts/will_and_testament.md` was refused with nothing but
        "not a valid slug". Underscores are the obvious thing to try when the convention is
        not in front of you, and the correction is mechanical, so there is no reason to make
        the caller guess it."""
        r = self._raw("united_airlines.md", "--title", "United Airlines")
        self.assertNotEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertIn("not a valid slug", out)
        self.assertIn("lowercase-hyphenated", out)
        self.assertIn("united-airlines.md", out, "the corrected slug is not offered")

    def test_the_suggested_command_actually_works(self):
        """The only version of this assertion that proves anything: follow the refusal's
        own instructions and require that they succeed."""
        out = self._raw("united_airlines.md", "--title", "United Airlines")
        line = next(l.strip() for l in (out.stdout + out.stderr).splitlines()
                    if "rename_page.py" in l and "--title" in l)
        parts = line.split()
        dest = parts[3]
        self.assertTrue(dest.endswith("united-airlines.md"), dest)
        r = self._raw(Path(dest).name, "--title", "United Airlines")
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

    def test_renaming_without_a_title_says_the_title_is_unchanged(self):
        """The filename is the part that looks like the name, but the autolinker matches
        title: — so renaming the file alone changes nothing about what gets linked. Measured:
        after renaming concepts/will.md and relinking, the modal verb "will" was linked
        again, merely to the new path."""
        out = self._raw("united-airlines.md").stdout
        self.assertIn("title: is unchanged", out)
        self.assertIn("matches the TITLE", out)
        self.assertIn("--title", out)

    def test_no_such_note_when_a_title_was_given(self):
        self.assertNotIn("title: is unchanged", self._rename())

    def test_a_dry_run_writes_nothing(self):
        before = {p: p.read_bytes() for p in self.w.rglob("*.md")}
        out = self._rename("--dry-run")
        self.assertIn("Dry run", out)
        self.assertEqual({p: p.read_bytes() for p in self.w.rglob("*.md")}, before)


if __name__ == "__main__":
    unittest.main()
