"""Goofish login session persistence for GitHub Actions.

The browser storage state is never committed. It is encrypted with a key derived
from an existing Actions secret and kept in the Actions cache, so every successful
run saves the refreshed cookies and the session keeps renewing itself.
Fork pull requests get neither the secret nor this workflow, so they cannot decrypt it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path(".goofish_state")
ENC_FILE = STATE_DIR / "state.enc"
OK_FILE = STATE_DIR / "ok"
LOGIN_COOKIES = {"unb", "tracknick"}


def _fernet():
    material = os.environ.get("GOOFISH_STATE_KEY_MATERIAL", "").strip()
    if not material:
        return None
    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(hashlib.sha256(("goofish-state-v1|" + material).encode("utf-8")).digest())
    return Fernet(key)


def load_into(state_file: Path) -> str | None:
    """Write a Playwright storage_state file. Returns 'cache', 'secret' or None."""
    OK_FILE.unlink(missing_ok=True)  # only a fresh, verified save may be cached again
    f = _fernet()
    if f and ENC_FILE.exists():
        try:
            state_file.write_bytes(f.decrypt(ENC_FILE.read_bytes()))
            return "cache"
        except Exception:
            pass
    raw = os.environ.get("GOOFISH_STORAGE_STATE_B64", "").strip()
    if raw:
        try:
            state_file.write_bytes(base64.b64decode(raw))
            return "secret"
        except Exception:
            pass
    return None


def is_logged_in(ctx) -> bool:
    try:
        names = {c.get("name") for c in ctx.cookies("https://www.goofish.com")}
    except Exception:
        return False
    return LOGIN_COOKIES.issubset(names)


def save_from_context(ctx) -> bool:
    f = _fernet()
    if not f or not is_logged_in(ctx):
        return False
    STATE_DIR.mkdir(exist_ok=True)
    ENC_FILE.write_bytes(f.encrypt(json.dumps(ctx.storage_state()).encode("utf-8")))
    OK_FILE.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    return True
