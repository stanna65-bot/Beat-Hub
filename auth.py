"""
Producer authentication.

Passwords are hashed with PBKDF2-HMAC-SHA256 (stdlib `hashlib`, no extra
dependency needed) — salted per-user, 260k iterations. Sessions are handled
by Starlette's signed-cookie SessionMiddleware (added in main.py), so a
logged-in producer just has `producer_id` sitting in `request.session`.
"""

import hashlib
import hmac
import os

from fastapi import Request, HTTPException
from starlette.responses import RedirectResponse

from database import get_db

_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$")
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(dk_hex)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return hmac.compare_digest(actual, expected)


def current_producer(request: Request):
    """Return the logged-in producer's row, or None."""
    producer_id = request.session.get("producer_id")
    if not producer_id:
        return None
    conn = get_db()
    producer = conn.execute("SELECT * FROM producers WHERE id = ?", (producer_id,)).fetchone()
    conn.close()
    return producer


def require_producer(request: Request):
    """FastAPI dependency: 302 to /login if not authenticated, else return the producer row."""
    producer = current_producer(request)
    if not producer:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return producer


class LoginRedirect(Exception):
    """Raised to force a redirect to /login from inside a route body if needed."""
    pass
