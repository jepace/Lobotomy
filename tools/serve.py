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
    _log_file = Path(__file__).resolve().parent / "server.log"
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fh = logging.handlers.RotatingFileHandler(
        _log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

_setup_logging()
log = logging.getLogger("lobotomy.serve")

from agent import (
    REPO_ROOT, WIKI_DIR, RAW_DIR,
    get_client_and_model,
    run_agent_streaming,
    system_prompt,
    orientation_message,
)
from config import cfg_get, cfg_int, cfg_bool
try:
    from auth import (
        auth_bp, require_login, get_current_user,
        maybe_send_verification, _resend_ready,
    )
    HAS_AUTH = True
except ImportError:
    HAS_AUTH = False

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__, template_folder="templates")
app.secret_key = cfg_get("web", "secret_key") or os.urandom(32)

if HAS_AUTH:
    app.register_blueprint(auth_bp)

def require_login(f):  # noqa: F811
    if not HAS_AUTH:
        return f
    from auth import require_login as _rl
    return _rl(f)

# ---------------------------------------------------------------------------
# Setup / onboarding
# ---------------------------------------------------------------------------

def _is_configured() -> bool:
    client, _, err = get_client_and_model()
    return err is None

@app.before_request
def _check_setup():
    if request.endpoint in ("setup", "static"):
        return
    if HAS_AUTH and request.endpoint and request.endpoint.startswith("auth_"):
        return
    if not _is_configured():
        return redirect(url_for("setup"))
    if HAS_AUTH:
        from auth import require_login as _rl
        if request.endpoint not in ("setup",) and not request.path.startswith("/auth"):
            pass

@app.route("/setup", methods=["GET", "POST"])
def setup():
    error = email = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "save_llm":
            from config import cfg_set
            provider = request.form.get("provider", "").strip()
            api_key  = request.form.get("api_key", "").strip()
            model    = request.form.get("model", "").strip()
            if provider: cfg_set("llm", "provider", provider)
            if api_key:  cfg_set("llm", "api_key",  api_key)
            if model:    cfg_set("llm", "model",     model)
            if _is_configured():
                return redirect(url_for("index"))
            error = "Configuration saved but API key test failed. Check your key."
        elif action == "register" and HAS_AUTH:
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            email    = request.form.get("email", "").strip()
            if not username or not password:
                error = "Username and password are required."
            else:
                from auth import create_user, get_user
                if get_user(username):
                    error = f"User '{username}' already exists."
                else:
                    create_user(username, password, email=email, role="admin")
                    if email and _resend_ready():
                        maybe_send_verification(username, email)
                    return redirect(url_for("auth_login"))
    from agent import PROVIDERS
    return render_template("setup.html", error=error, email=email,
                           providers=list(PROVIDERS.keys()))

# ---------------------------------------------------------------------------
# Auth wrappers (no-op when auth module absent)
# ---------------------------------------------------------------------------

def get_current_user():  # noqa: F811
    if not HAS_AUTH:
        return {"username": "local", "role": "admin"}
    from auth import get_current_user as _gcu
    return _gcu()

# ---------------------------------------------------------------------------
# Auth routes (only when auth module is present)
# ---------------------------------------------------------------------------

@app.route("/auth/login", methods=["GET", "POST"])
def auth_login():
    if not HAS_AUTH:
        return redirect(url_for("index"))
    next_url = request.args.get("next", "")
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        from auth import check_password, get_user
        u = get_user(username)
        if u and check_password(u, password):
            session["username"] = username
            session.permanent = True
            app.permanent_session_lifetime = datetime.timedelta(days=30)
            if not next_url.startswith("/") or "//" in next_url or "%2f" in next_url.lower():
                next_url = url_for("index")
            return redirect(next_url)
        error = "Invalid username or password."
    return render_template("login.html", error=error)

@app.route("/auth/logout")
def auth_logout():
    session.clear()
    return redirect(url_for("auth_login"))

@app.route("/auth/verify")
def auth_verify():
    if not HAS_AUTH:
        return redirect(url_for("index"))
    token = request.args.get("token", "")
    from auth import verify_email_token, get_user
    username = verify_email_token(token)
    if username:
        return render_template("verify_done.html")
    return render_template("verify_done.html", error="This link is invalid or has expired.")

@app.route("/auth/verify-pending")
def auth_verify_pending():
    msg = request.args.get("msg", "")
    return render_template("verify_pending.html", message=msg)

@app.route("/auth/resend-verification", methods=["POST"])
def auth_resend_verification():
    if not HAS_AUTH:
        return redirect(url_for("index"))
    username = request.form.get("username", "").strip()
    from auth import get_user
    u = get_user(username)
    msg = "If that account exists and has an unverified email, a new link has been sent."
    if u and u.get("email") and not u.get("email_verified") and _resend_ready():
        maybe_send_verification(username, u["email"])
    return render_template("verify_pending.html", message=msg)

@app.route("/auth/forgot-password", methods=["GET", "POST"])
def auth_forgot_password():
    message = None
    if request.method == "POST" and HAS_AUTH:
        email = request.form.get("email", "").strip()
        from auth import request_password_reset
        message = request_password_reset(email)
    return render_template("forgot_password.html", message=message)

@app.route("/auth/reset-password", methods=["GET", "POST"])
def auth_reset_password():
    if not HAS_AUTH:
        return redirect(url_for("index"))
    token = request.args.get("token", "") or request.form.get("token", "")
    error = None
    if request.method == "POST":
        password = request.form.get("password", "").strip()
        confirm  = request.form.get("confirm", "").strip()
        if password != confirm:
            error = "Passwords do not match."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        else:
            from auth import reset_password_with_token
            ok = reset_password_with_token(token, password)
            if ok:
                return render_template("login.html",
                    error="Password reset. Please log in with your new password.")
            error = "This reset link is invalid or has expired."
    return render_template("reset_password.html", token=token, error=error)

@app.route("/settings")
@require_login
def user_settings():
    u = get_current_user()
    return render_template("settings.html",
        username=u.get("username", ""),
        email=u.get("email", ""),
        email_verified=u.get("email_verified", False),
        has_auth=HAS_AUTH,
        resend_ready=_resend_ready() if HAS_AUTH else False,
    )

@app.route("/api/settings", methods=["POST"])
@require_login
def api_settings():
    if not HAS_AUTH:
        return {"ok": False, "error": "Auth not available"}, 400
    u   = get_current_user()
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action == "change_password":
        from auth import check_password, set_password, get_user
        current  = data.get("current_password", "")
        new_pw   = data.get("new_password", "")
        if not check_password(get_user(u["username"]), current):
            return {"ok": False, "error": "Current password is incorrect"}, 400
        if len(new_pw) < 8:
            return {"ok": False, "error": "New password must be at least 8 characters"}, 400
        set_password(u["username"], new_pw)
        return {"ok": True}
    if action == "change_email":
        email = data.get("email", "").strip()
        from auth import update_user
        update_user(u["username"], {"email": email, "email_verified": False})
        if email and _resend_ready():
            maybe_send_verification(u["username"], email)
            return {"ok": True, "message": "Email updated. Verification email sent."}
        return {"ok": True, "message": "Email updated."}
    return {"ok": False, "error": "Unknown action"}, 400

# ---------------------------------------------------------------------------
# Chat history helpers
# ---------------------------------------------------------------------------

HISTORY_FILE = REPO_ROOT / "chat_history.json"
_HISTORY_LOCK = __import__("threading").Lock()
_MAX_HISTORY   = 200

def _load_history() -> list:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []

def _save_history(hist: list) -> None:
    HISTORY_FILE.write_text(
        json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8"
    )

def _trim_for_storage(msg: dict) -> dict:
    """
    Prepare a message for persistent storage.

    0. Strip content from assistant messages that also have tool_calls — storing
       both causes 'duplicate assistant message content' errors on replay.
    1. Drop large base64 image blobs from user messages.
    2. Drop binary tool-result content that would bloat the file.
    """
    msg = dict(msg)
    if msg.get("role") == "assistant" and msg.get("tool_calls") and "content" in msg:
        del msg["content"]
    if msg.get("role") == "user" and isinstance(msg.get("content"), list):
        cleaned = []
        for blk in msg["content"]:
            if isinstance(blk, dict) and blk.get("type") == "image_url":
                url = blk.get("image_url", {}).get("url", "")
                if url.startswith("data:"):
                    continue
            cleaned.append(blk)
        msg["content"] = cleaned if cleaned else msg["content"]
    if msg.get("role") == "tool" and isinstance(msg.get("content"), list):
        cleaned = []
        for blk in msg["content"]:
            if isinstance(blk, dict) and blk.get("type") == "image_url":
                continue
            cleaned.append(blk)
        msg["content"] = cleaned if cleaned else msg["content"]
    return msg

# ---------------------------------------------------------------------------
# Index / redirects
# ---------------------------------------------------------------------------

@app.route("/")
@require_login
def index():
    return redirect(url_for("chat"))

# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

@app.route("/chat")
@require_login
def chat():
    return render_template("chat.html")

@app.route("/api/chat-history")
@require_login
def api_chat_history():
    with _HISTORY_LOCK:
        hist = _load_history()
    out = []
    for msg in hist:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = [b.get("text", "") for b in content
                          if isinstance(b, dict) and b.get("type") == "text"]
            content = " ".join(text_parts).strip()
        if content:
            out.append({"role": role, "content": content})
    return {"messages": out}

@app.route("/api/clear-history", methods=["POST"])
@require_login
def api_clear_history():
    with _HISTORY_LOCK:
        _save_history([])
    return {"ok": True}

@app.route("/api/chat", methods=["POST"])
@require_login
def api_chat():
    data    = request.get_json(silent=True) or {}
    user_msg = data.get("message", "").strip()
    files    = data.get("files", [])
    if not user_msg and not files:
        return {"error": "empty message"}, 400

    client, model, err = get_client_and_model()
    if err:
        return {"error": err}, 500

    with _HISTORY_LOCK:
        history = _load_history()

    if history and history[-1].get("role") == "assistant" and not history[-1].get("content"):
        history = history[:-1]

    if files:
        content_parts = [{"type": "text", "text": user_msg}] if user_msg else []
        for f in files:
            name    = f.get("name", "file")
            b64data = f.get("data", "")
            mime    = f.get("type", "application/octet-stream")
            if mime.startswith("image/"):
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64data}"},
                })
            else:
                import base64
                try:
                    text = base64.b64decode(b64data).decode("utf-8", errors="replace")
                    content_parts.append({
                        "type": "text",
                        "text": f"[Attached file: {name}]\n{text}",
                    })
                except Exception:
                    content_parts.append({"type": "text", "text": f"[Attached file: {name} — could not decode]"})
        history.append({"role": "user", "content": content_parts})
    else:
        history.append({"role": "user", "content": user_msg})

    sys_prompt = system_prompt()
    if not history or history[0].get("role") != "assistant":
        try:
            orient = orientation_message()
            history.insert(0, {"role": "assistant", "content": orient})
        except Exception as e:
            log.warning("orientation_message failed: %s", e)

    def generate():
        nonlocal history
        new_msgs = []
        try:
            for chunk in run_agent_streaming(
                client, model, sys_prompt, history
            ):
                if isinstance(chunk, str):
                    yield f"data: {json.dumps({'text': chunk})}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "new_messages":
                    new_msgs = chunk["messages"]
        except Exception as e:
            log.exception("Agent error")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

        to_store = list(history)
        for m in new_msgs:
            to_store.append(_trim_for_storage(m))
        if len(to_store) > _MAX_HISTORY:
            to_store = to_store[-_MAX_HISTORY:]
        with _HISTORY_LOCK:
            _save_history(to_store)

    return Response(stream_with_context(generate()), mimetype="text/event-stream")

# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------

INBOX_DIR = RAW_DIR / "inbox"

@app.route("/inbox")
@require_login
def inbox():
    return render_template("inbox.html")

@app.route("/api/inbox")
@require_login
def api_inbox():
    items = []
    if INBOX_DIR.is_dir():
        for f in sorted(INBOX_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not f.is_file():
                continue
            stat = f.stat()
            wikified = (WIKI_DIR / "sources" / (f.stem + ".md")).exists()
            items.append({
                "name":      f.name,
                "size":      stat.st_size,
                "mtime":     stat.st_mtime,
                "wikified":  wikified,
            })
    return {"items": items}

@app.route("/api/inbox/<path:filename>/delete", methods=["POST"])
@require_login
def api_inbox_delete(filename):
    p = INBOX_DIR / filename
    try:
        p.resolve().relative_to(INBOX_DIR.resolve())
    except ValueError:
        abort(403)
    if not p.exists():
        abort(404)
    p.unlink()
    return {"ok": True}

@app.route("/api/inbox/<path:filename>/view")
@require_login
def api_inbox_view(filename):
    p = INBOX_DIR / filename
    try:
        p.resolve().relative_to(INBOX_DIR.resolve())
    except ValueError:
        abort(403)
    if not p.exists():
        abort(404)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return send_file(p, mimetype="application/pdf")
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        import mimetypes
        return send_file(p, mimetype=mimetypes.guess_type(str(p))[0] or "image/png")
    text = p.read_text(encoding="utf-8", errors="replace")
    return {"content": text}

@app.route("/api/inbox/upload", methods=["POST"])
@require_login
def api_inbox_upload():
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in request.files.getlist("files"):
        fname = Path(f.filename).name
        if not fname:
            continue
        dest = INBOX_DIR / fname
        base, ext = dest.stem, dest.suffix
        i = 1
        while dest.exists():
            dest = INBOX_DIR / f"{base}-{i}{ext}"
            i += 1
        f.save(dest)
        saved.append(dest.name)
    return {"ok": True, "saved": saved}

# ---------------------------------------------------------------------------
# Blog
# ---------------------------------------------------------------------------

BLOG_DIR = REPO_ROOT / "blog"

@app.route("/blog")
@require_login
def blog_list():
    posts = []
    if BLOG_DIR.is_dir():
        for f in sorted(BLOG_DIR.glob("*.md"), reverse=True):
            text = f.read_text(encoding="utf-8", errors="replace")
            meta, body = _parse_frontmatter(text)
            posts.append({
                "slug":    f.stem,
                "title":   meta.get("title", f.stem.replace("-", " ").title()),
                "date":    meta.get("date", ""),
                "excerpt": body.strip()[:200],
            })
    return render_template("blog.html", posts=posts)

@app.route("/blog/<slug>")
@require_login
def blog_post(slug):
    p = BLOG_DIR / f"{slug}.md"
    if not p.exists():
        abort(404)
    text = p.read_text(encoding="utf-8", errors="replace")
    meta, _ = _parse_frontmatter(text)
    return render_template("blog-post.html",
        content=render_md(p),
        title=meta.get("title", slug.replace("-", " ").title()),
    )

# ---------------------------------------------------------------------------
# Markdown rendering helpers
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
        v = v.strip().strip('"\'')
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1]
            meta[k] = [x.strip().strip('"\'') for x in inner.split(",") if x.strip()]
        else:
            meta[k] = v
    body = "\n".join(lines[end + 1:])
    return meta, body

def render_md(p: Path) -> str:
    text = p.read_text(encoding="utf-8", errors="replace")
    _, body = _parse_frontmatter(text)
    extensions = ["tables", "fenced_code", "nl2br", "sane_lists"]
    try:
        extensions.append("mdx_linkify")
    except Exception:
        pass
    html = md_lib.markdown(body, extensions=extensions)
    return html

# ---------------------------------------------------------------------------
# Wiki
# ---------------------------------------------------------------------------

def wiki_sections() -> list:
    sections = []
    for label, subdir in [
        ("Index",     "index.md"),
        ("Sources",   "sources/index.md"),
        ("Entities",  "entities/index.md"),
        ("Concepts",  "concepts/index.md"),
        ("Synthesis", "synthesis/index.md"),
    ]:
        p = WIKI_DIR / subdir
        if p.exists():
            sections.append({"label": label, "path": subdir})
    return sections

@app.route("/wiki/")
@app.route("/wiki")
@require_login
def wiki_home():
    return redirect(url_for("wiki_page", page_path="index.md"))

@app.route("/wiki/search")
@require_login
def wiki_search():
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return {"results": []}

    results = []
    query_words = q.lower().split()

    for subdir in ("", "sources", "entities", "concepts", "synthesis"):
        d = WIKI_DIR / subdir if subdir else WIKI_DIR
        if not d.is_dir():
            continue
        for f in d.glob("*.md"):
            text = f.read_text(encoding="utf-8", errors="replace")
            meta, body = _parse_frontmatter(text)
            title = meta.get("title", f.stem.replace("-", " ").title())
            haystack = (title + " " + body).lower()
            if all(w in haystack for w in query_words):
                excerpt = ""
                for w in query_words:
                    idx = body.lower().find(w)
                    if idx != -1:
                        start = max(0, idx - 60)
                        excerpt = body[start:idx + 100].strip()
                        excerpt = re.sub(r"\s+", " ", excerpt)
                        break
                rel = str(f.relative_to(WIKI_DIR))
                results.append({"path": rel, "title": title, "excerpt": excerpt})
                if len(results) >= 20:
                    break
        if len(results) >= 20:
            break

    return {"results": results}

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
    raw_text = p.read_text(encoding="utf-8", errors="replace")
    meta, _  = _parse_frontmatter(raw_text)
    source_url = meta.get("url", "").strip()
    return render_template(
        "wiki.html",
        content=render_md(p),
        title=p.stem.replace("-", " ").title(),
        sections=wiki_sections(),
        current_path=str(p.relative_to(WIKI_DIR)),
        source_url=source_url,
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
    raw_text = p.read_text(encoding="utf-8", errors="replace")
    meta, _  = _parse_frontmatter(raw_text)
    return render_template(
        "wiki-edit.html",
        raw=raw_text,
        title=meta.get("title", p.stem.replace("-", " ").title()),
        page_path=str(p.relative_to(WIKI_DIR)),
    )

@app.route("/api/wiki/<path:page_path>/save", methods=["POST"])
@require_login
def wiki_save(page_path):
    p = WIKI_DIR / page_path
    if not p.suffix:
        p = p.with_suffix(".md")
    try:
        p.resolve().relative_to(WIKI_DIR.resolve())
    except ValueError:
        return {"ok": False, "error": "Invalid path"}, 403
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    if not content.strip():
        return {"ok": False, "error": "Empty content"}, 400
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"ok": True}

@app.route("/wiki-lint")
@require_login
def wiki_lint():
    if not WIKI_DIR.exists():
        return render_template("wiki-lint.html", orphans=[], broken_links=[])

    _LINT_SKIP = {
        "index.md",
        "log.md",
        "overview.md",
        "reading-list.md",
        "tasks.md",
        "tasks-archive.md",
        "sources/index.md",
        "entities/index.md",
        "concepts/index.md",
        "synthesis/index.md",
    }

    index_p = WIKI_DIR / "index.md"
    index_text = index_p.read_text(encoding="utf-8") if index_p.exists() else ""

    # Orphan check
    orphans = []
    for subdir in ("sources", "entities", "concepts", "synthesis"):
        d = WIKI_DIR / subdir
        if not d.is_dir():
            continue
        for f in d.glob("*.md"):
            rel = str(f.relative_to(WIKI_DIR))
            if rel in _LINT_SKIP:
                continue
            if rel not in index_text and f.name not in index_text:
                orphans.append(rel)

    # Broken link check
    broken_links = []
    for f in WIKI_DIR.rglob("*.md"):
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'\[([^\]]*)\]\(([^)]+)\)', text):
            href = m.group(2)
            if href.startswith("http://") or href.startswith("https://"):
                continue
            href_path = href.split("#")[0]
            if not href_path:
                continue
            target = (f.parent / href_path).resolve()
            if not target.exists():
                broken_links.append({
                    "file": str(f.relative_to(WIKI_DIR)),
                    "link": href,
                    "text": m.group(1),
                })

    return render_template("wiki-lint.html", orphans=orphans, broken_links=broken_links)

# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

TASKS_FILE = WIKI_DIR / "tasks.md"

@app.route("/tasks")
@require_login
def tasks():
    return render_template("tasks.html")

@app.route("/api/tasks")
@require_login
def api_tasks():
    if not TASKS_FILE.exists():
        return {"tasks": []}
    text = TASKS_FILE.read_text(encoding="utf-8")
    tasks_list = _parse_tasks(text)
    return {"tasks": tasks_list}

@app.route("/api/tasks/toggle", methods=["POST"])
@require_login
def api_tasks_toggle():
    data    = request.get_json(silent=True) or {}
    line_no = data.get("line")
    if line_no is None:
        return {"ok": False, "error": "missing line"}, 400
    if not TASKS_FILE.exists():
        return {"ok": False, "error": "tasks file missing"}, 404
    lines = TASKS_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    if line_no < 0 or line_no >= len(lines):
        return {"ok": False, "error": "line out of range"}, 400
    line = lines[line_no]
    today = datetime.date.today().isoformat()
    if "- [x]" in line:
        line = line.replace("- [x]", "- [ ]", 1)
        line = re.sub(r"\s*#done:\d{4}-\d{2}-\d{2}", "", line)
    elif "- [ ]" in line:
        line = line.replace("- [ ]", "- [x]", 1)
        line = line.rstrip("\n") + f" #done:{today}\n"
    lines[line_no] = line
    TASKS_FILE.write_text("".join(lines), encoding="utf-8")
    return {"ok": True}

def _parse_tasks(text: str) -> list:
    tasks_list = []
    current_section = "Inbox"
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## "):
            current_section = line[3:].strip()
            continue
        m = re.match(r'^(\s*)- \[([ x])\] (.+)$', line)
        if not m:
            continue
        indent  = len(m.group(1))
        checked = m.group(2) == "x"
        body    = m.group(3)
        tags    = {}
        for tag in re.findall(r'#(\w+)(?::(\S+))?', body):
            tags[tag[0]] = tag[1] or True
        clean = re.sub(r'#\w+(?::\S+)?', '', body).strip()
        tasks_list.append({
            "line":    i,
            "indent":  indent,
            "done":    checked,
            "text":    clean,
            "raw":     body,
            "section": current_section,
            "tags":    tags,
        })
    return tasks_list

# ---------------------------------------------------------------------------
# Reading list
# ---------------------------------------------------------------------------

READING_LIST_FILE = WIKI_DIR / "reading-list.md"

@app.route("/reading-list")
@require_login
def reading_list():
    return render_template("reading-list.html")

@app.route("/api/reading-list")
@require_login
def api_reading_list():
    if not READING_LIST_FILE.exists():
        return {"items": []}
    text = READING_LIST_FILE.read_text(encoding="utf-8")
    items = []
    for line in text.splitlines():
        m = re.match(r'^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|', line)
        if not m or m.group(1).lower() in ("title", "---"):
            continue
        items.append({
            "title":  m.group(1),
            "source": m.group(2),
            "added":  m.group(3),
            "status": m.group(4),
        })
    return {"items": items}

# ---------------------------------------------------------------------------
# Navigation helper
# ---------------------------------------------------------------------------

@app.route("/nav")
@require_login
def nav():
    return render_template("nav.html")

# ---------------------------------------------------------------------------
# Job queue (long-running agent tasks)
# ---------------------------------------------------------------------------

try:
    from job_queue import job_queue_bp, start_job_queue_worker
    app.register_blueprint(job_queue_bp)
    start_job_queue_worker()
    log.info("Job queue worker started.")
except ImportError:
    log.info("job_queue module not found — long-running jobs disabled.")

# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@app.route("/admin")
@require_login
def admin():
    u = get_current_user()
    if HAS_AUTH and u.get("role") != "admin":
        abort(403)
    from auth import list_users
    users = list_users() if HAS_AUTH else []
    return render_template("admin.html", users=users)

@app.route("/api/admin/users", methods=["POST"])
@require_login
def api_admin_users():
    u = get_current_user()
    if HAS_AUTH and u.get("role") != "admin":
        abort(403)
    data   = request.get_json(silent=True) or {}
    action = data.get("action")
    if action == "create":
        from auth import create_user, get_user
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        email    = data.get("email", "").strip()
        role     = data.get("role", "user")
        if not username or not password:
            return {"ok": False, "error": "username and password required"}, 400
        if get_user(username):
            return {"ok": False, "error": f"User '{username}' already exists"}, 400
        create_user(username, password, email=email, role=role)
        return {"ok": True}
    if action == "delete":
        from auth import delete_user
        username = data.get("username", "")
        if username == u.get("username"):
            return {"ok": False, "error": "Cannot delete yourself"}, 400
        delete_user(username)
        return {"ok": True}
    if action == "reset_password":
        from auth import set_password
        username = data.get("username", "")
        password = data.get("password", "")
        if len(password) < 8:
            return {"ok": False, "error": "Password must be at least 8 characters"}, 400
        set_password(username, password)
        return {"ok": True}
    return {"ok": False, "error": "unknown action"}, 400

# ---------------------------------------------------------------------------
# API: inbound email webhook (Resend)
# ---------------------------------------------------------------------------

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
    webhook_secret = cfg_get("api", "resend_webhook_secret", "").strip()

    # Verify Resend/Svix webhook signature if secret is configured
    if webhook_secret:
        try:
            import hashlib, hmac, base64, time as _time
            msg_id        = request.headers.get("svix-id", "")
            msg_timestamp = request.headers.get("svix-timestamp", "")
            msg_signature = request.headers.get("svix-signature", "")
            if not (msg_id and msg_timestamp and msg_signature):
                return {"error": "Missing webhook signature headers"}, 401
            # Reject timestamps older than 5 minutes
            try:
                ts = int(msg_timestamp)
                if abs(_time.time() - ts) > 300:
                    return {"error": "Webhook timestamp too old"}, 401
            except ValueError:
                return {"error": "Invalid timestamp"}, 401
            signed_content = f"{msg_id}.{msg_timestamp}.{request.get_data(as_text=True)}"
            secret_bytes   = base64.b64decode(webhook_secret.removeprefix("whsec_"))
            expected       = base64.b64encode(
                hmac.new(secret_bytes, signed_content.encode(), hashlib.sha256).digest()
            ).decode()
            # svix-signature may contain multiple comma-separated "v1,<sig>" entries
            sigs = [s.split(",", 1)[1] for s in msg_signature.split(" ") if "," in s]
            if not any(hmac.compare_digest(expected, s) for s in sigs):
                return {"error": "Invalid signature"}, 401
        except Exception as e:
            log.warning("Inbound email signature check failed: %s", e)
            return {"error": "Signature verification error"}, 401

    data = request.get_json(silent=True) or {}

    # Reject if a magic inbound address is configured and this email wasn't sent to it
    inbound_address = cfg_get("api", "resend_inbound_address", "").strip().lower()
    if inbound_address:
        to_field = (data.get("to") or data.get("To") or "").strip().lower()
        if inbound_address not in to_field:
            return {"ok": True}  # silently discard spam

    # Resend inbound email payload fields
    subject  = (data.get("subject") or data.get("Subject") or "").strip()
    from_addr = (data.get("from")    or data.get("From")    or "").strip()
    text_body = (data.get("text")    or data.get("plain")   or "").strip()

    if not subject and not text_body:
        return {"error": "Empty email"}, 400

    title = subject or "Email article"

    # Look for a bare URL in the first few lines of the body
    url = ""
    body_content = text_body
    if text_body:
        for line in text_body.splitlines()[:10]:
            line = line.strip()
            if re.match(r'https?://\S+', line):
                url = line
                break

    # Build the inbox file content
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:60]
    slug = slug or "email"
    fname = f"{slug}.md"
    dest  = INBOX_DIR / fname
    base, ext = dest.stem, dest.suffix
    i = 1
    while dest.exists():
        dest = INBOX_DIR / f"{base}-{i}{ext}"
        i += 1

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    if url:
        file_content = f"{url}\n"
    else:
        file_content = (
            f"---\ntitle: {json.dumps(title)}\nfrom: {json.dumps(from_addr)}\ndate: {now}\n---\n\n"
            + text_body
        )

    dest.write_text(file_content, encoding="utf-8")
    log.info("Inbound email saved: %s (from %s)", dest.name, from_addr)
    return {"ok": True, "saved": dest.name}

# ---------------------------------------------------------------------------
# API: browser extension / bookmarklet
# ---------------------------------------------------------------------------

@app.route("/api/save-url", methods=["POST"])
@require_login
def api_save_url():
    """Save a URL to the inbox. Called by the browser extension."""
    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()
    if not url or not url.startswith("http"):
        return {"ok": False, "error": "invalid url"}, 400
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    title = (data.get("title") or url)[:80]
    slug  = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:50]
    slug  = slug or "article"
    fname = f"{slug}.md"
    dest  = INBOX_DIR / fname
    base, ext = dest.stem, dest.suffix
    i = 1
    while dest.exists():
        dest = INBOX_DIR / f"{base}-{i}{ext}"
        i += 1
    dest.write_text(url + "\n", encoding="utf-8")
    log.info("URL saved to inbox: %s → %s", url, dest.name)
    return {"ok": True, "saved": dest.name}

# ---------------------------------------------------------------------------
# Bookmarklet page
# ---------------------------------------------------------------------------

@app.route("/bookmarklet")
@require_login
def bookmarklet():
    base = request.host_url.rstrip("/")
    return render_template("bookmarklet.html", base_url=base)

# ---------------------------------------------------------------------------
# Stats / housekeeping
# ---------------------------------------------------------------------------

@app.route("/api/stats")
@require_login
def api_stats():
    stats = {}
    if WIKI_DIR.exists():
        stats["wiki_pages"] = sum(1 for _ in WIKI_DIR.rglob("*.md"))
    if INBOX_DIR.exists():
        stats["inbox_items"] = sum(1 for f in INBOX_DIR.iterdir() if f.is_file())
    hist = _load_history()
    stats["chat_messages"] = sum(1 for m in hist if m.get("role") in ("user", "assistant"))
    return stats

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host = cfg_get("web", "host", "0.0.0.0")
    port = cfg_int("web", "port", 5000)
    debug = cfg_bool("web", "debug", False)
    log.info("Starting Lobotomy server on %s:%d (debug=%s)", host, port, debug)
    app.run(host=host, port=port, debug=debug)
