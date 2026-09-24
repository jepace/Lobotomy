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

    def test_a_dry_run_writes_nothing(self):
        before = {p: p.read_bytes() for p in self.w.rglob("*.md")}
        out = self._rename("--dry-run")
        self.assertIn("Dry run", out)
        self.assertEqual({p: p.read_bytes() for p in self.w.rglob("*.md")}, before)


if __name__ == "__main__":
    unittest.main()
