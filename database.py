import hashlib
import hmac
import os
import secrets
from fastapi import HTTPException, Request
from database import get_db

ITERATIONS = 300_000


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        salt_hex, digest_hex = (stored or "").split("$", 1)
        got = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), ITERATIONS
        ).hex()
        return hmac.compare_digest(got, digest_hex)
    except Exception:
        return False


def current_producer(request: Request):
    raw_id = request.session.get("producer_id")
    try:
        pid = int(raw_id)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None

    conn = get_db()
    try:
        return conn.execute(
            "SELECT * FROM producers WHERE id=? LIMIT 1", (pid,)
        ).fetchone()
    finally:
        conn.close()


def require_producer(request: Request):
    producer = current_producer(request)
    if not producer:
        request.session.pop("producer_id", None)
        request.session.pop("remember_me", None)
        raise HTTPException(401, "Login required")
    return producer


def is_super_admin(request: Request):
    return (
        request.session.get("super_admin") is True
        and request.session.get("role") == "super_admin"
    )


def require_super_admin(request: Request):
    if not is_super_admin(request):
        raise HTTPException(401, "Super admin login required")
    return True


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_token():
    return secrets.token_urlsafe(32)
