#!/usr/bin/env python3
"""
Single-user authentication for LLM Wiki.
All state is stored as JSON files in wiki/ — no database.

Files (all gitignored):
  wiki/.user.json        Account: email, password hash, verified flag
  wiki/.tokens.json      Active verification and reset tokens
  wiki/.login_log.json   Recent login attempts (for rate limiting)

Configuration: set admin.email and admin.password in config.json.
Email verification requires email.resend_api_key in config.json.
Without it, accounts are auto-verified on creation.
"""

import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from datetime import timezone, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import cfg_get

REPO_ROOT  = Path(__file__).resolve().parent.parent
WIKI_DIR   = REPO_ROOT / "wiki"

_USER_FILE    = WIKI_DIR / ".user.json"
_TOKENS_FILE  = WIKI_DIR / ".tokens.json"
_LOG_FILE     = WIKI_DIR / ".login_log.json"

# ---------------------------------------------------------------------------
# JSON file helpers
# ---------------------------------------------------------------------------

def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

# ---------------------------------------------------------------------------
# Password hashing  (stdlib scrypt — no external deps)
# ---------------------------------------------------------------------------

def _hash(password: str) -> str:
    salt = os.urandom(16)
    key  = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return (salt + key).hex()


def _verify(password: str, stored_hex: str) -> bool:
    try:
        raw  = bytes.fromhex(stored_hex)
        salt, key = raw[:16], raw[16:]
        test = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(key, test)
    except Exception:
        return False

# ---------------------------------------------------------------------------
# User account  (single user, stored in wiki/.user.json)
# ---------------------------------------------------------------------------

def init_auth() -> None:
    """Provision the admin account from config.json if it doesn't exist yet."""
    email    = cfg_get("admin", "email").strip().lower()
    password = cfg_get("admin", "password").strip()
    if not email or not password:
        return

    if _USER_FILE.exists():
        return  # already provisioned

    # If Resend is configured, require email verification; otherwise auto-verify.
    verified = not _resend_ready()
    _write(_USER_FILE, {
        "email":      email,
        "pw_hash":    _hash(password),
        "verified":   verified,
        "created_at": _now_iso(),
    })
    if verified:
        print(f"[wiki] Admin account created for {email} (auto-verified — Resend not configured).")
    else:
        print(f"[wiki] Admin account created for {email}. Check your email to verify.")


def get_user() -> dict | None:
    return _read(_USER_FILE, None)


def set_verified() -> None:
    user = get_user()
    if user:
        user["verified"] = True
        _write(_USER_FILE, user)


def update_password(new_password: str) -> None:
    user = get_user()
    if user:
        user["pw_hash"] = _hash(new_password)
        _write(_USER_FILE, user)

# ---------------------------------------------------------------------------
# Login rate limiting  (5 failures → 15-minute lockout)
# ---------------------------------------------------------------------------

_WINDOW   = 900   # 15 minutes in seconds
_MAX_FAIL = 5


def record_attempt(success: bool) -> None:
    now   = time.time()
    log   = [e for e in _read(_LOG_FILE, []) if e["ts"] > now - _WINDOW]
    log.append({"ts": now, "ok": success})
    _write(_LOG_FILE, log)


def is_locked_out() -> bool:
    now  = time.time()
    log  = _read(_LOG_FILE, [])
    fail = sum(1 for e in log if not e["ok"] and e["ts"] > now - _WINDOW)
    return fail >= _MAX_FAIL


def seconds_until_unlock() -> int:
    log  = _read(_LOG_FILE, [])
    now  = time.time()
    fail_times = [e["ts"] for e in log if not e["ok"] and e["ts"] > now - _WINDOW]
    if not fail_times:
        return 0
    return max(0, int(min(fail_times) + _WINDOW - now))

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def authenticate(email: str, password: str) -> tuple[bool, str]:
    """Returns (success, message). Caller must call record_attempt() with the result."""
    if is_locked_out():
        mins = seconds_until_unlock() // 60 + 1
        return False, f"Too many failed attempts. Try again in {mins} minute(s)."

    expected = cfg_get("admin", "email").strip().lower()
    if not expected:
        return False, "Server not configured (admin.email missing from config.json)."

    if email.strip().lower() != expected:
        return False, "Invalid email or password."

    user = get_user()
    if not user or not _verify(password, user["pw_hash"]):
        return False, "Invalid email or password."

    if not user.get("verified"):
        return False, "Account not verified. Check your email for a verification link."

    return True, "ok"

# ---------------------------------------------------------------------------
# Tokens  (email verification + password reset, stored in wiki/.tokens.json)
# ---------------------------------------------------------------------------

def create_token(token_type: str, hours: int = 24) -> str:
    tokens = _read(_TOKENS_FILE, {})
    # Invalidate any existing unused token of this type
    tokens = {k: v for k, v in tokens.items()
              if not (v["type"] == token_type and not v["used"])}
    token = secrets.token_urlsafe(32)
    tokens[token] = {
        "type":       token_type,
        "expires_at": time.time() + hours * 3600,
        "used":       False,
    }
    _write(_TOKENS_FILE, tokens)
    return token


def consume_token(token: str, token_type: str) -> bool:
    """Mark the token used. Returns True if valid, correct type, unexpired, and unused."""
    tokens = _read(_TOKENS_FILE, {})
    entry  = tokens.get(token)
    if not entry:
        return False
    if entry["type"] != token_type:
        return False
    if entry["used"] or entry["expires_at"] < time.time():
        return False
    tokens[token]["used"] = True
    _write(_TOKENS_FILE, tokens)
    return True

# ---------------------------------------------------------------------------
# Email  (Resend)
# ---------------------------------------------------------------------------

def _resend_ready() -> bool:
    try:
        import resend  # noqa: F401
        return bool(cfg_get("email", "resend_api_key"))
    except ImportError:
        return False


def _base_url() -> str:
    return cfg_get("server", "base_url", "http://localhost:8080").rstrip("/")


def _from_addr() -> str:
    return cfg_get("email", "from_address", "onboarding@resend.dev")


def send_verification_email(email: str, token: str) -> None:
    import resend
    resend.api_key = cfg_get("email", "resend_api_key")
    link = f"{_base_url()}/auth/verify/{token}"
    resend.Emails.send({
        "from":    _from_addr(),
        "to":      [email],
        "subject": "Verify your wiki account",
        "html":    (
            f"<p>Click the link below to verify your wiki account:</p>"
            f"<p><a href='{link}'>{link}</a></p>"
            f"<p>This link expires in 24 hours.</p>"
        ),
    })


def send_reset_email(email: str, token: str) -> None:
    import resend
    resend.api_key = cfg_get("email", "resend_api_key")
    link = f"{_base_url()}/auth/reset/{token}"
    resend.Emails.send({
        "from":    _from_addr(),
        "to":      [email],
        "subject": "Reset your wiki password",
        "html":    (
            f"<p>Click the link below to reset your wiki password:</p>"
            f"<p><a href='{link}'>{link}</a></p>"
            f"<p>This link expires in 1 hour. If you didn't request this, ignore it.</p>"
        ),
    })


def maybe_send_verification() -> bool:
    """Send a verification email if Resend is configured and account is unverified."""
    if not _resend_ready():
        return False
    user = get_user()
    if not user or user.get("verified"):
        return False
    token = create_token("verify", hours=24)
    send_verification_email(user["email"], token)
    return True

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
