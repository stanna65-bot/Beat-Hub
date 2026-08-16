import hashlib
import hmac
import os

from fastapi import HTTPException, Request

from database import get_db


_ITERATIONS = 260_000
_MIN_PASSWORD_LENGTH = 8


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    if not isinstance(password, str):
        raise ValueError(
            "Password must be a string."
        )

    if len(password) < _MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least "
            f"{_MIN_PASSWORD_LENGTH} characters."
        )

    salt = os.urandom(16)

    derived_key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _ITERATIONS,
    )

    return (
        f"{salt.hex()}$"
        f"{derived_key.hex()}"
    )


def verify_password(
    password: str,
    stored: str,
) -> bool:
    if not password or not stored:
        return False

    try:
        salt_hex, expected_hex = stored.split(
            "$",
            1,
        )

        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)

    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _ITERATIONS,
    )

    return hmac.compare_digest(
        actual,
        expected,
    )


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def current_producer(request: Request):
    producer_id = request.session.get(
        "producer_id"
    )

    if not isinstance(producer_id, int):
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
    producer = current_producer(request)

    if not producer:
        raise HTTPException(
            status_code=303,
            headers={
                "Location": "/login"
            },
            detail="Authentication required.",
        )

    return producer
