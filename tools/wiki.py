#!/usr/bin/env python3
"""
LLM Wiki — AI-agnostic interactive client

Uses any OpenAI-compatible API (Gemini, OpenAI, Ollama, OpenRouter, etc.)

Configure with environment variables (or put them in a .env file and source it):
  WIKI_PROVIDER   Preset name: gemini | openai | ollama | openrouter  (default: openai)
  WIKI_API_KEY    API key (not needed for ollama)
  WIKI_API_BASE   Override base URL (optional; overrides WIKI_PROVIDER's default)
  WIKI_MODEL      Override model name (optional; overrides WIKI_PROVIDER's default)

Quickstart (Gemini free tier):
  1. Get a free API key at https://aistudio.google.com/apikey
  2. export WIKI_PROVIDER=gemini
     export WIKI_API_KEY=your-key-here
  3. python3 tools/wiki.py

Quickstart (Ollama — fully local, no API key):
  1. pkg install ollama && ollama pull llama3.2
  2. export WIKI_PROVIDER=ollama
  3. python3 tools/wiki.py

Usage:
  python3 tools/wiki.py                        # interactive REPL
  python3 tools/wiki.py "ingest raw/file.md"   # one-shot command

Requires:
  pip install openai
"""

import json
import os
import sys
import textwrap
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    print("Error: 'openai' package not installed.")
    print("  pip install openai")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Repository layout
# ---------------------------------------------------------------------------

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
        "base_url":      None,          # openai package default
        "default_model": "gpt-4o-mini",
    },
    "ollama": {
        "base_url":      "http://localhost:11434/v1",
        "default_model": "llama3.2",
        "api_key":       "ollama",      # ollama ignores the key but openai pkg requires one
    },
    "openrouter": {
        "base_url":      "https://openrouter.ai/api/v1",
        "default_model": "google/gemini-2.0-flash-exp:free",
    },
}


def get_client_and_model():
    provider_name = os.environ.get("WIKI_PROVIDER", "openai").lower()
    preset        = PROVIDERS.get(provider_name, PROVIDERS["openai"])

    api_key  = os.environ.get("WIKI_API_KEY")  or preset.get("api_key", "")
    base_url = os.environ.get("WIKI_API_BASE") or preset.get("base_url")
    model    = os.environ.get("WIKI_MODEL")    or preset["default_model"]

    if not api_key:
        print(f"Error: WIKI_API_KEY is not set.")
        print(f"  export WIKI_PROVIDER={provider_name}")
        print(f"  export WIKI_API_KEY=your-key-here")
        sys.exit(1)

    client = OpenAI(api_key=api_key, base_url=base_url)
    return client, model, provider_name

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
    # Safety: only wiki/ may be written; raw/ is immutable
    try:
        p.resolve().relative_to(WIKI_DIR.resolve())
    except ValueError:
        return (
            f"Error: write refused — only wiki/ files may be written. "
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
    lines = []
    for e in entries:
        tag = "dir" if e.is_dir() else "file"
        lines.append(f"  [{tag}]  {e.relative_to(REPO_ROOT)}")
    return "\n".join(lines)


def _move_file(src: str, dst: str) -> str:
    s = REPO_ROOT / src
    d = REPO_ROOT / dst
    if not s.exists():
        return f"Error: source not found: {src}"
    # Only allow moving FROM raw/inbox/ TO raw/ (inbox processing workflow)
    inbox = (REPO_ROOT / "raw" / "inbox").resolve()
    raw   = RAW_DIR.resolve()
    try:
        s.resolve().relative_to(inbox)
    except ValueError:
        return f"Error: move only permitted from raw/inbox/. Got src: {src}"
    try:
        d.resolve().relative_to(raw)
    except ValueError:
        return f"Error: move destination must be inside raw/. Got dst: {dst}"
    d.parent.mkdir(parents=True, exist_ok=True)
    s.rename(d)
    return f"Moved: {src} -> {dst}"


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
                "type":       "object",
                "properties": {"path": {"type": "string", "description": "Path relative to repo root, e.g. wiki/index.md"}},
                "required":   ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name":        "write_file",
            "description": "Write or overwrite a file. Restricted to wiki/ — raw/ is immutable.",
            "parameters":  {
                "type":       "object",
                "properties": {
                    "path":    {"type": "string", "description": "Path relative to repo root, must be inside wiki/"},
                    "content": {"type": "string", "description": "Complete file content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name":        "list_dir",
            "description": "List files and subdirectories inside a directory.",
            "parameters":  {
                "type":       "object",
                "properties": {"directory": {"type": "string", "description": "Directory path relative to repo root"}},
                "required":   ["directory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name":        "move_file",
            "description": "Move a file from raw/inbox/ to raw/ (used during inbox processing).",
            "parameters":  {
                "type":       "object",
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

def _system_prompt() -> str:
    base = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    return (
        base
        + "\n\n---\n\n"
        "You have four tools available: read_file, write_file, list_dir, move_file.\n"
        "Use them freely to read and update wiki pages as you work through any operation.\n"
        "raw/ is immutable — write_file will refuse writes there.\n"
        "Always follow the workflows defined above exactly."
    )


def _orientation_message() -> str:
    """Prime the session with the current wiki state."""
    snippets = []
    for rel, max_lines in [("wiki/index.md", None), ("wiki/log.md", 60), ("wiki/overview.md", None)]:
        p = REPO_ROOT / rel
        if p.exists():
            text = p.read_text(encoding="utf-8")
            if max_lines:
                text = "\n".join(text.splitlines()[:max_lines])
            snippets.append(f'<file path="{rel}">\n{text}\n</file>')
    return "Current wiki state (orientation):\n\n" + "\n\n".join(snippets)

# ---------------------------------------------------------------------------
# Agentic turn: call model, run tool loop until done
# ---------------------------------------------------------------------------

def _run_turn(client, model, messages, system):
    """Send messages, handle tool calls in a loop, return updated messages."""
    while True:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}] + messages,
            tools=TOOL_DEFS,
            max_tokens=4096,
        )
        msg = resp.choices[0].message

        # Normalise to a plain dict for storage (avoids pydantic serialisation issues)
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

        # Print any text the model emitted
        if msg.content:
            for line in msg.content.splitlines():
                print(textwrap.fill(line, width=100) if len(line) > 100 else line)
            print()

        if not msg.tool_calls:
            break   # turn complete

        # Execute tools and append results
        for tc in msg.tool_calls:
            fn = TOOL_FNS.get(tc.function.name)
            if fn is None:
                result = f"Error: unknown tool '{tc.function.name}'"
            else:
                try:
                    args   = json.loads(tc.function.arguments)
                    result = fn(args)
                except Exception as exc:
                    result = f"Error: {exc}"

            # Show a one-line summary so the user can see what's happening
            preview = result.splitlines()[0][:120]
            print(f"  \033[2m[{tc.function.name}] {preview}\033[0m")

            messages.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      result,
            })

    return messages

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    client, model, provider = get_client_and_model()

    # Seed conversation with orientation context (no model call needed for this)
    system   = _system_prompt()
    messages = [
        {"role": "user",      "content": _orientation_message()},
        {"role": "assistant", "content": "Oriented. Ready."},
    ]

    one_shot = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else None

    if one_shot:
        messages.append({"role": "user", "content": one_shot})
        _run_turn(client, model, messages, system)
        return

    print(f"\nLLM Wiki  [{provider} / {model}]")
    print("Commands: 'ingest raw/file.md'  |  'process inbox'  |  'query: ...'  |  'lint the wiki'")
    print("Type 'exit' or Ctrl-D to quit.\n")

    while True:
        try:
            user_text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_text:
            continue
        if user_text.lower() in ("exit", "quit", "q"):
            break

        messages.append({"role": "user", "content": user_text})
        messages = _run_turn(client, model, messages, system)


if __name__ == "__main__":
    main()
