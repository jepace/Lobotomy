#!/usr/bin/env python3
"""
Lobotomy — Web server

Mobile-friendly web app: chat with AI, browse wiki, capture articles.

Requirements:
  pip install flask markdown openai resend
  -- or on FreeBSD --
  pkg install py311-flask py311-markdown && pip install openai resend

Configuration: copy config.json.example to config.json and edit it.

Usage:
  python3 tools/serve.py
"""

import datetime
import functools
import json
import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------

missing = []
try:
    from flask import (Flask, Response, abort, flash, redirect,
                       render_template, request, session,
                       stream_with_context, url_for, send_file, make_response)
except ImportError:
    missing.append("flask")

try:
    import markdown as md_lib
except ImportError:
    missing.append("markdown")

if missing:
    print(f"Error: missing packages: {', '.join(missing)}")
    print(f"  pip install {' '.join(missing)}")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))

# ---------------------------------------------------------------------------
# Logging — must be set up before importing agent/job_queue so their loggers
# inherit this configuration.
# ---------------------------------------------------------------------------

def _setup_logging() -> None:
    _log_file = Path(__file__).resolve().parent.parent / "server.log"
    fmt = logging.Formatter(
        "%(asctime)s  %(name)-22s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.handlers.RotatingFileHandler(
        _log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root = logging.getLogger("lobotomy")
    root.setLevel(logging.DEBUG)
    root.addHandler(fh)
    root.addHandler(sh)

_setup_logging()
log = logging.getLogger("lobotomy.serve")

# Suppress noisy polling endpoints from werkzeug's access log.
class _SuppressPollingPaths(logging.Filter):
    _QUIET = {"/chat/status", "/api/status", "/inbox/list"}
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p in msg for p in self._QUIET)

logging.getLogger("werkzeug").addFilter(_SuppressPollingPaths())

from config import cfg_get, cfg_bool, cfg_int, validate_config
from agent import (REPO_ROOT, WIKI_DIR, RAW_DIR,
                   get_client_and_model, orientation_message,
                   stream_agent_turn, run_agent_turn, system_prompt,
                   _fix_wiki_links, _rebuild_index, _validate_ingest,
                   heal_index_if_stale, _atomic_write, search_wiki_core)

from job_queue import JobQueue
from auth  import (user_exists, create_user, authenticate, get_user, update_password,
                   set_verified, create_token, consume_token, record_attempt,
                   is_locked_out, send_verification_email, send_reset_email,
                   maybe_send_verification, _resend_ready,
                   get_settings, update_settings)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__, template_folder="templates")

_secret_file = WIKI_DIR / ".secret"
_secret = cfg_get("server", "secret")
if _secret:
    app.secret_key = _secret
elif _secret_file.exists():
    app.secret_key = _secret_file.read_text().strip()
else:
    _key = os.urandom(24).hex()
    _secret_file.write_text(_key)
    app.secret_key = _key

app.config.update(
    SESSION_COOKIE_HTTPONLY = True,
    SESSION_COOKIE_SAMESITE = "Lax",
    SESSION_COOKIE_SECURE   = cfg_bool("server", "https"),
)

@app.after_request
def add_cors_headers(response):
    """Add CORS headers for cross-origin requests."""
    if request.path.startswith("/api/"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Max-Age"] = "3600"
    return response

@app.before_request
def handle_preflight():
    """Handle CORS preflight requests."""
    if request.method == "OPTIONS" and request.path.startswith("/api/"):
        response = make_response("", 204)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Max-Age"] = "3600"
        return response

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def require_login(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not user_exists():
            return redirect(url_for("setup"))
        if not session.get("logged_in"):
            next_url = request.full_path.rstrip("?")
            return redirect(url_for("auth_login", next=next_url))
        return f(*args, **kwargs)
    return decorated


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if user_exists():
        return redirect(url_for("auth_login"))
    error = None
    email = ""
    if request.method == "POST":
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm", "")
        if not email or not password:
            error = "Email and password are required."
        elif password != confirm:
            error = "Passwords do not match."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        else:
            try:
                create_user(email, password)
                session["logged_in"] = True
                session["email"] = email
                return redirect(url_for("chat"))
            except RuntimeError as e:
                error = str(e)
    return render_template("setup.html", error=error, email=email)


@app.context_processor
def inject_globals():
    path = request.path
    if path.startswith("/wiki"):
        active = "wiki"
    elif path.startswith("/inbox") or path.startswith("/reading-list"):
        active = "inbox"
    elif path.startswith("/settings"):
        active = "settings"
    else:
        active = "chat"
    user = get_user() if session.get("logged_in") else None
    return {"active": active, "current_user": user}

# ---------------------------------------------------------------------------
# Blog helpers
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> tuple:
    """Return (meta_dict, body_text) from a markdown file with YAML frontmatter."""
    meta = {}
    if not text.startswith("---"):
        return meta, text
    lines = text.split("\n")
    end = -1
    for i, line in enumerate(lines[1:], 1):
        if line.rstrip() == "---":
            end = i
            break
    if end == -1:
        return meta, text
    for line in lines[1:end]:
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1]
            meta[k] = [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
        elif v.lower() == "true":
            meta[k] = True
        elif v.lower() == "false":
            meta[k] = False
        else:
            meta[k] = v
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return meta, body



# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/auth/login", methods=["GET", "POST"])
def auth_login():
    if session.get("logged_in"):
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        email    = (request.form.get("email")    or "").strip()
        password = (request.form.get("password") or "").strip()
        ok, msg  = authenticate(email, password)
        record_attempt(ok)
        if ok:
            session.clear()
            session["logged_in"]  = True
            session.permanent     = True
            app.permanent_session_lifetime = datetime.timedelta(days=30)
            next_url = request.args.get("next") or ""
            # Only follow safe relative paths (no scheme, no host, no path traversal)
            if not next_url.startswith("/") or "//" in next_url or "%2f" in next_url.lower():
                next_url = url_for("index")
            return redirect(next_url)
        error = msg

    return render_template("login.html", error=error)


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    session.clear()
    return redirect(url_for("auth_login"))


@app.route("/auth/verify/<token>")
def auth_verify(token):
    if consume_token(token, "verify"):
        set_verified()
        return render_template("verify_done.html")
    return render_template("verify_done.html", error="This link is invalid or has expired.")


@app.route("/auth/resend-verification", methods=["POST"])
def auth_resend_verification():
    sent = maybe_send_verification()
    msg  = "Verification email sent." if sent else "Could not send email — check RESEND_API_KEY."
    return render_template("verify_pending.html", message=msg)


@app.route("/auth/forgot", methods=["GET", "POST"])
def auth_forgot():
    message = None
    if request.method == "POST":
        if not _resend_ready():
            message = "Password reset requires Resend. Set RESEND_API_KEY."
        else:
            user = get_user()
            if user:
                token = create_token("reset", hours=1)
                send_reset_email(user["email"], token)
            # Always show the same message to prevent email enumeration
            message = "If that email is registered, a reset link has been sent."
    return render_template("forgot_password.html", message=message)


@app.route("/auth/reset/<token>", methods=["GET", "POST"])
def auth_reset(token):
    error = None
    if request.method == "POST":
        pw1 = request.form.get("password",  "")
        pw2 = request.form.get("password2", "")
        if len(pw1) < 10:
            error = "Password must be at least 10 characters."
        elif pw1 != pw2:
            error = "Passwords do not match."
        else:
            if consume_token(token, "reset"):
                update_password(pw1)
                session.clear()
                return render_template("login.html",
                                       message="Password updated. Please log in.")
            error = "This reset link is invalid or has expired."
    return render_template("reset_password.html", token=token, error=error)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.route("/settings")
@require_login
def settings_page():
    user = get_user() or {}
    settings = get_settings()
    push_key = cfg_get("api", "push_key", "")
    base_url = cfg_get("server", "base_url", "http://localhost:8080").rstrip("/")
    return render_template("settings.html",
                           user=user,
                           settings=settings,
                           push_key=push_key,
                           base_url=base_url)


@app.route("/settings/password", methods=["POST"])
@require_login
def settings_password():
    data = request.get_json(silent=True) or {}
    current  = data.get("current", "")
    new_pw   = data.get("new", "")
    confirm  = data.get("confirm", "")
    user = get_user()
    if not user:
        return {"error": "Not found"}, 404
    from auth import _verify
    if not _verify(current, user["pw_hash"]):
        return {"error": "Current password is incorrect"}, 400
    if len(new_pw) < 8:
        return {"error": "Password must be at least 8 characters"}, 400
    if new_pw != confirm:
        return {"error": "Passwords do not match"}, 400
    update_password(new_pw)
    return {"ok": True}


@app.route("/settings/preferences", methods=["POST"])
@require_login
def settings_preferences():
    data = request.get_json(silent=True) or {}
    allowed = {"daily_email_enabled", "daily_email_address"}
    patch = {k: v for k, v in data.items() if k in allowed}
    if patch:
        update_settings(patch)
    return {"ok": True}


@app.route("/settings/profile", methods=["POST"])
@require_login
def settings_profile():
    data = request.get_json(silent=True) or {}
    allowed = {"display_name", "timezone"}
    patch = {k: str(v).strip() for k, v in data.items() if k in allowed}
    if patch:
        update_settings(patch)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------

HISTORY_FILE       = WIKI_DIR / ".chat_history.json"
INBOX_LOG_FILE     = WIKI_DIR / ".inbox_log.json"
DISPLAY_LOG_FILE   = WIKI_DIR / ".chat_display_log.json"
MAX_HISTORY  = 80
MAX_DISPLAY_LOG = 500


def load_history() -> list:
    if HISTORY_FILE.exists():
        try:
            messages = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            messages = _sanitize_history(messages)
            return messages
        except Exception as e:
            log.error("Failed to load/sanitize chat history: %s", e, exc_info=True)
    return []


def _sanitize_history(messages: list) -> list:
    """
    Fix history for Gemini compatibility:
    0. Strip content from assistant messages that also have tool_calls — storing
       both causes Gemini's layer to split them into two model turns, placing a
       function-call turn after a text turn which violates ordering rules.
    1. Backfill 'name' in tool messages from preceding assistant tool_calls.
    2. Remove incomplete tool-call sequences (assistant with tool_calls but
       no following tool responses) — these are left by interrupted ingests
       and cause "function call turn must come after user/function response" errors.
    3. Drop any complete tool-call sequence where responses have unfixable empty names.
    """
    # Pass 0: strip content from tool-call messages to prevent Gemini ordering errors
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls") and "content" in msg:
            del msg["content"]

    # Pass 1: backfill tool response names
    id_to_name = {}
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in (msg.get("tool_calls") or []):
                tc_id = tc.get("id") or ""
                tc_name = (tc.get("function") or {}).get("name") or ""
                if tc_id and tc_name:
                    id_to_name[tc_id] = tc_name
        elif msg.get("role") == "tool" and not msg.get("name"):
            tc_id = msg.get("tool_call_id") or ""
            if tc_id in id_to_name:
                msg["name"] = id_to_name[tc_id]

    # Pass 2: drop incomplete or broken tool-call blocks
    # An assistant message with tool_calls must be immediately followed by
    # tool response(s) that all have non-empty names.  If the block is absent
    # or any tool response still has an empty name after Pass 1, drop it.
    # Any tool message outside a valid block is orphaned and also dropped.
    clean = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            # Collect the span of tool responses that follow
            j = i + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                j += 1
            if j == i + 1:
                # No tool responses follow — incomplete block, drop it and stop
                break
            # Drop the block if any tool response still has an empty name
            if any(not m.get("name") for m in messages[i + 1 : j]):
                i = j  # skip to after the broken sequence; keep scanning
                continue
            # Valid block — include it
            clean.extend(messages[i:j])
            i = j
        else:
            # Any tool message here is orphaned (not part of a valid assistant
            # tool_call block) — drop it unconditionally to prevent Gemini 400s.
            if msg.get("role") == "tool":
                i += 1
                continue
            clean.append(msg)
            i += 1

    return clean


def save_history(messages: list, source: str = "chat") -> None:
    """Append human-readable messages to the display log, then clear AI context."""
    _append_display_log(messages, source)
    clear_history()


def _append_display_log(messages: list, source: str) -> None:
    """Append conversation turns from this job to the persistent display log.

    Each turn is stored as one entry with the user message, the ordered list of
    tool calls made during that turn, and the final assistant reply.
    """
    import re as _re

    def _clean(text: str) -> str:
        text = _re.sub(r'\n*<file path="[^"]*">.*?</file>', "", text, flags=_re.DOTALL)
        text = _re.sub(r'\s*Current wiki state:.*', "", text, flags=_re.DOTALL)
        return text.strip()

    try:
        existing = []
        if DISPLAY_LOG_FILE.exists():
            try:
                existing = json.loads(DISPLAY_LOG_FILE.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        now = datetime.datetime.now().isoformat(timespec="seconds")

        # Walk the message list and group into turns: each user message starts
        # a new turn; tool calls and the final assistant reply belong to it.
        turns = []
        current: dict | None = None
        for m in messages:
            role = m.get("role")
            if role == "user":
                content = m.get("content", "")
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                content = _clean(content)
                if not content:
                    continue
                if current:
                    turns.append(current)
                current = {"role": "user", "content": content, "tools": [], "reply": "", "ts": now, "source": source}
            elif role == "assistant" and current is not None:
                # Collect tool calls from this assistant message.
                for tc in (m.get("tool_calls") or []):
                    fn = (tc.get("function") or {}).get("name", "")
                    try:
                        args = json.loads((tc.get("function") or {}).get("arguments") or "{}")
                    except Exception:
                        args = {}
                    # Build a short readable arg preview (path, query, url, title).
                    arg = args.get("path") or args.get("query") or args.get("url") or args.get("title") or ""
                    if fn and fn not in ("done",):
                        current["tools"].append(f"{fn}  {arg}".strip())
                # Capture the final text reply.
                content = m.get("content") or ""
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                content = _clean(content)
                if content:
                    current["reply"] = content
        if current:
            turns.append(current)

        if turns:
            combined = existing + turns
            if len(combined) > MAX_DISPLAY_LOG:
                combined = combined[-MAX_DISPLAY_LOG:]
            DISPLAY_LOG_FILE.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.error("Failed to append display log: %s", e)


def load_display_log() -> list:
    import re as _re
    if DISPLAY_LOG_FILE.exists():
        try:
            entries = json.loads(DISPLAY_LOG_FILE.read_text(encoding="utf-8"))
            cleaned = []
            for e in entries:
                c = e.get("content", "")
                c = _re.sub(r'\n*<file path="[^"]*">.*?</file>', "", c, flags=_re.DOTALL).strip()
                c = _re.sub(r'\s*Current wiki state:.*', "", c, flags=_re.DOTALL).strip()
                if c:
                    e = dict(e, content=c)
                    cleaned.append(e)
            return cleaned
        except Exception:
            pass
    return []


def clear_display_log() -> None:
    if DISPLAY_LOG_FILE.exists():
        DISPLAY_LOG_FILE.unlink()


def clear_history() -> None:
    if HISTORY_FILE.exists():
        HISTORY_FILE.unlink()

# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

_MD_EXTENSIONS = ["tables", "toc", "fenced_code", "attr_list", "sane_lists"]


def _rewrite_md_link(href: str, from_page: Path) -> str:
    if href.startswith(("http://", "https://", "/", "#", "mailto:")):
        return href
    resolved = (from_page.parent / href).resolve()
    # Try wiki directory first
    try:
        rel = resolved.relative_to(WIKI_DIR.resolve())
        # Strip .md extension for wiki links (router adds it back)
        path_str = str(rel).replace("\\", "/")
        if path_str.endswith(".md"):
            path_str = path_str[:-3]
        return f"/wiki/{path_str}"
    except ValueError:
        pass
    # Then try raw directory (keep extension for raw files)
    try:
        rel = resolved.relative_to(RAW_DIR.resolve())
        return f"/raw/{rel}".replace("\\", "/")
    except ValueError:
        return href


def render_md(path: Path) -> str:
    if not path.exists():
        return "<p><em>Page not found.</em></p>"
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)
    html = md_lib.markdown(text, extensions=_MD_EXTENSIONS)
    html = re.sub(
        r'href="([^"]*.md[^"]*)"',
        lambda m: f'href="{_rewrite_md_link(m.group(1), path)}"',
        html,
    )
    return html

def render_md_raw(text: str) -> str:
    """Render markdown from string content (for raw files)."""
    text = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)
    html = md_lib.markdown(text, extensions=_MD_EXTENSIONS)
    return html

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Inbox helpers
# ---------------------------------------------------------------------------

def _fetch_and_patch(dest: "pathlib.Path", url: str) -> None:
    """Background thread: fetch url, rewrite dest with content, clear fetch_failed."""
    import threading
    def _run():
        text, err = _clip_fetch(url)
        try:
            raw = dest.read_text(encoding="utf-8", errors="replace")
            fm, body = _parse_frontmatter(raw)
            if text:
                # Update title from content if still a URL-derived fallback
                if not fm.get("title") or fm.get("title") == url:
                    for line in text.splitlines():
                        line = line.strip().lstrip("#").strip()
                        if line and not line.startswith("<"):
                            fm["title"] = line[:120]
                            break
                fm.pop("fetch_failed", None)
                new_body = text
            else:
                fm["fetch_failed"] = True
                new_body = body  # leave existing placeholder
            # Reconstruct frontmatter
            lines = ["---"]
            for k, v in fm.items():
                if isinstance(v, bool):
                    lines.append(f"{k}: {'true' if v else 'false'}")
                elif isinstance(v, list):
                    lines.append(f"{k}: {json.dumps(v)}")
                else:
                    lines.append(f'{k}: "{v}"' if k == "title" else f"{k}: {v}")
            lines += ["---", ""]
            _atomic_write(dest, "\n".join(lines) + (new_body or ""))
            log.info("_fetch_and_patch: %s fetched ok=%s", dest.name, bool(text))
        except Exception as e:
            log.warning("_fetch_and_patch: failed to patch %s: %s", dest.name, e)
    threading.Thread(target=_run, daemon=True, name=f"fetch-{dest.stem}").start()


def _clip_fetch(url: str) -> "tuple[str | None, str | None]":
    """Fetch a URL and return (clean_text, error). Uses stdlib only."""
    import urllib.request
    import urllib.error
    from html.parser import HTMLParser

    class _Reader(HTMLParser):
        SKIP  = {"script", "style", "noscript", "nav", "header", "footer",
                 "aside", "template", "form", "button"}
        BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6",
                 "li", "tr", "blockquote", "article", "section", "pre"}

        def __init__(self):
            super().__init__()
            self._skip = 0
            self.parts = []

        def handle_starttag(self, tag, attrs):
            if tag in self.SKIP:
                self._skip += 1
            elif tag in self.BLOCK and not self._skip:
                self.parts.append("\n\n")

        def handle_endtag(self, tag):
            if tag in self.SKIP and self._skip:
                self._skip -= 1

        def handle_data(self, data):
            if not self._skip:
                self.parts.append(data)

    headers = {
        "User-Agent":              "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept":                  "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language":         "en-US,en;q=0.9",
        "Accept-Encoding":         "identity",
        "Sec-Fetch-Dest":          "document",
        "Sec-Fetch-Mode":          "navigate",
        "Sec-Fetch-Site":          "none",
        "Sec-Fetch-User":          "?1",
        "Upgrade-Insecure-Requests": "1",
        "Connection":              "keep-alive",
        "Cache-Control":           "max-age=0",
        "Pragma":                  "no-cache",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            ct  = resp.headers.get("Content-Type", "")
            raw = resp.read(1_000_000)
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return None, str(e)

    if "html" in ct.lower():
        parser = _Reader()
        try:
            parser.feed(raw.decode("utf-8", errors="replace"))
        except Exception as e:
            log.debug("HTML parse warning for %s: %s", url, e)
        text = re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()
        if not text:
            return None, "No text extracted — site may require JavaScript or be paywalled"
        return text[:100_000], None
    else:
        text = raw.decode("utf-8", errors="replace")[:100_000]
        if text.startswith('�'):  # Unicode replacement character, likely binary garbage
            return None, "Response appears to be binary or unreadable"
        return text, None


def list_inbox(show_archived: bool = False) -> list:
    candidates = []
    if RAW_DIR.is_dir():
        candidates += [
            f for f in RAW_DIR.iterdir()
            if f.is_file() and not f.name.startswith(".") and f.name != "index.md"
        ]
    items = []
    def _inbox_sort_key(f):
        try:
            fm, _ = _parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
            v = fm.get("added") or fm.get("saved") or ""
            if v:
                return str(v)
        except Exception:
            pass
        import datetime as _dt
        return _dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat()

    for f in sorted(candidates, key=_inbox_sort_key, reverse=True):
        if not f.is_file() or f.name.startswith("."):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""

        has_content = False
        source_url  = ""
        if f.suffix == ".url":
            lines      = [l.strip() for l in text.splitlines() if l.strip()]
            title      = lines[0][:100] if lines else f.stem
            url_line   = next((l for l in lines if l.startswith("URL:")), "")
            source_url = url_line[4:].strip()
            excerpt    = source_url
        else:
            # Strip frontmatter from all text files before extracting title/excerpt
            meta, body = _parse_frontmatter(text)
            source_url   = meta.get("url", "")
            fetch_failed = bool(meta.get("fetch_failed"))
            title        = meta.get("title", "").strip() or f.stem
            title        = title[:100]
            # Exclude fetch-failure placeholder lines from content check
            lines = [
                l.strip() for l in body.splitlines()
                if l.strip() and not l.startswith("#") and not l.startswith("<!--")
                   and "Content could not be fetched" not in l
            ]
            has_content = bool(lines) and not fetch_failed
            if fetch_failed or not lines:
                excerpt = source_url or ""
            else:
                excerpt = " ".join(lines[:3])[:200]

        mtime = datetime.date.fromtimestamp(f.stat().st_mtime).isoformat()
        wikified = False
        wiki_path = ""
        try:
            raw_text = f.read_text(encoding="utf-8", errors="replace")
            fm, _ = _parse_frontmatter(raw_text)
            wikified = bool(fm.get("wikified"))
            archived = bool(fm.get("archived"))
            if archived and not show_archived:
                continue
        except Exception:
            pass
        if wikified:
            # Find the wiki/sources/ page that has raw_source pointing to this file
            raw_rel = str(f.relative_to(REPO_ROOT))
            for wf in (WIKI_DIR / "sources").glob("*.md") if (WIKI_DIR / "sources").is_dir() else []:
                try:
                    wm, _ = _parse_frontmatter(wf.read_text(encoding="utf-8", errors="replace"))
                    if wm.get("raw_source") == raw_rel:
                        wiki_path = str(wf.relative_to(WIKI_DIR))
                        break
                except Exception:
                    pass
        items.append({
            "name":        f.name,
            "title":       title,
            "excerpt":     excerpt,
            "date":        mtime,
            "has_content": has_content,
            "source_url":  source_url,
            "ext":         f.suffix,
            "wikified":    wikified,
            "wiki_path":   wiki_path,
            "archived":    archived,
        })
    return items

# ---------------------------------------------------------------------------
# Wiki navigation helpers
# ---------------------------------------------------------------------------

def wiki_sections() -> list:
    pages = []
    for name, label in [
        ("index.md",        "Index"),
        ("log.md",          "Log"),
    ]:
        if (WIKI_DIR / name).exists():
            pages.append({"path": name, "label": label})
    pages.append({"path": "tags", "label": "Tags"})
    for d in ["sources", "entities", "concepts", "synthesis"]:
        dpath = WIKI_DIR / d
        if dpath.is_dir():
            # Only show tab if directory has content beyond the stub index.md
            extra = [f for f in dpath.glob("*.md") if f.name != "index.md"]
            if extra:
                pages.append({"path": d + "/", "label": d.title()})
    return pages

# ---------------------------------------------------------------------------
# Main routes
# ---------------------------------------------------------------------------

@app.route("/")
@require_login
def index():
    return redirect(url_for("chat"))


@app.route("/chat")
@require_login
def chat():
    display = load_display_log()
    return render_template("chat.html", history=display)


@app.route("/chat/send", methods=["POST"])
@require_login
def chat_send():
    data       = request.get_json(silent=True) or {}
    message    = (data.get("message") or "").strip()
    inbox_file = (data.get("inbox_file") or "").strip()
    if not message:
        return {"error": "Empty message"}, 400

    client, model, error = get_client_and_model()
    if error:
        return {"error": error}, 503

    sys_prompt = system_prompt()
    history    = load_history()
    if not history:
        history = [
            {"role": "user",      "content": orientation_message()},
            {"role": "assistant", "content": "Oriented. Ready."},
        ]
    # Pre-load inbox file so the AI skips its read_file round-trip.
    _inbox_setup = None
    if inbox_file:
        inbox_path = RAW_DIR / Path(inbox_file).name
        try:
            file_content = inbox_path.read_text(encoding="utf-8", errors="replace")
            message = (
                f'{message}\n\n'
                f'<file path="raw/{inbox_path.name}">\n{file_content}\n</file>'
            )
            # Prime the globals that _create_file uses for url: and raw_source: fallbacks.
            # Without this, the LLM skips read_file (content already injected) so the
            # globals never get set and frontmatter ends up missing both fields.
            import agent as _agent
            import re as _re
            stripped = file_content.strip()
            _inbox_path_str = str(inbox_path.resolve().relative_to(REPO_ROOT.resolve()))
            _inbox_url = ""
            if stripped.startswith("http") and "\n" not in stripped:
                _inbox_url = stripped
            else:
                _m = _re.search(r'^url:\s*["\']?([^\s"\'\n]+)', file_content, _re.MULTILINE)
                if _m:
                    _inbox_url = _m.group(1).strip()
            _inbox_setup = lambda _p=_inbox_path_str, _u=_inbox_url: _agent.init_session(inbox_path=_p, inbox_url=_u)
        except OSError:
            _inbox_setup = None  # file unreadable — AI will fall back to read_file normally

    history.append({"role": "user", "content": message})

    def on_done(messages):
        save_history(messages, source="inbox" if inbox_file else "chat")
        if inbox_file:
            ingested = any(
                isinstance(m.get("content"), str) and m["content"].startswith("__ingested__:1")
                for m in messages
                if m.get("role") == "system"
            )
            if ingested:
                # Belt-and-suspenders: never mark wikified if the raw file still has
                # fetch_failed set (agent may have called done(ingested=1) despite no content).
                raw_path = RAW_DIR / Path(inbox_file).name
                try:
                    raw_fm, _ = _parse_frontmatter(raw_path.read_text(encoding="utf-8"))
                    if raw_fm.get("fetch_failed"):
                        log.warning("on_done: refusing to mark wikified — fetch_failed=true in %s", inbox_file)
                        ingested = False
                except Exception:
                    pass
            if ingested:
                _mark_inbox_wikified(inbox_file)
            else:
                log.warning("on_done: inbox_file=%s but no __ingested__:1 in messages — not marking wikified", inbox_file)

    log.info("Chat send: model=%s history_len=%d", model, len(history))
    job_id = job_queue.submit(client, model, history, sys_prompt, on_done=on_done, setup=_inbox_setup)
    return {"job_id": job_id}


def _link_raw_source_to_wiki(raw_path, inbox_url: str) -> None:
    """
    After archiving a raw file, find the matching wiki/sources/ page and stamp
    raw_source: into its frontmatter so wiki_page() can link to it directly.

    Matching strategy (in order):
      1. wiki/sources/ page whose raw_source: already matches — already done, return early
      2. wiki/sources/ page whose url: frontmatter matches inbox_url (exact)
      3. wiki/sources/ page whose slug matches the raw filename stem exactly
         e.g. raw/wirecutter-2026-shredders.html → sources/wirecutter-2026-shredders.md
    """
    import json as _json

    wiki_sources = WIKI_DIR / "sources"
    if not wiki_sources.is_dir():
        return

    raw_rel = str(raw_path.relative_to(REPO_ROOT))
    raw_stem = raw_path.stem  # filename without extension
    best = None

    for wf in wiki_sources.glob("*.md"):
        if wf.name == "index.md":
            continue
        try:
            wtext = wf.read_text(encoding="utf-8")
        except OSError:
            continue
        wmeta, _ = _parse_frontmatter(wtext)
        if wmeta.get("raw_source"):
            if wmeta["raw_source"] == raw_rel:
                return  # already stamped correctly
            continue    # already linked to something else
        if inbox_url and wmeta.get("url", "").strip() == inbox_url:
            best = wf
            break
        if wf.stem == raw_stem:
            best = wf
            break

    if best is None:
        log.warning("_link_raw_source_to_wiki: no wiki/sources/ page found for %s", raw_path.name)
        return

    try:
        wtext = best.read_text(encoding="utf-8")
        wmeta, wbody = _parse_frontmatter(wtext)
        wmeta["raw_source"] = raw_rel
        fm_lines = ["---"]
        for k, v in wmeta.items():
            if isinstance(v, list):
                fm_lines.append(f"{k}: {_json.dumps(v)}")
            elif isinstance(v, bool):
                fm_lines.append(f"{k}: {'true' if v else 'false'}")
            else:
                sv = str(v)
                fm_lines.append(f"{k}: {_json.dumps(sv) if (chr(34) in sv or ':' in sv) else sv}")
        fm_lines.append("---")
        _atomic_write(best, "\n".join(fm_lines) + "\n" + wbody)
        log.info("Stamped raw_source=%s into %s", raw_rel, best.name)

        # Also stamp wiki_page back onto the raw file's frontmatter
        wiki_page_rel = str(best.relative_to(WIKI_DIR))
        try:
            raw_text = raw_path.read_text(encoding="utf-8")
            raw_fm, raw_body = _parse_frontmatter(raw_text)
            raw_fm["wiki_page"] = wiki_page_rel
            raw_fm_lines = ["---"]
            for k, v in raw_fm.items():
                if isinstance(v, list):
                    raw_fm_lines.append(f"{k}: {_json.dumps(v)}")
                elif isinstance(v, bool):
                    raw_fm_lines.append(f"{k}: {'true' if v else 'false'}")
                else:
                    sv = str(v)
                    raw_fm_lines.append(f"{k}: {_json.dumps(sv) if (chr(34) in sv or ':' in sv) else sv}")
            raw_fm_lines.append("---")
            raw_path.write_text("\n".join(raw_fm_lines) + "\n" + raw_body, encoding="utf-8")
            log.info("Stamped wiki_page=%s into %s", wiki_page_rel, raw_path.name)
        except Exception as e2:
            log.warning("Failed to stamp wiki_page into raw file %s: %s", raw_path.name, e2)
    except Exception as e:
        log.error("_link_raw_source_to_wiki failed for %s: %s", raw_path.name, e)



def _mark_inbox_wikified(filename: str) -> None:
    """Mark a raw file as wikified in its frontmatter. Safe to call from any thread."""
    import json as _json
    p = RAW_DIR / filename
    try:
        p.resolve().relative_to(RAW_DIR.resolve())
    except ValueError:
        log.warning("_mark_inbox_wikified: invalid path %s", filename)
        return
    if not p.exists():
        log.warning("_mark_inbox_wikified: file not found %s", filename)
        return
    try:
        text = p.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)
        if fm.get("wikified"):
            return  # already marked
        fm["wikified"] = True
        fm["wikified_date"] = datetime.date.today().isoformat()
        fm_lines = ["---"]
        for k, v in fm.items():
            if isinstance(v, list):
                fm_lines.append(f"{k}: {_json.dumps(v)}")
            elif isinstance(v, bool):
                fm_lines.append(f"{k}: {'true' if v else 'false'}")
            else:
                sv = str(v)
                fm_lines.append(f"{k}: {_json.dumps(sv) if (chr(34) in sv or ':' in sv) else sv}")
        fm_lines.append("---")
        p.write_text("\n".join(fm_lines) + "\n" + body, encoding="utf-8")
        log.info("Marked wikified: %s", filename)
        _link_raw_source_to_wiki(p, fm.get("url", "").strip())
        _rebuild_index({})
        result = _fix_wiki_links({})
        log.info("fix_wiki_links: %s", result)
        result = _rebuild_index({})
        log.info("rebuild_index: %s", result)
        report = _validate_ingest({})
        log.info("validate_ingest:\n%s", report)
    except Exception as e:
        log.error("_mark_inbox_wikified failed for %s: %s", filename, e)


@app.route("/chat/stream/<job_id>")
@require_login
def chat_stream(job_id):
    def generate():
        yield from job_queue.tail(job_id)
    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")


@app.route("/chat/status")
@require_login
def chat_status():
    return job_queue.status()


@app.route("/chat/clear", methods=["POST"])
@require_login
def chat_clear():
    clear_history()
    clear_display_log()
    return {"ok": True}


@app.route("/chat/cancel", methods=["POST"])
@require_login
def chat_cancel():
    data   = request.get_json(silent=True) or {}
    job_id = (data.get("job_id") or "").strip()
    if not job_id:
        return {"error": "Missing job_id"}, 400
    found = job_queue.cancel(job_id)
    return {"ok": found}


@app.route("/wiki/search")
@require_login
def wiki_search():
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return {"results": []}
    res = search_wiki_core(q, WIKI_DIR)
    if res["error"]:
        return {"results": []}
    return {
        "results": [
            {"path": str(r["path"].relative_to(WIKI_DIR)), "title": r["title"], "excerpt": r["snippet"]}
            for r in res["results"][:12]
        ]
    }


@app.route("/wiki/")
@require_login
def wiki_home():
    return redirect(url_for("wiki_page", page_path="index.md"))


@app.route("/wiki/<path:page_path>")
@require_login
def wiki_page(page_path):
    p = WIKI_DIR / page_path
    if p.is_dir():
        p = p / "index.md"
    if not p.suffix:
        p = p.with_suffix(".md")
    try:
        p.resolve().relative_to(WIKI_DIR.resolve())
    except ValueError:
        abort(404)
    if not p.exists():
        abort(404)
    raw_text   = p.read_text(encoding="utf-8", errors="replace")
    meta, _    = _parse_frontmatter(raw_text)
    source_url = meta.get("url", "").strip() or None
    # For wiki/sources/ pages, check for a stamped raw_source field in frontmatter.
    raw_source_url = None
    # Check raw_source: field first, then fall back to sources: entries starting with raw/
    raw_source_url = None
    raw_source = meta.get("raw_source", "").strip()
    if raw_source and (REPO_ROOT / raw_source).exists():
        raw_source_url = "/" + raw_source
    else:
        for s in meta.get("sources", []):
            s = s.strip()
            if not s.startswith("raw/"):
                continue
            if (REPO_ROOT / s).exists():
                raw_source_url = "/" + s
                break
    return render_template(
        "wiki.html",
        content=render_md(p),
        title=p.stem.replace("-", " ").title(),
        sections=wiki_sections(),
        current_path=str(p.relative_to(WIKI_DIR)),
        source_url=source_url,
        raw_source_url=raw_source_url,
    )


@app.route("/wiki/<path:page_path>/edit")
@require_login
def wiki_edit(page_path):
    p = WIKI_DIR / page_path
    if p.is_dir():
        p = p / "index.md"
    if not p.suffix:
        p = p.with_suffix(".md")
    try:
        p.resolve().relative_to(WIKI_DIR.resolve())
    except ValueError:
        abort(404)
    if not p.exists():
        abort(404)
    return render_template(
        "wiki-edit.html",
        raw=p.read_text(encoding="utf-8"),
        title=p.stem.replace("-", " ").title(),
        page_path=str(p.relative_to(WIKI_DIR)),
    )


@app.route("/api/wiki/<path:page_path>/save", methods=["POST"])
@require_login
def wiki_save(page_path):
    p = WIKI_DIR / page_path
    try:
        p.resolve().relative_to(WIKI_DIR.resolve())
    except ValueError:
        return {"error": "Invalid path"}, 400
    if not p.exists():
        return {"error": "Page not found"}, 404
    data = request.get_json(silent=True) or {}
    content = data.get("content")
    if content is None:
        return {"error": "No content"}, 400
    p.write_text(content, encoding="utf-8")
    return {"ok": True}


@app.route("/wiki/lint")
@require_login
def wiki_lint():
    import re as _re
    SKIP_ALL   = {"log.md"}
    SKIP_FM    = {"index.md", "overview.md", "reading-list.md"}
    required_fields = {"title", "type", "tags", "created", "updated", "sources"}
    index_text = (WIKI_DIR / "index.md").read_text(encoding="utf-8") if (WIKI_DIR / "index.md").exists() else ""

    broken_links, missing_fm, not_indexed = [], [], []
    for f in sorted(WIKI_DIR.rglob("*.md")):
        if f.name in SKIP_ALL:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(f.relative_to(WIKI_DIR))

        if f.name not in SKIP_FM:
            fm_match = _re.match(r"^---\s*\n(.*?)\n---", text, _re.DOTALL)
            if fm_match:
                fm_keys = {l.split(":", 1)[0].strip() for l in fm_match.group(1).splitlines() if ":" in l}
                missing = required_fields - fm_keys
                if missing:
                    missing_fm.append({"file": rel, "missing": ", ".join(sorted(missing))})
            else:
                missing_fm.append({"file": rel, "missing": "no frontmatter"})

        for link_text, link_path in _re.findall(r'\[([^\]]+)\]\(([^)]+)\)', text):
            if link_path.startswith(("http", "#", "mailto")):
                continue
            target = (f.parent / link_path).resolve()
            if not target.exists():
                broken_links.append({"file": rel, "link": link_path, "text": link_text})

        if f.name not in SKIP_FM and f.parent != WIKI_DIR and rel not in index_text:
            not_indexed.append(rel)

    return render_template("wiki-lint.html",
                           broken_links=broken_links,
                           missing_fm=missing_fm,
                           not_indexed=not_indexed)


@app.route("/wiki/tags")
@require_login
def wiki_tags():
    import re as _re
    from collections import defaultdict
    tag_map = defaultdict(list)  # tag -> list of {title, path, type}
    for f in sorted(WIKI_DIR.rglob("*.md")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = _re.match(r"^---\s*\n(.*?)\n---", text, _re.DOTALL)
        if not fm:
            continue
        meta = {}
        for line in fm.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        tags_raw = meta.get("tags", "")
        tags = [t.strip().strip('"') for t in tags_raw.strip("[]").split(",") if t.strip().strip('"')]
        title = meta.get("title", "").strip('"')
        pg_type = meta.get("type", "").strip()
        rel = str(f.relative_to(WIKI_DIR))
        for tag in tags:
            if tag and tag not in ("index", "meta"):
                tag_map[tag].append({"title": title, "path": rel, "type": pg_type})
    tags_sorted = sorted(tag_map.items(), key=lambda x: (-len(x[1]), x[0]))
    return render_template("wiki-tags.html", tags=tags_sorted, sections=wiki_sections(), current_path="tags")


@app.route("/wiki/tags/<tag>")
@require_login
def wiki_tag(tag):
    import re as _re
    pages = []
    for f in sorted(WIKI_DIR.rglob("*.md")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = _re.match(r"^---\s*\n(.*?)\n---", text, _re.DOTALL)
        if not fm:
            continue
        meta = {}
        for line in fm.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        tags_raw = meta.get("tags", "")
        tags = [t.strip().strip('"') for t in tags_raw.strip("[]").split(",") if t.strip().strip('"')]
        if tag not in tags:
            continue
        title = meta.get("title", "").strip('"')
        pg_type = meta.get("type", "").strip()
        updated = meta.get("updated", "").strip()
        rel = str(f.relative_to(WIKI_DIR))
        # Grab first non-heading, non-empty line of body as summary
        body = text[fm.end():]
        summary = next((l.strip() for l in body.splitlines() if l.strip() and not l.startswith("#")), "")
        pages.append({"title": title, "path": rel, "type": pg_type, "updated": updated, "summary": summary})
    pages.sort(key=lambda x: x["title"].lower())
    return render_template("wiki-tag.html", tag=tag, pages=pages, sections=wiki_sections(), current_path="tags")


@app.route("/wiki/fix-broken-links", methods=["POST"])
@require_login
def wiki_fix_broken_links():
    import re as _re
    SKIP = {"log.md"}
    total_fixed = 0
    pages_fixed = 0

    def _check(m, page_path):
        text, target_str = m.group(1), m.group(2)
        if target_str.startswith(("http", "#", "mailto")):
            return m.group(0)
        resolved = (page_path.parent / target_str).resolve()
        if resolved.exists():
            return m.group(0)
        return text

    for f in sorted(WIKI_DIR.rglob("*.md")):
        if f.name in SKIP:
            continue
        try:
            original = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fixed = _re.sub(r'\[([^\]]+)\]\(([^)]+)\)', lambda m: _check(m, f), original)
        if fixed != original:
            f.write_text(fixed, encoding="utf-8")
            total_fixed += original.count("[") - fixed.count("[")
            pages_fixed += 1

    return redirect(url_for("wiki_lint"))


@app.route("/raw/")
@app.route("/raw")
@require_login
def raw_index():
    from flask import redirect
    index = RAW_DIR / "index.md"
    if not index.exists():
        _rebuild_index({})
    return render_template("wiki.html",
               title="Raw Sources",
               content=render_md(index),
               current_path="",
               sections=wiki_sections(),
               source_url=None,
               raw_source_url=None)


@app.route("/raw/<path:filename>")
@require_login
def raw_file(filename):
    p = RAW_DIR / filename
    try:
        p.resolve().relative_to(RAW_DIR.resolve())
    except ValueError:
        abort(404)
    if not p.exists():
        abort(404)
    if p.is_dir():
        abort(404)

    # Serve as plain text or HTML depending on file type
    if p.suffix in ('.md', '.txt', '.url'):
        content = p.read_text(encoding='utf-8', errors='replace')
        return render_template(
            "raw.html",
            title=p.stem.replace("-", " ").title(),
            content=content if p.suffix == '.txt' else render_md_raw(content),
            filename=filename,
        )
    else:
        # For other files, serve as download
        return send_file(p, as_attachment=True)


@app.route("/reading-list")
@app.route("/inbox")
@require_login
def inbox():
    push_key    = cfg_get("api", "push_key", "")
    base_url    = cfg_get("server", "base_url", "http://localhost:8080").rstrip("/")
    show_archived = request.args.get("archived") == "1"
    return render_template("inbox.html", items=list_inbox(show_archived=show_archived),
                           show_archived=show_archived,
                           push_key=push_key, base_url=base_url)


@app.route("/inbox/log")
@require_login
def inbox_log():
    """Render inbox processing history (full agent turns) in the chat UI."""
    try:
        messages = json.loads(INBOX_LOG_FILE.read_text(encoding="utf-8")) if INBOX_LOG_FILE.exists() else []
    except Exception:
        messages = []
    display = [
        {"role": m["role"], "content": m.get("content", "")}
        for m in messages
        if m["role"] in ("user", "assistant") and m.get("content")
    ]
    return render_template("chat.html", history=display)


@app.route("/inbox/list")
@require_login
def inbox_list():
    """API endpoint that returns inbox items as JSON for polling/auto-refresh."""
    items = list_inbox()
    return {"items": items}


@app.route("/inbox/search")
@require_login
def inbox_search():
    q = request.args.get("q", "").strip()
    show_archived = request.args.get("archived", "0") == "1"
    if len(q) < 2:
        return {"results": []}
    words = [w.lower() for w in q.split() if w]
    results = []
    for f in RAW_DIR.iterdir():
        if not f.is_file() or f.name.startswith(".") or f.name == "index.md":
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, body = _parse_frontmatter(text)
        is_archived = bool(fm.get("archived"))
        if is_archived != show_archived:
            continue
        text_lower = text.lower()
        if not all(w in text_lower for w in words):
            continue
        title = fm.get("title", "").strip() or f.stem
        excerpt = ""
        for line in body.splitlines():
            if line.strip() and any(w in line.lower() for w in words):
                excerpt = line.strip()[:120]
                break
        results.append({"name": f.name, "title": title, "excerpt": excerpt})
    return {"results": results}


# ---------------------------------------------------------------------------
# External API — /api/*
# ---------------------------------------------------------------------------

def _api_auth():
    """
    Validate Bearer token for external API routes.
    Returns (True, None) on success, or (False, (body, status)) on failure.
    """
    push_key = cfg_get("api", "push_key", "").strip()
    if not push_key:
        return False, ({"error": "API not configured — set api.push_key in config.json",
                        "code": "NOT_CONFIGURED"}, 501)
    auth = request.headers.get("Authorization", "").strip()
    if not auth.startswith("Bearer "):
        return False, ({"error": "Authorization header required: Bearer <token>",
                        "code": "UNAUTHORIZED"}, 401)
    if auth[7:].strip() != push_key:
        return False, ({"error": "Invalid API key", "code": "FORBIDDEN"}, 403)
    return True, None


@app.route("/api/status")
def api_status():
    """
    Health check — no auth required.
    Returns whether the push API is configured and the API version.
    """
    return {
        "ok": True,
        "version": "1",
        "push_configured": bool(cfg_get("api", "push_key", "").strip()),
    }


@app.route("/api/search")
def api_search():
    ok, err = _api_auth()
    if not ok:
        return jsonify(err[0]), err[1]
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "q parameter required"}), 400
    res = search_wiki_core(q, WIKI_DIR)
    if res["error"]:
        return jsonify({"error": res["error"]}), 400
    return jsonify({
        "keywords": res["keywords"],
        "scope": res["scope"],
        "results": [
            {"title": r["title"], "path": r["rel"], "snippet": r["snippet"],
             "created": r["created"], "score": r["score"]}
            for r in res["results"]
        ],
    })


@app.route("/save")
def save_redirect():
    """
    Bookmarklet-friendly save endpoint. Accepts GET with query params so the
    bookmarklet can just set location.href — no fetch(), no CORS, no CSP issues.

      /save?url=<url>&title=<title>&key=<push_key>

    Saves the item and redirects back to the original URL with a fragment so
    the user sees a visual confirmation via the browser's back-navigation.
    """
    url   = request.args.get("url",   "").strip()
    title = request.args.get("title", "").strip()
    key   = request.args.get("key",   "").strip()

    push_key = cfg_get("api", "push_key", "").strip()
    if not push_key or key != push_key:
        return "Unauthorized", 403
    if not url:
        return "Missing url", 400

    # Reuse the same save logic as /api/push
    inbox_dir = RAW_DIR
    for existing in inbox_dir.glob("*.md"):
        if existing.name == "index.md":
            continue
        try:
            meta, _ = _parse_frontmatter(existing.read_text(encoding="utf-8", errors="replace"))
            if meta.get("url", "").strip() == url:
                return redirect(url + "#lobotomy-saved")
        except Exception:
            pass

    if not title:
        title = url.rstrip("/").split("/")[-1].split("?")[0].replace("-", " ").replace("_", " ") or url

    slug      = re.sub(r"[^a-z0-9]+", "-", title.lower())[:60].strip("-") or "article"
    base_name = f"{slug}.md"
    dest      = inbox_dir / base_name
    if dest.exists():
        import time as _t
        base_name = f"{slug}-{int(_t.time())}.md"
        dest      = inbox_dir / base_name

    today = datetime.date.today().isoformat()
    now   = datetime.datetime.now().isoformat(timespec="seconds")
    fm    = ["---", f'title: "{title}"', f"url: {url}",
             f"saved: {today}", f"added: {now}",
             "wikified: false", "source: bookmarklet", "fetch_failed: true", "---", ""]
    _atomic_write(dest, "\n".join(fm))
    _fetch_and_patch(dest, url)

    return redirect(url + "#lobotomy-saved")


@app.route("/api/push", methods=["POST", "OPTIONS"])
def api_push():
    """
    Push an article into Lobotomy Reading.

    Auth: Authorization: Bearer <push_key>

    Body (JSON):
      url        string   URL of the article. If content is omitted, Lobotomy
                          fetches the page automatically.
      title      string   Article title. Auto-extracted if omitted.
      content    string   Full article body text. Skips the fetch if provided.
      tags       string[] Optional tag list.
      source     string   Identifier for the calling application.
      author     string   Article author.

    At least one of url or content is required.
    If only content is given, title is also required.
    """
    # CORS preflight — bookmarklet runs from foreign origins
    if request.method == "OPTIONS":
        resp = make_response("", 204)
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        return resp

    ok, err = _api_auth()
    if not ok:
        return err

    data       = request.get_json(silent=True) or {}
    url        = (data.get("url")     or "").strip()
    title      = (data.get("title")   or "").strip()
    content    = (data.get("content") or "").strip()
    tags       = data.get("tags")   or []
    source     = (data.get("source") or "external-api").strip()
    author     = (data.get("author") or "").strip()

    if not isinstance(tags, list):
        tags = [str(tags)]
    tags = [str(t).strip() for t in tags if str(t).strip()]

    # Validation
    if not url and not content:
        return {"error": "Provide url, content, or both", "code": "MISSING_FIELDS"}, 400
    if not url and not title:
        return {"error": "title is required when url is not provided",
                "code": "MISSING_FIELDS"}, 400

    # Deduplication: if the same URL already exists in raw/, return it
    inbox_dir = RAW_DIR
    if url:
        for existing in inbox_dir.glob("*.md"):
            if existing.name == "index.md":
                continue
            try:
                meta, _ = _parse_frontmatter(
                    existing.read_text(encoding="utf-8", errors="replace"))
                if meta.get("url", "").strip() == url:
                    return {
                        "ok":        True,
                        "duplicate": True,
                        "id":        existing.stem,
                        "filename":  existing.name,
                        "title":     meta.get("title", existing.stem),
                        "url":       url,
                        "saved":     meta.get("saved", ""),
                    }
            except (OSError, ValueError, KeyError) as e:
                log.debug("Skipping unreadable inbox file %s: %s", existing.name, e)

    # Save immediately with fetch_failed; background thread fetches and patches the file.
    needs_fetch = bool(url and not content)

    # Final title fallback
    if not title:
        title = url.rstrip("/").split("/")[-1].split("?")[0].replace("-", " ").replace("_", " ") or url

    # Build unique slug filename
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:60].strip("-") or "article"
    base_name = f"{slug}.md"
    dest = inbox_dir / base_name
    if dest.exists():
        import time as _t
        base_name = f"{slug}-{int(_t.time())}.md"
        dest = inbox_dir / base_name

    # Write file with YAML frontmatter
    today = datetime.date.today().isoformat()
    now   = datetime.datetime.now().isoformat(timespec="seconds")
    fm    = ["---", f'title: "{title}"']
    if url:
        fm.append(f"url: {url}")
    fm.append(f"saved: {today}")
    fm.append(f"added: {now}")
    fm.append(f"wikified: false")
    fm.append(f"source: {source}")
    if needs_fetch:
        fm.append(f"fetch_failed: true")
    if author:
        fm.append(f"author: {author}")
    if tags:
        fm.append(f"tags: {json.dumps(tags)}")
    fm += ["---", ""]

    body = content or ""
    _atomic_write(dest, "\n".join(fm) + body)

    if needs_fetch:
        _fetch_and_patch(dest, url)

    return {
        "ok":          True,
        "duplicate":   False,
        "id":          dest.stem,
        "filename":    base_name,
        "title":       title,
        "url":         url or None,
        "saved":       today,
        "fetch_failed": needs_fetch,
    }, 201, {"Access-Control-Allow-Origin": "*"}


@app.route("/api/inbound-email", methods=["POST"])
def api_inbound_email():
    """
    Resend inbound email webhook — saves the email as an inbox item.

    Configure in Resend dashboard:
      Webhook URL: https://your-domain/api/inbound-email
      Signing secret: set as api.resend_webhook_secret in config.json

    The email subject becomes the article title.
    If the plain-text body contains a URL on its own line, that URL is stored
    and the page is fetched automatically (same as the browser extension).
    Otherwise the email body is stored as article content directly.

    To send an article: email your inbound address with the URL in the body,
    or forward an email with a readable body.
    """
    log.debug("Inbound email webhook received")
    webhook_secret = cfg_get("api", "resend_webhook_secret", "").strip()
    log.debug("Webhook secret configured: %s", bool(webhook_secret))

    # Verify Resend/Svix webhook signature if secret is configured
    if webhook_secret:
        try:
            import hashlib, hmac, base64, time as _time
            msg_id        = request.headers.get("svix-id", "")
            msg_timestamp = request.headers.get("svix-timestamp", "")
            msg_signature = request.headers.get("svix-signature", "")
            log.debug("Verifying webhook signature: id=%s timestamp=%s sig_present=%s",
                     msg_id[:8] if msg_id else "missing",
                     msg_timestamp,
                     bool(msg_signature))
            if not (msg_id and msg_timestamp and msg_signature):
                log.warning("Inbound email: missing webhook signature headers")
                return {"error": "Missing webhook signature headers"}, 401
            # Reject timestamps older than 5 minutes
            try:
                ts = int(msg_timestamp)
                if abs(_time.time() - ts) > 300:
                    log.warning("Inbound email: webhook timestamp too old (%s)", msg_timestamp)
                    return {"error": "Webhook timestamp too old"}, 401
            except ValueError:
                log.warning("Inbound email: invalid timestamp format")
                return {"error": "Invalid timestamp"}, 401
            signed_content = f"{msg_id}.{msg_timestamp}.{request.get_data(as_text=True)}"
            secret_bytes   = base64.b64decode(webhook_secret.removeprefix("whsec_"))
            expected       = base64.b64encode(
                hmac.new(secret_bytes, signed_content.encode(), hashlib.sha256).digest()
            ).decode()
            # svix-signature may contain multiple comma-separated "v1,<sig>" entries
            sigs = [s.split(",", 1)[1] for s in msg_signature.split(" ") if "," in s]
            if not any(hmac.compare_digest(expected, s) for s in sigs):
                log.warning("Inbound email: signature verification failed")
                return {"error": "Invalid signature"}, 401
            log.debug("Webhook signature verified successfully")
        except Exception as e:
            log.warning("Inbound email signature check failed: %s", e, exc_info=True)
            return {"error": "Signature verification error"}, 401

    payload = request.get_json(silent=True) or {}
    log.debug("Inbound email payload keys: %s", list(payload.keys()))

    # Resend wraps email fields under payload['data']
    data = payload.get("data") or payload
    log.debug("Inbound email data keys: %s", list(data.keys()) if isinstance(data, dict) else type(data).__name__)

    # Debug: log full data structure for troubleshooting (redact sensitive info)
    safe_data = {k: v for k, v in data.items() if k not in ('from', 'to', 'cc', 'bcc')}
    log.debug("Inbound email data (truncated): %s", str(safe_data)[:500])

    # Reject if a magic inbound address is configured and this email wasn't sent to it
    inbound_address = cfg_get("api", "resend_inbound_address", "").strip().lower()
    if inbound_address:
        to_raw = data.get("to") or data.get("To") or ""
        # Resend sends 'to' as a list of email addresses
        if isinstance(to_raw, list):
            to_field = " ".join(to_raw).lower()
        else:
            to_field = str(to_raw).lower()
        log.debug("Inbound address filter enabled: %s, to_field: %s", inbound_address, to_field)
        if inbound_address not in to_field:
            log.info("Inbound email filtered: not sent to configured inbound address (%s)", inbound_address)
            return {"ok": True}  # silently discard spam

    # Resend inbound email payload fields
    subject   = (data.get("subject") or data.get("Subject") or "").strip()
    from_addr = (data.get("from")    or data.get("From")    or "").strip()
    text_body = (data.get("text")    or data.get("plain")   or data.get("body") or "").strip()
    email_id  = data.get("email_id", "")

    # Resend webhook doesn't include body — fetch from API if email_id available
    if not text_body and email_id:
        log.debug("No body in webhook, fetching from Resend API using email_id: %s", email_id)
        try:
            import urllib.request
            import urllib.error
            resend_api_key = cfg_get("email", "resend_api_key", "").strip()
            if resend_api_key:
                headers = {
                    "Authorization": f"Bearer {resend_api_key}",
                    "Accept": "application/json",
                }
                req = urllib.request.Request(
                    f"https://api.resend.com/emails/{email_id}",
                    headers=headers
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    import json as _json
                    email_data = _json.loads(resp.read().decode())
                    text_body = (email_data.get("text") or "").strip()
                    if not text_body:
                        html_body = (email_data.get("html") or "").strip()
                        if html_body:
                            text_body = re.sub(r"<[^>]+>", " ", html_body)
                            text_body = re.sub(r"\s{2,}", " ", text_body).strip()
                    log.debug("Fetched from API, body_len: %d", len(text_body))
            else:
                log.warning("Inbound email has no body and no Resend API key configured")
        except Exception as e:
            log.warning("Failed to fetch email from Resend API: %s", e)

    # Fall back to HTML body with tags stripped if plain text is absent
    if not text_body:
        html_body = (data.get("html") or data.get("Html") or "").strip()
        if html_body:
            text_body = re.sub(r"<[^>]+>", " ", html_body)
            text_body = re.sub(r"\s{2,}", " ", text_body).strip()
            log.debug("Using HTML from webhook, stripped length: %d", len(text_body))

    # Log attachments info for debugging
    attachments = data.get("attachments") or []
    if attachments:
        log.debug("Email has %d attachment(s): %s", len(attachments),
                 [f"{a.get('filename', 'unknown')} ({a.get('content_type', 'unknown')})"
                  for a in (attachments if isinstance(attachments, list) else [])][:5])

    log.debug("Inbound email: subject=%s, from=%s, body_len=%d",
             subject[:60] if subject else "(empty)",
             from_addr,
             len(text_body))

    if not subject and not text_body:
        log.warning("Inbound email: rejected as empty (no subject and no body)")
        return {"error": "Empty email"}, 400

    title = subject or "Email article"

    # Look for a bare URL in the first few lines of the body
    url = ""
    body_content = text_body
    if text_body:
        for line in text_body.splitlines()[:10]:
            line = line.strip()
            if re.match(r"https?://\S+$", line):
                url = line
                log.debug("Found URL in email body: %s", url)
                break

    # Fetch the URL if found, otherwise use the text body as content
    content = ""
    if url:
        log.debug("Attempting to fetch URL content from: %s", url)
        fetched, fetch_err = _clip_fetch(url)
        if fetched:
            content = fetched
            log.debug("URL fetch succeeded, content length: %d", len(content))
            if not subject:
                for line in content.splitlines():
                    line = line.strip().lstrip("#").strip()
                    if line and not line.startswith("<"):
                        title = line[:120]
                        log.debug("Auto-extracted title from fetched content: %s", title[:60])
                        break
        else:
            log.debug("URL fetch failed (%s), using email body as content", fetch_err)
            content = text_body
    else:
        log.debug("No URL found in email body, using body as content")
        content = text_body

    # Deduplication on URL
    inbox_dir = RAW_DIR
    if url:
        log.debug("Checking for duplicate URL in raw/")
        for existing in inbox_dir.glob("*.md"):
            if existing.name == "index.md":
                continue
            try:
                meta, _ = _parse_frontmatter(
                    existing.read_text(encoding="utf-8", errors="replace"))
                if meta.get("url", "").strip() == url:
                    log.info("Inbound email duplicate: %s (existing file: %s)", url, existing.name)
                    return {"ok": True, "duplicate": True, "filename": existing.name}, 200
            except (OSError, ValueError, KeyError) as e:
                log.debug("Error checking duplicate for %s: %s", existing.name, e)

    # Build filename
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:60].strip("-") or "email"
    base_name = f"{slug}.md"
    dest = inbox_dir / base_name
    if dest.exists():
        import time as _t
        base_name = f"{slug}-{int(_t.time())}.md"
        dest = inbox_dir / base_name
    log.debug("Will save to: %s", dest)

    today = datetime.date.today().isoformat()
    fm = ["---", f'title: "{title}"']
    if url:
        fm.append(f"url: {url}")
    fm.append(f"saved: {today}")
    fm.append(f"added: {today}")
    fm.append(f"wikified: false")
    fm.append(f"source: email")
    if from_addr:
        fm.append(f"author: {from_addr}")
    fm += ["---", ""]

    try:
        dest.write_text("\n".join(fm) + content, encoding="utf-8")
        log.info("Inbound email saved: %s from %s (size: %d bytes)", base_name, from_addr, len("\n".join(fm) + content))
        return {"ok": True, "duplicate": False, "filename": base_name}, 201
    except Exception as e:
        log.error("Failed to save inbound email: %s", e, exc_info=True)
        return {"error": "Failed to save email"}, 500


@app.route("/api/inbox")
def api_inbox_list():
    """
    List items currently in the inbox.

    Auth: Authorization: Bearer <push_key>

    Query params:
      limit   int    Max items to return (default 20, max 100).
      since   date   ISO date — only return items saved on or after this date.
      source  str    Filter by source application name.
    """
    ok, err = _api_auth()
    if not ok:
        return err

    limit  = min(max(int(request.args.get("limit", 20)), 1), 100)
    since  = request.args.get("since",  "").strip()
    source = request.args.get("source", "").strip()

    items = []
    inbox_dir = RAW_DIR
    if inbox_dir.is_dir():
        candidates = sorted(
            [f for f in inbox_dir.glob("*.md") if f.name != "index.md"],
            key=lambda f: -f.stat().st_mtime)
        for f in candidates:
            try:
                meta, _ = _parse_frontmatter(
                    f.read_text(encoding="utf-8", errors="replace"))
                saved = meta.get("saved", "")
                if since and saved and saved < since:
                    continue
                if source and meta.get("source", "") != source:
                    continue
                items.append({
                    "id":       f.stem,
                    "filename": f.name,
                    "title":    meta.get("title", f.stem),
                    "url":      meta.get("url")    or None,
                    "saved":    saved,
                    "source":   meta.get("source", ""),
                    "author":   meta.get("author", "") or None,
                    "tags":     meta.get("tags")   or [],
                })
                if len(items) >= limit:
                    break
            except (OSError, ValueError, KeyError) as e:
                log.debug("Skipping unreadable inbox file %s: %s", f.name, e)

    return {"ok": True, "items": items, "count": len(items)}


@app.route("/api/inbox/<path:filename>", methods=["DELETE"])
def api_inbox_delete(filename):
    """
    Delete an item from the inbox by filename.

    Auth: Authorization: Bearer <push_key>

    Only non-archived items in raw/ can be deleted this way.
    """
    ok, err = _api_auth()
    if not ok:
        return err

    p = RAW_DIR / filename
    try:
        p.resolve().relative_to(RAW_DIR.resolve())
    except ValueError:
        return {"error": "Invalid filename", "code": "INVALID_PATH"}, 400

    if not p.exists():
        return {"error": "Item not found", "code": "NOT_FOUND"}, 404

    p.unlink()
    return {"ok": True, "deleted": filename}


@app.route("/inbox/clip")
@require_login
def inbox_clip():
    """
    Browser bookmarklet / iOS Shortcut endpoint.
    GET /inbox/clip?url=...&title=...
    Fetches the article, saves full content as .md (falls back to .url if fetch fails).
    Returns a lightweight dark-mode confirmation page.
    """
    url   = request.args.get("url",   "").strip()
    title = request.args.get("title", "").strip()
    if not url:
        return "Missing url parameter", 400

    display_title = title or url
    slug_src = title if title else url.split("//")[-1].split("?")[0]
    slug = re.sub(r"[^a-z0-9]+", "-", slug_src.lower())[:60].strip("-") or "clipping"

    def _unique(base_name):
        dest = RAW_DIR / base_name
        if not dest.exists():
            return base_name, dest
        stem, ext = base_name.rsplit(".", 1)
        import time as _t
        name = f"{stem}-{int(_t.time())}.{ext}"
        return name, RAW_DIR / name

    # Try to fetch full article content
    text, fetch_err = _clip_fetch(url)
    read_url = None

    if text:
        base_name, dest = _unique(f"{slug}.md")
        today = datetime.date.today().isoformat()
        md_content = (
            f'---\ntitle: "{display_title}"\nurl: {url}\nsaved: {today}\nadded: {today}\nwikified: false\n---\n\n'
            f'{text}'
        )
        dest.write_text(md_content, encoding="utf-8")
        read_url = url_for("inbox_read", filename=base_name)
        status_msg = "Saved with full content"
    else:
        base_name, dest = _unique(f"{slug}.url")
        dest.write_text(f"{display_title}\nURL: {url}\n", encoding="utf-8")
        status_msg = f"URL saved — offline reading unavailable ({fetch_err or 'fetch failed'})"

    inbox_url = url_for("inbox")
    read_link = f'<a href="{read_url}">Read now</a>' if read_url else ""
    return f"""<!doctype html>
<html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
  body{{font-family:-apple-system,sans-serif;background:#000;color:#f2f2f7;
       display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:16px;box-sizing:border-box}}
  .card{{background:#1c1c1e;border-radius:16px;padding:28px 24px;max-width:340px;width:100%;text-align:center}}
  h1{{font-size:22px;margin:0 0 6px}}
  .sub{{color:#8e8e93;font-size:13px;margin:0 0 20px;word-break:break-word}}
  a{{display:block;text-decoration:none;border-radius:10px;padding:12px;
     font-weight:600;font-size:16px;margin-bottom:10px}}
  .pri{{background:#0a84ff;color:#fff}}
  .sec{{background:#2c2c2e;color:#f2f2f7}}
</style></head>
<body><div class="card">
  <h1>Saved</h1>
  <p class="sub">{display_title[:80]}</p>
  {read_link}
  <a class="pri" href="{inbox_url}">Reading</a>
  <a class="sec" href="javascript:window.close()">Close</a>
</div>
<script>
setTimeout(()=>window.close(),2000)
</script>
</body></html>"""


@app.route("/inbox/read/<path:filename>")
@require_login
def inbox_read(filename):
    p = RAW_DIR / filename
    try:
        p.resolve().relative_to(RAW_DIR.resolve())
    except ValueError:
        abort(404)
    if not p.exists():
        abort(404)
    import markdown as _md
    text = p.read_text(encoding="utf-8", errors="replace")
    meta, body = _parse_frontmatter(text)
    body_html = _md.markdown(body, extensions=["extra", "nl2br"])
    return render_template(
        "reader.html",
        title     = meta.get("title", p.stem),
        url       = meta.get("url", ""),
        saved     = meta.get("saved", ""),
        body_html = body_html,
        filename  = filename,
    )


@app.route("/inbox/add", methods=["POST"])
@require_login
def inbox_add():
    data    = request.get_json(silent=True) or {}
    content = (data.get("content")  or "").strip()
    name    = (data.get("filename") or "").strip()
    if not content:
        return {"error": "Empty content"}, 400
    # If the content is just a bare URL, save immediately and fetch in background
    is_url = content.startswith("http") and "\n" not in content and " " not in content.strip()
    if is_url:
        url = content
        title = url.rstrip("/").split("/")[-1].split("?")[0].replace("-", " ").replace("_", " ") or url
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:60].strip("-") or "article"
        base_name = name or f"{slug}.md"
        dest = RAW_DIR / base_name
        today = datetime.date.today().isoformat()
        now   = datetime.datetime.now().isoformat(timespec="seconds")
        fm    = ["---", f'title: "{title}"', f"url: {url}", f"saved: {today}",
                 f"added: {now}", "wikified: false", "source: manual", "fetch_failed: true", "---", ""]
        _atomic_write(dest, "\n".join(fm))
        _fetch_and_patch(dest, url)
        return {"ok": True, "filename": dest.name, "fetch_failed": True}
    if not name:
        slug = re.sub(r"[^a-z0-9]+", "-", content[:60].lower()).strip("-")
        name = f"{slug}.txt"
    dest = RAW_DIR / name
    dest.write_text(content, encoding="utf-8")
    return {"ok": True, "filename": name}


@app.route("/inbox/delete", methods=["POST"])
@require_login
def inbox_delete():
    data = request.get_json(silent=True) or {}
    name = (data.get("filename") or "").strip()
    if not name:
        return {"error": "No filename"}, 400
    p = RAW_DIR / name
    try:
        p.resolve().relative_to(RAW_DIR.resolve())
    except ValueError:
        return {"error": "Invalid path"}, 400
    if p.exists():
        p.unlink()
    return {"ok": True}


@app.route("/inbox/process-all", methods=["POST"])
@require_login
def inbox_process_all():
    """Process all unprocessed inbox items sequentially via the job queue."""
    global _batch_running
    if _batch_running:
        return {"error": "A batch is already running."}, 409
    unprocessed = [
        item for item in list_inbox()
        if not item.get("wikified") and not item.get("archived")
    ]
    if not unprocessed:
        return {"queued": 0}

    client, model, error = get_client_and_model()
    if error:
        return {"error": error}, 503

    import re as _re
    import agent as _agent

    _batch_running = True
    _batch_succeeded: list = []
    _batch_failed:    list = []

    def _submit_item(items, index):
        """Submit one item; on_done sets globals then submits the next."""
        global _batch_running
        if index >= len(items):
            _batch_running = False
            log.info(
                "inbox/process-all: done — %d succeeded, %d failed%s",
                len(_batch_succeeded),
                len(_batch_failed),
                ("; failed: " + ", ".join(_batch_failed)) if _batch_failed else "",
            )
            return
        item = items[index]
        filename = item["filename"]
        inbox_path = RAW_DIR / Path(filename).name
        try:
            file_content = inbox_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log.warning("inbox/process-all: cannot read %s: %s", filename, e)
            _submit_item(items, index + 1)
            return

        inbox_path_str = str(inbox_path.resolve().relative_to(REPO_ROOT.resolve()))
        inbox_url = ""
        stripped = file_content.strip()
        if stripped.startswith("http") and "\n" not in stripped:
            inbox_url = stripped
        else:
            _m = _re.search(r'^url:\s*["\']?([^\s"\'\n]+)', file_content, _re.MULTILINE)
            if _m:
                inbox_url = _m.group(1).strip()

        def _setup(_p=inbox_path_str, _u=inbox_url):
            _agent.init_session(inbox_path=_p, inbox_url=_u)

        history = [
            {"role": "user",      "content": orientation_message()},
            {"role": "assistant", "content": "Oriented. Ready."},
            {"role": "user",      "content": (
                f'Ingest raw/{inbox_path.name}.\n\n'
                f'<file path="raw/{inbox_path.name}">\n{file_content}\n</file>'
            )},
        ]

        def on_done(messages, _fname=filename):
            save_history(messages, source="inbox")
            ingested = any(
                isinstance(m.get("content"), str) and m["content"].startswith("__ingested__:1")
                for m in messages
                if m.get("role") == "system"
            )
            if ingested:
                raw_path = RAW_DIR / Path(_fname).name
                try:
                    raw_fm, _ = _parse_frontmatter(raw_path.read_text(encoding="utf-8"))
                    if raw_fm.get("fetch_failed"):
                        log.warning("inbox/process-all: refusing to mark wikified — fetch_failed=true in %s", _fname)
                        ingested = False
                except Exception:
                    pass
            if ingested:
                _mark_inbox_wikified(_fname)
                _batch_succeeded.append(_fname)
                log.info("inbox/process-all: marked wikified %s", _fname)
            else:
                _batch_failed.append(_fname)
                log.warning("inbox/process-all: no __ingested__:1 for %s — not marking wikified", _fname)
            # Submit the next item now that this one is done.
            _submit_item(items, index + 1)

        job_id = job_queue.submit(client, model, history, system_prompt(), on_done=on_done, setup=_setup)
        log.info("inbox/process-all: submitted %s as job %s", filename, job_id)

    try:
        _submit_item(unprocessed, 0)
    except Exception:
        _batch_running = False
        raise
    return {"queued": len(unprocessed)}


@app.route("/inbox/view/<path:filename>")
@require_login
def inbox_view(filename):
    p = RAW_DIR / filename
    try:
        p.resolve().relative_to(RAW_DIR.resolve())
    except ValueError:
        abort(404)
    if not p.exists():
        abort(404)
    content = p.read_text(encoding="utf-8", errors="replace")
    # For .url files, expose the URL separately so the UI can open it
    if p.suffix == ".url":
        url_line = next((l for l in content.splitlines() if l.startswith("URL:")), "")
        url = url_line[4:].strip()
        return {"content": content, "url": url}
    # Strip YAML frontmatter before returning to the inline reader
    import markdown as _md
    meta, body = _parse_frontmatter(content)
    source_url = meta.get("url", "") or None
    body = body.strip()
    html = _md.markdown(body, extensions=["extra", "nl2br"]) if body else ""
    return {"content": body, "html": html, "url": source_url}


@app.route("/inbox/debug-fetch")
@require_login
def inbox_debug_fetch():
    """Debug endpoint: test what _clip_fetch returns for a URL."""
    url = request.args.get("url", "").strip()
    if not url:
        return {"error": "Missing url parameter"}, 400
    text, err = _clip_fetch(url)
    return {
        "url": url,
        "success": text is not None,
        "error": err,
        "text_length": len(text) if text else 0,
        "text_preview": (text[:200] if text else "") if text and not text.startswith('�') else "[binary or error]",
        "starts_with_gzip": text.startswith('\x1f\x8b') if text else False,
    }


@app.route("/inbox/archive", methods=["POST"])
@require_login
def inbox_archive():
    data = request.get_json(silent=True) or {}
    name = (data.get("filename") or "").strip()
    if not name:
        return {"error": "No filename"}, 400
    src = RAW_DIR / name
    try:
        src.resolve().relative_to(RAW_DIR.resolve())
    except ValueError:
        return {"error": "Invalid path"}, 400
    if not src.exists():
        return {"error": "File not found"}, 404
    try:
        import json as _json
        text = src.read_text(encoding="utf-8", errors="replace")
        fm, body = _parse_frontmatter(text)
        fm["archived"] = True
        fm_lines = ["---"]
        for k, v in fm.items():
            if isinstance(v, list):
                fm_lines.append(f"{k}: {_json.dumps(v)}")
            elif isinstance(v, bool):
                fm_lines.append(f"{k}: {'true' if v else 'false'}")
            else:
                sv = str(v)
                fm_lines.append(f"{k}: {_json.dumps(sv) if (chr(34) in sv or ':' in sv) else sv}")
        fm_lines.append("---")
        src.write_text("\n".join(fm_lines) + "\n" + body, encoding="utf-8")
        _rebuild_index({})
    except Exception as e:
        log.error("inbox_archive failed for %s: %s", name, e)
        return {"error": str(e)}, 500
    return {"ok": True}


@app.route("/inbox/unarchive", methods=["POST"])
@require_login
def inbox_unarchive():
    import json as _json
    data = request.get_json(silent=True) or {}
    name = (data.get("filename") or "").strip()
    if not name:
        return {"error": "No filename"}, 400
    p = RAW_DIR / name
    try:
        p.resolve().relative_to(RAW_DIR.resolve())
    except ValueError:
        return {"error": "Invalid path"}, 400
    if not p.exists():
        return {"error": "File not found"}, 404
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        fm, body = _parse_frontmatter(text)
        fm.pop("archived", None)
        fm_lines = ["---"]
        for k, v in fm.items():
            if isinstance(v, list):
                fm_lines.append(f"{k}: {_json.dumps(v)}")
            elif isinstance(v, bool):
                fm_lines.append(f"{k}: {'true' if v else 'false'}")
            else:
                sv = str(v)
                fm_lines.append(f"{k}: {_json.dumps(sv) if (chr(34) in sv or ':' in sv) else sv}")
        fm_lines.append("---")
        p.write_text("\n".join(fm_lines) + "\n" + body, encoding="utf-8")
        _rebuild_index({})
    except Exception as e:
        log.error("inbox_unarchive failed for %s: %s", name, e)
        return {"error": str(e)}, 500
    return {"ok": True}


@app.route("/inbox/mark-wikified", methods=["POST"])
@require_login
def inbox_mark_wikified():
    data = request.get_json(silent=True) or {}
    name = (data.get("filename") or "").strip()
    if not name:
        return {"error": "No filename"}, 400
    p = RAW_DIR / name
    try:
        p.resolve().relative_to(RAW_DIR.resolve())
    except ValueError:
        return {"error": "Invalid path"}, 400
    if not p.exists():
        return {"error": "File not found"}, 404
    _mark_inbox_wikified(name)
    # Find the matching wiki/sources/ page to return a direct link
    # First check if wiki_page was stamped directly into the raw file's frontmatter
    wiki_path = ""
    try:
        fm, _ = _parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        wiki_path = fm.get("wiki_page", "")
    except Exception:
        pass
    if not wiki_path:
        raw_rel = f"raw/{name}"
        for wf in (WIKI_DIR / "sources").glob("*.md") if (WIKI_DIR / "sources").is_dir() else []:
            try:
                wm, _ = _parse_frontmatter(wf.read_text(encoding="utf-8", errors="replace"))
                if wm.get("raw_source") == raw_rel:
                    wiki_path = str(wf.relative_to(WIKI_DIR))
                    break
            except Exception:
                pass
    return {"ok": True, "wiki_path": wiki_path}

@app.route("/wiki/debug-sources")
@require_login
def debug_sources():
    """Debug endpoint: shows raw/ state and how wiki/sources/ pages would match."""
    raw_sources_dir = RAW_DIR
    wiki_sources_dir = WIKI_DIR / "sources"

    raw_files = []
    if raw_sources_dir.is_dir():
        for f in sorted(raw_sources_dir.iterdir()):
            if f.is_file() and not f.name.startswith(".") and f.name != "index.md":
                try:
                    meta, _ = _parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
                    raw_files.append({"name": f.name, "stem": f.stem, "url": meta.get("url", ""),
                                      "wikified": bool(meta.get("wikified"))})
                except Exception as e:
                    raw_files.append({"name": f.name, "stem": f.stem, "url": f"[error: {e}]",
                                      "wikified": False})

    wiki_pages = []
    if wiki_sources_dir.is_dir():
        for f in sorted(wiki_sources_dir.glob("*.md")):
            try:
                meta, _ = _parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
                wiki_url = meta.get("url", "").strip()
                matched = None
                for r in raw_files:
                    if wiki_url and r["url"].strip() == wiki_url:
                        matched = f"url:{r['name']}"
                        break
                if not matched:
                    for r in raw_files:
                        if r["stem"] == f.stem:
                            matched = f"stem:{r['name']}"
                            break
                wiki_pages.append({"name": f.name, "url": wiki_url, "matched_raw": matched})
            except Exception as e:
                wiki_pages.append({"name": f.name, "url": "", "matched_raw": f"[error: {e}]"})

    return {
        "raw_dir_exists": raw_sources_dir.is_dir(),
        "raw_dir_path": str(raw_sources_dir),
        "raw_files": raw_files,
        "wiki_sources_dir_exists": wiki_sources_dir.is_dir(),
        "wiki_pages": wiki_pages,
    }


@app.route("/raw/<path:filename>")
@require_login
def raw_source_file(filename):
    p = RAW_DIR / filename
    try:
        p.resolve().relative_to(RAW_DIR.resolve())
    except ValueError:
        abort(404)
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="text/plain; charset=utf-8", as_attachment=False)


@app.route("/inbox/edit", methods=["POST"])
@require_login
def inbox_edit():
    data    = request.get_json(silent=True) or {}
    name    = (data.get("filename") or "").strip()
    content = (data.get("content")  or "").strip()
    if not name:
        return {"error": "No filename"}, 400
    p = RAW_DIR / name
    try:
        p.resolve().relative_to(RAW_DIR.resolve())
    except ValueError:
        return {"error": "Invalid path"}, 400
    if not p.exists():
        return {"error": "File not found"}, 404
    if p.suffix == ".md":
        existing = p.read_text(encoding="utf-8", errors="replace")
        # Preserve the frontmatter block, replace only the body
        m = re.match(r'^---\n.*?\n---\n', existing, re.DOTALL)
        if m:
            fm_block = existing[:m.end()]
            # If user pasted content, clear fetch_failed so Read/Wikify become available
            if content.strip():
                fm_block = re.sub(r'^fetch_failed:.*\n', '', fm_block, flags=re.MULTILINE)
            p.write_text(fm_block + "\n" + content, encoding="utf-8")
        else:
            p.write_text(content, encoding="utf-8")
    elif p.suffix == ".url":
        # User pasted article text into a URL-only item — promote to .md with frontmatter
        existing = p.read_text(encoding="utf-8", errors="replace")
        lines = [l.strip() for l in existing.splitlines() if l.strip()]
        title = lines[0] if lines else p.stem
        url_line = next((l for l in lines if l.startswith("URL:")), "")
        url_val = url_line[4:].strip() if url_line else ""
        today = datetime.date.today().isoformat()
        md_name = p.stem + ".md"
        md_path = p.parent / md_name
        fm = f'---\ntitle: "{title}"\n'
        if url_val:
            fm += f'url: {url_val}\n'
        fm += f'saved: {today}\n---\n\n'
        md_path.write_text(fm + content, encoding="utf-8")
        p.unlink()
        return {"ok": True, "filename": md_name}
    else:
        p.write_text(content, encoding="utf-8")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Blog routes
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Inbox process-all state
# ---------------------------------------------------------------------------

_batch_running = False  # True while process-all is executing



# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

job_queue = JobQueue(WIKI_DIR / ".jobs")


def _migrate_raw_subdirs() -> None:
    """One-time migration: move files from raw/inbox/ and raw/sources/ into raw/."""
    for subdir_name in ("inbox", "sources"):
        subdir = RAW_DIR / subdir_name
        if not subdir.is_dir():
            continue
        moved = 0
        for f in list(subdir.iterdir()):
            if not f.is_file():
                continue
            dest = RAW_DIR / f.name
            if dest.exists():
                import time as _t
                dest = RAW_DIR / f"{f.stem}-migrated-{int(_t.time())}{f.suffix}"
            f.rename(dest)
            moved += 1
            log.info("Migration: moved %s/%s -> raw/%s", subdir_name, f.name, dest.name)
        if moved > 0:
            log.info("Migration: moved %d files from raw/%s/ to raw/", moved, subdir_name)
        try:
            subdir.rmdir()
            log.info("Migration: removed empty raw/%s/", subdir_name)
        except OSError:
            log.warning("Migration: raw/%s/ not empty after migration, leaving it", subdir_name)


if __name__ == "__main__":
    host = cfg_get("server", "host", "127.0.0.1")
    port = cfg_int("server", "port", default=8080)

    _migrate_raw_subdirs()

    issues = validate_config()
    for level, msg in issues:
        prefix = "ERROR" if level == "error" else "WARNING"
        print(f"[{prefix}] {msg}")

    errors = [m for l, m in issues if l == "error"]
    if errors:
        print("\nFix these errors and restart.")
        sys.exit(1)

    heal_index_if_stale()

    if not user_exists():
        print(f"[INFO] No account found. Visit http://{host}:{port}/setup to create one.")

    provider = cfg_get("llm", "provider", "openai")
    print(f"\nLobotomy  http://{host}:{port}  (provider: {provider})\n")
    app.run(host=host, port=port, debug=False, threaded=True)
