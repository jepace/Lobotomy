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
