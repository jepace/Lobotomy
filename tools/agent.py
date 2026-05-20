#!/usr/bin/env python3
"""Shared LLM agent logic used by both the CLI (wiki.py) and web server (serve.py)."""

import collections
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Generator

log = logging.getLogger("lobotomy.agent")

sys.path.insert(0, str(Path(__file__).parent))
from config import cfg_get, cfg_int, cfg_api_key, cfg_provider

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
        "base_url":      "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.0-flash",
    },
    "openai": {
        "base_url":      "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
    "groq": {
        "base_url":      "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
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
    """Return (client_dict, model_name, error_string_or_None).
    client_dict has keys: api_key, endpoint.
    """
    provider_name = cfg_get("llm", "provider", "openai").lower()
    preset        = PROVIDERS.get(provider_name, PROVIDERS["openai"])

    p        = cfg_provider(provider_name)
    api_key  = cfg_api_key(provider_name) or preset.get("api_key", "")
    base_url = (p.get("api_base") or cfg_get("llm", "api_base") or preset.get("base_url", "https://api.openai.com/v1")).rstrip("/")
    model    = p.get("model") or cfg_get("llm", "model") or preset["default_model"]

    if not api_key:
        return None, None, (
            f"No API key for provider '{provider_name}'.\n"
            f"  Set llm.api_key in config.json."
        )

    client = {"api_key": api_key, "endpoint": f"{base_url}/chat/completions"}
    return client, model, None


# ---------------------------------------------------------------------------
# Minimal HTTP client for OpenAI-compatible APIs
# ---------------------------------------------------------------------------

class _LLMError(Exception):
    def __init__(self, msg: str, retryable: bool = False, retry_after: float = None):
        super().__init__(msg)
        self.retryable   = retryable
        self.retry_after = retry_after


def _llm_post(endpoint: str, api_key: str, payload: dict) -> dict:
    """POST payload to an OpenAI-compatible chat/completions endpoint."""
    log.debug("LLM POST %s model=%s messages=%d",
              endpoint, payload.get("model", "?"), len(payload.get("messages", [])))
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req  = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
            "User-Agent":    "llm-wiki/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            log.debug("LLM response ok: choices=%d", len(result.get("choices", [])))
            return result
    except urllib.error.HTTPError as e:
        body = {}
        raw_body = ""
        try:
            raw_body = e.read().decode("utf-8", errors="replace")
            body = json.loads(raw_body)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass
        msg = (
            body.get("error", {}).get("message", "") if isinstance(body, dict) else ""
        ) or e.reason or f"HTTP {e.code}"
        retry_after = None
        try:
            ra = e.headers.get("Retry-After")
            if ra:
                retry_after = float(ra)
        except (TypeError, ValueError):
            pass
        code = e.code
        log.warning("LLM HTTP %d: %s", code, msg)
        if code == 401:
            raise _LLMError("Authentication failed — check llm.api_key in config.json.")
        if code == 403:
            raise _LLMError("Permission denied — API key may lack access to this model.")
        if code == 404:
            raise _LLMError("Model not found — check llm.model in config.json.")
        if code == 400:
            # Include raw response for debugging 400 errors
            detail = f"{msg}\n{raw_body}" if raw_body and raw_body != msg else msg
            raise _LLMError(f"Bad request: {detail}")
        if code == 429:
            raise _LLMError(f"Rate limited: {msg}", retryable=True, retry_after=retry_after)
        if code >= 500:
            raise _LLMError(f"Server error {code}: {msg}", retryable=True)
        raise _LLMError(f"HTTP {code}: {msg}")
    except urllib.error.URLError as e:
        log.warning("LLM connection error: %s", e.reason)
        raise _LLMError(f"Connection error: {e.reason}", retryable=True)
    except TimeoutError:
        log.warning("LLM request timed out (120s)")
        raise _LLMError("Request timed out — provider too slow.", retryable=True)
    except OSError as e:
        log.warning("LLM network error: %s", e)
        raise _LLMError(f"Network error: {e}", retryable=True)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_RAW_READ_LIMIT  = 60_000  # chars for raw source files
_WIKI_READ_LIMIT = 20_000  # chars for wiki pages — generous but prevents context blowout


def _read_file(path: str, offset: int = 0) -> "str | list":
    """Return a string, or a list of content blocks for image/image-only PDF."""
    p = REPO_ROOT / path
    if not p.exists():
        return f"Error: not found: {path}"
    if not p.is_file():
        return f"Error: not a file: {path}"
    if p.suffix.lower() == ".pdf":
        return _read_pdf(p)
    if p.suffix.lower() in _IMAGE_EXTS:
        return _read_image(p)
    text = p.read_text(encoding="utf-8", errors="replace")
    # If this raw file is just a URL stub and we already fetched it, return the cached content
    try:
        p.resolve().relative_to(RAW_DIR.resolve())
        stripped = text.strip()
        global _current_inbox_url, _current_inbox_path, _session_updated_pages, _session_entity_pages, _current_source_page
        _current_inbox_path = str(p.resolve().relative_to(REPO_ROOT.resolve()))
        _current_inbox_url = ""  # always reset so stale URL from prior ingest isn't inherited
        _current_source_page = ""
        _session_entity_pages = []
        _session_updated_pages = []
        if stripped.startswith("http") and "\n" not in stripped:
            _current_inbox_url = stripped
            with _fetch_cache_lock:
                if stripped in _fetch_cache:
                    return _fetch_cache[stripped]
        else:
            # Raw item with frontmatter — extract url: field
            import re as _re
            _m = _re.search(r'^url:\s*["\']?([^\s"\'\n]+)', text, _re.MULTILINE)
            if _m:
                _current_inbox_url = _m.group(1).strip()
    except ValueError:
        pass
    try:
        p.resolve().relative_to(RAW_DIR.resolve())
        limit = _RAW_READ_LIMIT
    except ValueError:
        try:
            p.resolve().relative_to(WIKI_DIR.resolve())
            limit = _WIKI_READ_LIMIT
        except ValueError:
            limit = _RAW_READ_LIMIT
    # Strip system-owned frontmatter fields before exposing to LLM.
    # These are maintained exclusively by code; the LLM must never write them.
    # The rendered ## Sources section in the body is still returned.
    import re as _re2
    _fm = _re2.match(r'^(---\s*\n)(.*?\n)(---\s*\n)', text, _re2.DOTALL)
    if _fm:
        _stripped_fm = _re2.sub(
            r'^(sources|created|raw_source):[ \t]*.*\n?', '', _fm.group(2), flags=_re2.MULTILINE
        )
        text = _fm.group(1) + _stripped_fm + _fm.group(3) + text[_fm.end():]

    total = len(text)
    if offset:
        text = text[offset:]
    if len(text) > limit:
        remaining = total - offset - limit
        text = text[:limit] + f"\n\n[TRUNCATED — showing chars {offset}–{offset+limit} of {total} total. Call read_file with offset={offset+limit} to continue.]"
    elif offset:
        text = text + f"\n\n[END OF FILE — read chars {offset}–{total} of {total} total.]"
    return text


def _read_image(p) -> list:
    import base64, mimetypes
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    b64  = base64.b64encode(p.read_bytes()).decode()
    return [
        {"type": "text", "text": f"Image: {p.name}"},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]


def _read_pdf(p) -> "str | list":
    import base64, shutil, subprocess, tempfile

    # 1. pypdf — pure Python, handles most text-layer PDFs
    try:
        import pypdf
        reader = pypdf.PdfReader(str(p))
        pages  = [page.extract_text() or "" for page in reader.pages]
        text   = "\n\n".join(pages).strip()
        if text:
            return text
    except ImportError:
        pass
    except Exception as e:
        log.debug("pypdf failed for %s: %s", p.name, e)

    # 2. pdftotext (poppler) — better text extraction for some PDFs
    if shutil.which("pdftotext"):
        try:
            result = subprocess.run(
                ["pdftotext", "-layout", str(p), "-"],
                capture_output=True, timeout=30,
            )
            text = result.stdout.decode("utf-8", errors="replace").strip()
            if text:
                return text
        except (OSError, subprocess.SubprocessError) as e:
            log.debug("pdftotext failed for %s: %s", p.name, e)

    # 3. pdftoppm (poppler) — render pages as images for vision
    if shutil.which("pdftoppm"):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run(
                    ["pdftoppm", "-png", "-r", "150", "-l", "20", str(p),
                     f"{tmp}/page"],
                    capture_output=True, timeout=60, check=True,
                )
                png_files = sorted(Path(tmp).glob("page-*.png"))
                if not png_files:
                    png_files = sorted(Path(tmp).glob("page*.png"))
                if png_files:
                    blocks = [{"type": "text", "text":
                               f"PDF '{p.name}' rendered as images ({len(png_files)} page(s)):"}]
                    for i, png_path in enumerate(png_files[:20]):
                        b64 = base64.b64encode(png_path.read_bytes()).decode()
                        blocks.append({"type": "image_url",
                                       "image_url": {"url": f"data:image/png;base64,{b64}"}})
                    return blocks
        except (OSError, subprocess.SubprocessError) as e:
            log.debug("pdftoppm failed for %s: %s", p.name, e)

    return (
        f"Could not extract text from '{p.name}'. "
        "Install poppler for full PDF support: pkg install poppler"
    )


def _strip_broken_wiki_links(content: str, page_path: Path) -> str:
    """Strip all internal wiki links from body text, keeping display text.
    External links (http/mailto/#) are preserved.
    The autolinker re-adds correct internal links after every write."""
    import re
    def _check(m):
        text, target = m.group(1), m.group(2)
        if target.startswith("http") or target.startswith("#") or target.startswith("mailto"):
            return m.group(0)
        return text  # drop all internal links — autolinker handles them
    return re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _check, content)


_SOURCES_SECTION_TYPES = {"entity", "concept", "synthesis"}


def _inject_sources_section(content: str, page_path: Path) -> str:
    """Render a ## Sources section from frontmatter and append/replace it at the bottom.
    Only applies to entity, concept, and synthesis pages."""
    import re
    fm_match = re.match(r"^(---\s*\n.*?\n---\s*\n)", content, re.DOTALL)
    if not fm_match:
        return content
    fm_text = fm_match.group(1)

    type_m = re.search(r"^type:\s*(\S+)", fm_text, re.MULTILINE)
    if not type_m:
        return content
    pg_type = type_m.group(1)

    # Source pages get a simple ## Sources with URL and raw file link
    if pg_type == "source":
        body = content[len(fm_text):]
        body = re.sub(r"\n*^#{1,6} Sources\b.*", "", body, flags=re.DOTALL | re.MULTILINE).rstrip()
        url_m = re.search(r'^url:\s*["\']?([^"\'\n]+)["\']?', fm_text, re.MULTILINE)
        raw_m = re.search(r'^raw_source:\s*["\']?([^"\'\n]+)["\']?', fm_text, re.MULTILINE)
        lines = []
        if url_m or raw_m:
            lines.append("\n\n## Sources\n")
            if url_m:
                u = url_m.group(1).strip()
                lines.append(f"- **URL**: [{u}]({u})")
            if raw_m:
                raw_rel = raw_m.group(1).strip()
                up_count = len(page_path.relative_to(WIKI_DIR).parts)
                prefix = "../" * up_count
                lines.append(f"- **Raw**: [{raw_rel}]({prefix}{raw_rel})")
        return fm_text + body + ("\n".join(lines) + "\n" if lines else "\n")

    if pg_type not in _SOURCES_SECTION_TYPES:
        return content

    src_m = re.search(r"^sources:\s*\[([^\]]*)\]", fm_text, re.MULTILINE)
    source_paths = []
    if src_m and src_m.group(1).strip():
        for s in src_m.group(1).split(","):
            s = s.strip().strip('"').strip("'")
            if s:
                # Paths are wiki-root-relative per AGENT.md convention.
                # Fall back to page-relative resolution for legacy entries.
                wiki_root_candidate = WIKI_DIR / s
                if wiki_root_candidate.exists():
                    source_paths.append(s)
                else:
                    resolved = (page_path.parent / s).resolve()
                    found = False
                    try:
                        rel = str(resolved.relative_to(WIKI_DIR.resolve()))
                        if (WIKI_DIR / rel).exists():
                            s = rel
                            found = True
                    except ValueError:
                        pass
                    if not found:
                        # LLM may prefix with wrong subdir — search by basename.
                        basename = Path(s).name
                        matches = list(WIKI_DIR.rglob(basename))
                        if len(matches) == 1:
                            s = str(matches[0].relative_to(WIKI_DIR))
                    source_paths.append(s)

    # Strip existing ## Sources section (assumed to be at end of file)
    body = content[len(fm_text):]
    body = re.sub(r"\n*^#{1,6} Sources\b.*", "", body, flags=re.DOTALL | re.MULTILINE).rstrip()

    if not source_paths:
        return fm_text + body + "\n"

    # Compute path prefix relative to this page's directory
    up_count = len(page_path.parent.relative_to(WIKI_DIR).parts)
    prefix = "../" * up_count

    lines = ["\n\n## Sources\n"]
    for sp in source_paths:
        src_file = WIKI_DIR / sp
        title = sp
        suffix = ""
        if src_file.exists():
            src_text = src_file.read_text(encoding="utf-8", errors="replace")
            tm = re.match(r"^---\s*\n(.*?)\n---", src_text, re.DOTALL)
            if tm:
                url = None
                for line in tm.group(1).splitlines():
                    if line.startswith("title:"):
                        title = line.split(":", 1)[1].strip().strip('"')
                    elif line.startswith("url:"):
                        url = line.split(":", 1)[1].strip().strip('"').strip("'")
                suffix = ""  # no external links on entity/concept pages
        lines.append(f"- [{title}]({prefix}{sp}){suffix}")

    return fm_text + body + "\n".join(lines) + "\n"


def _atomic_write(p: Path, content: str) -> None:
    """Write content to p atomically: write to a sibling .tmp file, then rename."""
    global _title_map_cache
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, p)
        # Invalidate the title map cache whenever a wiki page changes so the
        # next autolink call rebuilds it with the new page included.
        try:
            p.resolve().relative_to(WIKI_DIR.resolve())
            _title_map_cache = None
        except ValueError:
            pass
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _update_file(path: str, content: str) -> str:
    p = REPO_ROOT / path
    try:
        p.resolve().relative_to(WIKI_DIR.resolve())
    except ValueError:
        return (
            f"Error: write refused — only wiki/ is writable. "
            f"raw/ is immutable. Got: {path}"
        )
    if p.resolve() == (WIKI_DIR / "log.md").resolve():
        return "Error: update_file refused on wiki/log.md — use prepend_log to add entries."
    if p.resolve() == (WIKI_DIR / "index.md").resolve():
        return "Error: update_file refused on wiki/index.md — it is auto-generated; use rebuild_index if needed."
    if not p.exists():
        return f"Error: update_file refused — {path} does not exist. Use create_file to create new pages."

    assert p.exists(), f"update_file invariant violated: {path} must exist"

    # Reject partial writes — update_file requires the complete file content.
    import re as _re
    if not content.lstrip().startswith("---"):
        existing = p.read_text(encoding="utf-8", errors="replace")
        if _re.match(r"^---\s*\n", existing):
            return (
                f"Error: update_file requires the complete file content including frontmatter. "
                f"You sent a fragment without frontmatter. Read {path} first, then resend the "
                f"full file with your changes incorporated."
            )

    _subdir = p.parent.name
    if _subdir in ("entities", "concepts"):
        # Always preserve sources already on disk — the LLM must never shrink this list.
        if _re.search(r"^sources:\s*\[", content, _re.MULTILINE):
            existing_sources: list[str] = []
            if p.exists():
                disk_text = p.read_text(encoding="utf-8", errors="replace")
                disk_src_m = _re.search(r"^sources:\s*\[([^\]]*)\]", disk_text, _re.MULTILINE)
                if disk_src_m and disk_src_m.group(1).strip():
                    for _s in disk_src_m.group(1).split(","):
                        _s = _s.strip().strip('"').strip("'")
                        if _s and _s not in existing_sources:
                            existing_sources.append(_s)
            if _current_source_page and _current_source_page not in existing_sources:
                existing_sources.append(_current_source_page)
            merged_str = ", ".join(f'"{s}"' for s in existing_sources)
            content = _re.sub(
                r"^sources:\s*\[[^\]]*\]",
                f"sources: [{merged_str}]",
                content, flags=_re.MULTILINE,
            )
    is_new = False  # update_file is update-only; create_file handles new pages
    assert not is_new, "update_file invariant violated: is_new should never be True here"
    wiki_rel = str(p.relative_to(WIKI_DIR))
    if wiki_rel not in _session_entity_pages and wiki_rel not in _session_updated_pages:
        _session_updated_pages.append(wiki_rel)
    # Restore system-owned scalar fields from disk — never trust LLM-supplied values.
    if not is_new:
        _disk_existing = p.read_text(encoding="utf-8", errors="replace")
        for _field in ("created", "raw_source"):
            _disk_m = _re.search(r"^" + _field + r":[ \t]*\S.*", _disk_existing, _re.MULTILINE)
            if _disk_m:
                if _re.search(r"^" + _field + r":", content, _re.MULTILINE):
                    content = _re.sub(r"^" + _field + r":.*", _disk_m.group(0), content, flags=_re.MULTILINE)
                else:
                    content = _re.sub(r"^(updated:.*)", r"\1\n" + _disk_m.group(0), content, flags=_re.MULTILINE, count=1)
    content = _strip_broken_wiki_links(content, p)
    content = _inject_sources_section(content, p)
    _atomic_write(p, content)
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


_fetch_cache: dict[str, str] = {}
_fetch_cache_lock = threading.Lock()

# Cache for the autolinker title map.  Stores (title, wiki-relative-path) pairs
# built by scanning all wiki page frontmatter.  Invalidated whenever any wiki
# page is written so it is rebuilt at most once per batch of autolink calls.
_title_map_cache: list[tuple[str, str]] | None = None  # (title, wiki_rel_path)
_current_inbox_url: str = ""
_current_inbox_path: str = ""
_session_updated_pages: list = []  # existing wiki pages written (not created) this session
_current_source_page: str = ""   # wiki/sources/ path created in this session
_session_entity_pages: list = [] # wiki/entities|concepts/ paths created this session


def _backfill_inbox_from_fetch(url: str, content: str) -> None:
    """If a raw file is just a URL stub pointing to `url`, replace its body with fetched content."""
    import json as _json
    inbox = RAW_DIR
    if not inbox.is_dir():
        return
    for f in inbox.iterdir():
        if not f.is_file() or f.name == "index.md" or f.name.startswith("."):
            continue
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Parse frontmatter and body
        meta = {}
        body = raw
        if raw.startswith("---"):
            lines = raw.split("\n")
            end = next((i for i, l in enumerate(lines[1:], 1) if l.rstrip() == "---"), -1)
            if end != -1:
                for line in lines[1:end]:
                    if ":" in line:
                        k, _, v = line.partition(":")
                        meta[k.strip()] = v.strip().strip('"')
                body = "\n".join(lines[end + 1:]).strip()
        # Match: frontmatter url field OR body is just the URL
        file_url = meta.get("url", "").strip()
        body_is_stub = body.strip() in ("", url)
        if file_url != url and not body_is_stub:
            continue
        # Only backfill if there's no substantial body yet
        if len(body.strip()) > len(url) + 20:
            continue
        # Rebuild with content as body
        if meta:
            fm_lines = ["---"]
            for k, v in meta.items():
                fm_lines.append(f"{k}: {_json.dumps(v) if (chr(34) in v or ':' in v) else v}")
            fm_lines.append("---")
            new_text = "\n".join(fm_lines) + "\n\n" + content
        else:
            new_text = content
        _atomic_write(f, new_text)
        log.debug("Backfilled fetched content into %s", f.name)
        return


def _fetch_url(url: str) -> str:
    with _fetch_cache_lock:
        if url in _fetch_cache:
            log.debug("fetch_url cache hit: %s", url)
            return _fetch_cache[url]
    import urllib.parse
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
        "Accept-Encoding": "identity",
        "DNT":             "1",
    }

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    try:
        req  = urllib.request.Request(url, headers=headers)
        with opener.open(req, timeout=15) as resp:
            ct  = resp.headers.get("Content-Type", "")
            raw = resp.read(2_000_000)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return (
                f"Error 403 Forbidden — {url}\n"
                "The site is blocking automated access. "
                "Save the article as a .txt/.html file and drop it in raw/ instead."
            )
        if e.code == 429:
            return f"Error 429 Too Many Requests — {url}\nRate limited. Try again later."
        return f"HTTP {e.code} fetching {url}: {e.reason}"
    except urllib.error.URLError as e:
        reason = str(e.reason) if hasattr(e, "reason") else str(e)
        if "timed out" in reason.lower() or "timeout" in reason.lower():
            msg = (
                f"Error: fetch timed out for {url}\n"
                "Do NOT retry fetch_url — the site is unreachable or too slow. "
                "Stop and ask the user to paste the article text instead."
            )
        else:
            msg = f"Error fetching {url}: {e}"
        with _fetch_cache_lock:
            _fetch_cache[url] = msg
        return msg
    except Exception as e:
        msg = f"Error: {e}"
        with _fetch_cache_lock:
            _fetch_cache[url] = msg
        return msg

    if "html" in ct.lower():
        import re as _re
        p = _Stripper()
        try:
            p.feed(raw.decode("utf-8", errors="replace"))
        except Exception as e:
            log.debug("HTML parse warning for %s: %s", url, e)
        text = _re.sub(r"\s+", " ", "".join(p.chunks)).strip()
        if not text:
            msg = (
                f"Fetched {url} but extracted no text.\n"
                "The page may require JavaScript to render (e.g. a SPA or Cloudflare-protected site).\n"
                "Save the article manually and drop it in raw/ instead."
            )
            with _fetch_cache_lock:
                _fetch_cache[url] = msg
            return msg
    else:
        text = raw.decode("utf-8", errors="replace")
    text = text[:50_000]
    with _fetch_cache_lock:
        _fetch_cache[url] = text
    _backfill_inbox_from_fetch(url, text)
    return text



def _linkify_log_paths(entry: str) -> str:
    """Replace bare wiki-relative .md paths in a log entry with titled markdown links.
    Paths are resolved relative to wiki/ (where log.md lives). Already-linked paths
    (inside [...](…)) are left alone."""
    import re
    combined = re.compile(
        r"(\[[^\]]*\]\([^)]*\))"           # group 1: existing link — skip
        r"|(?<!\()([^\s,\[]+\.md)\b"       # group 2: bare .md path
    )
    def _repl(m):
        if m.group(1):
            return m.group(1)
        raw_path = m.group(2)
        candidate = WIKI_DIR / raw_path
        if not candidate.exists():
            return m.group(2)
        text = candidate.read_text(encoding="utf-8", errors="replace")
        fm = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        title = None
        if fm:
            for line in fm.group(1).splitlines():
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"')
                    break
        if not title:
            return m.group(2)
        return f"[{title}]({raw_path})"
    return combined.sub(_repl, entry)


def _prepend_log(entry: str) -> str:
    """Prepend a log entry to wiki/log.md, preserving all existing entries."""
    log_path = WIKI_DIR / "log.md"
    if not log_path.exists():
        return "Error: wiki/log.md does not exist"
    entry = _linkify_log_paths(entry)
    text = log_path.read_text(encoding="utf-8")

    # Structure: frontmatter (---...---), prose paragraph, --- divider, then entries.
    # Find the end of the frontmatter block (the closing ***).
    fm_open = text.find("---")
    fm_close = text.find("\n---", fm_open + 3)
    if fm_close == -1:
        _atomic_write(log_path, text.rstrip() + "\n\n" + entry.strip() + "\n")
        return "Log entry prepended to wiki/log.md (no frontmatter close found)"

    # Find the --- divider that separates the prose intro from the entries.
    # fm_close points at the \n before the closing ---, so fm_close+4 skips past it.
    divider = text.find("\n---\n", fm_close + 4)
    if divider == -1:
        # No prose divider — insert right after the frontmatter close.
        insert_at = fm_close + 4  # character after the closing ---\n
        before = text[:insert_at]
        after  = text[insert_at:]
        _atomic_write(log_path, before + "\n" + entry.strip() + "\n\n" + after.lstrip("\n"))
        return "Log entry prepended to wiki/log.md (inserted after frontmatter)"

    # Insert new entry immediately after the divider.
    before = text[:divider + 5]   # up to and including \n---\n
    after  = text[divider + 5:]   # existing entries
    _atomic_write(log_path, before + "\n" + entry.strip() + "\n\n" + after.lstrip("\n"))
    return "Log entry prepended to wiki/log.md"


_DONE_SENTINEL = "__AGENT_DONE__:"

# Matches quoted wiki paths like 'sources/foo.md' or 'wiki/entities/bar.md'
_WIKI_PATH_RE = re.compile(r"'((?:wiki/)?(?:[\w-]+/)+[\w-]+\.md)'")


def _linkify_summary(text: str) -> str:
    """Turn quoted wiki paths in a done-summary into markdown links."""
    def _replace(m: re.Match) -> str:
        raw = m.group(1).removeprefix("wiki/")   # e.g. sources/foo.md
        url = "/wiki/" + raw.removesuffix(".md")  # e.g. /wiki/sources/foo
        return f"[{raw}]({url})"
    return _WIKI_PATH_RE.sub(_replace, text)


def _auto_write_log_entry() -> None:
    """Write a log entry automatically when done() fires and files were touched."""
    import datetime, re as _re
    today = datetime.date.today().isoformat()

    # Determine operation type
    operation = "ingest" if _current_inbox_path else "edit"

    # Read article title from the source page frontmatter.
    title = ""
    if _current_source_page:
        src_p = WIKI_DIR / _current_source_page
        if src_p.exists():
            text = src_p.read_text(encoding="utf-8", errors="replace")
            m = _re.search(r"^title:\s*\"?([^\"\n]+)\"?", text, _re.MULTILINE)
            if m:
                title = m.group(1).strip()
    if not title:
        title = "unknown"

    def _tag(wiki_rel: str) -> str:
        subdir = wiki_rel.split("/")[0] if "/" in wiki_rel else ""
        p = WIKI_DIR / wiki_rel
        name = wiki_rel.rsplit("/", 1)[-1].removesuffix(".md")
        pg_type = ""
        if p.exists():
            txt = p.read_text(encoding="utf-8", errors="replace")
            m = _re.search(r'^title:\s*"?([^"\n]+)"?', txt, _re.MULTILINE)
            if m:
                name = m.group(1).strip()
            tm = _re.search(r'^type:\s*(\S+)', txt, _re.MULTILINE)
            if tm:
                pg_type = tm.group(1).strip()
        letter = {"sources": "S", "entities": "E", "concepts": "C", "synthesis": "X"}.get(subdir) \
            or {"source": "S", "entity": "E", "concept": "C", "synthesis": "X", "overview": "X"}.get(pg_type, "?")
        link = f"[{name}]({wiki_rel})"
        return f"[{letter}] {link}"

    created = []
    if _current_source_page:
        created.append(_current_source_page)
    created.extend(p for p in _session_entity_pages if p != _current_source_page)

    updated = [p for p in _session_updated_pages if p not in created]

    # Skip if nothing was touched
    if not created and not updated:
        return

    lines = [f"## [{today}] {operation} | {title}", ""]
    lines.append(f"- **Operation**: {operation}")
    if _current_inbox_path:
        raw_link = f"[R] [{_current_inbox_path}](../{_current_inbox_path})"
        lines.append(f"- **Source file**: {raw_link}")

    if created:
        lines.append(f"- **Documents created**: {', '.join(_tag(p) for p in created)}")

    if updated:
        lines.append(f"- **Documents updated**: {', '.join(_tag(p) for p in updated)}")

    _prepend_log("\n".join(lines))


def _post_process_session() -> None:
    """Run once when done() is called: patch sources: frontmatter, autolink all
    created/updated pages, re-inject ## Sources sections, rebuild index."""
    import re as _re

    all_pages = list(dict.fromkeys(_session_entity_pages + _session_updated_pages))

    # Ensure _current_source_page is in sources: for pages *created* this session only.
    # Pre-existing pages that the LLM happened to update should not inherit this source.
    if _current_source_page:
        for wiki_rel in list(dict.fromkeys(_session_entity_pages)):
            ep_path = WIKI_DIR / wiki_rel
            if not ep_path.exists():
                continue
            try:
                ep_content = ep_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            src_m = _re.search(r"^sources:\s*\[([^\]]*)\]", ep_content, _re.MULTILINE)
            if src_m:
                existing = [s.strip().strip('"').strip("'") for s in src_m.group(1).split(",") if s.strip().strip('"').strip("'")]
                if _current_source_page in existing:
                    continue
                new_src_str = ", ".join(f'"{s}"' for s in [_current_source_page] + existing)
                ep_content = _re.sub(r"^sources:\s*\[[^\]]*\]", f"sources: [{new_src_str}]", ep_content, flags=_re.MULTILINE)
            else:
                # sources: field missing entirely — insert before the closing --- of frontmatter
                fm_match = _re.match(r"^---\s*\n.*?\n(---\s*\n)", ep_content, _re.DOTALL)
                if not fm_match:
                    continue
                insert_at = fm_match.start(1)
                ep_content = ep_content[:insert_at] + f'sources: ["{_current_source_page}"]\n' + ep_content[insert_at:]
            ep_content = ep_content.replace("<!-- WARNING: no sources cited — update sources: frontmatter -->\n\n", "")
            _atomic_write(ep_path, ep_content)
            log.debug("post_process: added %s to sources: of %s", _current_source_page, wiki_rel)

    # Autolink all pages touched this session (all entities now exist, so all titles resolve).
    for wiki_rel in all_pages:
        _autolink({"path": f"wiki/{wiki_rel}"})

    # Re-inject ## Sources on all entity/concept pages touched this session.
    for wiki_rel in list(dict.fromkeys(_session_entity_pages + _session_updated_pages)):
        ep_path = WIKI_DIR / wiki_rel
        if not ep_path.exists():
            continue
        try:
            ep_content = ep_path.read_text(encoding="utf-8", errors="replace")
            new_content = _inject_sources_section(ep_content, ep_path)
            if new_content != ep_content:
                _atomic_write(ep_path, new_content)
        except OSError:
            continue

    _rebuild_index({})


def _done(args: dict) -> str:
    ingested = "1" if args.get("ingested") else "0"
    return _DONE_SENTINEL + ingested + "|" + args.get("summary", "")


def _rebuild_index(args: dict) -> str:
    """Rebuild wiki/index.md, subdirectory indexes, and raw/index.md."""
    import re
    _t0_rebuild = time.time()

    def parse_title_updated(text: str) -> tuple[str, str]:
        m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        title = updated = ""
        if m:
            for line in m.group(1).splitlines():
                if line.startswith("title:") and not title:
                    title = line.split(":", 1)[1].strip().strip('"')
                elif line.startswith("updated:") and not updated:
                    updated = line.split(":", 1)[1].strip().strip('"')
        return title, updated

    def first_desc_line(text: str) -> str:
        in_fm, fm_done = False, False
        for line in text.splitlines():
            if line.strip() == "---":
                if not in_fm:
                    in_fm = True
                elif not fm_done:
                    fm_done = True
                continue
            if not fm_done:
                continue
            s = line.strip()
            if s and not s.startswith("#"):
                return s[:120]
        return ""

    sections = [("Sources", "sources"), ("Entities", "entities"),
                ("Concepts", "concepts"), ("Synthesis", "synthesis")]
    today = __import__("datetime").date.today().isoformat()
    blocks = []

    for heading, subdir in sections:
        d = WIKI_DIR / subdir
        entries = []
        if d.is_dir():
            for f in sorted(d.glob("*.md")):
                if f.name == "index.md":
                    continue
                text = f.read_text(encoding="utf-8", errors="replace")
                title, updated = parse_title_updated(text)
                title   = title or f.stem
                updated = updated or today
                desc    = first_desc_line(text)
                line    = f"- [{title}]({subdir}/{f.name})"
                if desc:
                    line += f" — {desc}"
                line += f" *(updated: {updated})*"
                entries.append((title.lower(), line))
        entries.sort(key=lambda x: x[0])
        block = f"## {heading}\n\n"
        block += ("\n".join(e for _, e in entries) if entries
                  else f"_No {subdir} pages yet._")
        blocks.append(block)

    index_path = WIKI_DIR / "index.md"
    existing   = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    cut = existing.find("\n## ")
    prose = (existing[:cut].rstrip() if cut != -1 else existing.rstrip())
    prose = re.sub(r"_Last updated: \d{4}-\d{2}-\d{2}_", f"_Last updated: {today}_", prose)
    _atomic_write(index_path, prose + "\n\n---\n\n" + "\n\n---\n\n".join(blocks) + "\n")

    # Per-subdirectory wiki index files
    section_blocks = dict(zip([s for _, s in sections], blocks))
    for heading, subdir in sections:
        d = WIKI_DIR / subdir
        if not d.is_dir():
            continue
        sub_index = d / "index.md"
        existing_sub = sub_index.read_text(encoding="utf-8") if sub_index.exists() else ""
        divider_sub = existing_sub.find(f"\n---\n\n## {heading}")
        if divider_sub != -1:
            prose_sub = existing_sub[:divider_sub].rstrip()
        elif existing_sub.strip():
            prose_sub = existing_sub.rstrip()
        else:
            prose_sub = f"# {heading}\n\n_Last updated: {today}_"
        prose_sub = re.sub(r"_Last updated: \d{4}-\d{2}-\d{2}_", f"_Last updated: {today}_", prose_sub)
        block_local = section_blocks[subdir].replace(f"({subdir}/", "(")
        _atomic_write(sub_index, prose_sub + "\n\n---\n\n" + block_local + "\n")

    # raw/index.md — parallel to wiki subdirectory indexes
    raw_entries = []
    for f in sorted(RAW_DIR.iterdir()):
        if not f.is_file() or f.name == "index.md" or f.name.startswith("."):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
            meta: dict = {}
            if m:
                for line in m.group(1).splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip().strip('"').strip("'")
            title      = meta.get("title") or f.stem.replace("-", " ").title()
            added      = meta.get("added", "")
            wikified   = meta.get("wikified", "false").lower() == "true"
            wiki_page  = meta.get("wiki_page", "")
            status     = "✓" if wikified else "—"
            wiki_link  = f"[wiki](../wiki/{wiki_page})" if wiki_page else ""
            raw_entries.append(f"| [{f.name}]({f.name}) | {title} | {added} | {status} | {wiki_link} |")
        except Exception:
            raw_entries.append(f"| [{f.name}]({f.name}) | | | | |")

    raw_index_path = RAW_DIR / "index.md"
    raw_header = (
        f"# Raw Sources\n\n_Last updated: {today}_\n\n"
        "| File | Title | Added | Wikified | Wiki Page |\n"
        "|------|-------|-------|----------|-----------|\n"
    )
    _atomic_write(raw_index_path, raw_header + "\n".join(raw_entries) + "\n")

    total = sum(
        sum(1 for f in (WIKI_DIR / s).glob("*.md") if f.name != "index.md")
        for _, s in sections if (WIKI_DIR / s).is_dir()
    )
    raw_total = len(raw_entries)
    log.debug("rebuild_index: done in %.1fs (%d wiki pages, %d raw files)", time.time() - _t0_rebuild, total, raw_total)
    return (f"Rebuilt wiki/index.md ({total} pages), subdirectory indexes, "
            f"and raw/index.md ({raw_total} raw files).")


def heal_index_if_stale() -> None:
    """Rebuild wiki/index.md if any wiki page is newer than it — call at startup."""
    index_path = WIKI_DIR / "index.md"
    index_mtime = index_path.stat().st_mtime if index_path.exists() else 0.0
    stale = False
    for subdir in ("sources", "entities", "concepts", "synthesis"):
        d = WIKI_DIR / subdir
        if not d.is_dir():
            continue
        for f in d.glob("*.md"):
            if f.name != "index.md" and f.stat().st_mtime > index_mtime:
                stale = True
                break
        if stale:
            break
    if stale:
        log.info("wiki/index.md is stale — rebuilding on startup")
        _rebuild_index({})


def _title_alts(title: str) -> str:
    """
    Return a regex alternation string matching `title` bare OR with exactly one
    contiguous sub-span of words already wrapped in a markdown link.
    This lets the autolinker upgrade partial links like
      'CASA of [Monterey County](url)' → '[CASA of Monterey County](new_url)'.
    """
    import re
    words = title.split()
    n = len(words)

    def _esc(ws: list) -> str:
        return r"\s+".join(re.escape(w) for w in ws)

    alts = [_esc(words)]  # bare match
    for s in range(n):
        for e in range(s + 1, n + 1):
            if s == 0 and e == n:
                continue  # whole title already linked — skip
            pre, span, post = words[:s], words[s:e], words[e:]
            p = ""
            if pre:  p += _esc(pre) + r"\s+"
            p += r"\[" + _esc(span) + r"\]\([^)]*\)"
            if post: p += r"\s+" + _esc(post)
            alts.append(p)
    return "(?:" + "|".join(alts) + ")"


def _autolink(args: dict) -> str:
    """Replace all bare occurrences of each other wiki page title with a markdown link."""
    import re

    target_str = args.get("path", "")
    target_p   = REPO_ROOT / target_str
    if not target_p.exists():
        return f"Error: not found: {target_str}"
    try:
        target_p.resolve().relative_to(WIKI_DIR.resolve())
    except ValueError:
        return "Error: autolink only works on wiki/ pages."
    _t0_autolink = time.time()

    # Build (or reuse) the cached title map.  The cache stores wiki-root-relative
    # paths; we convert to target-relative below.  Invalidated by _atomic_write.
    global _title_map_cache
    if _title_map_cache is None:
        raw: list[tuple[str, str]] = []   # (title_or_alias, wiki_rel_path_str)
        seen: set[str] = set()
        for subdir in ("entities", "concepts", "synthesis", "sources"):
            d = WIKI_DIR / subdir
            if not d.is_dir():
                continue
            for f in d.glob("*.md"):
                if f.name == "index.md":
                    continue
                text = f.read_text(encoding="utf-8", errors="replace")
                m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
                if not m:
                    continue
                fm_lines = m.group(1).splitlines()
                title = None
                aliases: list[str] = []
                no_autolink = False
                i = 0
                while i < len(fm_lines):
                    line = fm_lines[i]
                    if line.startswith("title:"):
                        title = line.split(":", 1)[1].strip().strip('"')
                    elif line.startswith("no_autolink:"):
                        val = line.split(":", 1)[1].strip().lower()
                        no_autolink = val in ("true", "yes", "1")
                    elif line.startswith("aliases:"):
                        rest = line.split(":", 1)[1].strip()
                        if rest.startswith("["):
                            import json
                            try:
                                aliases = json.loads(rest)
                            except Exception:
                                pass
                        else:
                            j = i + 1
                            while j < len(fm_lines) and fm_lines[j].startswith("- "):
                                aliases.append(fm_lines[j][2:].strip().strip('"'))
                                j += 1
                    i += 1
                if title and not no_autolink:
                    wiki_rel = str(f.relative_to(WIKI_DIR))
                    key = title.lower()
                    if key not in seen:
                        seen.add(key)
                        raw.append((title, wiki_rel))
                    for alias in aliases:
                        if alias:
                            akey = alias.lower()
                            if akey not in seen:
                                seen.add(akey)
                                raw.append((alias, wiki_rel))
        raw.sort(key=lambda x: -len(x[0]))
        _title_map_cache = raw
        log.debug("autolink: built title map with %d entries", len(raw))

    # Convert cached wiki-relative paths to paths relative to this target file.
    prefix = "../" * len(target_p.parent.relative_to(WIKI_DIR).parts)
    title_map = [
        (title, prefix + wiki_rel)
        for title, wiki_rel in _title_map_cache
        if WIKI_DIR / wiki_rel != target_p
    ]

    if not title_map:
        return "No other wiki pages found to link."

    content  = target_p.read_text(encoding="utf-8", errors="replace")
    fm_match = re.match(r"^(---\s*\n.*?\n---\s*\n)", content, re.DOTALL)
    frontmatter, body = (fm_match.group(1), content[len(fm_match.group(1)):]) if fm_match else ("", content)

    linked = 0
    for title, link_path in title_map:
        # Group 1: an existing complete link — pass through unchanged (never nest links inside).
        # Group 2: the title bare or with a sub-span already linked — replace with new link.
        combined = re.compile(
            r"(\[[^\]]*\]\([^)]*\))"
            r"|(?<!\w)(" + _title_alts(title) + r")(?!\w)",
            re.IGNORECASE,
        )
        def _replacer(m, _lp=link_path):
            if m.group(1):           # existing complete link — keep as-is
                return m.group(1)
            # Strip any inner link syntax (e.g. [Monterey County](url) → Monterey County)
            display = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", m.group(2))
            return f"[{display}]({_lp})"

        new_lines = []
        for line in body.split("\n"):
            if re.match(r"^#{1,6}\s", line):
                new_lines.append(line)
            else:
                new_lines.append(combined.sub(_replacer, line))
        new_body = "\n".join(new_lines)
        if new_body != body:
            body = new_body
            linked += 1

    result = body

    new_content = frontmatter + result
    elapsed = time.time() - _t0_autolink
    if new_content == content:
        log.debug("autolink: no changes in %s (%.1fs, %d titles)", target_str, elapsed, len(title_map))
        return f"Autolink: no changes in {target_str}."
    _atomic_write(target_p, new_content)
    log.debug("autolink: linked %d title(s) in %s (%.1fs, %d titles checked)", linked, target_str, elapsed, len(title_map))
    return f"Autolinked {linked} title(s) in {target_str}."


def _fix_wiki_links(_args: dict) -> str:
    """Scan all wiki pages and fix relative links missing a ../ prefix."""
    import re

    SUBDIRS = ("sources", "entities", "concepts", "synthesis")
    bad_link_re = re.compile(
        r'\((\.\.\./)*((?:' + '|'.join(SUBDIRS) + r')/[^)#\s]+\.md)\)'
    )

    total_fixed = 0
    pages_fixed = 0

    for sd in SUBDIRS:
        d = WIKI_DIR / sd
        if not d.is_dir():
            continue
        for page in d.glob("*.md"):
            if page.name == "index.md":
                continue
            text = page.read_text(encoding="utf-8", errors="replace")
            original = text

            def _fix(m, _text=None):
                inner = m.group(2)
                return f"(../{inner})"

            text, n = bad_link_re.subn(_fix, text)

            if n and text != original:
                _atomic_write(page, text)
                total_fixed += n
                pages_fixed += 1

    if total_fixed == 0:
        return "No bad wiki links found — nothing to fix."
    return f"Fixed {total_fixed} bad link(s) across {pages_fixed} page(s)."


def _search_wiki(args: dict) -> str:
    """Keyword search across all wiki pages. Returns matching pages with title, path, snippet.
    Supports in:<subdir> scope token (e.g. 'california in:sources')."""
    import re
    query = args.get("query", "").strip()
    if not query:
        return "Error: query is required."
    scope = None
    required_tag = None
    kw_tokens = []
    for t in query.split():
        tl = t.lower()
        if tl.startswith("in:"):
            scope = tl[3:].strip("/")
        elif tl.startswith("tag:"):
            required_tag = tl[4:]
        else:
            kw_tokens.append(t)
    keywords = kw_tokens
    if not keywords and not required_tag:
        return "Error: no search keywords provided (only filter tokens found)."
    patterns = [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords]
    valid_subdirs = {"sources", "entities", "concepts", "synthesis"}
    if scope and scope in valid_subdirs:
        search_root = WIKI_DIR / scope
        exclude_sources = False
    else:
        search_root = WIKI_DIR
        exclude_sources = True  # sources excluded unless in:sources requested

    _META_STEMS = {"log", "overview", "index", "lint"}
    results = []
    for f in sorted(search_root.rglob("*.md")):
        if exclude_sources and f.is_relative_to(WIKI_DIR / "sources"):
            continue
        if f.stem in _META_STEMS:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        # tag: filter
        if required_tag:
            if not fm:
                continue
            tags_line = next((l for l in fm.group(1).splitlines() if l.startswith("tags:")), "")
            page_tags = [t.strip().strip('"').lower() for t in tags_line.split(":", 1)[-1].strip().strip("[]").split(",") if t.strip().strip('"')]
            if required_tag not in page_tags:
                continue
        # Strip system-owned frontmatter fields and link URLs before scoring.
        # Prevents filenames embedded in sources: from creating false matches,
        # and stops system metadata from leaking into snippets shown to the LLM.
        _sys_fields = re.compile(r'^(sources|created|raw_source):[ \t].*', re.MULTILINE)
        searchable = _sys_fields.sub('', text)
        searchable = re.sub(r'\]\([^)]*\)', ']()', searchable)
        # AND logic: every keyword must appear at least once.
        if patterns and not all(p.search(searchable) for p in patterns):
            continue
        score = sum(len(p.findall(searchable)) for p in patterns) if patterns else 1
        if not score:
            continue
        # Extract title from frontmatter
        title = f.stem
        if fm:
            for line in fm.group(1).splitlines():
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"')
                    break
        # Find first matching line for snippet — skip system-owned frontmatter lines
        _skip_snippet = re.compile(r'^(sources|created|raw_source):')
        snippet = ""
        for line in text.splitlines():
            if _skip_snippet.match(line.strip()):
                continue
            if patterns and any(p.search(line) for p in patterns):
                snippet = line.strip()[:120]
                break
        rel = str(f.relative_to(WIKI_DIR))
        results.append((score, title, rel, snippet))

    results.sort(key=lambda x: -x[0])
    scope_desc = f" in {scope}/" if scope and scope in valid_subdirs else ""
    if not results:
        return f"No wiki pages found matching: {' '.join(keywords)}{scope_desc}"
    lines = [f"Found {len(results)} page(s) matching '{' '.join(keywords)}'{scope_desc}:\n"]
    for _, title, rel, snippet in results[:10]:
        lines.append(f"- [{title}]({rel})")
        if snippet:
            lines.append(f"  > {snippet}")
    return "\n".join(lines)


def _search_raw(args: dict) -> str:
    """Keyword search across all raw source files. Returns filename, source URL, and snippet."""
    import re
    query = args.get("query", "").strip()
    if not query:
        return "Error: query is required."
    keywords = query.split()
    if not keywords:
        return "Error: no search keywords provided."
    patterns = [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords]

    results = []
    for f in sorted(RAW_DIR.rglob("*")):
        if not f.is_file() or f.name.startswith(".") or f.name == "index.md":
            continue
        if f.suffix not in (".md", ".txt", ".html", ".url"):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Strip link URLs so path tokens don't create false matches
        searchable = re.sub(r'\]\([^)]*\)', ']()', text)
        if not all(p.search(searchable) for p in patterns):
            continue
        score = sum(len(p.findall(searchable)) for p in patterns)
        # Extract title and url from frontmatter if present
        title = f.stem
        source_url = ""
        fm = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        if fm:
            for line in fm.group(1).splitlines():
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"')
                elif line.startswith("url:"):
                    source_url = line.split(":", 1)[1].strip().strip('"')
        # Find first matching line for snippet
        snippet = ""
        body_start = (text.index("---", 3) + 3) if text.startswith("---") and "---" in text[3:] else 0
        for line in text[body_start:].splitlines():
            if any(p.search(line) for p in patterns):
                snippet = line.strip()[:120]
                break
        rel = str(f.relative_to(REPO_ROOT))
        results.append((score, title, rel, source_url, snippet))

    results.sort(key=lambda x: -x[0])
    if not results:
        return f"No raw files found matching: {' '.join(keywords)}"
    lines = [f"Found {len(results)} raw file(s) matching '{' '.join(keywords)}':\n"]
    for _, title, rel, source_url, snippet in results[:15]:
        url_suffix = f" — {source_url}" if source_url else ""
        lines.append(f"- {rel} ({title}){url_suffix}")
        if snippet:
            lines.append(f"  > {snippet}")
    return "\n".join(lines)


def _create_file(args: dict) -> str:
    """Write a new wiki page with auto-populated frontmatter."""
    global _current_source_page, _session_entity_pages
    import datetime, re
    path    = args.get("path", "")
    title   = args.get("title", "")
    pg_type = args.get("type", "")
    tags    = args.get("tags", [])
    body    = args.get("body", "")
    sources = args.get("sources", [])
    url     = args.get("url", "")
    if not url and pg_type == "source":
        url = _current_inbox_url

    if not path or not title or not pg_type:
        return "Error: path, title, and type are required."

    p = REPO_ROOT / path
    try:
        p.resolve().relative_to(WIKI_DIR.resolve())
    except ValueError:
        return f"Error: create_file only writes inside wiki/. Got: {path}"

    _subdir = p.parent.name

    if p.exists():
        return f"Error: create_file refused — {path} already exists. Use update_file to update existing pages."

    # Only one source page per ingest session.
    if _subdir == "sources" and _current_source_page:
        return (f"Error: create_file refused — a source page ({_current_source_page}) was already "
                f"created this session. Each ingest produces exactly one source page.")
    if _subdir in ("entities", "concepts"):
        # Always derive sources from the current session source page; ignore LLM-supplied value.
        sources = [_current_source_page] if _current_source_page else []
    _missing_sources = _subdir in ("entities", "concepts") and not sources

    today = datetime.date.today().isoformat()
    created = today
    raw_source = ""

    if pg_type == "source" and not raw_source:
        raw_source = _current_inbox_path

    tag_str = ", ".join(f'"{t}"' for t in (tags if isinstance(tags, list) else [tags]) if t is not None)
    src_str = ", ".join(f'"{s}"' for s in (sources if isinstance(sources, list) else [sources]) if s is not None)
    url_line = f'url: "{url}"\n' if url else ""
    raw_source_line = f'raw_source: "{raw_source}"\n' if raw_source else ""
    frontmatter = (
        f'---\ntitle: "{title}"\ntype: {pg_type}\ntags: [{tag_str}]\n'
        f'created: {created}\nupdated: {today}\nsources: [{src_str}]\n{url_line}{raw_source_line}---\n\n'
    )
    body_text = body.lstrip("\n")
    # Strip frontmatter the LLM accidentally included in body (would produce a duplicate --- block).
    if body_text.startswith("---"):
        _fm_strip = re.match(r'^---\s*\n.*?\n---\s*\n', body_text, re.DOTALL)
        if _fm_strip:
            body_text = body_text[_fm_strip.end():]
    if _missing_sources:
        body_text = "<!-- WARNING: no sources cited — update sources: frontmatter -->\n\n" + body_text
    content = frontmatter + _strip_broken_wiki_links(body_text, p)
    content = _inject_sources_section(content, p)
    p.parent.mkdir(parents=True, exist_ok=True)
    assert not p.exists(), f"create_file invariant violated: {path} must not exist before write"
    _atomic_write(p, content)
    assert p.exists(), f"create_file invariant violated: {path} must exist after write"

    wiki_rel = str(p.relative_to(WIKI_DIR))
    if wiki_rel not in _session_entity_pages:
        _session_entity_pages.append(wiki_rel)

    if _subdir == "sources":
        _current_source_page = str(p.relative_to(WIKI_DIR))

    action = "Created"
    suffix = " — WARNING: no sources cited, update sources: frontmatter before calling done()" if _missing_sources else ""
    return f"{action} {path} ({len(content)} bytes){suffix}"


def _validate_ingest(args: dict) -> str:
    """
    Run all self-check steps for a completed ingest:
    - Frontmatter completeness on all new/touched pages
    - Broken internal links
    - Pages missing from index
    Returns a structured pass/fail report.
    """
    import re

    source_slug = args.get("source_slug", "")
    # Collect all wiki pages to check (full wiki scan is safe — files are small)
    all_pages = list(WIKI_DIR.rglob("*.md"))
    index_text = (WIKI_DIR / "index.md").read_text(encoding="utf-8") if (WIKI_DIR / "index.md").exists() else ""

    required_fields = {"title", "type", "tags", "created", "updated", "sources"}
    broken_links, missing_fm, not_indexed = [], [], []

    for f in all_pages:
        if f.name in ("index.md", "log.md", "overview.md", "reading-list.md", "tasks.md", "tasks-archive.md"):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Check frontmatter
        fm_match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        if fm_match:
            fm_keys = {line.split(":", 1)[0].strip() for line in fm_match.group(1).splitlines() if ":" in line}
            missing = required_fields - fm_keys
            if missing:
                missing_fm.append(f"{f.relative_to(WIKI_DIR)}: missing {', '.join(sorted(missing))}")
        else:
            missing_fm.append(f"{f.relative_to(WIKI_DIR)}: no frontmatter")

        # Check internal links
        for link_text, link_path in re.findall(r'\[([^\]]+)\]\(([^)]+)\)', text):
            if link_path.startswith("http") or link_path.startswith("#"):
                continue
            target = (f.parent / link_path).resolve()
            if not target.exists():
                broken_links.append(f"{f.relative_to(WIKI_DIR)}: [{link_text}]({link_path})")

        # Check index coverage (skip operational files at wiki root)
        if f.parent != WIKI_DIR:
            rel = str(f.relative_to(WIKI_DIR))
            if rel not in index_text:
                not_indexed.append(rel)

    lines = ["## Ingest Validation Report\n"]
    ok = True

    if broken_links:
        ok = False
        lines.append(f"### ❌ Broken links ({len(broken_links)})")
        lines.extend(f"  - {b}" for b in broken_links[:20])
    else:
        lines.append("### ✓ No broken links")

    if missing_fm:
        ok = False
        lines.append(f"\n### ❌ Missing frontmatter fields ({len(missing_fm)})")
        lines.extend(f"  - {m}" for m in missing_fm[:20])
    else:
        lines.append("### ✓ All frontmatter complete")

    if not_indexed:
        ok = False
        lines.append(f"\n### ❌ Pages not in index ({len(not_indexed)})")
        lines.extend(f"  - {p}" for p in not_indexed[:20])
        lines.append("  → Fix: index is rebuilt automatically at end of wikification")
    else:
        lines.append("### ✓ All pages in index")

    lines.append(f"\n**{'PASS' if ok else 'FAIL'}** — {len(all_pages)} pages checked.")
    return "\n".join(lines)


TOOL_FNS = {
    "read_file":       lambda a: _read_file(a["path"], int(a.get("offset", 0))),
    "update_file":      lambda a: _update_file(a["path"], a["content"]),
    "list_dir":         lambda a: _list_dir(a["directory"]),
    "fetch_url":        lambda a: _fetch_url(a["url"]),

    "search_wiki":      _search_wiki,
    "search_raw":       _search_raw,
    "create_file":      _create_file,

    "done":             _done,
}

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name":        "read_file",
            "description": (
                "Read any file in the repository (wiki pages, raw sources, CLAUDE.md, etc.). "
                "Large files are truncated; the truncation message tells you the total size and "
                "the next offset to use. Call again with offset=N to read subsequent pages."
            ),
            "parameters":  {
                "type": "object",
                "properties": {
                    "path":   {"type": "string",  "description": "Path relative to repo root"},
                    "offset": {"type": "integer", "description": "Character offset to start reading from (default 0)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name":        "update_file",
            "description": "Update an existing wiki page. Requires the complete file content. Fails if the file does not exist — use create_file for new pages.",
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
            "name":        "fetch_url",
            "description": "Fetch a web page or URL and return its text content. Use for ingesting articles from URLs.",
            "parameters":  {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Full URL to fetch"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name":        "done",
            "description": (
                "Signal that you have finished ALL your work for this task. "
                "You MUST call this tool when your task is complete — do not simply stop responding. "
                "Provide a concise summary of what you accomplished. "
                "Set ingested=true ONLY if you successfully created a new wiki source page during this session. "
                "Set ingested=false if the source file contained fetch_failed:true, had no readable content, "
                "or if you could not create a meaningful wiki page."
            ),
            "parameters":  {
                "type": "object",
                "properties": {
                    "summary": {
                        "type":        "string",
                        "description": "What you accomplished — files created/updated, actions taken.",
                    },
                    "ingested": {
                        "type":        "boolean",
                        "description": "True only if a new wiki source page was created in this session. Must be false if the raw file had fetch_failed:true or contained no article content.",
                    },
                },
                "required": ["summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name":        "search_wiki",
            "description": (
                "Keyword search across wiki pages. Returns matching page titles, paths, and "
                "a snippet of the matching line. Use this to check whether an entity or concept "
                "already has a page before creating one. By default excludes wiki/sources/ pages "
                "(use 'in:sources' scope token to search sources explicitly, e.g. 'california in:sources')."
            ),
            "parameters":  {
                "type": "object",
                "properties": {
                    "query": {
                        "type":        "string",
                        "description": "One or more keywords to search for (space-separated, AND logic — all keywords must appear in the page).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name":        "search_raw",
            "description": (
                "Keyword search across all raw source files in raw/. Returns filename, "
                "original URL, and a snippet of the matching line. Use this to find raw "
                "sources that mention a person, topic, or event — especially useful when "
                "retroactively reviewing old articles for a newly prominent entity."
            ),
            "parameters":  {
                "type": "object",
                "properties": {
                    "query": {
                        "type":        "string",
                        "description": "One or more keywords to search for (space-separated, AND logic — all keywords must appear in the file).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name":        "create_file",
            "description": (
                "Write a new wiki page with auto-populated frontmatter (created/updated dates "
                "filled automatically). Preferred over update_file for new wiki pages — "
                "eliminates frontmatter errors. Pass the page body without frontmatter."
            ),
            "parameters":  {
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "Repo-relative path, e.g. wiki/entities/jane-smith.md"},
                    "title":   {"type": "string", "description": "Human-readable title (Title Case)"},
                    "type":    {"type": "string", "description": "Page type: source | entity | concept | synthesis"},
                    "tags":    {"type": "array",  "items": {"type": "string"}, "description": "List of lowercase hyphenated tags"},
                    "body":    {"type": "string", "description": "Full page body content (no frontmatter — that is added automatically)"},
                    "url":     {"type": "string", "description": "Original article URL. Source pages only — omit for entity/concept/synthesis pages."},
                },
                "required": ["path", "title", "type", "body"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def system_prompt() -> str:
    schema_path = REPO_ROOT / "LOBOTOMY.md"
    if not schema_path.exists():
        schema_path = REPO_ROOT / "CLAUDE.md"  # fallback during transition
    base = schema_path.read_text(encoding="utf-8")
    return (
        base
        + "\n\n---\n\n"
        "## Tool quick-reference\n\n"
        "| Tool | When to use |\n"
        "|------|-------------|\n"
        "| read_file | Read any repo file. Raw sources truncated at 60k chars, wiki pages at 20k. |\n"
        "| update_file | Update an existing wiki page (must already exist; raw/ is blocked). |\n"
        "| create_file | **Preferred** for new wiki pages — auto-fills frontmatter dates. |\n"
        "| search_wiki | Check if an entity/concept page exists before creating one. |\n"
        "| search_raw | Search raw source files by keyword — use when retroactively reviewing old articles for a newly prominent entity. |\n"
        "| list_dir | List directory contents. |\n"
        "| fetch_url | Fetch a web page for inbox processing. |\n"
        "| done | **Required** — signal task complete. Never stop without calling done(). |\n\n"
        "Exception: purely conversational replies need no tool use and no done() call."
    )


def orientation_message() -> str:
    import datetime
    today = datetime.date.today().isoformat()
    snippets = [f"Today's date: {today}"]
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
    ladder = [10, 20, 30, 60]
    delay = float(ladder[min(attempt - 1, len(ladder) - 1)])
    if isinstance(exc, _LLMError) and exc.retry_after:
        delay = max(delay, exc.retry_after)
    return delay


def _is_retryable(exc) -> bool:
    return isinstance(exc, _LLMError) and exc.retryable


def _error_message(exc) -> str:
    return str(exc)


# ---------------------------------------------------------------------------
# Agentic loops
# ---------------------------------------------------------------------------

def _create(client: dict, messages: list, system: str) -> dict:
    """Non-streaming create with two-phase retry and RPM awareness."""
    provider_name = cfg_get("llm", "provider", "openai").lower()
    model = cfg_get("llm", "model") or PROVIDERS.get(provider_name, PROVIDERS["openai"])["default_model"]
    payload = {
        "model":      model,
        "messages":   [{"role": "system", "content": system}] + messages,
        "tools":      TOOL_DEFS,
        "max_tokens": cfg_int("llm", "max_tokens", default=16384),
    }
    max_r = _max_retries()
    poll  = _retry_poll_interval()

    # Phase 1: exponential backoff
    for attempt in range(max_r + 1):
        _rpm_wait_sync()
        try:
            result = _llm_post(client["endpoint"], client["api_key"], payload)
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
            result = _llm_post(client["endpoint"], client["api_key"], payload)
            _record_request()
            return result
        except Exception as e:
            if not _is_retryable(e):
                raise


def run_agent_turn(client: dict, model: str, messages: list, system: str) -> list:
    """Run one user turn to completion (no streaming). Returns updated messages."""
    _round = 0
    while True:
        _round += 1
        log.debug("run_agent_turn round %d: %d messages", _round, len(messages) + 1)
        resp = _create(client, messages, system)
        _inter_delay = cfg_int("llm", "inter_request_delay", 0)
        if _inter_delay > 0:
            time.sleep(_inter_delay)
        try:
            msg = resp["choices"][0]["message"]
        except (KeyError, IndexError) as e:
            log.error("Malformed LLM response: %s  raw=%s", e, str(resp)[:500])
            raise _LLMError(f"Malformed response from LLM: {e}")

        content    = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []

        if not content and not tool_calls:
            log.error("LLM returned empty response (round %d) — did not call done()", _round)
            raise _LLMError("LLM returned an empty response without calling done(). Check model config or try again.")

        stored: dict = {"role": "assistant"}
        if tool_calls:
            # Pass through raw tool_calls unchanged — Gemini thinking mode attaches
            # a thought_signature to each call that must be echoed back verbatim.
            # Do NOT store content alongside tool_calls: Gemini's OpenAI-compatible
            # layer may split a message with both into two separate model turns,
            # putting the text turn before the function-call turn, which violates
            # Gemini's ordering rules and causes a 400 error on subsequent calls.
            stored["tool_calls"] = tool_calls
        else:
            stored["content"] = content
        messages.append(stored)

        if not tool_calls:
            break

        for i, tc in enumerate(tool_calls):
            fn_name = (tc.get("function") or {}).get("name") or ""
            fn = TOOL_FNS.get(fn_name)
            try:
                args   = json.loads((tc.get("function") or {}).get("arguments") or "{}")
                result = fn(args) if fn else f"Unknown tool: {fn_name}"
            except (json.JSONDecodeError, TypeError, ValueError, OSError) as e:
                result = f"Error: {e}"
            result_preview = str(result)[:200].replace("\n", " ") if isinstance(result, str) else str(result)[:200]
            log.debug("Tool call: %s  arg=%s  result=%s", fn_name or "(unknown)", str(list(args.values())[:1])[:60], result_preview)

            # done() ends the loop — return immediately without sending it back to the LLM.
            if isinstance(result, str) and result.startswith(_DONE_SENTINEL):
                payload = result[len(_DONE_SENTINEL):]
                ingested_flag, _, summary = payload.partition("|")
                log.info("Agent called done() in round %d (ingested=%s): %s", _round, ingested_flag, summary[:120])
                _post_process_session()
                _auto_write_log_entry()
                # Append placeholder responses for any unexecuted tool calls in this batch
                # so the message history stays consistent (every tool_call must have a response).
                remaining = tool_calls[i + 1:]
                if remaining:
                    log.warning("done() called mid-batch with %d unexecuted tool call(s) — adding placeholders", len(remaining))
                for rtc in remaining:
                    rname = (rtc.get("function") or {}).get("name") or "unknown_tool"
                    messages.append({"role": "tool", "tool_call_id": rtc.get("id", ""),
                                     "name": rname, "content": "Skipped — done() called in same batch."})
                if summary:
                    messages.append({"role": "assistant", "content": summary})
                messages.append({"role": "system", "content": f"__ingested__:{ingested_flag}"})
                return messages

            if not isinstance(result, str):
                result = json.dumps(result)
            # Gemini requires a non-empty name on every tool response
            if not fn_name:
                fn_name = "unknown_tool"
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "name": fn_name, "content": result})

    return messages


def stream_agent_turn(client: dict, model: str, messages: list, system: str,
                      stop_event=None) -> Generator:
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
    provider_name  = cfg_get("llm", "provider", "openai").lower()
    resolved_model = model or PROVIDERS.get(provider_name, PROVIDERS["openai"])["default_model"]
    last_user = next(
        (m["content"] for m in reversed(messages)
         if m.get("role") == "user" and isinstance(m.get("content"), str)),
        "",
    )
    log.info("stream_agent_turn: model=%s messages=%d request=%s",
             resolved_model, len(messages), last_user[:120].replace("\n", " "))
    payload_base = {
        "model":      resolved_model,
        "tools":      TOOL_DEFS,
        "max_tokens": cfg_int("llm", "max_tokens", default=16384),
    }

    _round = 0
    _continuations = 0
    _had_tool_calls = False
    _tools_since_last_continuation = 0  # reset each continuation; 0 = no progress = give up
    while True:
        if stop_event and stop_event.is_set():
            log.info("stream_agent_turn: cancelled by stop_event before round %d", _round + 1)
            yield json.dumps({"type": "cancelled"}) + "\n"
            yield json.dumps({"type": "done"}) + "\n"
            return
        _round += 1
        payload = dict(payload_base)
        payload["messages"] = [{"role": "system", "content": system}] + messages
        log.debug("Agent round %d: sending %d messages to LLM", _round, len(payload["messages"]))

        max_r = _max_retries()
        poll  = _retry_poll_interval()
        resp  = None

        # Phase 1: exponential backoff
        for attempt in range(max_r + 1):
            yield from _rpm_wait_streaming()
            try:
                resp = _llm_post(client["endpoint"], client["api_key"], payload)
                _record_request()
                _inter_delay = cfg_int("llm", "inter_request_delay", 0)
                if _inter_delay > 0:
                    time.sleep(_inter_delay)
                break
            except Exception as e:
                if not _is_retryable(e):
                    log.error("LLM non-retryable error: %s", e)
                    yield json.dumps({"type": "error", "content": _error_message(e)}) + "\n"
                    yield json.dumps({"type": "done"}) + "\n"
                    return
                if attempt == max_r:
                    break  # exhausted phase 1 — fall through to phase 2
                delay = _retry_delay(attempt + 1, e)
                log.warning("LLM retryable error (attempt %d/%d, delay %ds): %s",
                            attempt + 1, max_r, delay, e)
                yield json.dumps({"type": "retrying", "attempt": attempt + 1,
                                  "delay": int(delay), "max": max_r}) + "\n"
                time.sleep(delay)

        # Phase 2: poll indefinitely until provider recovers
        if resp is None:
            poll_attempt = 0
            while True:
                poll_attempt += 1
                log.warning("LLM unavailable — polling indefinitely (attempt %d)", poll_attempt)
                yield json.dumps({
                    "type":    "retrying",
                    "attempt": poll_attempt,
                    "delay":   poll,
                    "max":     None,
                    "msg":     f"Provider unavailable — retrying in {poll}s (attempt {poll_attempt})",
                }) + "\n"
                time.sleep(poll)
                yield from _rpm_wait_streaming()
                try:
                    resp = _llm_post(client["endpoint"], client["api_key"], payload)
                    _record_request()
                    break
                except Exception as e:
                    if not _is_retryable(e):
                        log.error("LLM non-retryable error during polling: %s", e)
                        yield json.dumps({"type": "error", "content": _error_message(e)}) + "\n"
                        yield json.dumps({"type": "done"}) + "\n"
                        return

        try:
            msg = resp["choices"][0]["message"]
        except (KeyError, IndexError) as e:
            log.error("Malformed LLM response: %s  raw=%s", e, str(resp)[:500])
            yield json.dumps({"type": "error",
                              "content": f"Malformed response from LLM: {e}"}) + "\n"
            yield json.dumps({"type": "done"}) + "\n"
            return

        content    = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []

        if not content and not tool_calls:
            if _had_tool_calls and _tools_since_last_continuation > 0:
                _continuations += 1
                _tools_since_last_continuation = 0
                log.warning("LLM empty response mid-task (round %d) — compressing history and continuing (continuation %d)",
                            _round, _continuations)
                yield json.dumps({"type": "tool", "name": "↻ continuing…", "arg": ""}) + "\n"

                # Compress history: strip bulky tool responses and write arguments,
                # replacing them with a compact done-so-far summary. This prevents
                # the context window from filling with echoed file contents.
                written, read_files, called = [], [], []
                for m in messages:
                    if m.get("role") == "assistant" and m.get("tool_calls"):
                        for tc in m["tool_calls"]:
                            fn  = (tc.get("function") or {}).get("name", "")
                            try:
                                args = json.loads((tc.get("function") or {}).get("arguments") or "{}")
                            except Exception:
                                args = {}
                            path = args.get("path", "")
                            if fn == "update_file" and path:
                                written.append(path)
                            elif fn == "read_file" and path:
                                read_files.append(path)
                            elif fn and fn not in ("done",):
                                called.append(fn + (f"({path})" if path else ""))

                # Rebuild messages: keep only non-tool-call turns + original user message
                messages[:] = [m for m in messages
                               if m.get("role") == "user"
                               or (m.get("role") == "assistant"
                                   and not m.get("tool_calls")
                                   and m.get("content"))]

                parts = ["You were mid-task and hit a context limit. Here is what you have already done:"]
                if written:
                    parts.append("Files written: " + ", ".join(written))
                if read_files:
                    parts.append("Files read: " + ", ".join(read_files))
                if called:
                    parts.append("Other tools called: " + ", ".join(called))
                parts.append("\nContinue from the next incomplete step. Do NOT re-read or re-write files "
                             "already listed above. Call done() when all steps are finished.")
                messages.append({"role": "user", "content": "\n".join(parts)})
                log.info("Compressed to %d messages for continuation", len(messages))
                continue

            if _continuations > 0:
                # A continuation round produced nothing — model has no more work to do
                # but failed to call done(). The actual wiki content is already on disk.
                # Auto-complete rather than surfacing a confusing error.
                log.warning("LLM returned empty in continuation round %d — auto-completing "
                            "(%d continuations, work on disk)", _round, _continuations)
                yield json.dumps({"type": "done"}) + "\n"
                return
            log.error("LLM returned empty response (round %d, model=%s) — no tool calls made",
                      _round, resolved_model)
            yield json.dumps({"type": "error",
                              "content": "The model returned an empty response without calling done(). "
                                         "Try again or switch to a more capable model."}) + "\n"
            yield json.dumps({"type": "done"}) + "\n"
            return

        stored: dict = {"role": "assistant"}
        if tool_calls:
            # Pass through raw tool_calls unchanged — Gemini thinking mode attaches
            # a thought_signature to each call that must be echoed back verbatim.
            # Do NOT store content alongside tool_calls: Gemini's OpenAI-compatible
            # layer may split a message with both into two separate model turns,
            # violating ordering rules and causing 400 errors on subsequent calls.
            stored["tool_calls"] = tool_calls
        else:
            stored["content"] = content
        messages.append(stored)

        if content:
            log.debug("LLM text: %s", content[:120].replace("\n", " "))
            yield json.dumps({"type": "text", "content": content}) + "\n"

        if not tool_calls:
            break

        _had_tool_calls = True
        _tools_since_last_continuation += len(tool_calls)
        for i, tc in enumerate(tool_calls):
            fn_name = (tc.get("function") or {}).get("name") or ""
            fn = TOOL_FNS.get(fn_name)
            try:
                args        = json.loads((tc.get("function") or {}).get("arguments") or "{}")
                arg_preview = str(next(iter(args.values()), ""))[:80]
                result      = fn(args) if fn else f"Unknown tool: {fn_name}"
            except json.JSONDecodeError as e:
                log.error("Tool %s: failed to parse arguments JSON: %s", fn_name, e)
                arg_preview = ""
                result      = f"Error: malformed tool arguments: {e}"
            except (TypeError, ValueError, OSError) as e:
                arg_preview = ""
                result      = f"Error: {e}"

            log.debug("Tool call: %s  arg=%s", fn_name or "(unknown)", arg_preview[:60])
            result_preview = str(result)[:200].replace("\n", " ") if isinstance(result, str) else str(result)[:200]
            log.debug("Tool result [%s]: %s", fn_name or "(unknown)", result_preview)

            # done() is a control signal — it ends the loop, not a message for the LLM.
            if isinstance(result, str) and result.startswith(_DONE_SENTINEL):
                payload = result[len(_DONE_SENTINEL):]
                ingested_flag, _, summary = payload.partition("|")
                log.info("Agent called done() in round %d (ingested=%s): %s", _round, ingested_flag, summary[:120])
                _post_process_session()
                _auto_write_log_entry()
                # Append placeholder responses for any unexecuted tool calls in this batch.
                remaining = tool_calls[i + 1:]
                if remaining:
                    log.warning("done() called mid-batch with %d unexecuted tool call(s) — adding placeholders", len(remaining))
                for rtc in remaining:
                    rname = (rtc.get("function") or {}).get("name") or "unknown_tool"
                    messages.append({"role": "tool", "tool_call_id": rtc.get("id", ""),
                                     "name": rname, "content": "Skipped — done() called in same batch."})
                if summary:
                    yield json.dumps({"type": "text", "content": _linkify_summary(summary)}) + "\n"
                # Stamp ingested flag into messages so on_done() callbacks can check it.
                messages.append({"role": "system", "content": f"__ingested__:{ingested_flag}"})
                yield json.dumps({"type": "done", "ingested": ingested_flag == "1"}) + "\n"
                return

            yield json.dumps({"type": "tool", "name": fn_name or "(unknown)", "arg": arg_preview}) + "\n"
            if not isinstance(result, str):
                result = json.dumps(result)
            # Gemini requires a non-empty name on every tool response
            if not fn_name:
                fn_name = "unknown_tool"
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "name": fn_name, "content": result})

        if stop_event and stop_event.is_set():
            log.info("stream_agent_turn: cancelled by stop_event after tool calls in round %d", _round)
            yield json.dumps({"type": "cancelled"}) + "\n"
            yield json.dumps({"type": "done"}) + "\n"
            return

    yield json.dumps({"type": "done"}) + "\n"
