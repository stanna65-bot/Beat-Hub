import hashlib
import hmac
import os
import secrets

from fastapi import HTTPException, Request
from database import get_db


ITERATIONS = int(
    os.getenv(
        "PASSWORD_HASH_ITERATIONS",
        "300000"
    )
)


def hash_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError(
            "Password must be at least 8 characters."
        )

    salt = os.urandom(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        ITERATIONS
    )

    return f"{salt.hex()}${digest.hex()}"


def verify_password(
    password: str,
    stored: str
) -> bool:
    try:
        salt, digest = stored.split("$", 1)

        calculated = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
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
    # A Super Admin session must never accidentally
    # become a producer session.
    if request.session.get("super_admin"):
        return None

    producer_id = request.session.get(
        "producer_id"
    )

    if not producer_id:
        return None

    connection = get_db()

    try:
        return connection.execute(
            """
            SELECT *
            FROM producers
            WHERE id=?
            """,
            (producer_id,)
        ).fetchone()

    finally:
        connection.close()


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
            detail="Super Admin login required"
        )

    return True


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()
