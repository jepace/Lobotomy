#!/usr/bin/env python3
"""Shared LLM agent logic used by both the CLI (wiki.py) and web server (serve.py)."""

import collections
import json
import sys
import threading
import time
from pathlib import Path
from typing import Generator

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # checked at call time

sys.path.insert(0, str(Path(__file__).parent))
from config import cfg_get, cfg_int

# RPM rate-limit tracking (shared across threads)
_request_times: collections.deque = collections.deque()
_rpm_lock = threading.Lock()

REPO_ROOT = Path(__file__).resolve().parent.parent
WIKI_DIR  = REPO_ROOT / "wiki"
RAW_DIR   = REPO_ROOT / "raw"

# ---------------------------------------------------------------------------
# Provider presets
# ---------------------------------------------------------------------------

PROVIDERS = {
    "gemini": {
        "base_url":      "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.0-flash",
    },
    "openai": {
        "base_url":      None,
        "default_model": "gpt-4o-mini",
    },
    "ollama": {
        "base_url":      "http://localhost:11434/v1",
        "default_model": "llama3.2",
        "api_key":       "ollama",
    },
    "openrouter": {
        "base_url":      "https://openrouter.ai/api/v1",
        "default_model": "google/gemini-2.0-flash-exp:free",
    },
}


def get_client_and_model():
    """Return (OpenAI-compatible client, model_name, error_string_or_None)."""
    if OpenAI is None:
        return None, None, "openai package not installed — run: pip install openai"

    provider_name = cfg_get("llm", "provider", "openai").lower()
    preset        = PROVIDERS.get(provider_name, PROVIDERS["openai"])

    api_key  = cfg_get("llm", "api_key")  or preset.get("api_key", "")
    base_url = cfg_get("llm", "api_base") or preset.get("base_url")
    model    = cfg_get("llm", "model")    or preset["default_model"]

    if not api_key:
        return None, None, (
            f"No API key for provider '{provider_name}'.\n"
            f"  Set llm.api_key in config.json."
        )

    return OpenAI(api_key=api_key, base_url=base_url), model, None


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def _read_file(path: str) -> "str | list":
    """Return a string, or a list of OpenAI content blocks for image/image-only PDF."""
    p = REPO_ROOT / path
    if not p.exists():
        return f"Error: not found: {path}"
    if not p.is_file():
        return f"Error: not a file: {path}"
    if p.suffix.lower() == ".pdf":
        return _read_pdf(p)
    if p.suffix.lower() in _IMAGE_EXTS:
        return _read_image(p)
    return p.read_text(encoding="utf-8", errors="replace")


def _read_image(p) -> list:
    import base64, mimetypes
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    b64  = base64.b64encode(p.read_bytes()).decode()
    return [
        {"type": "text", "text": f"Image: {p.name}"},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]


def _read_pdf(p) -> "str | list":
    import base64
    # Try text extraction first (fast, works for PDFs with a text layer)
    try:
        import pypdf
        reader = pypdf.PdfReader(str(p))
        pages  = [page.extract_text() or "" for page in reader.pages]
        text   = "\n\n".join(pages).strip()
        if text:
            return text
    except ImportError:
        pass
    except Exception:
        pass

    # No text layer — render pages as images for vision
    try:
        import fitz  # pymupdf
    except ImportError:
        return "Error: PDF has no text layer and pymupdf is not installed (pip install pymupdf)"

    doc    = fitz.open(str(p))
    blocks = [{"type": "text", "text": f"PDF '{p.name}' rendered as images ({len(doc)} page(s)):"}]
    limit  = 20  # avoid overwhelming the context window
    for i, page in enumerate(doc):
        if i >= limit:
            blocks.append({"type": "text", "text": f"(truncated — showing first {limit} of {len(doc)} pages)"})
            break
        png   = page.get_pixmap(dpi=150).tobytes("png")
        b64   = base64.b64encode(png).decode()
        blocks.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    return blocks


def _write_file(path: str, content: str) -> str:
    p = REPO_ROOT / path
    try:
        p.resolve().relative_to(WIKI_DIR.resolve())
    except ValueError:
        return (
            f"Error: write refused — only wiki/ is writable. "
            f"raw/ is immutable. Got: {path}"
        )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Written {len(content)} bytes to {path}"


def _list_dir(directory: str) -> str:
    d = REPO_ROOT / directory
    if not d.is_dir():
        return f"Error: not a directory: {directory}"
    entries = sorted(d.iterdir())
    if not entries:
        return "(empty)"
    return "\n".join(
        f"  [{'dir' if e.is_dir() else 'file'}]  {e.relative_to(REPO_ROOT)}"
        for e in entries
    )


def _fetch_url(url: str) -> str:
    import urllib.request, urllib.error, urllib.parse
    from html.parser import HTMLParser
    from http.cookiejar import CookieJar

    class _Stripper(HTMLParser):
        _SKIP = {"script", "style", "noscript", "template"}
        def __init__(self):
            super().__init__()
            self._depth = 0
            self.chunks = []
        def handle_starttag(self, tag, attrs):
            if tag in self._SKIP: self._depth += 1
        def handle_endtag(self, tag):
            if tag in self._SKIP and self._depth: self._depth -= 1
        def handle_data(self, data):
            if not self._depth: self.chunks.append(data)

    headers = {
        "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",  # avoid dealing with gzip in stdlib
        "DNT":             "1",
    }

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    try:
        req  = urllib.request.Request(url, headers=headers)
        with opener.open(req, timeout=15) as resp:
            status = resp.status
            ct     = resp.headers.get("Content-Type", "")
            raw    = resp.read(2_000_000)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return (
                f"Error 403 Forbidden — {url}\n"
                "The site is blocking automated access. "
                "Save the article as a .txt/.html file and drop it in raw/inbox/ instead."
            )
        if e.code == 429:
            return f"Error 429 Too Many Requests — {url}\nRate limited. Try again later."
        return f"HTTP {e.code} fetching {url}: {e.reason}"
    except urllib.error.URLError as e:
        return f"Error fetching {url}: {e}"
    except Exception as e:
        return f"Error: {e}"

    if "html" in ct.lower():
        import re as _re
        p = _Stripper()
        try: p.feed(raw.decode("utf-8", errors="replace"))
        except Exception: pass
        text = _re.sub(r"\s+", " ", "".join(p.chunks)).strip()
        if not text:
            return (
                f"Fetched {url} but extracted no text.\n"
                "The page may require JavaScript to render (e.g. a SPA or Cloudflare-protected site).\n"
                "Save the article manually and drop it in raw/inbox/ instead."
            )
    else:
        text = raw.decode("utf-8", errors="replace")
    return text[:50_000]


def _move_file(src: str, dst: str) -> str:
    s = REPO_ROOT / src
    d = REPO_ROOT / dst
    if not s.exists():
        return f"Error: source not found: {src}"
    try:
        s.resolve().relative_to((RAW_DIR / "inbox").resolve())
    except ValueError:
        return f"Error: move only permitted from raw/inbox/. Got: {src}"
    try:
        d.resolve().relative_to(RAW_DIR.resolve())
    except ValueError:
        return f"Error: destination must be inside raw/. Got: {dst}"
    d.parent.mkdir(parents=True, exist_ok=True)
    s.rename(d)
    return f"Moved {src} -> {dst}"


TOOL_FNS = {
    "read_file":  lambda a: _read_file(a["path"]),
    "write_file": lambda a: _write_file(a["path"], a["content"]),
    "list_dir":   lambda a: _list_dir(a["directory"]),
    "move_file":  lambda a: _move_file(a["src"], a["dst"]),
    "fetch_url":  lambda a: _fetch_url(a["url"]),
}

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name":        "read_file",
            "description": "Read any file in the repository (wiki pages, raw sources, CLAUDE.md, etc.).",
            "parameters":  {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path relative to repo root"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name":        "write_file",
            "description": "Write or overwrite a file. Only wiki/ is writable; raw/ is immutable.",
            "parameters":  {
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "Path relative to repo root, must be inside wiki/"},
                    "content": {"type": "string", "description": "Complete file content"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name":        "list_dir",
            "description": "List files and subdirectories in a directory.",
            "parameters":  {
                "type": "object",
                "properties": {"directory": {"type": "string", "description": "Directory path relative to repo root"}},
                "required": ["directory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name":        "move_file",
            "description": "Move a file from raw/inbox/ to raw/ (inbox processing workflow).",
            "parameters":  {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "Source path, must be inside raw/inbox/"},
                    "dst": {"type": "string", "description": "Destination path, must be inside raw/"},
                },
                "required": ["src", "dst"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name":        "fetch_url",
            "description": "Fetch a web page or URL and return its text content. Use for ingesting articles from URLs.",
            "parameters":  {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Full URL to fetch"}},
                "required": ["url"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def system_prompt() -> str:
    base = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    return (
        base
        + "\n\n---\n\n"
        "Tools available: read_file, write_file, list_dir, move_file, fetch_url.\n"
        "raw/ is immutable — write_file refuses writes there.\n"
        "Follow the workflows in CLAUDE.md exactly."
    )


def orientation_message() -> str:
    snippets = []
    for rel, max_lines in [
        ("wiki/index.md",    None),
        ("wiki/log.md",      60),
        ("wiki/overview.md", None),
    ]:
        p = REPO_ROOT / rel
        if p.exists():
            text = p.read_text(encoding="utf-8")
            if max_lines:
                text = "\n".join(text.splitlines()[:max_lines])
            snippets.append(f'<file path="{rel}">\n{text}\n</file>')
    return "Current wiki state:\n\n" + "\n\n".join(snippets)

# ---------------------------------------------------------------------------
# API error classification & rate limiting
# ---------------------------------------------------------------------------

def _max_retries() -> int:
    return cfg_int("llm", "max_retries", default=6)


def _retry_poll_interval() -> int:
    return cfg_int("llm", "retry_poll_interval", default=300)


def _rpm_max() -> int:
    return cfg_int("llm", "max_rpm", default=0)  # 0 = disabled


def _record_request() -> None:
    with _rpm_lock:
        _request_times.append(time.monotonic())


def _rpm_wait_sync() -> None:
    """Block (non-streaming) until we're under the configured RPM limit."""
    limit = _rpm_max()
    if not limit:
        return
    while True:
        with _rpm_lock:
            cutoff = time.monotonic() - 60.0
            while _request_times and _request_times[0] < cutoff:
                _request_times.popleft()
            if len(_request_times) < limit:
                return
        time.sleep(5)


def _rpm_wait_streaming():
    """Generator: yields a rate-limit event once then sleeps 5s chunks until under limit."""
    limit = _rpm_max()
    if not limit:
        return
    notified = False
    while True:
        with _rpm_lock:
            cutoff = time.monotonic() - 60.0
            while _request_times and _request_times[0] < cutoff:
                _request_times.popleft()
            if len(_request_times) < limit:
                return
        if not notified:
            yield json.dumps({"type": "retrying", "attempt": 0, "delay": 5, "max": 0,
                              "msg": "Rate limit reached — waiting for next window…"}) + "\n"
            notified = True
        time.sleep(5)


def _retry_delay(attempt: int, exc) -> float:
    """Seconds to wait before retry (1-based attempt). Respects Retry-After header."""
    base  = float(cfg_get("llm", "retry_base_delay", default=5))
    delay = base * (2.0 ** (attempt - 1))  # 5, 10, 20, 40, 80, 160
    try:
        ra = exc.response.headers.get("retry-after") if exc.response else None
        if ra:
            delay = max(delay, float(ra))
    except Exception:
        pass
    return delay


def _is_retryable(exc) -> bool:
    try:
        from openai import RateLimitError, InternalServerError, APIConnectionError, APITimeoutError
        return isinstance(exc, (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError))
    except ImportError:
        return False


def _error_message(exc) -> str:
    try:
        from openai import AuthenticationError, PermissionDeniedError, BadRequestError, NotFoundError
        if isinstance(exc, AuthenticationError):
            return "Authentication failed — check llm.api_key in config.json."
        if isinstance(exc, PermissionDeniedError):
            return "Permission denied — your API key may not have access to this model."
        if isinstance(exc, NotFoundError):
            return "Model not found — check llm.model in config.json."
        if isinstance(exc, BadRequestError):
            return f"Bad request: {exc.message}"
    except ImportError:
        pass
    return str(exc)


# ---------------------------------------------------------------------------
# Agentic loops
# ---------------------------------------------------------------------------

def _create(client, messages, system):
    """Non-streaming create with two-phase retry and RPM awareness."""
    kwargs = dict(
        model=cfg_get("llm", "model") or PROVIDERS.get(
            cfg_get("llm", "provider", "openai").lower(), PROVIDERS["openai"]
        )["default_model"],
        messages=[{"role": "system", "content": system}] + messages,
        tools=TOOL_DEFS,
        max_tokens=4096,
    )
    max_r = _max_retries()
    poll  = _retry_poll_interval()

    # Phase 1: exponential backoff
    for attempt in range(max_r + 1):
        _rpm_wait_sync()
        try:
            result = client.chat.completions.create(**kwargs)
            _record_request()
            return result
        except Exception as e:
            if not _is_retryable(e):
                raise
            if attempt < max_r:
                time.sleep(_retry_delay(attempt + 1, e))

    # Phase 2: poll every retry_poll_interval until provider recovers
    while True:
        time.sleep(poll)
        _rpm_wait_sync()
        try:
            result = client.chat.completions.create(**kwargs)
            _record_request()
            return result
        except Exception as e:
            if not _is_retryable(e):
                raise


def run_agent_turn(client, model: str, messages: list, system: str) -> list:
    """Run one user turn to completion (no streaming). Returns updated messages."""
    while True:
        resp = _create(client, messages, system)
        msg  = resp.choices[0].message

        stored: dict = {"role": "assistant"}
        if msg.content:
            stored["content"] = msg.content
        if msg.tool_calls:
            stored["tool_calls"] = [
                {
                    "id":       tc.id,
                    "type":     "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        messages.append(stored)

        if not msg.tool_calls:
            break

        for tc in msg.tool_calls:
            fn = TOOL_FNS.get(tc.function.name)
            try:
                args   = json.loads(tc.function.arguments)
                result = fn(args) if fn else f"Unknown tool: {tc.function.name}"
            except Exception as e:
                result = f"Error: {e}"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    return messages


def stream_agent_turn(client, model: str, messages: list, system: str) -> Generator:
    """
    Run one user turn and yield newline-delimited JSON events:
      {"type": "tool",      "name": "...", "arg": "..."}
      {"type": "text",      "content": "..."}
      {"type": "retrying",  "attempt": N, "delay": S, "max": M, ["msg": "..."]}
      {"type": "error",     "content": "..."}
      {"type": "done"}
    Phase 1: exponential backoff (max_retries attempts).
    Phase 2: poll every retry_poll_interval seconds indefinitely.
    Updates messages in-place so history can be saved after the generator finishes.
    """
    kwargs = dict(
        model=model,
        messages=None,
        tools=TOOL_DEFS,
        max_tokens=4096,
    )
    while True:
        kwargs["messages"] = [{"role": "system", "content": system}] + messages

        max_r = _max_retries()
        poll  = _retry_poll_interval()
        resp  = None

        # Phase 1: exponential backoff
        for attempt in range(max_r + 1):
            yield from _rpm_wait_streaming()
            try:
                resp = client.chat.completions.create(**kwargs)
                _record_request()
                break
            except Exception as e:
                if not _is_retryable(e):
                    yield json.dumps({"type": "error", "content": _error_message(e)}) + "\n"
                    yield json.dumps({"type": "done"}) + "\n"
                    return
                if attempt == max_r:
                    break  # exhausted phase 1 — fall through to phase 2
                delay = _retry_delay(attempt + 1, e)
                yield json.dumps({"type": "retrying", "attempt": attempt + 1,
                                  "delay": int(delay), "max": max_r}) + "\n"
                time.sleep(delay)

        # Phase 2: poll indefinitely until provider recovers
        if resp is None:
            poll_attempt = 0
            while True:
                poll_attempt += 1
                yield json.dumps({
                    "type":    "retrying",
                    "attempt": poll_attempt,
                    "delay":   poll,
                    "max":     -1,
                    "msg":     f"Provider unavailable — retrying in {poll}s (attempt {poll_attempt})",
                }) + "\n"
                time.sleep(poll)
                yield from _rpm_wait_streaming()
                try:
                    resp = client.chat.completions.create(**kwargs)
                    _record_request()
                    break
                except Exception as e:
                    if not _is_retryable(e):
                        yield json.dumps({"type": "error", "content": _error_message(e)}) + "\n"
                        yield json.dumps({"type": "done"}) + "\n"
                        return

        msg = resp.choices[0].message

        stored: dict = {"role": "assistant"}
        if msg.content:
            stored["content"] = msg.content
        if msg.tool_calls:
            stored["tool_calls"] = [
                {
                    "id":       tc.id,
                    "type":     "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        messages.append(stored)

        if msg.content:
            yield json.dumps({"type": "text", "content": msg.content}) + "\n"

        if not msg.tool_calls:
            break

        for tc in msg.tool_calls:
            fn = TOOL_FNS.get(tc.function.name)
            try:
                args        = json.loads(tc.function.arguments)
                arg_preview = str(list(args.values())[0])[:80] if args else ""
                result      = fn(args) if fn else f"Unknown tool: {tc.function.name}"
            except Exception as e:
                arg_preview = ""
                result      = f"Error: {e}"

            yield json.dumps({"type": "tool", "name": tc.function.name, "arg": arg_preview}) + "\n"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    yield json.dumps({"type": "done"}) + "\n"
