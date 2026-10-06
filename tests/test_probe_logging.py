"""Who is talking to us, and keeping a port scan out of the log this project reads.

Asked *"im being probed — are we cool?"* over a log carrying thirty of these interleaved
with a running ingest:

    GET /images.php -> 404 in 0ms
    GET /admin.php  -> 404 in 0ms
    GET /makeasmtp.php?p= -> 404 in 0ms
    GET /xiugai.php?p= -> 404 in 0ms

Cool, on that scan: every path is a `.php` file and this is Flask, so there is no
interpreter in the request path and no amount of probing makes one. But the log could not
answer the question, for two separate reasons, and both are fixed here.

**It could not say who.** Everything arrives through nginx, so `request.remote_addr` is the
PROXY on every line — the same 192.168.x.x for the scan and for the owner loading
/reading-list. The real address was only ever in nginx's log. `_client_ip()` reads
`X-Forwarded-For`, but **only when the immediate peer is private or loopback**, because
that header is attacker-controlled: reached directly, it returns the real peer rather than
believing whatever was sent. The RIGHTMOST entry is the one our proxy observed; anything
left of it came from the client.

**And it drowned the thing being asked about.** Thirty probes in fifteen seconds, a line
each, interleaved with an ingest. This project has already had its disk filled once by its
own log volume, so one-line-per-probe is not a safe default — but dropping them silently is
worse, because then the next "am I being probed?" has no answer at all. The first is logged
in full, the rest are counted, and a summary lands per window: 32 probes became 2 lines.

The discriminator is `request.url_rule is None`, which Flask sets only when NOTHING matched.
A 404 from a route that exists — a wiki page that is gone — is a real answer to a real
request and keeps its own line.
"""
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import serve

PROXY = "192.168.254.11"
REAL = "45.148.10.77"
# Straight from the reported log.
PROBES = ["/images.php", "/alls.php", "/admin.php", "/geck.php", "/goods.php",
          "/dejavu.php", "/h02ugyh.php", "/155.php", "/ops.php", "/mac.php",
          "/makeasmtp.php", "/puc.php", "/8.php", "/1.php", "/gtc.php", "/inputs.php",
          "/classwithtostring.php", "/adminfuns.php", "/222.php", "/BDKR28WP.php",
          "/wp.php", "/simple.php", "/chosen.php", "/als.php", "/f35.php", "/gecko.php",
          "/xiugai.php", "/inx.php", "/11.php", "/x.php", "/y.php", "/z.php"]


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []
        # WARNING only: another module raising the logger's level would otherwise sweep
        # DEBUG chatter in here and the counts would be nonsense.
        self.setLevel(logging.WARNING)

    def emit(self, record):
        self.lines.append(record.getMessage())


class ServeTestCase(unittest.TestCase):

    def setUp(self):
        self.w = TempWiki()
        self.w.__enter__()
        self._saved = (serve.RAW_DIR, serve.WIKI_DIR)
        serve.RAW_DIR, serve.WIKI_DIR = self.w.raw, self.w.wiki
        serve._probe_state.clear()
        self.cap = _Capture()
        self.logger = logging.getLogger("lobotomy.serve")
        self.logger.addHandler(self.cap)
        self.c = serve.app.test_client()

    def tearDown(self):
        self.logger.removeHandler(self.cap)
        serve._probe_state.clear()
        serve.RAW_DIR, serve.WIKI_DIR = self._saved
        self.w.__exit__(None, None, None)

    def get(self, path, peer=PROXY, fwd=REAL):
        headers = {"X-Forwarded-For": fwd} if fwd else {}
        return self.c.get(path, environ_overrides={"REMOTE_ADDR": peer}, headers=headers)


class ClientIpTest(unittest.TestCase):
    """X-Forwarded-For is a header anyone can send. Trust is the whole question."""

    def ip(self, peer, fwd=None):
        headers = {"X-Forwarded-For": fwd} if fwd else {}
        with serve.app.test_request_context(
                "/x", environ_overrides={"REMOTE_ADDR": peer}, headers=headers):
            return serve._client_ip()

    def test_the_proxys_forwarded_address_is_used(self):
        self.assertEqual(self.ip(PROXY, REAL), REAL)

    def test_loopback_is_also_a_trusted_peer(self):
        self.assertEqual(self.ip("127.0.0.1", REAL), REAL)

    def test_a_public_peer_is_not_believed(self):
        """Reached directly rather than through nginx, the header is a lie by default."""
        self.assertEqual(self.ip("8.8.8.8", "1.1.1.1"), "8.8.8.8")

    def test_the_rightmost_entry_wins(self):
        """Our proxy appends what IT saw. Entries to the left were supplied by the client
        and would let a prober write any address it liked into this log."""
        self.assertEqual(self.ip(PROXY, "10.0.0.9, 1.2.3.4, " + REAL), REAL)

    def test_no_header_falls_back_to_the_peer(self):
        self.assertEqual(self.ip(PROXY), PROXY)

    def test_an_empty_header_falls_back_to_the_peer(self):
        self.assertEqual(self.ip(PROXY, "   "), PROXY)

    def test_a_malformed_peer_does_not_raise(self):
        self.assertEqual(self.ip("not-an-ip", REAL), "not-an-ip")

    def test_a_missing_peer_does_not_raise(self):
        self.assertEqual(self.ip("", REAL), "?")


class ProbeAggregationTest(ServeTestCase):

    def test_the_reported_scan_collapses_to_two_lines(self):
        for p in PROBES:
            self.get(p)
        self.assertEqual(len(self.cap.lines), 2, self.cap.lines)

    def test_the_first_probe_is_logged_in_full(self):
        self.get("/images.php")
        self.assertIn("/images.php", self.cap.lines[0])
        self.assertIn("matched no route", self.cap.lines[0])

    def test_every_line_names_the_real_source(self):
        for p in PROBES:
            self.get(p)
        for line in self.cap.lines:
            self.assertIn(REAL, line)
            self.assertNotIn(PROXY, line)

    def test_the_summary_counts_what_it_suppressed(self):
        for p in PROBES:
            self.get(p)
        self.assertRegex(self.cap.lines[1], r"probe: \d+ more unmatched paths")

    def test_a_scan_is_never_silent(self):
        """Dropping these would make the next 'am I being probed?' unanswerable, which is
        the opposite failure and a worse one."""
        for p in PROBES[:3]:
            self.get(p)
        self.assertTrue(self.cap.lines)

    def test_two_sources_are_counted_separately(self):
        for p in PROBES[:5]:
            self.get(p, fwd="1.1.1.1")
        for p in PROBES[:5]:
            self.get(p, fwd="2.2.2.2")
        self.assertEqual(sum(1 for l in self.cap.lines if "1.1.1.1" in l), 1)
        self.assertEqual(sum(1 for l in self.cap.lines if "2.2.2.2" in l), 1)


class RealFourOhFourTest(ServeTestCase):
    """A 404 from a route that EXISTS is an answer to a real request, not noise."""

    def setUp(self):
        super().setUp()
        with self.c.session_transaction() as s:
            s["logged_in"] = True

    def test_a_missing_file_on_a_real_route_keeps_its_own_line(self):
        self.get("/raw/does-not-exist.md")
        self.assertEqual(len(self.cap.lines), 1, self.cap.lines)
        self.assertIn("/raw/does-not-exist.md", self.cap.lines[0])
        self.assertNotIn("probe:", self.cap.lines[0])

    def test_it_is_not_aggregated_away_by_a_concurrent_scan(self):
        for p in PROBES[:5]:
            self.get(p)
        self.cap.lines.clear()
        self.get("/raw/missing.md")
        self.assertEqual(len(self.cap.lines), 1, self.cap.lines)

    def test_an_ordinary_failing_post_still_logs_with_the_client(self):
        r = self.c.post("/inbox/add", json={"content": ""},
                        environ_overrides={"REMOTE_ADDR": PROXY},
                        headers={"X-Forwarded-For": REAL})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(self.cap.lines), 1, self.cap.lines)
        self.assertIn(REAL, self.cap.lines[0])

    def test_the_quiet_poll_paths_are_still_quiet(self):
        """They are real routes, so they never reach the probe path — and the 8-second
        poll filling this log is what put it at 1.0GB and filled the disk."""
        with self.c.session_transaction() as s:
            s["logged_in"] = True
        for _ in range(5):
            self.c.get("/inbox/list", environ_overrides={"REMOTE_ADDR": PROXY})
        self.assertEqual(self.cap.lines, [])


class ApiKeyTest(unittest.TestCase):
    """The unauthenticated API routes all go through one check, so it has to be right."""

    def setUp(self):
        self.c = serve.app.test_client()

    def test_it_fails_closed_when_no_key_is_configured(self):
        """The dangerous shape would be `token == push_key` with both empty, which
        authenticates everyone. It answers 501 instead."""
        import config
        orig = config.cfg_get

        def no_key(section, key, default=None):
            if (section, key) == ("api", "push_key"):
                return ""
            return orig(section, key, default)

        serve.cfg_get = no_key
        try:
            r = self.c.get("/api/inbox", headers={"Authorization": "Bearer "})
            self.assertEqual(r.status_code, 501)
        finally:
            serve.cfg_get = orig

    def test_a_missing_header_is_refused(self):
        self.assertIn(self.c.get("/api/inbox").status_code, (401, 501))

    def test_the_comparison_is_constant_time(self):
        """A plain != leaks the key one character at a time to anyone who can measure the
        reply, and this route needs no login."""
        src = Path(serve.__file__).read_text(encoding="utf-8")
        self.assertIn("hmac.compare_digest(auth[7:].strip(), push_key)", src)
        self.assertNotIn("if auth[7:].strip() != push_key:", src)


class StaticPostureTest(unittest.TestCase):
    """Things a probe would look for, asserted so they cannot quietly come back."""

    @classmethod
    def setUpClass(cls):
        cls.src = Path(serve.__file__).read_text(encoding="utf-8")

    def test_the_werkzeug_debugger_is_off(self):
        """debug=True exposes an interactive console — the one genuine RCE in a Flask app
        and the only thing on this box a scanner could have used."""
        self.assertIn("debug=False", self.src)
        self.assertNotIn("debug=True", self.src)

    def test_every_file_serving_handler_contains_its_path(self):
        """A send_file hands a file off disk to whoever asked. Each handler that calls one
        must resolve the path and THEN check it is still inside the directory it is
        supposed to be in — that is what makes ../../etc/passwd a 404 and not a download.

        Asserted per handler rather than by counting the guard: the first version of this
        test hardcoded 3 and the file has 10, so it failed against code that was correct.
        """
        import re
        handlers = re.findall(r"^def (\w+)\(.*?(?=^def |\Z)", self.src,
                              re.MULTILINE | re.DOTALL)
        bodies = re.findall(r"^def \w+\(.*?(?=^def |\Z)", self.src,
                            re.MULTILINE | re.DOTALL)
        checked = 0
        for body in bodies:
            if "send_file(" not in body:
                continue
            checked += 1
            self.assertRegex(body, r"\.resolve\(\)\.relative_to\(\w+\.resolve\(\)\)",
                             "a send_file handler has no containment check:\n"
                             + body[:200])
        self.assertGreaterEqual(checked, 2, "no send_file handlers found to check")

    def test_nothing_serves_a_bare_wildcard_path_unauthenticated(self):
        import re
        for rule, decs in re.findall(r'@app\.route\((.*?)\)\n((?:@[\w.]+\n)*)', self.src):
            if "<path:" in rule and "require_login" not in decs:
                self.assertIn("api", rule, f"unauthenticated wildcard route {rule}")


if __name__ == "__main__":
    unittest.main()
