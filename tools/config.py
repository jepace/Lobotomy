#!/usr/bin/env python3
"""
Load config.json from the repo root.

Usage in other modules:
    from config import cfg_get, cfg_bool, cfg_int, cfg_active_provider
"""

import json
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_FILE = REPO_ROOT / "config.json"

_c: dict = {}
_mtime: float = 0.0
_lock = threading.Lock()


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


def cfg_active_provider() -> str:
    """Return the active provider name.
    New format: llm.active.  Legacy format: llm.provider.  Default: openai."""
    _reload_if_changed()
    llm = _c.get("llm", {})
    name = llm.get("active") or llm.get("provider") or "openai"
    return str(name).lower()


def cfg_provider(provider: str) -> dict:
    """Return per-provider config block.
    New format: llm.providers.{provider}.
    Legacy format (flat llm block): returned as a dict with api_key/model/api_base keys."""
    _reload_if_changed()
    llm = _c.get("llm", {})
    # New multi-provider format
    if "providers" in llm:
        return llm["providers"].get(provider, {})
    # Legacy flat format — expose legacy keys as a provider dict
    if provider == (llm.get("provider") or "").lower():
        p = {}
        if llm.get("api_key"):
            p["api_key"] = llm["api_key"]
        if llm.get("model"):
            p["model"] = llm["model"]
        if llm.get("api_base"):
            p["api_base"] = llm["api_base"]
        return p
    return {}


def cfg_api_key(provider: str) -> str:
    """Return API key for provider. Checks llm.providers.{provider}.api_key,
    then llm.keys.{provider} (legacy), then llm.api_key."""
    p = cfg_provider(provider)
    if p.get("api_key"):
        return str(p["api_key"])
    v = _c.get("llm", {}).get("keys", {}).get(provider)
    return str(v) if v else cfg_get("llm", "api_key")


def cfg_available_models(provider: str) -> list:
    """Return the curated list of available models for a provider, or [] if not configured."""
    p = cfg_provider(provider)
    models = p.get("available_models", [])
    return list(models) if isinstance(models, list) else []


def cfg_all_providers() -> list:
    """Return list of configured provider names (new format only)."""
    _reload_if_changed()
    llm = _c.get("llm", {})
    if "providers" in llm:
        return list(llm["providers"].keys())
    # Legacy: single provider
    name = llm.get("provider") or "openai"
    return [name.lower()]


def cfg_write_llm(patch: dict) -> None:
    """Atomically patch llm config keys and write config.json back.
    patch is a flat dict of top-level llm keys (e.g. {"active": "gemini"})
    or nested paths handled by caller building the right structure.
    Supports special key "providers" to merge provider sub-blocks.
    """
    with _lock:
        _reload_if_changed()
        llm = _c.setdefault("llm", {})
        providers_patch = patch.pop("providers", None)
        llm.update(patch)
        if providers_patch:
            existing = llm.setdefault("providers", {})
            for pname, pdata in providers_patch.items():
                existing.setdefault(pname, {}).update(pdata)
        try:
            text = json.dumps(_c, indent=2, ensure_ascii=False)
            _CONFIG_FILE.write_text(text, encoding="utf-8")
        except OSError as e:
            raise RuntimeError(f"Could not write config.json: {e}") from e
        _mtime = _CONFIG_FILE.stat().st_mtime


def validate_config() -> list:
    """Check config for common issues. Returns list of (level, message) tuples.
    level: 'error' (blocks startup), 'warning' (functionality degraded)
    """
    issues = []

    provider = cfg_active_provider()
    valid_providers = {"gemini", "openai", "groq", "ollama", "openrouter"}
    if provider not in valid_providers:
        issues.append(("error", f"llm active provider '{provider}' not recognized (valid: {', '.join(valid_providers)})"))

    api_key = cfg_api_key(provider).strip()
    if not api_key and provider != "ollama":
        issues.append(("error", f"No API key for provider '{provider}' — set llm.providers.{provider}.api_key in config.json"))

    model = cfg_provider(provider).get("model") or cfg_get("llm", "model", "").strip()
    if not model:
        issues.append(("warning", "No model set for active provider — will use provider default"))

    resend_key = cfg_get("email", "resend_api_key", "").strip()
    from_addr = cfg_get("email", "from_address", "").strip()
    if not resend_key:
        issues.append(("warning", "email.resend_api_key not set — email features disabled"))
    elif not from_addr:
        issues.append(("warning", "email.from_address not set — email may fail"))

    return issues
