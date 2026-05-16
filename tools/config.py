#!/usr/bin/env python3
"""
Load config.json from the repo root.

Usage in other modules:
    from config import cfg_get, cfg_bool, cfg_int
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_FILE = REPO_ROOT / "config.json"

_c: dict = {}
_mtime: float = 0.0


def _load() -> None:
    """Load config.json, raising SystemExit with a clear message on any failure."""
    global _c, _mtime
    if not _CONFIG_FILE.exists():
        sys.exit(
            f"[config] config.json not found at {_CONFIG_FILE}\n"
            f"Copy config.json.example to config.json and fill in your settings."
        )
    try:
        text = _CONFIG_FILE.read_text(encoding="utf-8")
    except OSError as e:
        sys.exit(f"[config] Could not read config.json: {e}")
    try:
        _c = json.loads(text)
    except json.JSONDecodeError as e:
        sys.exit(f"[config] config.json is not valid JSON: {e}")
    _mtime = _CONFIG_FILE.stat().st_mtime


def _reload_if_changed() -> None:
    global _mtime
    try:
        mtime = _CONFIG_FILE.stat().st_mtime
    except FileNotFoundError:
        sys.exit(f"[config] config.json was deleted while running — cannot continue.")
    if mtime != _mtime:
        _load()


_load()


def cfg_get(section: str, key: str, default: str = "") -> str:
    _reload_if_changed()
    v = _c.get(section, {}).get(key)
    return str(v) if v is not None else default


def cfg_int(section: str, key: str, default: int = 0) -> int:
    _reload_if_changed()
    v = _c.get(section, {}).get(key)
    return int(v) if v is not None else default


def cfg_bool(section: str, key: str, default: bool = False) -> bool:
    _reload_if_changed()
    v = _c.get(section, {}).get(key)
    return bool(v) if v is not None else default


def cfg_provider(provider: str) -> dict:
    """Return per-provider config block from llm.providers.{provider}, or {}."""
    _reload_if_changed()
    return _c.get("llm", {}).get("providers", {}).get(provider, {})


def cfg_api_key(provider: str) -> str:
    """Return API key for provider. Checks llm.providers.{provider}.api_key,
    then llm.keys.{provider} (legacy), then llm.api_key."""
    p = cfg_provider(provider)
    if p.get("api_key"):
        return str(p["api_key"])
    v = _c.get("llm", {}).get("keys", {}).get(provider)
    return str(v) if v else cfg_get("llm", "api_key")


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

    api_key = cfg_api_key(provider).strip()
    if not api_key and provider != "ollama":
        issues.append(("error", f"No API key for provider '{provider}' — set llm.api_key or llm.keys.{provider}"))

    model = cfg_provider(provider).get("model") or cfg_get("llm", "model", "").strip()
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
