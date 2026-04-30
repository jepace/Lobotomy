#!/usr/bin/env python3
"""
Lobotomy — Web server

Mobile-friendly web app: chat with AI, browse wiki, manage tasks, capture articles.

Requirements:
  pip install flask markdown openai resend
  -- or on FreeBSD --
  pkg install py311-flask py311-markdown && pip install openai resend

Configuration: copy config.example.json to config.json and edit it.

Usage:
  python3 tools/serve.py
"""

import datetime
import functools
import json
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
                       stream_with_context, url_for, send_file)
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
from config import cfg_get, cfg_bool, cfg_int, validate_config
from agent import (REPO_ROOT, WIKI_DIR, RAW_DIR,
                   get_client_and_model, orientation_message,
                   stream_agent_turn, system_prompt)

BLOG_DIR = REPO_ROOT / "blog"
from job_queue import JobQueue
from task_manager import read_tasks, write_tasks, get_all_contexts, get_all_projects
from auth  import (user_exists, create_user, authenticate, get_user, update_password,
                   set_verified, create_token, consume_token, record_attempt,
                   is_locked_out, send_verification_email, send_reset_email,
                   maybe_send_verification, _resend_ready)

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
    elif path.startswith("/tasks"):
        active = "tasks"
    elif path.startswith("/inbox"):
        active = "inbox"
    elif path.startswith("/blog"):
        active = "blog"
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


def _rfc822(date_str: str) -> str:
    try:
        d = datetime.date.fromisoformat(str(date_str))
        return d.strftime("%a, %d %b %Y 00:00:00 +0000")
    except (ValueError, TypeError):
        return ""


def _blog_posts(published_only: bool = True) -> list:
    BLOG_DIR.mkdir(parents=True, exist_ok=True)
    posts = []
    for f in sorted(BLOG_DIR.glob("*.md"), reverse=True):
        text = f.read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(text)
        if published_only and not meta.get("published", False):
            continue
        posts.append({
            "slug":      f.stem,
            "title":     meta.get("title", f.stem),
            "date":      meta.get("date", ""),
            "tags":      meta.get("tags", []),
            "summary":   meta.get("summary", ""),
            "published": meta.get("published", False),
        })
    return posts

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
# Chat history
# ---------------------------------------------------------------------------

HISTORY_FILE = WIKI_DIR / ".chat_history.json"
MAX_HISTORY  = 80


def load_history() -> list:
    if HISTORY_FILE.exists():
        try:
            messages = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            # Backfill 'name' in tool messages from preceding assistant tool_calls.
            # Needed because old history was saved without the name field,
            # and Gemini requires it.
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
            return messages
        except Exception:
            pass
    return []


def save_history(messages: list) -> None:
    HISTORY_FILE.write_text(
        json.dumps(messages[-MAX_HISTORY:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_history() -> None:
    if HISTORY_FILE.exists():
        HISTORY_FILE.unlink()

# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

_MD_EXTENSIONS = ["tables", "toc", "fenced_code", "attr_list"]


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
        r'href="([^"]*\.md[^"]*)"',
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
# Task helpers
# ---------------------------------------------------------------------------

def parse_tasks() -> list:
    tasks_file = WIKI_DIR / "tasks.md"
    if not tasks_file.exists():
        return []
    lines        = tasks_file.read_text(encoding="utf-8").splitlines()
    tasks        = []
    current_sect = "Inbox"
    today        = datetime.date.today().isoformat()
    for i, line in enumerate(lines):
        if line.startswith("## "):
            current_sect = line[3:].strip()
            continue
        m = re.match(r"^(\s*)-\s+\[([ x])\]\s+(.+)$", line)
        if not m:
            continue
        indent, checked, text = m.groups()
        p   = re.search(r"#p:(\w+)",     text)
        d   = re.search(r"#due:(\S+)",   text)
        c   = re.search(r"#ctx:(\w+)",   text)
        s   = re.search(r"#s:(\w+)",     text)
        st  = re.search(r"#start:(\S+)", text)
        lg  = re.search(r"#len:(\S+)",   text)
        rep = re.search(r"#rep:(\S+)",   text)
        start = st.group(1) if st else ""
        # Hide tasks whose start date is in the future
        if start and start > today and checked != "x":
            continue
        tasks.append({
            "line":     i,
            "done":     checked == "x",
            "text":     re.sub(r"#\S+", "", text).strip(),
            "section":  current_sect,
            "indent":   len(indent) // 2,
            "priority": p.group(1) if p else "",
            "due":      d.group(1) if d else "",
            "context":  c.group(1) if c else "",
            "status":   s.group(1) if s else "",
            "start":    start,
            "length":   lg.group(1) if lg else "",
            "repeat":   rep.group(1) if rep else "",
            "star":     bool(re.search(r"#star\b", text)),
        })
    return tasks


def _next_due(rep: str, current_due: str, done_date: str) -> str:
    import calendar
    m = re.match(r"^(\d+)([dwmy])(\+?)$", rep.lower())
    if not m:
        return ""
    n, unit, after = int(m.group(1)), m.group(2), m.group(3) == "+"
    base_str = done_date if after else current_due
    try:
        base = datetime.date.fromisoformat(base_str)
    except (ValueError, TypeError):
        return ""
    if unit == "d":
        return (base + datetime.timedelta(days=n)).isoformat()
    if unit == "w":
        return (base + datetime.timedelta(weeks=n)).isoformat()
    if unit == "m":
        mo = base.month - 1 + n
        yr = base.year + mo // 12
        mo = mo % 12 + 1
        dy = min(base.day, calendar.monthrange(yr, mo)[1])
        return datetime.date(yr, mo, dy).isoformat()
    if unit == "y":
        try:
            return base.replace(year=base.year + n).isoformat()
        except ValueError:
            return base.replace(year=base.year + n, day=28).isoformat()
    return ""


def _toggle_task(line_num: int, action: str) -> bool:
    tasks_file = WIKI_DIR / "tasks.md"
    lines = tasks_file.read_text(encoding="utf-8").splitlines()
    if not (0 <= line_num < len(lines)):
        return False
    line = lines[line_num]
    if action == "complete" and "- [ ]" in line:
        today = datetime.date.today().isoformat()
        lines[line_num] = line.replace("- [ ]", "- [x]") + f" #done:{today}"
        rep_m = re.search(r"#rep:(\S+)", line)
        if rep_m:
            due_m    = re.search(r"#due:(\S+)", line)
            due      = due_m.group(1) if due_m else today
            next_due = _next_due(rep_m.group(1), due, today)
            if next_due:
                new_line = re.sub(r"\s*#done:\S+", "", line)
                if due_m:
                    new_line = re.sub(r"#due:\S+", f"#due:{next_due}", new_line)
                else:
                    new_line += f" #due:{next_due}"
                lines.insert(line_num + 1, new_line)
    elif action == "reopen" and "- [x]" in line:
        lines[line_num] = re.sub(r"\s*#done:\S+", "", line).replace("- [x]", "- [ ]")
    else:
        return False
    tasks_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def _add_task(text: str, section: str = "Inbox") -> None:
    tasks_file = WIKI_DIR / "tasks.md"
    if not tasks_file.exists():
        tasks_file.write_text("# Tasks\n\n## Inbox\n\n", encoding="utf-8")
    content = tasks_file.read_text(encoding="utf-8")
    entry   = f"- [ ] {text.strip()}\n"
    pattern = rf"(## {re.escape(section)}\n)"
    if re.search(pattern, content):
        content = re.sub(pattern, r"\1" + entry, content, count=1)
    else:
        content = content.rstrip("\n") + f"\n\n## {section}\n\n{entry}"
    tasks_file.write_text(content, encoding="utf-8")

# ---------------------------------------------------------------------------
# Inbox helpers
# ---------------------------------------------------------------------------

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
        "Accept-Encoding":         "gzip, deflate",
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
            ce  = resp.headers.get("Content-Encoding", "")
            raw = resp.read(1_000_000)
        # Decompress if needed — check both header and magic bytes
        # (some servers send gzip without declaring Content-Encoding)
        if raw.startswith(b'\x1f\x8b'):  # gzip magic bytes
            import gzip as _gzip
            try:
                raw = _gzip.decompress(raw)
            except Exception as e:
                raise Exception(f"gzip decompression failed: {e}")
        elif raw.startswith(b'\x78\x9c') or raw.startswith(b'\x78\xda'):  # deflate magic bytes
            import zlib as _zlib
            try:
                raw = _zlib.decompress(raw)
            except Exception as e:
                raise Exception(f"deflate decompression failed: {e}")
        elif "gzip" in ce.lower():
            import gzip as _gzip
            try:
                raw = _gzip.decompress(raw)
            except Exception as e:
                raise Exception(f"gzip decompression (header-based) failed: {e}")
        elif "deflate" in ce.lower():
            import zlib as _zlib
            try:
                raw = _zlib.decompress(raw)
            except Exception as e:
                raise Exception(f"deflate decompression (header-based) failed: {e}")
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return None, str(e)

    # Sanity check: if we still have gzipped data, something went wrong
    if raw.startswith(b'\x1f\x8b'):
        return None, "Failed to decompress gzipped content"

    if "html" in ct.lower():
        parser = _Reader()
        try:
            parser.feed(raw.decode("utf-8", errors="replace"))
        except Exception:
            pass
        text = re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()
        if not text:
            return None, "No text extracted — site may require JavaScript or be paywalled"
        return text[:100_000], None
    else:
        text = raw.decode("utf-8", errors="replace")[:100_000]
        if text.startswith('�'):  # Unicode replacement character, likely binary garbage
            return None, "Response appears to be binary or unreadable"
        return text, None


def list_inbox() -> list:
    inbox = RAW_DIR / "inbox"
    if not inbox.is_dir():
        return []
    items = []
    for f in sorted(inbox.iterdir(), key=lambda x: -x.stat().st_mtime):
        if not f.is_file() or f.name.startswith("."):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""

        has_content = False
        source_url  = ""
        if f.suffix == ".md":
            meta, _ = _parse_frontmatter(text)
            if meta.get("url"):
                has_content = True
                source_url  = meta.get("url", "")
                title       = meta.get("title", f.stem)[:100]
                excerpt     = source_url
            else:
                lines   = [l.strip() for l in text.splitlines() if l.strip()]
                title   = lines[0][:100] if lines else f.stem
                excerpt = " ".join(lines[1:4])[:200] if len(lines) > 1 else ""
        elif f.suffix == ".url":
            lines      = [l.strip() for l in text.splitlines() if l.strip()]
            title      = lines[0][:100] if lines else f.stem
            url_line   = next((l for l in lines if l.startswith("URL:")), "")
            source_url = url_line[4:].strip()
            excerpt    = source_url
        else:
            lines   = [l.strip() for l in text.splitlines() if l.strip()]
            title   = lines[0][:100] if lines else f.stem
            excerpt = " ".join(lines[1:4])[:200] if len(lines) > 1 else ""

        mtime = datetime.date.fromtimestamp(f.stat().st_mtime).isoformat()
        items.append({
            "name":        f.name,
            "title":       title,
            "excerpt":     excerpt,
            "date":        mtime,
            "has_content": has_content,
            "source_url":  source_url,
            "ext":         f.suffix,
        })
    return items

    return items

# ---------------------------------------------------------------------------
# Wiki navigation helpers
# ---------------------------------------------------------------------------

def wiki_sections() -> list:
    pages = []
    for name, label in [
        ("index.md",        "Index"),
        ("overview.md",     "Overview"),
        ("reading-list.md", "Reading List"),
        ("log.md",          "Log"),
    ]:
        if (WIKI_DIR / name).exists():
            pages.append({"path": name, "label": label})
    for d in ["sources", "entities", "concepts", "synthesis"]:
        if (WIKI_DIR / d).is_dir():
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
    history = load_history()
    display = [
        {"role": m["role"], "content": m.get("content", "")}
        for m in history
        if m["role"] in ("user", "assistant") and m.get("content")
    ]
    return render_template("chat.html", history=display)


@app.route("/chat/send", methods=["POST"])
@require_login
def chat_send():
    data    = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
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
    history.append({"role": "user", "content": message})

    job_id = job_queue.submit(client, model, history, sys_prompt,
                              on_done=save_history)
    return {"job_id": job_id}


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
    return {"ok": True}


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
    return render_template(
        "wiki.html",
        content=render_md(p),
        title=p.stem.replace("-", " ").title(),
        sections=wiki_sections(),
        current_path=str(p.relative_to(WIKI_DIR)),
    )


@app.route("/wiki/lint")
@require_login
def wiki_lint():
    """Find orphaned pages (created but not in index.md)."""
    index_path = WIKI_DIR / "index.md"
    if not index_path.exists():
        return render_template("wiki-lint.html", orphans=[], broken_links=[])

    index_text = index_path.read_text(encoding='utf-8')
    indexed_paths = set()
    for match in re.finditer(r'\]\(([\w/.-]+\.md)\)', index_text):
        indexed_paths.add(match.group(1))

    # Find all .md files
    all_files = set()
    for root, dirs, files in os.walk(WIKI_DIR):
        for f in files:
            if f.endswith('.md'):
                rel_path = str(Path(root) / f).replace(str(WIKI_DIR), '').lstrip('/')
                if rel_path not in ('index.md', 'log.md', 'overview.md', 'reading-list.md', 'tasks.md'):
                    all_files.add(rel_path)

    orphans = sorted(all_files - indexed_paths)

    # Check for broken links
    broken = []
    for fpath in all_files | indexed_paths:
        p = WIKI_DIR / fpath
        if p.exists():
            content = p.read_text(encoding='utf-8', errors='replace')
            for match in re.finditer(r'\[([^\]]+)\]\(([^")]+)\)', content):
                link_target = match.group(2)
                if not link_target.startswith(('http://', 'https://', '/', '#')):
                    resolved = (p.parent / link_target).resolve()
                    if not resolved.exists():
                        broken.append({"file": fpath, "link": link_target, "text": match.group(1)})

    return render_template("wiki-lint.html", orphans=orphans, broken_links=broken)


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


@app.route("/tasks")
@require_login
def tasks():
    tasks_list = read_tasks()
    tasks_list.sort(key=lambda t: t.due or "9999-12-31")
    return render_template("tasks_view.html", tasks=tasks_list,
                           all_contexts=get_all_contexts(),
                           all_projects=get_all_projects())


@app.route("/tasks/toggle", methods=["POST"])
@require_login
def tasks_toggle():
    data   = request.get_json(silent=True) or {}
    line   = data.get("line")
    action = data.get("action")
    if line is None or action not in ("complete", "reopen"):
        return {"error": "bad request"}, 400
    return {"ok": _toggle_task(int(line), action)}


@app.route("/tasks/add", methods=["POST"])
@require_login
def tasks_add():
    data    = request.get_json(silent=True) or {}
    text    = (data.get("text")    or "").strip()
    section = (data.get("section") or "Inbox").strip()
    if not text:
        return {"error": "Empty task"}, 400
    _add_task(text, section)
    return {"ok": True}


@app.route("/tasks/update", methods=["POST"])
@require_login
def tasks_update():
    data = request.get_json(silent=True) or {}
    task_id = data.get("task_id")
    field = data.get("field")
    value = data.get("value", "").strip()

    if task_id is None or field is None:
        return {"error": "missing task_id or field"}, 400

    tasks_list = read_tasks()
    if not (0 <= task_id < len(tasks_list)):
        return {"error": "task not found"}, 404

    task = tasks_list[task_id]

    if field == "description":
        task.description = value
    elif field == "context":
        task.set_context(value if value else None)
    elif field == "due":
        task.set_due(value if value else None)
    elif field == "priority":
        task.set_priority(value if value else None)
    elif field == "project":
        task.set_project(value if value else None)
    elif field == "recurrence":
        task.set_recurrence(value if value else None)
    elif field == "start":
        task.set_start(value if value else None)
    elif field == "notes":
        task.set_notes(value)
    elif field == "complete":
        if value == "true":
            task.complete_task()
        else:
            task.reopen_task()
    else:
        return {"error": "unknown field"}, 400

    write_tasks(tasks_list)
    return {"ok": True}


@app.route("/tasks/bulk-update", methods=["POST"])
@require_login
def tasks_bulk_update():
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    task_ids = data.get("task_ids", [])
    value = data.get("value", "").strip()

    if action is None:
        return {"error": "missing action"}, 400

    tasks_list = read_tasks()

    for task_id in task_ids:
        if not (0 <= task_id < len(tasks_list)):
            continue

        task = tasks_list[task_id]

        if action == "set-priority":
            task.set_priority(value if value else None)
        elif action == "set-context":
            task.set_context(value if value else None)
        elif action == "set-due":
            task.set_due(value if value else None)
        elif action == "set-project":
            task.set_project(value if value else None)
        elif action == "delete":
            task.description = "[DELETED]"
            task.complete = True
        else:
            return {"error": "unknown action"}, 400

    write_tasks(tasks_list)
    return {"ok": True}


@app.route("/inbox")
@require_login
def inbox():
    return render_template("inbox.html", items=list_inbox())


@app.route("/inbox/list")
@require_login
def inbox_list():
    """API endpoint that returns inbox items as JSON for polling/auto-refresh."""
    items = list_inbox()
    return {"items": items}


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
    (RAW_DIR / "inbox").mkdir(parents=True, exist_ok=True)

    def _unique(base_name):
        dest = RAW_DIR / "inbox" / base_name
        if not dest.exists():
            return base_name, dest
        stem, ext = base_name.rsplit(".", 1)
        import time as _t
        name = f"{stem}-{int(_t.time())}.{ext}"
        return name, RAW_DIR / "inbox" / name

    # Try to fetch full article content
    text, fetch_err = _clip_fetch(url)
    read_url = None

    if text:
        base_name, dest = _unique(f"{slug}.md")
        today = datetime.date.today().isoformat()
        md_content = (
            f'---\ntitle: "{display_title}"\nurl: {url}\nsaved: {today}\n---\n\n'
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
  <a class="pri" href="{inbox_url}">Reading List</a>
  <a class="sec" href="javascript:history.back()">Back</a>
</div>
<script>
setTimeout(()=>window.close(),2000)
</script>
</body></html>"""


@app.route("/inbox/read/<path:filename>")
@require_login
def inbox_read(filename):
    p = RAW_DIR / "inbox" / filename
    try:
        p.resolve().relative_to((RAW_DIR / "inbox").resolve())
    except ValueError:
        abort(404)
    if not p.exists():
        abort(404)
    text = p.read_text(encoding="utf-8", errors="replace")
    meta, body = _parse_frontmatter(text)
    return render_template(
        "reader.html",
        title   = meta.get("title", p.stem),
        url     = meta.get("url", ""),
        saved   = meta.get("saved", ""),
        body    = body,
        filename= filename,
    )


@app.route("/inbox/add", methods=["POST"])
@require_login
def inbox_add():
    data    = request.get_json(silent=True) or {}
    content = (data.get("content")  or "").strip()
    name    = (data.get("filename") or "").strip()
    if not content:
        return {"error": "Empty content"}, 400
    if not name:
        slug = re.sub(r"[^a-z0-9]+", "-", content[:60].lower()).strip("-")
        name = f"{slug}.txt"
    dest = RAW_DIR / "inbox" / name
    dest.write_text(content, encoding="utf-8")
    return {"ok": True, "filename": name}


@app.route("/inbox/delete", methods=["POST"])
@require_login
def inbox_delete():
    data = request.get_json(silent=True) or {}
    name = (data.get("filename") or "").strip()
    if not name:
        return {"error": "No filename"}, 400
    p = RAW_DIR / "inbox" / name
    try:
        p.resolve().relative_to((RAW_DIR / "inbox").resolve())
    except ValueError:
        return {"error": "Invalid path"}, 400
    if p.exists():
        p.unlink()
    return {"ok": True}


@app.route("/inbox/view/<path:filename>")
@require_login
def inbox_view(filename):
    p = RAW_DIR / "inbox" / filename
    try:
        p.resolve().relative_to((RAW_DIR / "inbox").resolve())
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
    meta, body = _parse_frontmatter(content)
    source_url = meta.get("url", "") or None
    return {"content": body.strip(), "url": source_url}


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
    src = RAW_DIR / "inbox" / name
    try:
        src.resolve().relative_to((RAW_DIR / "inbox").resolve())
    except ValueError:
        return {"error": "Invalid path"}, 400
    if not src.exists():
        return {"error": "File not found"}, 404
    dst = RAW_DIR / name
    # Avoid overwriting existing files in raw/
    if dst.exists():
        stem, suffix = src.stem, src.suffix
        i = 1
        while dst.exists():
            dst = RAW_DIR / f"{stem}-{i}{suffix}"
            i += 1
    src.rename(dst)
    return {"ok": True}

# ---------------------------------------------------------------------------
# Blog routes
# ---------------------------------------------------------------------------

@app.route("/blog/")
def blog_index():
    logged_in = bool(session.get("logged_in"))
    posts = _blog_posts(published_only=not logged_in)
    return render_template("blog_index.html", posts=posts)


@app.route("/blog/rss.xml")
def blog_rss():
    import xml.etree.ElementTree as ET
    posts = _blog_posts(published_only=True)
    base  = cfg_get("server", "base_url", "http://localhost:8080").rstrip("/")

    rss  = ET.Element("rss", attrib={"version": "2.0"})
    chan = ET.SubElement(rss, "channel")
    ET.SubElement(chan, "title").text       = "Lobotomy Blog"
    ET.SubElement(chan, "link").text        = f"{base}/blog/"
    ET.SubElement(chan, "description").text = "Posts from Lobotomy"
    ET.SubElement(chan, "language").text    = "en"

    for p in posts[:20]:
        item = ET.SubElement(chan, "item")
        ET.SubElement(item, "title").text       = p["title"]
        ET.SubElement(item, "link").text        = f"{base}/blog/{p['slug']}"
        ET.SubElement(item, "guid").text        = f"{base}/blog/{p['slug']}"
        ET.SubElement(item, "pubDate").text     = _rfc822(p["date"])
        ET.SubElement(item, "description").text = p["summary"]

    xml_str = ET.tostring(rss, encoding="unicode")
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str,
        mimetype="application/rss+xml",
    )


@app.route("/blog/new", methods=["GET", "POST"])
@require_login
def blog_new():
    error = None
    if request.method == "POST":
        title     = (request.form.get("title")   or "").strip()
        tags_raw  = (request.form.get("tags")    or "").strip()
        summary   = (request.form.get("summary") or "").strip()
        body      = (request.form.get("body")    or "").strip()
        published = bool(request.form.get("published"))
        if not title:
            error = "Title is required."
        else:
            today    = datetime.date.today().isoformat()
            slug     = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            slug     = f"{today}-{slug}"
            tag_list = [t.strip() for t in tags_raw.split(",") if t.strip()]
            tag_str  = "[" + ", ".join(tag_list) + "]" if tag_list else "[]"
            pub_str  = "true" if published else "false"
            content  = (
                f'---\ntitle: "{title}"\ndate: {today}\ntags: {tag_str}\n'
                f'published: {pub_str}\nsummary: "{summary}"\n---\n\n{body}'
            )
            BLOG_DIR.mkdir(parents=True, exist_ok=True)
            (BLOG_DIR / f"{slug}.md").write_text(content, encoding="utf-8")
            return redirect(url_for("blog_post", slug=slug))
    return render_template("blog_new.html", post=None, slug=None, error=error)


@app.route("/blog/<slug>/edit", methods=["GET", "POST"])
@require_login
def blog_edit(slug):
    if not re.match(r"^[\w-]+$", slug):
        abort(404)
    f = BLOG_DIR / f"{slug}.md"
    if not f.exists():
        abort(404)
    error = None
    if request.method == "POST":
        title     = (request.form.get("title")   or "").strip()
        tags_raw  = (request.form.get("tags")    or "").strip()
        summary   = (request.form.get("summary") or "").strip()
        body      = (request.form.get("body")    or "").strip()
        published = bool(request.form.get("published"))
        if not title:
            error = "Title is required."
        else:
            orig_meta, _ = _parse_frontmatter(f.read_text(encoding="utf-8"))
            date     = orig_meta.get("date", datetime.date.today().isoformat())
            tag_list = [t.strip() for t in tags_raw.split(",") if t.strip()]
            tag_str  = "[" + ", ".join(tag_list) + "]" if tag_list else "[]"
            pub_str  = "true" if published else "false"
            content  = (
                f'---\ntitle: "{title}"\ndate: {date}\ntags: {tag_str}\n'
                f'published: {pub_str}\nsummary: "{summary}"\n---\n\n{body}'
            )
            f.write_text(content, encoding="utf-8")
            return redirect(url_for("blog_post", slug=slug))
    text = f.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    tags_val   = meta.get("tags", [])
    post = {
        "title":     meta.get("title", ""),
        "tags":      ", ".join(tags_val) if isinstance(tags_val, list) else str(tags_val),
        "summary":   meta.get("summary", ""),
        "body":      body,
        "published": meta.get("published", False),
    }
    return render_template("blog_new.html", post=post, slug=slug, error=error)


@app.route("/blog/<slug>")
def blog_post(slug):
    if not re.match(r"^[\w-]+$", slug):
        abort(404)
    f = BLOG_DIR / f"{slug}.md"
    if not f.exists():
        abort(404)
    text = f.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    if not meta.get("published") and not session.get("logged_in"):
        abort(404)
    html = md_lib.markdown(body, extensions=_MD_EXTENSIONS)
    return render_template("blog_post.html", meta=meta, content=html, slug=slug)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

job_queue = JobQueue(WIKI_DIR / ".jobs")


if __name__ == "__main__":
    host = cfg_get("server", "host", "127.0.0.1")
    port = cfg_int("server", "port", default=8080)

    issues = validate_config()
    for level, msg in issues:
        prefix = "ERROR" if level == "error" else "WARNING"
        print(f"[{prefix}] {msg}")

    errors = [m for l, m in issues if l == "error"]
    if errors:
        print("\nFix these errors and restart.")
        sys.exit(1)

    if not user_exists():
        print(f"[INFO] No account found. Visit http://{host}:{port}/setup to create one.")

    provider = cfg_get("llm", "provider", "openai")
    print(f"\nLobotomy  http://{host}:{port}  (provider: {provider})\n")
    app.run(host=host, port=port, debug=False, threaded=True)
