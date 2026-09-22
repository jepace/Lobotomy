"""Running a maintenance tool as root must not lock the server out of its own wiki.

The tools are run by hand, often as root, while the server runs as another user (www on
the FreeBSD jail). Anything root creates inside wiki/ would default to root-owned, and the
server would then fail with EACCES on files and directories it can never fix itself —
silently, in the case of history, because _snapshot_version deliberately never raises.

So every write path gives what it creates the owner of the tree above it: existing files
keep their own uid/gid, new files take their parent directory's, and every directory level
that mkdir creates takes its parent's. That is what makes `python3 tools/whatever.py`
safe to run as root and removes the need to remember `su -m www -c '...'`.

These tests only mean anything when run as root; they skip otherwise, which is the normal
case on a development machine. On a box where they can run, they are the difference
between "we think this is safe" and "we checked".
"""
import os
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

IS_ROOT = os.name == "posix" and os.geteuid() == 0
SERVER_UID = 1001          # stands in for www; any uid that is not root will do
SERVER_GID = 1001


@unittest.skipUnless(IS_ROOT, "ownership behaviour only differs when running as root")
class RootOwnershipTest(TempWikiTestCase):

    def setUp(self):
        super().setUp()
        # A wiki owned by the server user, as on the real box.
        os.chown(self.w.wiki, SERVER_UID, SERVER_GID)
        for d in self.w.wiki.iterdir():
            if d.is_dir():
                os.chown(d, SERVER_UID, SERVER_GID)

    def assertOwnedByServer(self, path, what):
        st = Path(path).stat()
        self.assertEqual(st.st_uid, SERVER_UID,
                         f"{what} is owned by uid {st.st_uid}, not the server — "
                         f"the server cannot write it and cannot repair it")

    def test_new_page_created_by_root_is_owned_by_the_server(self):
        agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/acme.md", "title": "Acme Corp", "type": "entity",
            "body": "## Overview\n\nA firm.\n"})
        self.assertOwnedByServer(self.w.wiki / "entities" / "acme.md", "the new page")

    def test_new_subdirectory_created_by_root_is_owned_by_the_server(self):
        # The hole this file was written for: create_file and _atomic_write used a bare
        # mkdir(parents=True), so a subdirectory that did not exist yet came out
        # root-owned even though the page inside it did not.
        shutil.rmtree(self.w.wiki / "synthesis")
        agent.TOOL_FNS["create_file"]({
            "path": "wiki/synthesis/compare.md", "title": "Compare", "type": "synthesis",
            "body": "## Question / Thesis\n\nX.\n"})
        self.assertOwnedByServer(self.w.wiki / "synthesis", "the new subdirectory")
        self.assertOwnedByServer(self.w.wiki / "synthesis" / "compare.md", "the new page")

    def test_history_directory_created_by_root_is_owned_by_the_server(self):
        # Worth its own test because a failure here is invisible: _snapshot_version never
        # raises, so a root-owned history directory means the page just stops being
        # revertable and nothing says so.
        p = self.w.page("entities/acme.md", title="Acme Corp", type="entity",
                        body="## Overview\n\nA firm.\n")
        os.chown(p, SERVER_UID, SERVER_GID)
        self.w.read("wiki/entities/acme.md")
        agent._update_file("wiki/entities/acme.md", "## Overview\n\nA firm, expanded.\n")
        hist = agent.HISTORY_DIR / "entities" / "acme.md"
        self.assertTrue(hist.is_dir(), "no history directory was created")
        self.assertOwnedByServer(agent.HISTORY_DIR / "entities", "the history subdirectory")
        self.assertOwnedByServer(hist, "the page's history directory")

    def test_existing_page_keeps_its_own_owner_and_mode(self):
        p = self.w.page("entities/acme.md", title="Acme Corp", type="entity",
                        body="## Overview\n\nA firm.\n")
        os.chown(p, SERVER_UID, SERVER_GID)
        os.chmod(p, 0o640)
        self.w.read("wiki/entities/acme.md")
        agent._update_file("wiki/entities/acme.md", "## Overview\n\nA firm, expanded.\n")
        st = p.stat()
        self.assertEqual(st.st_uid, SERVER_UID, "a root-run rewrite re-owned the page")
        self.assertEqual(st.st_mode & 0o777, 0o640, "a root-run rewrite changed the mode")

    def test_repair_pass_run_as_root_leaves_everything_writable(self):
        # The end-to-end claim the docs make: run a maintenance tool as root, and the
        # server can still write every file and directory it touched.
        for slug in ("a", "b", "c"):
            p = self.w.page(f"entities/{slug}.md", title=f"{slug.upper()} Corp",
                            type="entity",
                            body=f"# {slug.upper()} Corp\n\nA firm that does things "
                                 f"and keeps doing them.\n\n## Background\n\nB.\n")
            os.chown(p, SERVER_UID, SERVER_GID)
        agent.promote_lead_to_opener()
        for path in list(self.w.wiki.rglob("*")):
            if path.name.startswith("."):
                continue
            self.assertOwnedByServer(path, str(path.relative_to(self.w.wiki)))


if __name__ == "__main__":
    unittest.main()
