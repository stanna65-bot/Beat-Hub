import os
import hashlib
import secrets
from functools import wraps

from fastapi import Request
from fastapi.responses import RedirectResponse


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120000,
    )

    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, stored_digest = stored_hash.split("$", 1)

        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            120000,
        )

        return secrets.compare_digest(
            digest.hex(),
            stored_digest,
        )

    except (ValueError, AttributeError):
        return False


def get_session_user(request: Request):
    return request.session.get("user_id")


def get_session_role(request: Request):
    return request.session.get("role")


def login_user(request: Request, user):
    request.session["user_id"] = user.id
    request.session["role"] = user.role
    request.session["username"] = user.username


def login_super_admin(request: Request):
    request.session["super_admin"] = True
    request.session["role"] = "super_admin"


def logout_user(request: Request):
    request.session.clear()


def is_super_admin(request: Request) -> bool:
    return bool(request.session.get("super_admin"))


def require_super_admin(request: Request):
    if not is_super_admin(request):
        return RedirectResponse(
            "/super-admin/login",
            status_code=303,
        )

    return None


def require_login(request: Request):
    if not get_session_user(request):
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    return None
