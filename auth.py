import hashlib
import hmac
import os
import secrets

from fastapi import HTTPException, Request

from database import get_db


ITERATIONS = 300_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt,
        ITERATIONS
    )

    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)

        calculated = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            bytes.fromhex(salt),
            ITERATIONS
        ).hex()

        return hmac.compare_digest(
            calculated,
            digest
        )

    except Exception:
        return False


def current_producer(request: Request):
    producer_id = request.session.get("producer_id")

    if not producer_id:
        return None

    c = get_db()

    try:
        return c.execute(
            """
            SELECT *
            FROM producers
            WHERE id=?
            LIMIT 1
            """,
            (producer_id,)
        ).fetchone()

    finally:
        c.close()


def require_producer(request: Request):
    producer = current_producer(request)

    if not producer:
        raise HTTPException(
            status_code=401,
            detail="Login required"
        )

    return producer


def is_super_admin(request: Request) -> bool:
    return bool(
        request.session.get("super_admin")
    )


def require_super_admin(request: Request):
    if not is_super_admin(request):
        raise HTTPException(
            status_code=401,
            detail="Super admin login required"
        )

    return True


def new_token():
    return secrets.token_urlsafe(32)


def token_hash(token):
    return hashlib.sha256(
        token.encode()
    ).hexdigest()
