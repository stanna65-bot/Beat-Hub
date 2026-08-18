import os
import secrets
import threading
import time
import uuid
import smtplib
import ssl
import sqlite3

from datetime import datetime, timedelta, timezone, date, time as dtime
from email.message import EmailMessage
from pathlib import Path

from fastapi import (
    FastAPI,
    Request,
    Form,
    UploadFile,
    File,
    HTTPException,
    Depends,
)

from fastapi.responses import (
    RedirectResponse,
    FileResponse,
    Response,
)

from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import auth
import mpesa

from database import (
    get_db,
    init_db,
    unique_slug,
)


# ============================================================
# APPLICATION PATHS
# ============================================================

BASE = Path(__file__).resolve().parent

STATIC = BASE / "static"

COVERS = STATIC / "uploads" / "covers"
AUDIO = STATIC / "uploads" / "audio"

ALBUM_COVERS = COVERS


COVERS.mkdir(
    parents=True,
    exist_ok=True,
)

AUDIO.mkdir(
    parents=True,
    exist_ok=True,
)


# ============================================================
# PLATFORM COMMISSION
# ============================================================

try:
    FEE_RATE = max(
        0,
        min(
            100,
            int(
                os.getenv(
                    "PLATFORM_COMMISSION_RATE",
                    "10",
                )
            ),
        ),
    )
except ValueError:
    FEE_RATE = 10


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="BeatHub - The Home of Beats",
)


# ============================================================
# SESSION CONFIGURATION
# ============================================================

SESSION_SECRET = os.getenv(
    "SESSION_SECRET",
    "CHANGE_THIS_SESSION_SECRET_IN_PRODUCTION",
)

SESSION_HTTPS_ONLY = (
    os.getenv(
        "SESSION_HTTPS_ONLY",
        "false",
    ).lower()
    == "true"
)

try:
    SESSION_MAX_AGE = int(
        os.getenv(
            "SESSION_MAX_AGE",
            str(60 * 60 * 24 * 30),
        )
    )
except ValueError:
    SESSION_MAX_AGE = 60 * 60 * 24 * 30


app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
    max_age=SESSION_MAX_AGE,
)


# ============================================================
# STATIC FILES / TEMPLATES
# ============================================================

app.mount(
    "/static",
    StaticFiles(
        directory=str(STATIC),
    ),
    name="static",
)


templates = Jinja2Templates(
    directory=str(BASE / "templates"),
)


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

init_db()


# ============================================================
# AUTH HELPERS
# ============================================================

def _normalize_login_email(value):
    """
    Normalize login/signup email addresses.

    Passwords are never changed here.
    """
    return (
        value or ""
    ).strip().casefold()


def _load_producer_from_session(request):
    """
    Resolve producer from the signed Starlette session.
    """

    raw_id = request.session.get(
        "producer_id"
    )

    try:
        producer_id = int(raw_id)
    except (
        TypeError,
        ValueError,
    ):
        return None

    if producer_id <= 0:
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
            (
                producer_id,
            ),
        ).fetchone()

    finally:
        c.close()


def _require_producer(request):
    """
    Require a valid producer session.
    """

    producer = _load_producer_from_session(
        request
    )

    if producer is None:
        request.session.pop(
            "producer_id",
            None,
        )

        request.session.pop(
            "remember_me",
            None,
        )

        raise HTTPException(
            status_code=401,
            detail="Login required",
        )

    return producer


def _verify_login_password(
    password,
    stored_hash,
):
    """
    Verify password using the existing auth implementation.
    """

    if not password or not stored_hash:
        return False

    try:
        return bool(
            auth.verify_password(
                password,
                stored_hash,
            )
        )

    except Exception:
        return False


# Keep the entire application on one producer-session path.
auth.current_producer = (
    _load_producer_from_session
)

auth.require_producer = (
    _require_producer
)


# ============================================================
# RENDER HELPERS
# ============================================================

def render(
    template_name,
    request,
    **kwargs,
):
    kwargs.update(
        request=request,
        producer=auth.current_producer(
            request
        ),
        super_admin=auth.is_super_admin(
            request
        ),
    )

    return templates.TemplateResponse(
        template_name,
        kwargs,
    )


def render_no_store(
    template_name,
    request,
    **kwargs,
):
    """
    Render sensitive pages with cache prevention.
    """

    response = render(
        template_name,
        request,
        **kwargs,
    )

    response.headers[
        "Cache-Control"
    ] = (
        "no-store, no-cache, "
        "must-revalidate, max-age=0, private"
    )

    response.headers[
        "Pragma"
    ] = "no-cache"

    response.headers[
        "Expires"
    ] = "0"

    return response


# ============================================================
# GENERAL HELPERS
# ============================================================

def now():
    return datetime.now(
        timezone.utc
    )


def iso(dt):
    return dt.astimezone(
        timezone.utc
    ).isoformat()


def parse_iso(value):
    return datetime.fromisoformat(
        value.replace(
            "Z",
            "+00:00",
        )
    ).astimezone(
        timezone.utc
    )


def ensure_wallet(
    c,
    producer_id,
):
    c.execute(
        """
        INSERT OR IGNORE INTO producer_wallets(
            producer_id
        )
        VALUES(?)
        """,
        (
            producer_id,
        ),
    )


def app_url(request):
    return (
        os.getenv(
            "APP_BASE_URL",
            "",
        ).rstrip("/")
        or str(
            request.base_url
        ).rstrip("/")
    )


# ============================================================
# EMAIL / PASSWORD RESET
# ============================================================

def send_reset(
    to,
    url,
):
    host = os.getenv(
        "SMTP_HOST",
        "",
    )

    username = os.getenv(
        "SMTP_USERNAME",
        "",
    )

    password = os.getenv(
        "SMTP_PASSWORD",
        "",
    )

    sender = (
        os.getenv(
            "SMTP_FROM_EMAIL",
            "",
        ).strip()
        or os.getenv(
            "SMTP_FROM",
            "",
        ).strip()
        or username
    )

    sender_name = (
        os.getenv(
            "SMTP_FROM_NAME",
            "BeatHub",
        ).strip()
        or "BeatHub"
    )

    if not all(
        (
            host,
            username,
            password,
            sender,
        )
    ):
        raise RuntimeError(
            "Email is not configured."
        )

    message = EmailMessage()

    message["Subject"] = (
        "Reset your BeatHub password"
    )

    message["From"] = (
        f"{sender_name} <{sender}>"
    )

    message["To"] = to

    message.set_content(
        f"""
Use this secure link to reset your BeatHub password.
It expires in 30 minutes:

{url}
"""
    )

    try:
        port = int(
            os.getenv(
                "SMTP_PORT",
                "587",
            )
        )
    except ValueError:
        port = 587

    with smtplib.SMTP(
        host,
        port,
        timeout=20,
    ) as server:

        server.starttls(
            context=ssl.create_default_context()
        )

        server.login(
            username,
            password,
        )

        server.send_message(
            message
        )


# ============================================================
# SECURE FILE UPLOAD
# ============================================================

def save_file(
    upload,
    folder,
    prefix,
    allowed_extensions,
    max_bytes,
):
    """
    Save an uploaded file using a random UUID filename.

    Returns the public static path.
    """

    if not upload or not upload.filename:
        raise HTTPException(
            status_code=400,
            detail="File is required.",
        )

    extension = Path(
        upload.filename
    ).suffix.lower()

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type.",
        )

    destination = (
        folder
        / (
            uuid.uuid4().hex
            + extension
        )
    )

    total_bytes = 0

    try:
        with destination.open(
            "wb"
        ) as output:

            while True:

                chunk = upload.file.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                total_bytes += len(
                    chunk
                )

                if total_bytes > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail="File too large.",
                    )

                output.write(
                    chunk
                )

    except Exception:
        destination.unlink(
            missing_ok=True
        )
        raise

    return (
        prefix
        + "/"
        + destination.name
    )


def delete_static_file(
    public_path,
):
    """
    Safely delete one file belonging to BeatHub static storage.
    """

    if not public_path:
        return

    try:
        path = (
            BASE
            / public_path.lstrip("/")
        ).resolve()

        static_root = (
            STATIC.resolve()
        )

        if static_root in path.parents:
            path.unlink(
                missing_ok=True
            )

    except Exception:
        pass


# ============================================================
# HEALTH / ROOT
# ============================================================

@app.api_route(
    "/health",
    methods=["GET", "HEAD"],
)
def health():
    return Response(
        "OK"
    )


@app.api_route(
    "/",
    methods=["HEAD"],
)
def head():
    return Response(
        status_code=200
    )


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home(
    r: Request,
):
    c = get_db()

    try:

        hot = c.execute(
            """
            SELECT
                b.*,
                p.name AS producer_name,
                p.slug AS producer_slug
            FROM beats b
            JOIN producers p
                ON p.id=b.producer_id
            WHERE b.is_hot_pick=1
            ORDER BY b.created_at DESC
            LIMIT 8
            """
        ).fetchall()

        services = c.execute(
            """
            SELECT
                s.*,
                p.name AS producer_name,
                p.slug AS producer_slug
            FROM session_services s
            JOIN producers p
                ON p.id=s.producer_id
            WHERE s.active=1
            ORDER BY s.created_at DESC
            LIMIT 6
            """
        ).fetchall()

    finally:
        c.close()

    return render(
        "home.html",
        r,
        hot_beats=hot,
        services=services,
    )


# ============================================================
# TERMS
# ============================================================

@app.get("/terms")
def terms(
    r: Request,
):
    return render(
        "terms.html",
        r,
    )


# ============================================================
# PRODUCER SIGNUP
# ============================================================

@app.get("/signup")
def signup_page(
    r: Request,
):
    if auth.current_producer(r):
        return RedirectResponse(
            "/admin",
            303,
        )

    return render_no_store(
        "signup.html",
        r,
        error=None,
    )


@app.post("/signup")
def signup(
    r: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str | None = Form(None),
    accept_terms: str | None = Form(None),
):
    name = name.strip()

    email = _normalize_login_email(
        email
    )

    if not name:
        return render_no_store(
            "signup.html",
            r,
            error=(
                "Your producer or stage name "
                "is required."
            ),
        )

    if (
        "@"
        not in email
        or len(email) > 254
    ):
        return render_no_store(
            "signup.html",
            r,
            error="Enter a valid email address.",
        )

    if len(password) < 8:
        return render_no_store(
            "signup.html",
            r,
            error=(
                "Password must be at least "
                "8 characters."
            ),
        )

    if (
        confirm_password is not None
        and password != confirm_password
    ):
        return render_no_store(
            "signup.html",
            r,
            error="Passwords do not match.",
        )

    c = get_db()

    try:

        existing = c.execute(
            """
            SELECT 1
            FROM producers
            WHERE lower(trim(email))=?
            """,
            (
                email,
            ),
        ).fetchone()

        if existing:
            return render_no_store(
                "signup.html",
                r,
                error=(
                    "Email already exists. "
                    "Please login or reset "
                    "your password."
                ),
            )

        producer_id = c.execute(
            """
            INSERT INTO producers(
                slug,
                email,
                password_hash,
                name
            )
            VALUES(?,?,?,?)
            """,
            (
                unique_slug(
                    c,
                    name,
                ),
                email,
                auth.hash_password(
                    password
                ),
                name,
            ),
        ).lastrowid

        ensure_wallet(
            c,
            producer_id,
        )

        c.commit()

    except Exception:
        c.rollback()
        raise

    finally:
        c.close()

    r.session.clear()

    r.session[
        "producer_id"
    ] = int(
        producer_id
    )

    response = RedirectResponse(
        "/admin",
        303,
    )

    response.set_cookie(
        key="beathub_last_email",
        value=email,
        max_age=60 * 60 * 24 * 365,
        httponly=False,
        samesite="lax",
        secure=SESSION_HTTPS_ONLY,
        path="/",
    )

    return response


# ============================================================
# PRODUCER LOGIN
# ============================================================

@app.get("/login")
def login_page(
    r: Request,
):
    if auth.current_producer(r):
        return RedirectResponse(
            "/admin",
            303,
        )

    return render_no_store(
        "login.html",
        r,
        error=None,
        saved_email=r.cookies.get(
            "beathub_last_email",
            "",
        ),
    )


@app.post("/login")
def login(
    r: Request,
    email: str = Form(...),
    password: str = Form(...),
    remember_me: str | None = Form(None),
):
    email = _normalize_login_email(
        email
    )

    if not email or not password:
        return render_no_store(
            "login.html",
            r,
            error=(
                "Enter your email and password."
            ),
            saved_email=email,
        )

    c = get_db()

    try:
        producer = c.execute(
            """
            SELECT *
            FROM producers
            WHERE lower(trim(email))=?
            LIMIT 1
            """,
            (
                email,
            ),
        ).fetchone()

    finally:
        c.close()

    if (
        not producer
        or not _verify_login_password(
            password,
            producer["password_hash"],
        )
    ):
        return render_no_store(
            "login.html",
            r,
            error="Incorrect email or password.",
            saved_email=email,
        )

    # Completely replace any previous session.
    r.session.clear()

    r.session[
        "producer_id"
    ] = int(
        producer["id"]
    )

    r.session[
        "remember_me"
    ] = (
        remember_me == "true"
    )

    response = RedirectResponse(
        "/admin",
        303,
    )

    response.set_cookie(
        key="beathub_last_email",
        value=email,
        max_age=60 * 60 * 24 * 365,
        httponly=False,
        samesite="lax",
        secure=SESSION_HTTPS_ONLY,
        path="/",
    )

    return response


# ============================================================
# PRODUCER LOGOUT
# ============================================================

@app.api_route(
    "/logout",
    methods=["GET", "POST"],
)
def logout(
    request: Request,
):
    """
    Complete producer logout.

    Supports both:
        POST /logout
        GET  /logout

    This makes the route compatible with either
    a form button or an existing logout link.
    """

    # Destroy the Starlette session completely.
    request.session.clear()

    response = RedirectResponse(
        url="/login",
        status_code=303,
    )

    # Starlette's default session cookie.
    response.delete_cookie(
        "session",
        path="/",
    )

    # Remove saved login email as requested.
    response.delete_cookie(
        "beathub_last_email",
        path="/",
    )

    # Backward compatibility with the earlier cookie name.
    response.delete_cookie(
        "remember_email",
        path="/",
    )

    # Prevent browser/proxy from showing
    # cached authenticated pages after logout.
    response.headers[
        "Cache-Control"
    ] = (
        "no-store, no-cache, "
        "must-revalidate, max-age=0, private"
    )

    response.headers[
        "Pragma"
    ] = "no-cache"

    response.headers[
        "Expires"
    ] = "0"

    return response


# ============================================================
# PASSWORD RESET
# ============================================================

@app.get("/forgot-password")
def forgot_page(
    r: Request,
):
    return render(
        "forgot_password.html",
        r,
        error=None,
        message=None,
    )


@app.post("/forgot-password")
def forgot(
    r: Request,
    email: str = Form(...),
):
    email = _normalize_login_email(
        email
    )

    message = (
        "If an account exists for that email, "
        "a reset link has been sent."
    )

    token = None
    producer = None

    c = get_db()

    try:

        producer = c.execute(
            """
            SELECT id, email
            FROM producers
            WHERE email=?
            """,
            (
                email,
            ),
        ).fetchone()

        if producer:

            token = auth.new_token()

            c.execute(
                """
                UPDATE password_reset_tokens
                SET used_at=CURRENT_TIMESTAMP
                WHERE producer_id=?
                AND used_at IS NULL
                """,
                (
                    producer["id"],
                ),
            )

            c.execute(
                """
                INSERT INTO password_reset_tokens(
                    producer_id,
                    token_hash,
                    expires_at
                )
                VALUES(?,?,?)
                """,
                (
                    producer["id"],
                    auth.token_hash(
                        token
                    ),
                    iso(
                        now()
                        + timedelta(
                            minutes=30
                        )
                    ),
                ),
            )

            c.commit()

    finally:
        c.close()

    if producer:

        try:
            send_reset(
                producer["email"],
                app_url(r)
                + "/reset-password/"
                + token,
            )

        except Exception:
            return render(
                "forgot_password.html",
                r,
                error=(
                    "Reset email could not be sent. "
                    "Please try again later."
                ),
                message=None,
            )

    return render(
        "forgot_password.html",
        r,
        error=None,
        message=message,
    )


@app.get(
    "/reset-password/{token}"
)
def reset_page(
    r: Request,
    token: str,
):
    return render(
        "reset_password.html",
        r,
        token=token,
        error=None,
    )


@app.post(
    "/reset-password/{token}"
)
def reset(
    r: Request,
    token: str,
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    if (
        len(password) < 8
        or password != confirm_password
    ):
        return render(
            "reset_password.html",
            r,
            token=token,
            error=(
                "Passwords must match and be "
                "at least 8 characters."
            ),
        )

    c = get_db()

    try:

        reset_record = c.execute(
            """
            SELECT *
            FROM password_reset_tokens
            WHERE token_hash=?
            AND used_at IS NULL
            """,
            (
                auth.token_hash(
                    token
                ),
            ),
        ).fetchone()

        if (
            not reset_record
            or parse_iso(
                reset_record["expires_at"]
            ) < now()
        ):
            return render(
                "reset_password.html",
                r,
                token=token,
                error=(
                    "This reset link is invalid "
                    "or expired."
                ),
            )

        c.execute(
            """
            UPDATE producers
            SET password_hash=?
            WHERE id=?
            """,
            (
                auth.hash_password(
                    password
                ),
                reset_record[
                    "producer_id"
                ],
            ),
        )

        c.execute(
            """
            UPDATE password_reset_tokens
            SET used_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                reset_record["id"],
            ),
        )

        c.commit()

    finally:
        c.close()

    return RedirectResponse(
        "/login",
        303,
    )


# ============================================================
# PUBLIC PRODUCER STORE
# ============================================================

@app.get("/p/{slug}")
def feed(
    r: Request,
    slug: str,
):
    c = get_db()

    try:

        producer = c.execute(
            """
            SELECT *
            FROM producers
            WHERE slug=?
            """,
            (
                slug,
            ),
        ).fetchone()

        if not producer:
            raise HTTPException(
                404,
                "Producer not found",
            )

        beats = c.execute(
            """
            SELECT *
            FROM beats
            WHERE producer_id=?
            ORDER BY
                is_hot_pick DESC,
                created_at DESC
            """,
            (
                producer["id"],
            ),
        ).fetchall()

        services = c.execute(
            """
            SELECT *
            FROM session_services
            WHERE producer_id=?
            AND active=1
            ORDER BY created_at DESC
            """,
            (
                producer["id"],
            ),
        ).fetchall()

        albums = c.execute(
            """
            SELECT
                a.*,
                COUNT(at.id) AS track_count
            FROM albums a
            LEFT JOIN album_tracks at
                ON at.album_id=a.id
            WHERE a.producer_id=?
            AND a.is_published=1
            GROUP BY a.id
            ORDER BY a.created_at DESC
            """,
            (
                producer["id"],
            ),
        ).fetchall()

    finally:
        c.close()

    return render(
        "feed.html",
        r,
        profile=producer,
        beats=beats,
        albums=albums,
        services=services,
    )


# ============================================================
# PUBLIC BEAT PAGE
# ============================================================

@app.get(
    "/p/{slug}/beat/{beat_id}"
)
def beat(
    r: Request,
    slug: str,
    beat_id: int,
):
    c = get_db()

    try:

        producer = c.execute(
            """
            SELECT *
            FROM producers
            WHERE slug=?
            """,
            (
                slug,
            ),
        ).fetchone()

        beat_record = c.execute(
            """
            SELECT *
            FROM beats
            WHERE id=?
            """,
            (
                beat_id,
            ),
        ).fetchone()

    finally:
        c.close()

    if (
        not producer
        or not beat_record
        or beat_record["producer_id"]
        != producer["id"]
    ):
        raise HTTPException(
            404,
            "Beat not found",
        )

    return render(
        "beat.html",
        r,
        profile=producer,
        beat=beat_record,
    )


# ============================================================
# PRODUCER ADMIN DASHBOARD
# ============================================================

@app.get("/admin")
def admin(
    r: Request,
    producer=Depends(
        auth.require_producer
    ),
):
    c = get_db()

    try:

        ensure_wallet(
            c,
            producer["id"],
        )

        # Make sure a newly-created wallet
        # is persisted.
        c.commit()

        wallet = c.execute(
            """
            SELECT *
            FROM producer_wallets
            WHERE producer_id=?
            """,
            (
                producer["id"],
            ),
        ).fetchone()

        beats = c.execute(
            """
            SELECT *
            FROM beats
            WHERE producer_id=?
            ORDER BY created_at DESC
            """,
            (
                producer["id"],
            ),
        ).fetchall()

        albums = c.execute(
            """
            SELECT
                a.*,
                COUNT(at.id) AS track_count
            FROM albums a
            LEFT JOIN album_tracks at
                ON at.album_id=a.id
            WHERE a.producer_id=?
            GROUP BY a.id
            ORDER BY a.created_at DESC
            """,
            (
                producer["id"],
            ),
        ).fetchall()

        services = c.execute(
            """
            SELECT *
            FROM session_services
            WHERE producer_id=?
            ORDER BY created_at DESC
            """,
            (
                producer["id"],
            ),
        ).fetchall()

        availability = c.execute(
            """
            SELECT *
            FROM producer_availability
            WHERE producer_id=?
            ORDER BY weekday
            """,
            (
                producer["id"],
            ),
        ).fetchall()

        bookings = c.execute(
            """
            SELECT
                b.*,
                s.title AS service_title
            FROM session_bookings b
            JOIN session_services s
                ON s.id=b.service_id
            WHERE b.producer_id=?
            ORDER BY b.start_at DESC
            LIMIT 50
            """,
            (
                producer["id"],
            ),
        ).fetchall()

        withdrawals = c.execute(
            """
            SELECT *
            FROM withdrawals
            WHERE producer_id=?
            ORDER BY requested_at DESC
            LIMIT 20
            """,
            (
                producer["id"],
            ),
        ).fetchall()

    finally:
        c.close()

    return render(
        "admin.html",
        r,
        wallet=wallet,
        beats=beats,
        albums=albums,
        services=services,
        availability=availability,
        bookings=bookings,
        withdrawals=withdrawals,
        totals={
            "available_balance":
                wallet[
                    "available_balance"
                ],

            "total_earnings":
                wallet[
                    "total_earnings"
                ],

            "total_withdrawn":
                wallet[
                    "total_withdrawn"
                ],
        },
    )


# ============================================================
# PRODUCER PROFILE
# ============================================================

@app.post(
    "/admin/profile"
)
def profile(
    r: Request,
    name: str = Form(...),
    bio: str = Form(""),
    phone: str = Form(""),
    payout_phone: str = Form(""),
    producer=Depends(
        auth.require_producer
    ),
):
    payout = (
        mpesa.normalize_phone(
            payout_phone
        )
        if payout_phone.strip()
        else ""
    )

    c = get_db()

    try:

        c.execute(
            """
            UPDATE producers
            SET
                name=?,
                bio=?,
                phone=?,
                payout_phone=?
            WHERE id=?
            """,
            (
                name.strip()[:100],
                bio.strip()[:2000],
                phone.strip()[:30],
                payout,
                producer["id"],
            ),
        )

        c.commit()

    finally:
        c.close()

    return RedirectResponse(
        "/admin",
        303,
    )


# ============================================================
# SINGLE BEAT UPLOAD
# ============================================================

@app.post("/admin/beat")
def add_beat(
    r: Request,
    title: str = Form(...),
    genre: str = Form(""),
    bpm: str = Form(""),
    price: int = Form(...),
    is_hot_pick: str = Form("0"),
    cover: UploadFile = File(...),
    audio: UploadFile = File(...),
    producer=Depends(
        auth.require_producer
    ),
):
    if price < 1:
        raise HTTPException(
            400,
            "Invalid price.",
        )

    if bpm.strip():

        try:
            bpm_value = int(
                bpm.strip()
            )
        except ValueError:
            raise HTTPException(
                400,
                "BPM must be a number.",
            )

        if bpm_value < 1:
            raise HTTPException(
                400,
                "Invalid BPM.",
            )

    else:
        bpm_value = None

    cover_path = save_file(
        cover,
        COVERS,
        "/static/uploads/covers",
        {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
        },
        10 * 1024 * 1024,
    )

    try:

        audio_path = save_file(
            audio,
            AUDIO,
            "/static/uploads/audio",
            {
                ".mp3",
                ".wav",
                ".m4a",
            },
            100 * 1024 * 1024,
        )

    except Exception:
        delete_static_file(
            cover_path
        )
        raise

    c = get_db()

    try:

        c.execute(
            """
            INSERT INTO beats(
                producer_id,
                title,
                genre,
                bpm,
                price,
                cover_path,
                audio_path,
                is_hot_pick
            )
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                producer["id"],
                title.strip()[:200],
                genre.strip()[:100],
                bpm_value,
                price,
                cover_path,
                audio_path,
                (
                    1
                    if is_hot_pick.lower()
                    in (
                        "1",
                        "on",
                        "true",
                    )
                    else 0
                ),
            ),
        )

        c.commit()

    except Exception:
        c.rollback()

        delete_static_file(
            cover_path
        )

        delete_static_file(
            audio_path
        )

        raise

    finally:
        c.close()

    return RedirectResponse(
        "/admin",
        303,
    )


# ============================================================
# ALBUM / MULTI-TRACK UPLOAD
# ============================================================

@app.post("/admin/album")
def add_album(
    r: Request,
    album_title: str = Form(...),
    album_price: int = Form(...),
    album_genre: str = Form(""),
    cover: UploadFile = File(...),
    tracks: list[UploadFile] = File(...),
    producer=Depends(
        auth.require_producer
    ),
):
    """
    Upload a complete album.

    Album:
        - title
        - genre
        - one cover
        - price
        - multiple audio tracks

    Existing single-track upload is completely
    independent and remains available.
    """

    title = (
        album_title
        .strip()
        [:200]
    )

    genre = (
        album_genre
        .strip()
        [:100]
    )

    if not title:
        raise HTTPException(
            400,
            "Album title is required.",
        )

    if album_price < 1:
        raise HTTPException(
            400,
            "Album price must be at least KES 1.",
        )

    if not tracks:
        raise HTTPException(
            400,
            "Upload at least one track.",
        )

    if len(tracks) > 50:
        raise HTTPException(
            400,
            "You can upload a maximum of 50 tracks at once.",
        )

    cover_path = None
    saved_audio = []

    try:

        # ----------------------------------------------------
        # ALBUM COVER
        # ----------------------------------------------------

        cover_path = save_file(
            cover,
            ALBUM_COVERS,
            "/static/uploads/covers",
            {
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
            },
            10 * 1024 * 1024,
        )

        # ----------------------------------------------------
        # TRACKS
        # ----------------------------------------------------

        seen_titles = set()

        for index, upload in enumerate(
            tracks,
            start=1,
        ):

            if (
                not upload
                or not upload.filename
            ):
                raise HTTPException(
                    400,
                    f"Track {index} has no filename.",
                )

            extension = Path(
                upload.filename
            ).suffix.lower()

            if extension not in {
                ".mp3",
                ".wav",
                ".m4a",
            }:
                raise HTTPException(
                    400,
                    (
                        f"Track {index} has an "
                        "unsupported audio format."
                    ),
                )

            # Automatically turn filename into title.
            track_title = (
                Path(
                    upload.filename
                ).stem
                .strip()
                [:200]
            )

            if not track_title:
                track_title = (
                    f"Track {index}"
                )

            original_key = (
                track_title.casefold()
            )

            if original_key in seen_titles:
                track_title = (
                    f"{track_title} ({index})"
                )

            seen_titles.add(
                original_key
            )

            audio_path = save_file(
                upload,
                AUDIO,
                "/static/uploads/audio",
                {
                    ".mp3",
                    ".wav",
                    ".m4a",
                },
                100 * 1024 * 1024,
            )

            saved_audio.append(
                (
                    index,
                    track_title,
                    audio_path,
                )
            )

        # ----------------------------------------------------
        # DATABASE TRANSACTION
        # ----------------------------------------------------

        c = get_db()

        try:

            c.execute(
                "BEGIN IMMEDIATE"
            )

            album_id = c.execute(
                """
                INSERT INTO albums(
                    producer_id,
                    title,
                    genre,
                    price,
                    cover_path,
                    is_published
                )
                VALUES(?,?,?,?,?,1)
                """,
                (
                    producer["id"],
                    title,
                    genre,
                    album_price,
                    cover_path,
                ),
            ).lastrowid

            for (
                track_number,
                track_title,
                audio_path,
            ) in saved_audio:

                c.execute(
                    """
                    INSERT INTO album_tracks(
                        album_id,
                        track_number,
                        title,
                        audio_path
                    )
                    VALUES(?,?,?,?)
                    """,
                    (
                        album_id,
                        track_number,
                        track_title,
                        audio_path,
                    ),
                )

            c.commit()

        except Exception:
            c.rollback()
            raise

        finally:
            c.close()

    except Exception:

        # If anything goes wrong, don't leave orphan
        # uploads sitting on the server.

        delete_static_file(
            cover_path
        )

        for (
            _track_number,
            _track_title,
            audio_path,
        ) in saved_audio:

            delete_static_file(
                audio_path
            )

        raise

    return RedirectResponse(
        "/admin",
        303,
    )


# ============================================================
# ALBUM PUBLISH / UNPUBLISH
# ============================================================

@app.post(
    "/admin/album/{album_id}/publish"
)
def publish_album(
    album_id: int,
    is_published: str = Form("1"),
    producer=Depends(
        auth.require_producer
    ),
):
    published = (
        1
        if str(
            is_published
        ).lower()
        in (
            "1",
            "true",
            "on",
            "yes",
        )
        else 0
    )

    c = get_db()

    try:

        result = c.execute(
            """
            UPDATE albums
            SET is_published=?
            WHERE id=?
            AND producer_id=?
            """,
            (
                published,
                album_id,
                producer["id"],
            ),
        )

        c.commit()

    finally:
        c.close()

    if not result.rowcount:
        raise HTTPException(
            404,
            "Album not found",
        )

    return RedirectResponse(
        "/admin",
        303,
    )


# ============================================================
# PUBLIC ALBUM PAGE
# ============================================================

@app.get(
    "/p/{slug}/album/{album_id}"
)
def album_page(
    r: Request,
    slug: str,
    album_id: int,
):
    c = get_db()

    try:

        album = c.execute(
            """
            SELECT
                a.*,
                p.name AS producer_name,
                p.slug AS producer_slug
            FROM albums a
            JOIN producers p
                ON p.id=a.producer_id
            WHERE a.id=?
            AND a.is_published=1
            AND p.slug=?
            """,
            (
                album_id,
                slug,
            ),
        ).fetchone()

        tracks = (
            c.execute(
                """
                SELECT *
                FROM album_tracks
                WHERE album_id=?
                ORDER BY track_number
                """,
                (
                    album_id,
                ),
            ).fetchall()
            if album
            else []
        )

    finally:
        c.close()

    if not album:
        raise HTTPException(
            404,
            "Album not found",
        )

    return render(
        "album.html",
        r,
        profile={
            "name":
                album[
                    "producer_name"
                ],
            "slug":
                album[
                    "producer_slug"
                ],
        },
        album=album,
        tracks=tracks,
    )


# ============================================================
# HOT PICK
# ============================================================

@app.post(
    "/admin/beat/{beat_id}/hot-pick"
)
def hot_pick(
    beat_id: int,
    is_hot_pick: str = Form("0"),
    producer=Depends(
        auth.require_producer
    ),
):
    hot = (
        1
        if str(
            is_hot_pick
        ).lower()
        in (
            "1",
            "true",
            "on",
            "yes",
        )
        else 0
    )

    c = get_db()

    try:

        result = c.execute(
            """
            UPDATE beats
            SET is_hot_pick=?
            WHERE id=?
            AND producer_id=?
            """,
            (
                hot,
                beat_id,
                producer["id"],
            ),
        )

        c.commit()

    finally:
        c.close()

    if not result.rowcount:
        raise HTTPException(
            404,
            "Beat not found",
        )

    return RedirectResponse(
        "/admin",
        303,
    )


# ============================================================
# SESSION SERVICES
# ============================================================

@app.post("/admin/service")
def add_service(
    r: Request,
    title: str = Form(...),
    description: str = Form(""),
    duration_minutes: int = Form(...),
    price: int = Form(...),
    location: str = Form(""),
    producer=Depends(
        auth.require_producer
    ),
):
    if (
        not 15
        <= duration_minutes
        <= 720
        or price < 1
    ):
        raise HTTPException(
            400,
            "Invalid service details.",
        )

    c = get_db()

    try:

        c.execute(
            """
            INSERT INTO session_services(
                producer_id,
                title,
                description,
                duration_minutes,
                price,
                location
            )
            VALUES(?,?,?,?,?,?)
            """,
            (
                producer["id"],
                title.strip()[:100],
                description.strip()[:1000],
                duration_minutes,
                price,
                location.strip()[:200],
            ),
        )

        c.commit()

    finally:
        c.close()

    return RedirectResponse(
        "/admin",
        303,
    )


# ============================================================
# AVAILABILITY
# ============================================================

@app.post(
    "/admin/availability"
)
def availability(
    r: Request,
    weekday: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    slot_minutes: int = Form(60),
    producer=Depends(
        auth.require_producer
    ),
):
    if not (
        0 <= weekday <= 6
        and 15 <= slot_minutes <= 240
        and start_time < end_time
    ):
        raise HTTPException(
            400,
            "Invalid availability.",
        )

    c = get_db()

    try:

        c.execute(
            """
            INSERT INTO producer_availability(
                producer_id,
                weekday,
                start_time,
                end_time,
                slot_minutes
            )
            VALUES(?,?,?,?,?)
            ON CONFLICT(
                producer_id,
                weekday
            )
            DO UPDATE SET
                start_time=excluded.start_time,
                end_time=excluded.end_time,
                slot_minutes=excluded.slot_minutes
            """,
            (
                producer["id"],
                weekday,
                start_time,
                end_time,
                slot_minutes,
            ),
        )

        c.commit()

    finally:
        c.close()

    return RedirectResponse(
        "/admin",
        303,
    )


# ============================================================
# PRODUCER WITHDRAWAL
# ============================================================

def request_producer_withdrawal(
    c,
    producer_id,
    amount,
    phone,
):
    c.execute(
        "BEGIN IMMEDIATE"
    )

    result = c.execute(
        """
        UPDATE producer_wallets
        SET
            available_balance=
                available_balance-?,
            pending_withdrawal=
                pending_withdrawal+?,
            updated_at=CURRENT_TIMESTAMP
        WHERE producer_id=?
        AND available_balance>=?
        """,
        (
            amount,
            amount,
            producer_id,
            amount,
        ),
    )

    if not result.rowcount:
        raise HTTPException(
            400,
            "Insufficient available balance.",
        )

    withdrawal_id = c.execute(
        """
        INSERT INTO withdrawals(
            producer_id,
            amount,
            phone,
            status
        )
        VALUES(?,?,?,'pending')
        """,
        (
            producer_id,
            amount,
            phone,
        ),
    ).lastrowid

    c.commit()

    return withdrawal_id


@app.post("/admin/withdraw")
def withdraw(
    amount: int = Form(...),
    producer=Depends(
        auth.require_producer
    ),
):
    if amount < 10:
        raise HTTPException(
            400,
            "Minimum withdrawal amount is 10.",
        )

    c = get_db()

    try:

        producer_record = c.execute(
            """
            SELECT payout_phone
            FROM producers
            WHERE id=?
            """,
            (
                producer["id"],
            ),
        ).fetchone()

        if (
            not producer_record
            or not producer_record[
                "payout_phone"
            ]
        ):
            raise HTTPException(
                400,
                "Add a payout number first.",
            )

        withdrawal_id = (
            request_producer_withdrawal(
                c,
                producer["id"],
                amount,
                producer_record[
                    "payout_phone"
                ],
            )
        )

    except Exception:

        try:
            c.rollback()
        except Exception:
            pass

        raise

    finally:
        c.close()

    try:

        result = (
            mpesa.initiate_producer_payout(
                producer_record[
                    "payout_phone"
                ],
                amount,
                f"WD{withdrawal_id}",
            )
        )

    except Exception as exc:

        c = get_db()

        try:

            c.execute(
                "BEGIN IMMEDIATE"
            )

            c.execute(
                """
                UPDATE withdrawals
                SET
                    status='failed',
                    failure_reason=?
                WHERE id=?
                """,
                (
                    str(exc)[:500],
                    withdrawal_id,
                ),
            )

            c.execute(
                """
                UPDATE producer_wallets
                SET
                    available_balance=
                        available_balance+?,
                    pending_withdrawal=
                        pending_withdrawal-?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE producer_id=?
                """,
                (
                    amount,
                    amount,
                    producer["id"],
                ),
            )

            c.commit()

        finally:
            c.close()

        raise HTTPException(
            502,
            str(exc),
        )

    if result.get(
        "simulated"
    ):

        c = get_db()

        try:

            c.execute(
                "BEGIN IMMEDIATE"
            )

            c.execute(
                """
                UPDATE withdrawals
                SET
                    status='completed',
                    payout_reference=?,
                    completed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    result["reference"],
                    withdrawal_id,
                ),
            )

            c.execute(
                """
                UPDATE producer_wallets
                SET
                    pending_withdrawal=
                        pending_withdrawal-?,
                    total_withdrawn=
                        total_withdrawn+?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE producer_id=?
                """,
                (
                    amount,
                    amount,
                    producer["id"],
                ),
            )

            c.commit()

        finally:
            c.close()

    return RedirectResponse(
        "/admin",
        303,
    )


# ============================================================
# PAYMENT SPLIT
# ============================================================

def split(
    c,
    kind,
    source_id,
    producer_id,
    amount,
):
    """
    Split one completed transaction between
    producer and platform.

    INSERT OR IGNORE prevents duplicate callback
    processing from crediting wallets twice.
    """

    amount = int(
        amount
    )

    if amount <= 0:
        raise HTTPException(
            400,
            "Transaction amount must be greater than zero.",
        )

    fee = round(
        amount * FEE_RATE / 100
    )

    net = (
        amount - fee
    )

    result = c.execute(
        """
        INSERT OR IGNORE INTO platform_ledger(
            source_type,
            source_id,
            gross_amount,
            platform_fee,
            producer_credit
        )
        VALUES(?,?,?,?,?)
        """,
        (
            kind,
            source_id,
            amount,
            fee,
            net,
        ),
    )

    if not result.rowcount:
        return None

    ensure_wallet(
        c,
        producer_id,
    )

    c.execute(
        """
        UPDATE producer_wallets
        SET
            available_balance=
                available_balance+?,
            total_earnings=
                total_earnings+?,
            updated_at=CURRENT_TIMESTAMP
        WHERE producer_id=?
        """,
        (
            net,
            net,
            producer_id,
        ),
    )

    c.execute(
        """
        UPDATE platform_wallet
        SET
            available_balance=
                available_balance+?,
            total_earnings=
                total_earnings+?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=1
        """,
        (
            fee,
            fee,
        ),
    )

    return (
        fee,
        net,
    )


# ============================================================
# BEAT CHECKOUT
# ============================================================

@app.post(
    "/checkout/{beat_id}"
)
def checkout(
    beat_id: int,
    phone: str = Form(...),
):
    try:

        phone = mpesa.normalize_phone(
            phone
        )

    except ValueError as exc:
        raise HTTPException(
            400,
            str(exc),
        )

    c = get_db()

    try:

        beat_record = c.execute(
            """
            SELECT *
            FROM beats
            WHERE id=?
            """,
            (
                beat_id,
            ),
        ).fetchone()

        if not beat_record:
            raise HTTPException(
                404,
                "Beat not found",
            )

        order_id = c.execute(
            """
            INSERT INTO orders(
                beat_id,
                buyer_phone,
                amount
            )
            VALUES(?,?,?)
            """,
            (
                beat_id,
                phone,
                beat_record[
                    "price"
                ],
            ),
        ).lastrowid

        c.commit()

    finally:
        c.close()

    try:

        result = mpesa.stk_push(
            phone,
            beat_record[
                "price"
            ],
            f"BEAT{beat_id}",
            beat_record[
                "title"
            ],
        )

    except Exception as exc:

        c = get_db()

        c.execute(
            """
            UPDATE orders
            SET
                status='failed',
                failure_reason=?
            WHERE id=?
            """,
            (
                str(exc)[:500],
                order_id,
            ),
        )

        c.commit()
        c.close()

        raise HTTPException(
            502,
            str(exc),
        )

    c = get_db()

    c.execute(
        """
        UPDATE orders
        SET checkout_request_id=?
        WHERE id=?
        """,
        (
            result[
                "checkout_request_id"
            ],
            order_id,
        ),
    )

    c.commit()
    c.close()

    if result.get(
        "simulated"
    ):

        threading.Thread(
            target=lambda: (
                time.sleep(1),
                complete_beat(
                    order_id
                ),
            ),
            daemon=True,
        ).start()

    return {
        "order_id": order_id,
        "status": "pending",
    }


def complete_beat(
    order_id,
):
    c = get_db()

    try:

        c.execute(
            "BEGIN IMMEDIATE"
        )

        order = c.execute(
            """
            SELECT
                o.*,
                b.producer_id
            FROM orders o
            JOIN beats b
                ON b.id=o.beat_id
            WHERE o.id=?
            """,
            (
                order_id,
            ),
        ).fetchone()

        if (
            not order
            or order["status"]
            not in (
                "pending",
                "completed",
            )
        ):
            c.rollback()
            return

        split_result = split(
            c,
            "beat",
            order_id,
            order["producer_id"],
            order["amount"],
        )

        c.execute(
            """
            UPDATE orders
            SET
                status='completed',
                completed_at=
                    COALESCE(
                        completed_at,
                        CURRENT_TIMESTAMP
                    )
            WHERE id=?
            """,
            (
                order_id,
            ),
        )

        if split_result:

            c.execute(
                """
                UPDATE orders
                SET
                    platform_fee=?,
                    producer_payout=?,
                    commission_rate_locked=?,
                    split_applied_at=CURRENT_TIMESTAMP,
                    download_token=
                        COALESCE(
                            download_token,
                            ?
                        )
                WHERE id=?
                """,
                (
                    split_result[0],
                    split_result[1],
                    FEE_RATE,
                    secrets.token_urlsafe(
                        32
                    ),
                    order_id,
                ),
            )

        c.commit()

    except Exception:
        c.rollback()
        raise

    finally:
        c.close()


@app.get(
    "/order/{oid}/status"
)
def order_status(
    oid: int,
):
    c = get_db()

    order = c.execute(
        """
        SELECT
            status,
            download_token
        FROM orders
        WHERE id=?
        """,
        (
            oid,
        ),
    ).fetchone()

    c.close()

    if not order:
        raise HTTPException(
            404,
            "Order not found",
        )

    return {
        "status":
            order[
                "status"
            ],

        "download_token":
            (
                order[
                    "download_token"
                ]
                if order[
                    "status"
                ]
                == "completed"
                else None
            ),
    }


# ============================================================
# BOOKING / SESSION SYSTEM
# ============================================================

@app.get(
    "/sessions/{service_id}/book"
)
def book_page(
    r: Request,
    service_id: int,
):
    c = get_db()

    service = c.execute(
        """
        SELECT
            s.*,
            p.name AS producer_name,
            p.slug AS producer_slug
        FROM session_services s
        JOIN producers p
            ON p.id=s.producer_id
        WHERE s.id=?
        AND s.active=1
        """,
        (
            service_id,
        ),
    ).fetchone()

    c.close()

    if not service:
        raise HTTPException(
            404,
            "Service not found",
        )

    return render(
        "book_session.html",
        r,
        service=service,
    )


def slot_free(
    c,
    producer_id,
    start,
    end,
    ignore=None,
):
    query = """
        SELECT 1
        FROM session_bookings
        WHERE producer_id=?
        AND status IN(
            'pending',
            'paid',
            'confirmed'
        )
        AND (
            hold_expires_at IS NULL
            OR hold_expires_at>?
        )
        AND start_at<?
        AND end_at>?
    """

    args = [
        producer_id,
        iso(now()),
        iso(end),
        iso(start),
    ]

    if ignore:
        query += " AND id<>?"
        args.append(
            ignore
        )

    return not c.execute(
        query,
        args,
    ).fetchone()


@app.get(
    "/api/services/{sid}/slots"
)
def slots(
    sid: int,
    day: str,
):
    selected_day = date.fromisoformat(
        day
    )

    c = get_db()

    service = c.execute(
        """
        SELECT *
        FROM session_services
        WHERE id=?
        AND active=1
        """,
        (
            sid,
        ),
    ).fetchone()

    if not service:
        c.close()

        raise HTTPException(
            404,
            "Service not found",
        )

    availability_record = c.execute(
        """
        SELECT *
        FROM producer_availability
        WHERE producer_id=?
        AND weekday=?
        """,
        (
            service[
                "producer_id"
            ],
            selected_day.weekday(),
        ),
    ).fetchone()

    if not availability_record:
        c.close()
        return []

    current = datetime.combine(
        selected_day,
        dtime.fromisoformat(
            availability_record[
                "start_time"
            ]
        ),
        tzinfo=timezone.utc,
    )

    end_of_day = datetime.combine(
        selected_day,
        dtime.fromisoformat(
            availability_record[
                "end_time"
            ]
        ),
        tzinfo=timezone.utc,
    )

    duration = timedelta(
        minutes=service[
            "duration_minutes"
        ]
    )

    output = []

    while (
        current + duration
        <= end_of_day
    ):

        if (
            current > now()
            and slot_free(
                c,
                service[
                    "producer_id"
                ],
                current,
                current + duration,
            )
        ):

            output.append(
                {
                    "start_at":
                        iso(current),

                    "end_at":
                        iso(
                            current
                            + duration
                        ),
                }
            )

        current += timedelta(
            minutes=availability_record[
                "slot_minutes"
            ]
        )

    c.close()

    return output


@app.post(
    "/sessions/{sid}/book"
)
def create_booking(
    sid: int,
    client_name: str = Form(...),
    client_phone: str = Form(...),
    client_email: str = Form(""),
    start_at: str = Form(...),
):
    try:

        phone = mpesa.normalize_phone(
            client_phone
        )

    except ValueError as exc:
        raise HTTPException(
            400,
            str(exc),
        )

    start = parse_iso(
        start_at
    )

    c = get_db()

    try:

        c.execute(
            "BEGIN IMMEDIATE"
        )

        c.execute(
            """
            UPDATE session_bookings
            SET
                status='cancelled',
                cancelled_at=CURRENT_TIMESTAMP
            WHERE producer_id IN(
                SELECT producer_id
                FROM session_services
                WHERE id=?
            )
            AND status='pending'
            AND hold_expires_at IS NOT NULL
            AND hold_expires_at<=?
            """,
            (
                sid,
                iso(now()),
            ),
        )

        service = c.execute(
            """
            SELECT *
            FROM session_services
            WHERE id=?
            AND active=1
            """,
            (
                sid,
            ),
        ).fetchone()

        if not service:
            raise HTTPException(
                404,
                "Service not found",
            )

        end = start + timedelta(
            minutes=service[
                "duration_minutes"
            ]
        )

        if (
            start <= now()
            or not slot_free(
                c,
                service[
                    "producer_id"
                ],
                start,
                end,
            )
        ):
            raise HTTPException(
                409,
                "That time is no longer available.",
            )

        booking_id = c.execute(
            """
            INSERT INTO session_bookings(
                producer_id,
                service_id,
                client_name,
                client_phone,
                client_email,
                start_at,
                end_at,
                amount,
                status,
                hold_expires_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                service[
                    "producer_id"
                ],
                sid,
                client_name.strip()[:100],
                phone,
                client_email.strip()[:200],
                iso(start),
                iso(end),
                service["price"],
                "pending",
                iso(
                    now()
                    + timedelta(
                        minutes=10
                    )
                ),
            ),
        ).lastrowid

        c.commit()

    except sqlite3.IntegrityError:

        c.rollback()

        raise HTTPException(
            409,
            "That time is no longer available.",
        )

    finally:
        c.close()

    try:

        result = mpesa.stk_push(
            phone,
            service["price"],
            f"SESSION{booking_id}",
            service["title"],
        )

    except Exception as exc:

        c = get_db()

        c.execute(
            """
            UPDATE session_bookings
            SET
                status='cancelled',
                cancelled_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                booking_id,
            ),
        )

        c.commit()
        c.close()

        raise HTTPException(
            502,
            str(exc),
        )

    c = get_db()

    c.execute(
        """
        UPDATE session_bookings
        SET checkout_request_id=?
        WHERE id=?
        """,
        (
            result[
                "checkout_request_id"
            ],
            booking_id,
        ),
    )

    c.commit()
    c.close()

    if result.get(
        "simulated"
    ):

        threading.Thread(
            target=lambda: (
                time.sleep(1),
                complete_session(
                    booking_id
                ),
            ),
            daemon=True,
        ).start()

    return {
        "booking_id":
            booking_id,

        "status":
            "pending",
    }


def complete_session(
    booking_id,
):
    c = get_db()

    try:

        c.execute(
            "BEGIN IMMEDIATE"
        )

        booking = c.execute(
            """
            SELECT *
            FROM session_bookings
            WHERE id=?
            """,
            (
                booking_id,
            ),
        ).fetchone()

        if (
            not booking
            or booking["status"]
            != "pending"
        ):
            c.rollback()
            return

        split_result = split(
            c,
            "session",
            booking_id,
            booking[
                "producer_id"
            ],
            booking[
                "amount"
            ],
        )

        c.execute(
            """
            UPDATE session_bookings
            SET
                status='paid',
                paid_at=CURRENT_TIMESTAMP,
                hold_expires_at=NULL,
                platform_fee=?,
                producer_payout=?,
                split_applied_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                split_result[0],
                split_result[1],
                booking_id,
            ),
        )

        c.commit()

    except Exception:
        c.rollback()
        raise

    finally:
        c.close()


@app.get(
    "/booking/{bid}"
)
def booking_page(
    r: Request,
    bid: int,
):
    c = get_db()

    booking = c.execute(
        """
        SELECT
            b.*,
            s.title AS service_title,
            p.name AS producer_name,
            p.slug
        FROM session_bookings b
        JOIN session_services s
            ON s.id=b.service_id
        JOIN producers p
            ON p.id=b.producer_id
        WHERE b.id=?
        """,
        (
            bid,
        ),
    ).fetchone()

    messages = c.execute(
        """
        SELECT *
        FROM booking_messages
        WHERE booking_id=?
        ORDER BY id
        """,
        (
            bid,
        ),
    ).fetchall()

    proposals = c.execute(
        """
        SELECT *
        FROM booking_proposals
        WHERE booking_id=?
        AND confirmed_at IS NULL
        AND declined_at IS NULL
        ORDER BY id DESC
        """,
        (
            bid,
        ),
    ).fetchall()

    c.close()

    if not booking:
        raise HTTPException(
            404,
            "Booking not found",
        )

    return render(
        "booking.html",
        r,
        booking=booking,
        messages=messages,
        proposals=proposals,
    )


def booking_actor(
    r,
    booking,
):
    producer = auth.current_producer(
        r
    )

    if (
        producer
        and booking
        and producer["id"]
        == booking[
            "producer_id"
        ]
    ):
        return "producer"

    return "client"


@app.post(
    "/booking/{bid}/message"
)
def message(
    r: Request,
    bid: int,
    body: str = Form(...),
):
    c = get_db()

    booking = c.execute(
        """
        SELECT *
        FROM session_bookings
        WHERE id=?
        """,
        (
            bid,
        ),
    ).fetchone()

    if not booking:
        c.close()

        raise HTTPException(
            404,
            "Booking not found",
        )

    body = body.strip()

    if not body:
        c.close()

        raise HTTPException(
            400,
            "Message cannot be empty.",
        )

    role = booking_actor(
        r,
        booking,
    )

    c.execute(
        """
        INSERT INTO booking_messages(
            booking_id,
            sender_role,
            body
        )
        VALUES(?,?,?)
        """,
        (
            bid,
            role,
            body[:2000],
        ),
    )

    c.commit()
    c.close()

    return RedirectResponse(
        "/booking/"
        + str(bid),
        303,
    )


@app.post(
    "/booking/{bid}/propose"
)
def propose(
    r: Request,
    bid: int,
    start_at: str = Form(...),
):
    c = get_db()

    booking = c.execute(
        """
        SELECT *
        FROM session_bookings
        WHERE id=?
        """,
        (
            bid,
        ),
    ).fetchone()

    if not booking:
        c.close()

        raise HTTPException(
            404,
            "Booking not found",
        )

    role = booking_actor(
        r,
        booking,
    )

    start = parse_iso(
        start_at
    )

    if start <= now():
        c.close()

        raise HTTPException(
            400,
            "Proposed time must be in the future.",
        )

    end = start + (
        parse_iso(
            booking["end_at"]
        )
        - parse_iso(
            booking["start_at"]
        )
    )

    if not slot_free(
        c,
        booking[
            "producer_id"
        ],
        start,
        end,
        ignore=bid,
    ):
        c.close()

        raise HTTPException(
            409,
            "That proposed time is unavailable.",
        )

    c.execute(
        """
        INSERT INTO booking_proposals(
            booking_id,
            proposed_start_at,
            proposed_end_at,
            proposed_by
        )
        VALUES(?,?,?,?)
        """,
        (
            bid,
            iso(start),
            iso(end),
            role,
        ),
    )

    c.commit()
    c.close()

    return RedirectResponse(
        "/booking/"
        + str(bid),
        303,
    )


@app.post(
    "/booking/{bid}/proposal/{pid}/confirm"
)
def confirm_proposal(
    r: Request,
    bid: int,
    pid: int,
):
    c = get_db()

    try:

        c.execute(
            "BEGIN IMMEDIATE"
        )

        booking = c.execute(
            """
            SELECT *
            FROM session_bookings
            WHERE id=?
            """,
            (
                bid,
            ),
        ).fetchone()

        proposal = c.execute(
            """
            SELECT *
            FROM booking_proposals
            WHERE id=?
            AND booking_id=?
            AND confirmed_at IS NULL
            AND declined_at IS NULL
            """,
            (
                pid,
                bid,
            ),
        ).fetchone()

        if not booking or not proposal:
            raise HTTPException(
                404,
                "Proposal not found",
            )

        actor = booking_actor(
            r,
            booking,
        )

        if (
            actor
            == proposal[
                "proposed_by"
            ]
        ):
            raise HTTPException(
                403,
                "The other party must confirm this proposal.",
            )

        start = parse_iso(
            proposal[
                "proposed_start_at"
            ]
        )

        end = parse_iso(
            proposal[
                "proposed_end_at"
            ]
        )

        if not slot_free(
            c,
            booking[
                "producer_id"
            ],
            start,
            end,
            ignore=bid,
        ):
            raise HTTPException(
                409,
                "That proposed time is no longer available.",
            )

        c.execute(
            """
            UPDATE session_bookings
            SET
                start_at=?,
                end_at=?,
                status=CASE
                    WHEN status="paid"
                    THEN "confirmed"
                    ELSE status
                END
            WHERE id=?
            """,
            (
                iso(start),
                iso(end),
                bid,
            ),
        )

        c.execute(
            """
            UPDATE booking_proposals
            SET confirmed_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                pid,
            ),
        )

        c.commit()

    except Exception:

        try:
            c.rollback()
        except Exception:
            pass

        raise

    finally:
        c.close()

    return RedirectResponse(
        "/booking/"
        + str(bid),
        303,
    )


@app.get(
    "/booking/{bid}/status"
)
def booking_status(
    bid: int,
):
    c = get_db()

    booking = c.execute(
        """
        SELECT status
        FROM session_bookings
        WHERE id=?
        """,
        (
            bid,
        ),
    ).fetchone()

    c.close()

    if not booking:
        raise HTTPException(
            404,
            "Booking not found",
        )

    return {
        "status":
            booking[
                "status"
            ]
    }


# ============================================================
# SUPER ADMIN HELPERS
# ============================================================

def admin_phone():
    raw = os.getenv(
        "SUPER_ADMIN_PAYOUT_PHONE",
        "",
    ).strip()

    if not raw:
        return ""

    try:
        return mpesa.normalize_phone(
            raw
        )
    except ValueError:
        return ""


# ============================================================
# SUPER ADMIN LOGIN
# ============================================================

@app.get(
    "/super-admin/login"
)
def super_login_page(
    r: Request,
):
    if auth.is_super_admin(r):
        return RedirectResponse(
            "/super-admin",
            303,
        )

    return render_no_store(
        "super_admin_login.html",
        r,
        error=None,
    )


@app.post(
    "/super-admin/login"
)
def super_login(
    r: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    configured_username = os.getenv(
        "SUPER_ADMIN_USERNAME",
        "",
    ).strip()

    configured_password = os.getenv(
        "SUPER_ADMIN_PASSWORD",
        "",
    )

    if (
        not configured_username
        or not configured_password
    ):
        return render_no_store(
            "super_admin_login.html",
            r,
            error=(
                "Super Admin credentials "
                "are not configured on the server."
            ),
        )

    valid = (
        secrets.compare_digest(
            username.strip(),
            configured_username,
        )
        and
        secrets.compare_digest(
            password,
            configured_password,
        )
    )

    if not valid:
        return render_no_store(
            "super_admin_login.html",
            r,
            error="Invalid credentials.",
        )

    r.session.clear()

    r.session[
        "super_admin"
    ] = True

    r.session[
        "role"
    ] = "super_admin"

    r.session[
        "super_admin_login_at"
    ] = datetime.now(
        timezone.utc
    ).isoformat()

    return RedirectResponse(
        "/super-admin",
        303,
    )


# ============================================================
# SUPER ADMIN LOGOUT
# ============================================================

@app.api_route(
    "/super-admin/logout",
    methods=["GET", "POST"],
)
def super_logout(
    r: Request,
):
    r.session.clear()

    response = RedirectResponse(
        "/",
        303,
    )

    response.delete_cookie(
        "session",
        path="/",
    )

    response.delete_cookie(
        "beathub_last_email",
        path="/",
    )

    response.headers[
        "Cache-Control"
    ] = (
        "no-store, no-cache, "
        "must-revalidate, max-age=0, private"
    )

    response.headers[
        "Pragma"
    ] = "no-cache"

    response.headers[
        "Expires"
    ] = "0"

    return response


# ============================================================
# SUPER ADMIN DASHBOARD
# ============================================================

@app.get(
    "/super-admin"
)
def super_admin(
    r: Request,
):
    auth.require_super_admin(
        r
    )

    c = get_db()

    try:

        wallet = c.execute(
            """
            SELECT *
            FROM platform_wallet
            WHERE id=1
            """
        ).fetchone()

        if not wallet:

            c.execute(
                """
                INSERT OR IGNORE INTO platform_wallet(
                    id
                )
                VALUES(1)
                """
            )

            c.commit()

            wallet = c.execute(
                """
                SELECT *
                FROM platform_wallet
                WHERE id=1
                """
            ).fetchone()

        summary = c.execute(
            """
            SELECT
                COALESCE(
                    SUM(gross_amount),
                    0
                ) AS gross_sales,

                COALESCE(
                    SUM(platform_fee),
                    0
                ) AS platform_earnings,

                COALESCE(
                    SUM(producer_credit),
                    0
                ) AS producer_earnings,

                COUNT(*) AS completed_transactions

            FROM platform_ledger
            """
        ).fetchone()

        beat_summary = c.execute(
            """
            SELECT
                COALESCE(
                    SUM(gross_amount),
                    0
                ) AS gross,

                COALESCE(
                    SUM(platform_fee),
                    0
                ) AS fee,

                COUNT(*) AS count

            FROM platform_ledger
            WHERE source_type='beat'
            """
        ).fetchone()

        session_summary = c.execute(
            """
            SELECT
                COALESCE(
                    SUM(gross_amount),
                    0
                ) AS gross,

                COALESCE(
                    SUM(platform_fee),
                    0
                ) AS fee,

                COUNT(*) AS count

            FROM platform_ledger
            WHERE source_type='session'
            """
        ).fetchone()

        recent = c.execute(
            """
            SELECT
                pl.*,

                CASE
                    WHEN pl.source_type='beat'
                    THEN b.title
                    ELSE s.title
                END AS item_title,

                p.name AS producer_name

            FROM platform_ledger pl

            LEFT JOIN orders o
                ON pl.source_type='beat'
                AND pl.source_id=o.id

            LEFT JOIN beats b
                ON o.beat_id=b.id

            LEFT JOIN session_bookings sb
                ON pl.source_type='session'
                AND pl.source_id=sb.id

            LEFT JOIN session_services s
                ON sb.service_id=s.id

            LEFT JOIN producers p
                ON p.id=CASE
                    WHEN pl.source_type='beat'
                    THEN b.producer_id
                    ELSE sb.producer_id
                END

            ORDER BY pl.created_at DESC
            LIMIT 100
            """
        ).fetchall()

        withdrawals = c.execute(
            """
            SELECT *
            FROM platform_withdrawals
            ORDER BY requested_at DESC
            LIMIT 50
            """
        ).fetchall()

        pending_count = c.execute(
            """
            SELECT COUNT(*) AS count
            FROM platform_withdrawals
            WHERE status='pending'
            """
        ).fetchone()["count"]

        totals = {
            "gross_sales":
                summary[
                    "gross_sales"
                ],

            "platform_earnings":
                summary[
                    "platform_earnings"
                ],

            "producer_earnings":
                summary[
                    "producer_earnings"
                ],

            "completed_transactions":
                summary[
                    "completed_transactions"
                ],

            "available_balance":
                wallet[
                    "available_balance"
                ],

            "pending_withdrawal":
                wallet[
                    "pending_withdrawal"
                ],

            "total_withdrawn":
                wallet[
                    "total_withdrawn"
                ],

            "pending_withdrawals_count":
                pending_count,

            "beat_gross":
                beat_summary[
                    "gross"
                ],

            "beat_fee":
                beat_summary[
                    "fee"
                ],

            "beat_count":
                beat_summary[
                    "count"
                ],

            "session_gross":
                session_summary[
                    "gross"
                ],

            "session_fee":
                session_summary[
                    "fee"
                ],

            "session_count":
                session_summary[
                    "count"
                ],

            "commission_rate":
                FEE_RATE,
        }

    finally:
        c.close()

    return render(
        "super_admin.html",
        r,
        wallet=wallet,
        totals=totals,
        recent=recent,
        withdrawals=withdrawals,
        payout_phone=admin_phone(),
    )


# ============================================================
# SUPER ADMIN WITHDRAWAL
# ============================================================

@app.post(
    "/super-admin/withdraw"
)
def super_withdraw(
    r: Request,
    amount: int = Form(...),
):
    auth.require_super_admin(
        r
    )

    if amount < 10:
        raise HTTPException(
            400,
            "Minimum withdrawal amount is 10.",
        )

    phone = admin_phone()

    if not phone:
        raise HTTPException(
            400,
            "Configure a valid Super Admin payout number first.",
        )

    c = get_db()

    try:

        c.execute(
            "BEGIN IMMEDIATE"
        )

        wallet = c.execute(
            """
            SELECT available_balance
            FROM platform_wallet
            WHERE id=1
            """
        ).fetchone()

        if not wallet:
            raise HTTPException(
                500,
                "Platform wallet is not available.",
            )

        if (
            wallet[
                "available_balance"
            ]
            < amount
        ):
            raise HTTPException(
                400,
                "Insufficient available platform balance.",
            )

        withdrawal_id = c.execute(
            """
            INSERT INTO platform_withdrawals(
                amount,
                phone,
                status
            )
            VALUES(
                ?,
                ?,
                'pending'
            )
            """,
            (
                amount,
                phone,
            ),
        ).lastrowid

        c.execute(
            """
            UPDATE platform_wallet
            SET
                available_balance=
                    available_balance-?,
                pending_withdrawal=
                    pending_withdrawal+?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=1
            """,
            (
                amount,
                amount,
            ),
        )

        c.commit()

    except Exception:

        try:
            c.rollback()
        except Exception:
            pass

        raise

    finally:
        c.close()

    try:

        result = (
            mpesa.initiate_platform_payout(
                phone,
                amount,
                f"ADMINWD{withdrawal_id}",
            )
        )

    except Exception as exc:

        c = get_db()

        try:

            c.execute(
                "BEGIN IMMEDIATE"
            )

            c.execute(
                """
                UPDATE platform_withdrawals
                SET
                    status='failed',
                    failure_reason=?
                WHERE id=?
                """,
                (
                    str(exc)[:500],
                    withdrawal_id,
                ),
            )

            c.execute(
                """
                UPDATE platform_wallet
                SET
                    available_balance=
                        available_balance+?,
                    pending_withdrawal=
                        pending_withdrawal-?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=1
                """,
                (
                    amount,
                    amount,
                ),
            )

            c.commit()

        finally:
            c.close()

        raise HTTPException(
            502,
            "The payout provider could not process the withdrawal.",
        )

    if result.get(
        "simulated"
    ):

        c = get_db()

        try:

            c.execute(
                "BEGIN IMMEDIATE"
            )

            c.execute(
                """
                UPDATE platform_withdrawals
                SET
                    status='completed',
                    payout_reference=?,
                    completed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    result[
                        "reference"
                    ],
                    withdrawal_id,
                ),
            )

            c.execute(
                """
                UPDATE platform_wallet
                SET
                    pending_withdrawal=
                        pending_withdrawal-?,
                    total_withdrawn=
                        total_withdrawn+?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=1
                """,
                (
                    amount,
                    amount,
                ),
            )

            c.commit()

        finally:
            c.close()

    return RedirectResponse(
        "/super-admin",
        303,
    )


# ============================================================
# SECURE BEAT DOWNLOAD
# ============================================================

@app.get(
    "/download/{token}"
)
def download(
    token: str,
):
    c = get_db()

    record = c.execute(
        """
        SELECT
            o.status,
            b.audio_path
        FROM orders o
        JOIN beats b
            ON b.id=o.beat_id
        WHERE o.download_token=?
        """,
        (
            token,
        ),
    ).fetchone()

    c.close()

    if (
        not record
        or record["status"]
        != "completed"
    ):
        raise HTTPException(
            403,
            "Invalid download link.",
        )

    file_path = (
        BASE
        / record[
            "audio_path"
        ].lstrip("/")
    ).resolve()

    audio_root = AUDIO.resolve()

    if (
        not file_path.is_file()
        or audio_root not in file_path.parents
    ):
        raise HTTPException(
            404,
            "File unavailable.",
        )

    return FileResponse(
        file_path,
        filename=file_path.name,
    )


# ============================================================
# M-PESA CALLBACK
# ============================================================

@app.post(
    "/mpesa/callback"
)
async def callback(
    r: Request,
):
    """
    Keep the callback endpoint available
    for the existing Safaricom integration.
    """

    return {
        "ResultCode": 0,
        "ResultDesc":
            "Live Safaricom callback integration pending.",
    }
