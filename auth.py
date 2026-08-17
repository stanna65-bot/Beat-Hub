import base64
import hashlib
import hmac
import os
import secrets

from fastapi import HTTPException, Request

from database import get_db


ITERATIONS = 300_000


def hash_password(password):
    salt = os.urandom(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        ITERATIONS
    )

    return f"{salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    """
    Verify current BeatHub password hashes and compatible
    legacy PBKDF2/Bcrypt hashes.
    """

    if password is None or stored is None:
        return False

    try:
        stored = str(stored).strip()

        if not stored:
            return False

        # Current BeatHub format:
        # salt_hex$digest_hex
        if stored.count("$") == 1:
            salt_hex, digest_hex = stored.split("$", 1)

            salt = bytes.fromhex(salt_hex)

            if len(salt) != 16:
                return False

            if len(digest_hex) != 64:
                return False

            got = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt,
                ITERATIONS
            ).hex()

            return hmac.compare_digest(
                got,
                digest_hex
            )

        # Compatible Django-style PBKDF2 format:
        # pbkdf2_sha256$rounds$salt$hash
        parts = stored.split("$")

        if (
            len(parts) == 4
            and parts[0].lower() == "pbkdf2_sha256"
        ):
            rounds = int(parts[1])
            salt = parts[2]
            expected = parts[3]

            got = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt.encode("utf-8"),
                rounds
            )

            encoded = (
                base64.b64encode(got)
                .decode("ascii")
                .rstrip("=")
            )

            return hmac.compare_digest(
                encoded,
                expected
            )

        # Compatible bcrypt hashes.
        if stored.startswith(
            (
                "$2a$",
                "$2b$",
                "$2y$"
            )
        ):
            try:
                import bcrypt

                return bool(
                    bcrypt.checkpw(
                        password.encode("utf-8"),
                        stored.encode("utf-8")
                    )
                )

            except Exception:
                return False

        return False

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
            """
            SELECT *
            FROM producers
            WHERE id=?
            LIMIT 1
            """,
            (pid,)
        ).fetchone()

    finally:
        conn.close()


def require_producer(request: Request):
    producer = current_producer(request)

    if not producer:
        request.session.pop(
            "producer_id",
            None
        )

        request.session.pop(
            "remember_me",
            None
        )

        raise HTTPException(
            401,
            "Login required"
        )

    return producer


def is_super_admin(request: Request):
    return (
        request.session.get("super_admin") is True
        and request.session.get("role") == "super_admin"
    )


def require_super_admin(request: Request):
    if not is_super_admin(request):
        raise HTTPException(
            401,
            "Super admin login required"
        )

    return True


def token_hash(token):
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def new_token():
    return secrets.token_urlsafe(32)
