#!/usr/bin/env python3
"""Shared LLM agent logic used by both the CLI (wiki.py) and web server (serve.py)."""

import json
import os
import sys
from pathlib import Path
from typing import Generator

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # checked at call time

sys.path.insert(0, str(Path(__file__).parent))
from config import cfg_get

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

    provider_name = cfg_get("llm", "provider", "WIKI_PROVIDER", "openai").lower()
    preset        = PROVIDERS.get(provider_name, PROVIDERS["openai"])

    api_key  = cfg_get("llm", "api_key",  "WIKI_API_KEY")  or preset.get("api_key", "")
    base_url = cfg_get("llm", "api_base", "WIKI_API_BASE") or preset.get("base_url")
    model    = cfg_get("llm", "model",    "WIKI_MODEL")    or preset["default_model"]

    if not api_key:
        return None, None, (
            f"No API key for provider '{provider_name}'.\n"
            f"  Set llm.api_key in config.json, or: export WIKI_API_KEY=your-key"
        )

    return OpenAI(api_key=api_key, base_url=base_url), model, None


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _read_file(path: str) -> str:
    p = REPO_ROOT / path
    if not p.exists():
        return f"Error: not found: {path}"
    if not p.is_file():
        return f"Error: not a file: {path}"
    return p.read_text(encoding="utf-8", errors="replace")


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
]

# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def system_prompt() -> str:
    base = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    return (
        base
        + "\n\n---\n\n"
        "Tools available: read_file, write_file, list_dir, move_file.\n"
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
# Agentic loops
# ---------------------------------------------------------------------------

def run_agent_turn(client, model: str, messages: list, system: str) -> list:
    """Run one user turn to completion (no streaming). Returns updated messages."""
    while True:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}] + messages,
            tools=TOOL_DEFS,
            max_tokens=4096,
        )
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
      {"type": "tool",  "name": "...", "arg": "..."}   — tool being called
      {"type": "text",  "content": "..."}               — LLM text response
      {"type": "error", "content": "..."}               — error
      {"type": "done"}                                   — turn complete
    Updates messages in-place so history can be saved after the generator finishes.
    """
    while True:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}] + messages,
            tools=TOOL_DEFS,
            max_tokens=4096,
        )
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
