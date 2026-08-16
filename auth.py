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
        password.encode("utf-8"),
        salt,
        ITERATIONS,
    )

    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)

        salt = bytes.fromhex(salt_hex)

        calculated = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            ITERATIONS,
        ).hex()

        return hmac.compare_digest(
            calculated,
            digest_hex,
        )

    except Exception:
        return False


def current_producer(request: Request):
    """
    Returns the currently logged-in producer,
    or None when there is no valid producer session.
    """

    producer_id = request.session.get("producer_id")

    if not producer_id:
        return None

    conn = get_db()

    try:
        return conn.execute(
            """
            SELECT *
            FROM producers
            WHERE id = ?
            """,
            (producer_id,),
        ).fetchone()

    finally:
        conn.close()


def require_producer(request: Request):
    """
    FastAPI dependency for producer-protected routes.

    IMPORTANT:
    Request must be explicitly typed as Request.
    Without this annotation FastAPI interprets `request`
    as a required query parameter.
    """

    producer = current_producer(request)

    if not producer:
        raise HTTPException(
            status_code=401,
            detail="Login required",
        )

    return producer


def is_super_admin(request: Request) -> bool:
    """
    Returns True when the current session belongs
    to the logged-in super admin.
    """

    return bool(
        request.session.get("super_admin")
    )


def require_super_admin(request: Request):
    """
    Validates the super admin session.

    Request must be explicitly typed to prevent FastAPI
    from expecting `?request=` in the URL.
    """

    if not is_super_admin(request):
        raise HTTPException(
            status_code=401,
            detail="Super admin login required",
        )

    return True


def token_hash(token: str) -> str:
    """
    Hash reset tokens before storing or looking them up.
    """

    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def new_token() -> str:
    """
    Generates a cryptographically secure password-reset token.
    """

    return secrets.token_urlsafe(32)
