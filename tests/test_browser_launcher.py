"""A browser test that SKIPS reads as "nothing to run here", so it hides its own absence.

Six modules each carried their own copy of the chromium launcher, and every copy globbed
`chromium*/chrome-linux/chrome`. The browser images in use ship

    chromium-1243/chrome-linux64/chrome
    chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell

so on such an image none of the globs matched, every browser test skipped with "chromium
unavailable" — **and a working chromium was sitting right there.** 65 tests, including
every assertion about the reading list's own JavaScript, were silently not running.

`mutate.py` reported the fix MISSED, and was right to: break the launcher and the tests
go back to SKIPPING, which no runner counts as a failure. So the guard needs a test that
FAILS when a browser that exists cannot be found — this one.

The detection here is deliberately **not** the launcher's own globs, which would make the
assertion circular. It walks the browser root for anything executable with a chromium-ish
name, and then requires `launch_chromium` to produce a browser.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness

_NAMES = {"chrome", "chrome-headless-shell", "headless_shell"}


def _a_browser_exists_on_disk():
    """Any executable that is plainly a chromium, found without the launcher's help."""
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "/opt/pw-browsers")
    if not root.is_dir():
        return None
    for dirpath, _dirnames, filenames in os.walk(root):
        for n in filenames:
            if n in _NAMES:
                p = Path(dirpath) / n
                if p.is_file() and os.access(p, os.X_OK):
                    return p
    return None


class LauncherFindsAnInstalledBrowserTest(unittest.TestCase):
    def test_a_browser_on_disk_is_actually_launched(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("playwright not installed")
        found = _a_browser_exists_on_disk()
        if not found:
            raise unittest.SkipTest("no chromium on this machine at all")
        pw = sync_playwright().start()
        try:
            browser = harness.launch_chromium(pw)
        except Exception as e:
            self.fail(
                f"a chromium exists at {found} and the launcher did not find it: {e}\n"
                "Every browser test is skipping, which reads as 'nothing to run here'.")
        try:
            self.assertTrue(browser.version)
        finally:
            browser.close()
            pw.stop()


if __name__ == "__main__":
    unittest.main()
