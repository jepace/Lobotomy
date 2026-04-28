#!/usr/bin/env python3
"""
LLM Wiki — CLI client

Interactive terminal session for operating the wiki from the command line.
For web-based access use: python3 tools/serve.py

Configuration (environment variables):
  WIKI_PROVIDER   gemini | openai | ollama | openrouter  (default: openai)
  WIKI_API_KEY    API key (not needed for ollama)
  WIKI_MODEL      Override model name
  WIKI_API_BASE   Override API base URL

Quickstart (Gemini free tier):
  export WIKI_PROVIDER=gemini
  export WIKI_API_KEY=your-key
  python3 tools/wiki.py

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

sys.path.insert(0, str(Path(__file__).parent))
from agent import (PROVIDERS, get_client_and_model, orientation_message,
                   run_agent_turn, system_prompt, TOOL_FNS)


def main():
    client, model, error = get_client_and_model()
    if error:
        print(f"Error: {error}")
        sys.exit(1)

    provider = os.environ.get("WIKI_PROVIDER", "openai")
    sys_prompt = system_prompt()
    messages   = [
        {"role": "user",      "content": orientation_message()},
        {"role": "assistant", "content": "Oriented. Ready."},
    ]

    one_shot = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else None

    if one_shot:
        messages.append({"role": "user", "content": one_shot})
        messages = run_agent_turn_with_output(client, model, messages, sys_prompt)
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
        messages = run_agent_turn_with_output(client, model, messages, sys_prompt)


def run_agent_turn_with_output(client, model, messages, sys_prompt):
    """Wrapper around run_agent_turn that prints tool activity and responses."""
    import json as _json

    # We need the streaming version for output; use agent's stream variant
    from agent import stream_agent_turn

    for raw in stream_agent_turn(client, model, messages, sys_prompt):
        event = _json.loads(raw)
        if event["type"] == "tool":
            print(f"  \033[2m[{event['name']}] {event['arg']}\033[0m")
        elif event["type"] == "text":
            for line in event["content"].splitlines():
                print(textwrap.fill(line, 100) if len(line) > 100 else line)
            print()

    return messages


if __name__ == "__main__":
    main()
