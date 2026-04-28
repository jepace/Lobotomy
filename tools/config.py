#!/usr/bin/env python3
"""
Load config.json from the repo root.

Priority (highest first):
  1. config.json values
  2. Environment variable fallbacks (backwards compat)
  3. Hard-coded defaults

Usage in other modules:
    from config import cfg_get, cfg_bool, cfg_int
"""

import json
import os
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


def cfg_get(section: str, key: str, env_var: str = "", default: str = "") -> str:
    v = _c.get(section, {}).get(key)
    if v is not None:
        return str(v)
    if env_var:
        v = os.environ.get(env_var)
        if v is not None:
            return v
    return default


def cfg_int(section: str, key: str, env_var: str = "", default: int = 0) -> int:
    v = _c.get(section, {}).get(key)
    if v is not None:
        return int(v)
    if env_var:
        v = os.environ.get(env_var)
        if v is not None:
            return int(v)
    return default


def cfg_bool(section: str, key: str, env_var: str = "", default: bool = False) -> bool:
    v = _c.get(section, {}).get(key)
    if v is not None:
        return bool(v)
    if env_var:
        v = os.environ.get(env_var)
        if v is not None:
            return v.lower() in ("1", "true", "yes")
    return default
