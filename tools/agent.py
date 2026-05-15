#!/usr/bin/env python3
"""Shared LLM agent logic used by both the CLI (wiki.py) and web server (serve.py)."""

import collections
import json
import logging
import sys
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


def _read_file(path: str) -> "str | list":
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
    # If this inbox file is just a URL stub and we already fetched it, return the cached content
    try:
        p.resolve().relative_to((RAW_DIR / "inbox").resolve())
        stripped = text.strip()
        if stripped.startswith("http") and "\n" not in stripped and stripped in _fetch_cache:
            return _fetch_cache[stripped]
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
    if len(text) > limit:
        text = text[:limit] + f"\n\n[TRUNCATED — {len(text)} total chars, showing first {limit}]"
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
    """Replace any internal wiki link whose target doesn't exist with bare link text."""
    import re
    def _check(m):
        text, target = m.group(1), m.group(2)
        if target.startswith("http") or target.startswith("#") or target.startswith("mailto"):
            return m.group(0)
        resolved = (page_path.parent / target).resolve()
        if resolved.exists():
            return m.group(0)
        return text  # drop the broken link, keep display text
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
    if not type_m or type_m.group(1) not in _SOURCES_SECTION_TYPES:
        return content

    src_m = re.search(r"^sources:\s*\[([^\]]*)\]", fm_text, re.MULTILINE)
    source_paths = []
    if src_m and src_m.group(1).strip():
        for s in src_m.group(1).split(","):
            s = s.strip().strip('"').strip("'")
            if s:
                source_paths.append(s)

    # Strip existing ## Sources section (assumed to be at end of file)
    body = content[len(fm_text):]
    body = re.sub(r"\n*^## Sources\b.*", "", body, flags=re.DOTALL | re.MULTILINE).rstrip()

    if not source_paths:
        return fm_text + body + "\n"

    # Compute path prefix relative to this page's directory
    up_count = len(page_path.parent.relative_to(WIKI_DIR).parts)
    prefix = "../" * up_count

    lines = ["\n\n## Sources\n"]
    for sp in source_paths:
        src_file = WIKI_DIR / sp
        title = sp
        if src_file.exists():
            src_text = src_file.read_text(encoding="utf-8", errors="replace")
            tm = re.match(r"^---\s*\n(.*?)\n---", src_text, re.DOTALL)
            if tm:
                for line in tm.group(1).splitlines():
                    if line.startswith("title:"):
                        title = line.split(":", 1)[1].strip().strip('"')
                        break
        lines.append(f"- [{title}]({prefix}{sp})")

    return fm_text + body + "\n".join(lines) + "\n"


def _autolink_sources_if_entity(path: str) -> None:
    """When an entity or concept page is written, re-autolink all source pages so
    links that couldn't resolve at source-creation time are wired up now."""
    parts = Path(path).parts
    if len(parts) >= 2 and parts[-2] in ("entities", "concepts"):
        sources_dir = WIKI_DIR / "sources"
        if sources_dir.is_dir():
            for src in sources_dir.glob("*.md"):
                if src.name != "index.md":
                    _autolink({"path": str(src.relative_to(REPO_ROOT))})


def _write_file(path: str, content: str) -> str:
    p = REPO_ROOT / path
    try:
        p.resolve().relative_to(WIKI_DIR.resolve())
    except ValueError:
        return (
            f"Error: write refused — only wiki/ is writable. "
            f"raw/ is immutable. Got: {path}"
        )
    if p.resolve() == (WIKI_DIR / "log.md").resolve():
        return "Error: write_file refused on wiki/log.md — use prepend_log to add entries."
    p.parent.mkdir(parents=True, exist_ok=True)
    content = _strip_broken_wiki_links(content, p)
    content = _inject_sources_section(content, p)
    p.write_text(content, encoding="utf-8")
    _autolink({"path": path})
    _autolink_sources_if_entity(path)
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


def _backfill_inbox_from_fetch(url: str, content: str) -> None:
    """If an inbox file is just a URL stub pointing to `url`, replace its body with fetched content."""
    import json as _json
    inbox = RAW_DIR / "inbox"
    if not inbox.is_dir():
        return
    for f in inbox.iterdir():
        if not f.is_file():
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
        f.write_text(new_text, encoding="utf-8")
        log.debug("Backfilled fetched content into %s", f.name)
        return


def _fetch_url(url: str) -> str:
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
        try:
            p.feed(raw.decode("utf-8", errors="replace"))
        except Exception as e:
            log.debug("HTML parse warning for %s: %s", url, e)
        text = _re.sub(r"\s+", " ", "".join(p.chunks)).strip()
        if not text:
            return (
                f"Fetched {url} but extracted no text.\n"
                "The page may require JavaScript to render (e.g. a SPA or Cloudflare-protected site).\n"
                "Save the article manually and drop it in raw/inbox/ instead."
            )
    else:
        text = raw.decode("utf-8", errors="replace")
    text = text[:50_000]
    _fetch_cache[url] = text
    _backfill_inbox_from_fetch(url, text)
    return text



def _prepend_log(entry: str) -> str:
    """Prepend a log entry to wiki/log.md, preserving all existing entries."""
    log_path = WIKI_DIR / "log.md"
    if not log_path.exists():
        return "Error: wiki/log.md does not exist"
    text = log_path.read_text(encoding="utf-8")

    # Structure: frontmatter (---...---), prose paragraph, --- divider, then entries.
    # Find the end of the frontmatter block (the closing ***).
    fm_open = text.find("---")
    fm_close = text.find("\n---", fm_open + 3)
    if fm_close == -1:
        log_path.write_text(text.rstrip() + "\n\n" + entry.strip() + "\n", encoding="utf-8")
        return "Log entry prepended to wiki/log.md (no frontmatter close found)"

    # Find the --- divider that separates the prose intro from the entries.
    # fm_close points at the \n before the closing ---, so fm_close+4 skips past it.
    divider = text.find("\n---\n", fm_close + 4)
    if divider == -1:
        # No prose divider — insert right after the frontmatter close.
        insert_at = fm_close + 4  # character after the closing ---\n
        before = text[:insert_at]
        after  = text[insert_at:]
        log_path.write_text(before + "\n" + entry.strip() + "\n\n" + after.lstrip("\n"),
                            encoding="utf-8")
        return "Log entry prepended to wiki/log.md (inserted after frontmatter)"

    # Insert new entry immediately after the divider.
    before = text[:divider + 5]   # up to and including \n---\n
    after  = text[divider + 5:]   # existing entries
    log_path.write_text(before + "\n" + entry.strip() + "\n\n" + after.lstrip("\n"),
                        encoding="utf-8")
    return "Log entry prepended to wiki/log.md"


_DONE_SENTINEL = "__AGENT_DONE__:"


def _done(args: dict) -> str:
    ingested = "1" if args.get("ingested") else "0"
    return _DONE_SENTINEL + ingested + "|" + args.get("summary", "")


def _rebuild_index(args: dict) -> str:
    """Rebuild wiki/index.md from frontmatter of all pages in sources/, entities/, concepts/, synthesis/."""
    import re

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
    index_path.write_text(prose + "\n\n---\n\n" + "\n\n---\n\n".join(blocks) + "\n", encoding="utf-8")

    # Also write per-subdirectory index files
    section_blocks = dict(zip([s for _, s in sections], blocks))
    for heading, subdir in sections:
        d = WIKI_DIR / subdir
        if not d.is_dir():
            continue
        sub_index = d / "index.md"
        existing_sub = sub_index.read_text(encoding="utf-8") if sub_index.exists() else ""
        # Find the auto-generated block boundary: "\n---\n\n## <Heading>"
        # This pattern is unique to the divider _rebuild_index writes, so it won't
        # be confused with YAML frontmatter delimiters or any other "---" in the prose.
        divider_sub = existing_sub.find(f"\n---\n\n## {heading}")
        if divider_sub != -1:
            prose_sub = existing_sub[:divider_sub].rstrip()
        elif existing_sub.strip():
            prose_sub = existing_sub.rstrip()
        else:
            prose_sub = f"# {heading}\n\n_Last updated: {today}_"
        prose_sub = re.sub(r"_Last updated: \d{4}-\d{2}-\d{2}_", f"_Last updated: {today}_", prose_sub)
        # Rewrite entries with relative paths (no subdir/ prefix needed from inside the subdir)
        block_local = section_blocks[subdir].replace(f"({subdir}/", "(")
        sub_index.write_text(prose_sub + "\n\n---\n\n" + block_local + "\n", encoding="utf-8")

    total = sum(
        sum(1 for f in (WIKI_DIR / s).glob("*.md") if f.name != "index.md")
        for _, s in sections if (WIKI_DIR / s).is_dir()
    )
    return f"Rebuilt wiki/index.md and subdirectory indexes — {total} pages across {len(sections)} sections."


def _autolink(args: dict) -> str:
    """Replace first bare occurrence of each other wiki page title with a markdown link."""
    import re

    target_str = args.get("path", "")
    target_p   = REPO_ROOT / target_str
    if not target_p.exists():
        return f"Error: not found: {target_str}"
    try:
        target_p.resolve().relative_to(WIKI_DIR.resolve())
    except ValueError:
        return "Error: autolink only works on wiki/ pages."

    # Build title -> relative-link-path map
    title_map: list[tuple[str, str]] = []
    for subdir in ("sources", "entities", "concepts", "synthesis"):
        d = WIKI_DIR / subdir
        if not d.is_dir():
            continue
        for f in d.glob("*.md"):
            if f.name == "index.md" or f.resolve() == target_p.resolve():
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
            if not m:
                continue
            for line in m.group(1).splitlines():
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"')
                    if title:
                        rel = f.relative_to(WIKI_DIR)
                        # Build correct relative path (../ per directory level)
                        up_parts = target_p.parent.relative_to(WIKI_DIR).parts
                        prefix = "../" * len(up_parts)
                        link_path = f"{prefix}{rel}"
                        title_map.append((title, link_path))
                    break

    if not title_map:
        return "No other wiki pages found to link."

    title_map.sort(key=lambda x: -len(x[0]))  # longest first — avoids partial matches

    content  = target_p.read_text(encoding="utf-8", errors="replace")
    fm_match = re.match(r"^(---\s*\n.*?\n---\s*\n)", content, re.DOTALL)
    frontmatter, body = (fm_match.group(1), content[len(fm_match.group(1)):]) if fm_match else ("", content)

    # Split body into alternating [non-link, link, non-link, link, ...]
    # Substitute only in non-link segments to avoid corrupting existing link URLs.
    _LINK_RE = re.compile(r'\[[^\]]*\]\([^)]*\)')
    segments = _LINK_RE.split(body)
    links    = _LINK_RE.findall(body)

    linked = 0
    for title, link_path in title_map:
        pattern = re.compile(r'(?<!\w)(' + re.escape(title) + r')(?!\w)', re.IGNORECASE)
        new_segments = []
        any_replaced = False
        for seg in segments:
            def _rep(m, _lp=link_path):
                return f"[{m.group(1)}]({_lp})"
            new_seg = pattern.sub(_rep, seg)
            if new_seg != seg:
                any_replaced = True
            new_segments.append(new_seg)
        if any_replaced:
            segments = new_segments
            linked += 1

    # Reassemble: interleave segments and preserved links
    result = segments[0]
    for lnk, seg in zip(links, segments[1:]):
        result += lnk + seg

    target_p.write_text(frontmatter + result, encoding="utf-8")
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
                page.write_text(text, encoding="utf-8")
                total_fixed += n
                pages_fixed += 1

    if total_fixed == 0:
        return "No bad wiki links found — nothing to fix."
    return f"Fixed {total_fixed} bad link(s) across {pages_fixed} page(s)."


def _search_wiki(args: dict) -> str:
    """Keyword search across all wiki pages. Returns matching pages with title, path, snippet."""
    import re
    query = args.get("query", "").strip()
    if not query:
        return "Error: query is required."
    keywords = query.split()
    patterns = [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords]

    results = []
    for f in sorted(WIKI_DIR.rglob("*.md")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        score = sum(len(p.findall(text)) for p in patterns)
        if not score:
            continue
        # Extract title from frontmatter
        fm = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        title = f.stem
        if fm:
            for line in fm.group(1).splitlines():
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"')
                    break
        # Find first matching line for snippet
        snippet = ""
        for line in text.splitlines():
            if any(p.search(line) for p in patterns):
                snippet = line.strip()[:120]
                break
        rel = str(f.relative_to(WIKI_DIR))
        results.append((score, title, rel, snippet))

    results.sort(key=lambda x: -x[0])
    if not results:
        return f"No wiki pages found matching: {query}"
    lines = [f"Found {len(results)} page(s) matching '{query}':\n"]
    for _, title, rel, snippet in results[:10]:
        lines.append(f"- [{title}]({rel})")
        if snippet:
            lines.append(f"  > {snippet}")
    return "\n".join(lines)


def _create_page(args: dict) -> str:
    """Write a new wiki page with auto-populated frontmatter."""
    import datetime, re
    path    = args.get("path", "")
    title   = args.get("title", "")
    pg_type = args.get("type", "")
    tags    = args.get("tags", [])
    body    = args.get("body", "")
    sources = args.get("sources", [])

    if not path or not title or not pg_type:
        return "Error: path, title, and type are required."

    p = REPO_ROOT / path
    try:
        p.resolve().relative_to(WIKI_DIR.resolve())
    except ValueError:
        return f"Error: create_page only writes inside wiki/. Got: {path}"

    today = datetime.date.today().isoformat()
    existed = p.exists()
    if existed:
        # Preserve original created date
        old = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"created:\s*(\d{4}-\d{2}-\d{2})", old)
        created = m.group(1) if m else today
    else:
        created = today

    tag_str = ", ".join(f'"{t}"' for t in (tags if isinstance(tags, list) else [tags]))
    src_str = ", ".join(f'"{s}"' for s in (sources if isinstance(sources, list) else [sources]))
    frontmatter = (
        f'---\ntitle: "{title}"\ntype: {pg_type}\ntags: [{tag_str}]\n'
        f'created: {created}\nupdated: {today}\nsources: [{src_str}]\n---\n\n'
    )
    content = frontmatter + _strip_broken_wiki_links(body.lstrip("\n"), p)
    content = _inject_sources_section(content, p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    _autolink({"path": path})
    _autolink_sources_if_entity(path)
    action = "Updated" if existed else "Created"
    return f"{action} {path} ({len(content)} bytes)"


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
    "read_file":       lambda a: _read_file(a["path"]),
    "write_file":       lambda a: _write_file(a["path"], a["content"]),
    "list_dir":         lambda a: _list_dir(a["directory"]),
    "fetch_url":        lambda a: _fetch_url(a["url"]),
    "prepend_log":      lambda a: _prepend_log(a["entry"]),



    "search_wiki":      _search_wiki,
    "create_page":      _create_page,

    "done":             _done,
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
            "name":        "prepend_log",
            "description": (
                "Add a new entry to wiki/log.md. Always use this instead of write_file for log updates — "
                "it preserves all existing entries. Use this for Step 10 of the ingest workflow. "
                "Pages created/updated must be markdown links: [Title](sources/slug.md) — "
                "never plain text or paths starting with wiki/."
            ),
            "parameters":  {
                "type": "object",
                "properties": {
                    "entry": {
                        "type":        "string",
                        "description": "Complete log entry. Page refs must be markdown links with paths relative to wiki/, e.g. [Title](sources/slug.md).",
                    },
                },
                "required": ["entry"],
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
                "Set ingested=true ONLY if you successfully created a new wiki source page during this session."
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
                        "description": "True only if a new wiki source page was created in this session.",
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
                "Keyword search across all wiki pages. Returns matching page titles, paths, and "
                "a snippet of the matching line. Use this to check whether an entity or concept "
                "already has a page before creating one — much faster than reading index.md "
                "and then reading individual pages to verify."
            ),
            "parameters":  {
                "type": "object",
                "properties": {
                    "query": {
                        "type":        "string",
                        "description": "One or more keywords to search for (space-separated, OR logic)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name":        "create_page",
            "description": (
                "Write a new wiki page with auto-populated frontmatter (created/updated dates "
                "filled automatically). Preferred over write_file for new wiki pages — "
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
                    "sources": {"type": "array",  "items": {"type": "string"}, "description": "List of source page paths relative to wiki/, e.g. [\"sources/my-article.md\"]"},
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
    schema_path = REPO_ROOT / "AGENT.md"
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
        "| write_file | Write wiki pages (raw/ is blocked). Use create_page for new pages instead. |\n"
        "| create_page | **Preferred** for new wiki pages — auto-fills frontmatter dates. |\n"
        "| search_wiki | Check if an entity/concept page exists before creating one. |\n"
        "| prepend_log | Add entry to wiki/log.md. Never use write_file for the log. |\n"
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

        for tc in tool_calls:
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


def stream_agent_turn(client: dict, model: str, messages: list, system: str) -> Generator:
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
                            if fn == "write_file" and path:
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
        for tc in tool_calls:
            fn_name = (tc.get("function") or {}).get("name") or ""
            fn = TOOL_FNS.get(fn_name)
            try:
                args        = json.loads((tc.get("function") or {}).get("arguments") or "{}")
                arg_preview = str(list(args.values())[0])[:80] if args else ""
                result      = fn(args) if fn else f"Unknown tool: {fn_name}"
            except (json.JSONDecodeError, TypeError, ValueError, OSError) as e:
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
                if summary:
                    yield json.dumps({"type": "text", "content": summary}) + "\n"
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

    yield json.dumps({"type": "done"}) + "\n"
