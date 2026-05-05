#!/usr/bin/env python3
"""
Load config.json from the repo root.

Usage in other modules:
    from config import cfg_get, cfg_bool, cfg_int
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_FILE = REPO_ROOT / "config.json"


def _load() -> dict:
    if _CONFIG_FILE.exists():
        try:
            return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[wiki] Warning: could not parse config.json: {e}")
    return {}


_c = _load()


def cfg_get(section: str, key: str, default: str = "") -> str:
    v = _c.get(section, {}).get(key)
    return str(v) if v is not None else default


def cfg_int(section: str, key: str, default: int = 0) -> int:
    v = _c.get(section, {}).get(key)
    return int(v) if v is not None else default


def cfg_bool(section: str, key: str, default: bool = False) -> bool:
    v = _c.get(section, {}).get(key)
    return bool(v) if v is not None else default


def validate_config() -> list:
    """Check config for common issues. Returns list of (level, message) tuples.
    level: 'error' (blocks startup), 'warning' (functionality degraded)
    """
    issues = []

    # LLM provider
    provider = cfg_get("llm", "provider", "openai").lower()
    valid_providers = {"gemini", "openai", "groq", "ollama", "openrouter"}
    if provider not in valid_providers:
        issues.append(("error", f"llm.provider '{provider}' not recognized (valid: {', '.join(valid_providers)})"))

    api_key = cfg_get("llm", "api_key", "").strip()
    if not api_key and provider != "ollama":
        issues.append(("error", f"llm.api_key not set for provider '{provider}'"))

    model = cfg_get("llm", "model", "").strip()
    if not model:
        issues.append(("warning", "llm.model not set — will use provider default"))

    # Email (optional but warn if trying to use Resend without key)
    resend_key = cfg_get("email", "resend_api_key", "").strip()
    from_addr = cfg_get("email", "from_address", "").strip()
    if not resend_key:
        issues.append(("warning", "email.resend_api_key not set — email features disabled"))
    elif not from_addr:
        issues.append(("warning", "email.from_address not set — email may fail"))

    return issues
