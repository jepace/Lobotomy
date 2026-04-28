#!/usr/bin/env python3
"""
LLM Wiki — Web server

A mobile-friendly web app for browsing the wiki, managing tasks, capturing articles,
and chatting with the AI — all from a phone browser.

Requirements:
  pip install flask markdown openai
  -- or on FreeBSD --
  pkg install py311-flask py311-markdown && pip install openai

Configuration (environment variables):
  WIKI_PASSWORD   Password for the web UI (strongly recommended on a public VPS)
  WIKI_HOST       Bind address (default: 127.0.0.1 — use 0.0.0.0 to expose externally)
  WIKI_PORT       Port (default: 8080)
  WIKI_SECRET     Flask session secret (auto-generated and persisted if not set)
  WIKI_PROVIDER   LLM provider: gemini | openai | ollama | openrouter  (default: openai)
  WIKI_API_KEY    LLM API key
  WIKI_MODEL      Override model name
  WIKI_API_BASE   Override API base URL

Usage:
  python3 tools/serve.py
  WIKI_HOST=0.0.0.0 WIKI_PORT=8080 python3 tools/serve.py
"""

import datetime
import functools
import hashlib
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
    from flask import (Flask, Response, abort, redirect, render_template,
                       request, stream_with_context, url_for)
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

# agent.py lives alongside this file
sys.path.insert(0, str(Path(__file__).parent))
from agent import (REPO_ROOT, WIKI_DIR, RAW_DIR,
                   get_client_and_model, orientation_message, stream_agent_turn, system_prompt)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__, template_folder="templates")

# Persist the secret key so sessions survive server restarts
_secret_file = WIKI_DIR / ".secret"
if os.environ.get("WIKI_SECRET"):
    app.secret_key = os.environ["WIKI_SECRET"]
elif _secret_file.exists():
    app.secret_key = _secret_file.read_text().strip()
else:
    _key = os.urandom(24).hex()
    _secret_file.write_text(_key)
    app.secret_key = _key

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _check_password(pw: str) -> bool:
    expected = os.environ.get("WIKI_PASSWORD", "")
    if not expected:
        return True
    return hashlib.sha256(pw.encode()).hexdigest() == hashlib.sha256(expected.encode()).hexdigest()


def require_auth(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not os.environ.get("WIKI_PASSWORD"):
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or not _check_password(auth.password):
            return Response(
                "Authentication required.",
                401,
                {"WWW-Authenticate": 'Basic realm="Wiki"'},
            )
        return f(*args, **kwargs)
    return decorated

# ---------------------------------------------------------------------------
# Chat history (persisted to wiki/.chat_history.json, gitignored)
# ---------------------------------------------------------------------------

HISTORY_FILE = WIKI_DIR / ".chat_history.json"
MAX_HISTORY  = 80  # messages (~40 exchanges)


def load_history() -> list:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
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
    """Convert a relative .md link to a /wiki/ URL."""
    if href.startswith(("http://", "https://", "/", "#", "mailto:")):
        return href
    resolved = (from_page.parent / href).resolve()
    try:
        rel = resolved.relative_to(WIKI_DIR.resolve())
        return f"/wiki/{rel}"
    except ValueError:
        return href


def render_md(path: Path) -> str:
    if not path.exists():
        return "<p><em>Page not found.</em></p>"
    text = path.read_text(encoding="utf-8")
    # Strip YAML frontmatter
    text = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)
    html = md_lib.markdown(text, extensions=_MD_EXTENSIONS)
    # Rewrite relative .md links to /wiki/ URLs so navigation works
    html = re.sub(
        r'href="([^"]*\.md[^"]*)"',
        lambda m: f'href="{_rewrite_md_link(m.group(1), path)}"',
        html,
    )
    return html

# ---------------------------------------------------------------------------
# Task helpers
# ---------------------------------------------------------------------------

def parse_tasks() -> list:
    tasks_file = WIKI_DIR / "tasks.md"
    if not tasks_file.exists():
        return []
    lines = tasks_file.read_text(encoding="utf-8").splitlines()
    tasks        = []
    current_section = "Inbox"
    for i, line in enumerate(lines):
        if line.startswith("## "):
            current_section = line[3:].strip()
            continue
        m = re.match(r"^(\s*)-\s+\[([ x])\]\s+(.+)$", line)
        if not m:
            continue
        indent, checked, text = m.groups()
        p = re.search(r"#p:(\w+)", text)
        d = re.search(r"#due:(\S+)", text)
        c = re.search(r"#ctx:(\w+)", text)
        clean = re.sub(r"#\S+", "", text).strip()
        tasks.append({
            "line":     i,
            "done":     checked == "x",
            "text":     clean,
            "raw":      text,
            "section":  current_section,
            "indent":   len(indent) // 2,
            "priority": p.group(1) if p else "",
            "due":      d.group(1) if d else "",
            "context":  c.group(1) if c else "",
        })
    return tasks


def _toggle_task(line_num: int, action: str) -> bool:
    tasks_file = WIKI_DIR / "tasks.md"
    lines = tasks_file.read_text(encoding="utf-8").splitlines()
    if not (0 <= line_num < len(lines)):
        return False
    line = lines[line_num]
    if action == "complete" and "- [ ]" in line:
        today = datetime.date.today().isoformat()
        lines[line_num] = line.replace("- [ ]", "- [x]") + f" #done:{today}"
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
    # Try to append under the matching section heading
    pattern = rf"(## {re.escape(section)}\n)"
    if re.search(pattern, content):
        content = re.sub(pattern, r"\1" + entry, content, count=1)
    else:
        # Section doesn't exist — append new section at end
        content = content.rstrip("\n") + f"\n\n## {section}\n\n{entry}"
    tasks_file.write_text(content, encoding="utf-8")

# ---------------------------------------------------------------------------
# Inbox helpers
# ---------------------------------------------------------------------------

def list_inbox() -> list[str]:
    inbox = RAW_DIR / "inbox"
    if not inbox.is_dir():
        return []
    return sorted(
        f.name for f in inbox.iterdir()
        if f.is_file() and f.name not in (".gitkeep",)
    )

# ---------------------------------------------------------------------------
# Wiki navigation helpers
# ---------------------------------------------------------------------------

def wiki_sections() -> list[dict]:
    """Return top-level wiki pages for navigation."""
    pages = []
    for name, label in [
        ("index.md",        "Index"),
        ("overview.md",     "Overview"),
        ("reading-list.md", "Reading List"),
        ("log.md",          "Log"),
    ]:
        if (WIKI_DIR / name).exists():
            pages.append({"path": name, "label": label})
    dirs = []
    for d in ["sources", "entities", "concepts", "synthesis"]:
        if (WIKI_DIR / d).is_dir():
            dirs.append({"path": d + "/", "label": d.title()})
    return pages + dirs

# ---------------------------------------------------------------------------
# Template context
# ---------------------------------------------------------------------------

@app.context_processor
def inject_globals():
    path = request.path
    if path.startswith("/wiki"):
        active = "wiki"
    elif path.startswith("/tasks"):
        active = "tasks"
    elif path.startswith("/inbox"):
        active = "inbox"
    else:
        active = "chat"
    return {"active": active}

# ---------------------------------------------------------------------------
# Routes — chat
# ---------------------------------------------------------------------------

@app.route("/")
@require_auth
def index():
    return redirect(url_for("chat"))


@app.route("/chat")
@require_auth
def chat():
    history = load_history()
    # Pull only user/assistant text messages for display
    display = [
        {"role": m["role"], "content": m.get("content", "")}
        for m in history
        if m["role"] in ("user", "assistant") and m.get("content")
    ]
    return render_template("chat.html", history=display)


@app.route("/chat/send", methods=["POST"])
@require_auth
def chat_send():
    data    = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return {"error": "Empty message"}, 400

    client, model, error = get_client_and_model()
    if error:
        def err_stream():
            yield json.dumps({"type": "error", "content": error}) + "\n"
            yield json.dumps({"type": "done"}) + "\n"
        return Response(stream_with_context(err_stream()), mimetype="application/x-ndjson")

    sys_prompt = system_prompt()
    history    = load_history()

    if not history:
        history = [
            {"role": "user",      "content": orientation_message()},
            {"role": "assistant", "content": "Oriented. Ready."},
        ]

    history.append({"role": "user", "content": message})

    def generate():
        yield from stream_agent_turn(client, model, history, sys_prompt)
        save_history(history)

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")


@app.route("/chat/clear", methods=["POST"])
@require_auth
def chat_clear():
    clear_history()
    return {"ok": True}

# ---------------------------------------------------------------------------
# Routes — wiki browser
# ---------------------------------------------------------------------------

@app.route("/wiki/")
@require_auth
def wiki_home():
    return redirect(url_for("wiki_page", page_path="index.md"))


@app.route("/wiki/<path:page_path>")
@require_auth
def wiki_page(page_path):
    p = WIKI_DIR / page_path
    if p.is_dir():
        p = p / "index.md"
    if not p.suffix:
        p = p.with_suffix(".md")
    # Prevent path traversal
    try:
        p.resolve().relative_to(WIKI_DIR.resolve())
    except ValueError:
        abort(404)
    if not p.exists():
        abort(404)
    title = p.stem.replace("-", " ").title()
    return render_template(
        "wiki.html",
        content=render_md(p),
        title=title,
        sections=wiki_sections(),
        current_path=str(p.relative_to(WIKI_DIR)),
    )

# ---------------------------------------------------------------------------
# Routes — tasks
# ---------------------------------------------------------------------------

@app.route("/tasks")
@require_auth
def tasks():
    all_tasks = parse_tasks()
    # Group by section
    sections: dict[str, list] = {}
    for t in all_tasks:
        sections.setdefault(t["section"], []).append(t)
    return render_template("tasks.html", sections=sections,
                           today=datetime.date.today().isoformat())


@app.route("/tasks/toggle", methods=["POST"])
@require_auth
def tasks_toggle():
    data   = request.get_json(silent=True) or {}
    line   = data.get("line")
    action = data.get("action")
    if line is None or action not in ("complete", "reopen"):
        return {"error": "bad request"}, 400
    ok = _toggle_task(int(line), action)
    return {"ok": ok}


@app.route("/tasks/add", methods=["POST"])
@require_auth
def tasks_add():
    data    = request.get_json(silent=True) or {}
    text    = (data.get("text") or "").strip()
    section = (data.get("section") or "Inbox").strip()
    if not text:
        return {"error": "Empty task"}, 400
    _add_task(text, section)
    return {"ok": True}

# ---------------------------------------------------------------------------
# Routes — inbox
# ---------------------------------------------------------------------------

@app.route("/inbox")
@require_auth
def inbox():
    return render_template("inbox.html", items=list_inbox())


@app.route("/inbox/add", methods=["POST"])
@require_auth
def inbox_add():
    data    = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
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
@require_auth
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

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host = os.environ.get("WIKI_HOST", "127.0.0.1")
    port = int(os.environ.get("WIKI_PORT", "8080"))

    if not os.environ.get("WIKI_PASSWORD"):
        print("WARNING: WIKI_PASSWORD is not set — the server is unauthenticated.")
        print("         Set it before exposing the server to the internet.")

    provider = os.environ.get("WIKI_PROVIDER", "openai")
    if not os.environ.get("WIKI_API_KEY") and provider != "ollama":
        print(f"WARNING: WIKI_API_KEY is not set. Chat will return an error until it is.")

    print(f"\nLLM Wiki  http://{host}:{port}")
    print(f"Provider: {provider}  |  Run 'python3 tools/serve.py --help' for config options\n")

    app.run(host=host, port=port, debug=False, threaded=True)
