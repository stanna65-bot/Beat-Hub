import os
import secrets
import threading
import time
import uuid
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import auth
import mpesa

from database import get_db, init_db, unique_slug


# =============================================================================
# PATHS
# =============================================================================

BASE_DIR = Path(__file__).resolve().parent

STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

UPLOAD_DIR = STATIC_DIR / "uploads"
COVER_DIR = UPLOAD_DIR / "covers"
AUDIO_DIR = UPLOAD_DIR / "audio"

for directory in (
    STATIC_DIR,
    TEMPLATES_DIR,
    UPLOAD_DIR,
    COVER_DIR,
    AUDIO_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)


# =============================================================================
# PLATFORM SETTINGS
# =============================================================================

# Internal business rule.
# This is applied automatically when a sale is completed.
# The producer dashboard shows the producer's net earnings.
PLATFORM_COMMISSION_RATE = 10.0

MIN_PRICE = 1
MAX_PRICE = 10_000_000

MIN_BPM = 20
MAX_BPM = 400

MIN_WITHDRAWAL = 10

MAX_COVER_SIZE = 10 * 1024 * 1024
MAX_AUDIO_SIZE = 100 * 1024 * 1024

ALLOWED_COVER_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}

ALLOWED_AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
}


# =============================================================================
# APP
# =============================================================================

app = FastAPI(
    title="Beat Hub",
    version="1.0.0",
)


# =============================================================================
# SESSION
# =============================================================================

SESSION_SECRET = os.getenv(
    "SESSION_SECRET",
    "",
).strip()

if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(48)

    print(
        "WARNING: SESSION_SECRET is not set. "
        "A temporary secret is being used. "
        "Set SESSION_SECRET in Render environment variables."
    )

SESSION_HTTPS_ONLY = (
    os.getenv(
        "SESSION_HTTPS_ONLY",
        "true",
    )
    .strip()
    .lower()
    == "true"
)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
)


# =============================================================================
# STATIC FILES / TEMPLATES
# =============================================================================

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)

templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR),
)


# =============================================================================
# DATABASE INITIALIZATION
# =============================================================================

init_db()


# =============================================================================
# GENERAL HELPERS
# =============================================================================

def clean_text(value, max_length):
    value = (value or "").strip()

    if len(value) > max_length:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Maximum allowed length is "
                f"{max_length} characters."
            ),
        )

    return value


def validate_price(price):
    if price is None:
        raise HTTPException(
            status_code=400,
            detail="Price is required.",
        )

    if price < MIN_PRICE or price > MAX_PRICE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Price must be between "
                f"KES {MIN_PRICE:,} and "
                f"KES {MAX_PRICE:,}."
            ),
        )


def validate_bpm(bpm):
    if bpm is None:
        return

    if bpm < MIN_BPM or bpm > MAX_BPM:
        raise HTTPException(
            status_code=400,
            detail=(
                f"BPM must be between "
                f"{MIN_BPM} and {MAX_BPM}."
            ),
        )


# =============================================================================
# FILE UPLOAD HELPERS
# =============================================================================

def safe_extension(filename, allowed_extensions):
    if not filename:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file has no filename.",
        )

    extension = Path(filename).suffix.lower()

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="File type is not allowed.",
        )

    return extension


def save_upload(
    upload,
    destination_dir,
    public_prefix,
    allowed_extensions,
    max_size,
):
    if upload is None or not upload.filename:
        raise HTTPException(
            status_code=400,
            detail="A file is required.",
        )

    extension = safe_extension(
        upload.filename,
        allowed_extensions,
    )

    filename = f"{uuid.uuid4().hex}{extension}"

    destination_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination = (
        destination_dir / filename
    ).resolve()

    destination_root = destination_dir.resolve()

    if destination.parent != destination_root:
        raise HTTPException(
            status_code=400,
            detail="Invalid upload path.",
        )

    total_written = 0

    try:
        with destination.open("wb") as output:
            while True:
                chunk = upload.file.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                total_written += len(chunk)

                if total_written > max_size:
                    raise HTTPException(
                        status_code=413,
                        detail="Uploaded file is too large.",
                    )

                output.write(chunk)

    except HTTPException:
        destination.unlink(missing_ok=True)
        raise

    except Exception as exc:
        destination.unlink(missing_ok=True)

        raise HTTPException(
            status_code=500,
            detail="Could not save uploaded file.",
        ) from exc

    finally:
        try:
            upload.file.close()
        except Exception:
            pass

    return f"{public_prefix}/{filename}"


def delete_uploaded_path(path_value):
    if not path_value:
        return

    relative = path_value.lstrip("/")

    allowed_prefixes = (
        "static/uploads/covers/",
        "static/uploads/audio/",
    )

    if not relative.startswith(allowed_prefixes):
        return

    candidate = (
        BASE_DIR / relative
    ).resolve()

    uploads_root = UPLOAD_DIR.resolve()

    if uploads_root in candidate.parents:
        candidate.unlink(missing_ok=True)


def get_safe_audio_file(path_value):
    if not path_value:
        raise HTTPException(
            status_code=404,
            detail="Audio file not found.",
        )

    relative = path_value.lstrip("/")

    if not relative.startswith(
        "static/uploads/audio/"
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid audio path.",
        )

    candidate = (
        BASE_DIR / relative
    ).resolve()

    audio_root = AUDIO_DIR.resolve()

    if audio_root not in candidate.parents:
        raise HTTPException(
            status_code=403,
            detail="Invalid audio path.",
        )

    if not candidate.is_file():
        raise HTTPException(
            status_code=404,
            detail="Audio file is unavailable.",
        )

    return candidate


# =============================================================================
# WALLET HELPERS
# =============================================================================

def ensure_wallet(conn, producer_id):
    conn.execute(
        """
        INSERT OR IGNORE INTO producer_wallets (
            producer_id
        )
        VALUES (?)
        """,
        (producer_id,),
    )


def get_wallet(conn, producer_id):
    ensure_wallet(conn, producer_id)

    return conn.execute(
        """
        SELECT *
        FROM producer_wallets
        WHERE producer_id = ?
        """,
        (producer_id,),
    ).fetchone()


# =============================================================================
# HEALTH CHECK
# =============================================================================

@app.api_route(
    "/health",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
def health():
    return Response(
        content="OK",
        status_code=200,
        media_type="text/plain",
    )


@app.api_route(
    "/",
    methods=["HEAD"],
    include_in_schema=False,
)
def root_head():
    return Response(
        status_code=200,
    )


# =============================================================================
# HOME
# =============================================================================

@app.get("/")
def home(request: Request):

    producer = auth.current_producer(request)

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "producer": producer,
        },
    )


# =============================================================================
# SIGNUP
# =============================================================================

@app.get("/signup")
def signup_page(request: Request):

    if auth.current_producer(request):
        return RedirectResponse(
            "/admin",
            status_code=303,
        )

    return templates.TemplateResponse(
        "signup.html",
        {
            "request": request,
            "error": None,
        },
    )


@app.post("/signup")
def signup(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    name = clean_text(name, 100)
    email = (email or "").strip().lower()

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Name is required.",
        )

    if (
        not email
        or len(email) > 254
        or "@" not in email
    ):
        raise HTTPException(
            status_code=400,
            detail="Enter a valid email address.",
        )

    if len(password or "") < 8:
        raise HTTPException(
            status_code=400,
            detail=(
                "Password must be at least "
                "8 characters."
            ),
        )

    conn = get_db()

    try:
        existing = conn.execute(
            """
            SELECT id
            FROM producers
            WHERE email = ?
            """,
            (email,),
        ).fetchone()

        if existing:
            raise HTTPException(
                status_code=409,
                detail=(
                    "An account with this email "
                    "already exists."
                ),
            )

        slug = unique_slug(conn, name)

        cursor = conn.execute(
            """
            INSERT INTO producers (
                slug,
                email,
                password_hash,
                name
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                slug,
                email,
                auth.hash_password(password),
                name,
            ),
        )

        producer_id = cursor.lastrowid

        ensure_wallet(
            conn,
            producer_id,
        )

        conn.commit()

    finally:
        conn.close()

    request.session.clear()
    request.session["producer_id"] = producer_id

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# =============================================================================
# LOGIN / LOGOUT
# =============================================================================

@app.get("/login")
def login_page(request: Request):

    if auth.current_producer(request):
        return RedirectResponse(
            "/admin",
            status_code=303,
        )

    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": None,
        },
    )


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    email = (email or "").strip().lower()

    conn = get_db()

    try:
        producer = conn.execute(
            """
            SELECT *
            FROM producers
            WHERE email = ?
            """,
            (email,),
        ).fetchone()

    finally:
        conn.close()

    if (
        not producer
        or not auth.verify_password(
            password,
            producer["password_hash"],
        )
    ):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": (
                    "Incorrect email or password."
                ),
            },
            status_code=401,
        )

    request.session.clear()
    request.session["producer_id"] = producer["id"]

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


@app.post("/logout")
def logout(request: Request):
    request.session.clear()

    return RedirectResponse(
        "/",
        status_code=303,
    )


# =============================================================================
# PRODUCER PUBLIC PAGE
# =============================================================================

@app.get("/p/{slug}")
def producer_page(
    request: Request,
    slug: str,
):
    conn = get_db()

    try:
        producer = conn.execute(
            """
            SELECT *
            FROM producers
            WHERE slug = ?
            """,
            (slug,),
        ).fetchone()

        if not producer:
            raise HTTPException(
                status_code=404,
                detail="Producer not found.",
            )

        beats = conn.execute(
            """
            SELECT *
            FROM beats
            WHERE producer_id = ?
            ORDER BY
                is_hot_pick DESC,
                created_at DESC,
                id DESC
            """,
            (producer["id"],),
        ).fetchall()

    finally:
        conn.close()

    return templates.TemplateResponse(
        "feed.html",
        {
            "request": request,
            "producer": producer,
            "beats": beats,
        },
    )


@app.get("/p/{slug}/beat/{beat_id}")
def beat_detail(
    request: Request,
    slug: str,
    beat_id: int,
):
    conn = get_db()

    try:
        producer = conn.execute(
            """
            SELECT *
            FROM producers
            WHERE slug = ?
            """,
            (slug,),
        ).fetchone()

        if not producer:
            raise HTTPException(
                status_code=404,
                detail="Producer not found.",
            )

        beat = conn.execute(
            """
            SELECT *
            FROM beats
            WHERE
                id = ?
                AND producer_id = ?
            """,
            (
                beat_id,
                producer["id"],
            ),
        ).fetchone()

    finally:
        conn.close()

    if not beat:
        raise HTTPException(
            status_code=404,
            detail="Beat not found.",
        )

    return templates.TemplateResponse(
        "beat.html",
        {
            "request": request,
            "producer": producer,
            "beat": beat,
        },
    )


# =============================================================================
# ADMIN DASHBOARD
# =============================================================================

@app.get("/admin")
def admin_page(
    request: Request,
    producer=Depends(auth.require_producer),
):
    conn = get_db()

    try:
        ensure_wallet(conn, producer["id"])

        wallet = get_wallet(
            conn,
            producer["id"],
        )

        beats = conn.execute(
            """
            SELECT *
            FROM beats
            WHERE producer_id = ?
            ORDER BY
                is_hot_pick DESC,
                created_at DESC,
                id DESC
            """,
            (producer["id"],),
        ).fetchall()

        orders = conn.execute(
            """
            SELECT
                orders.id,
                orders.status,
                orders.amount,
                orders.producer_payout,
                orders.created_at,
                orders.completed_at,
                beats.title AS beat_title

            FROM orders

            JOIN beats
                ON beats.id = orders.beat_id

            WHERE beats.producer_id = ?

            ORDER BY
                orders.created_at DESC,
                orders.id DESC

            LIMIT 50
            """,
            (producer["id"],),
        ).fetchall()

        withdrawals = conn.execute(
            """
            SELECT *
            FROM withdrawals
            WHERE producer_id = ?
            ORDER BY
                requested_at DESC,
                id DESC
            LIMIT 20
            """,
            (producer["id"],),
        ).fetchall()

        sales_row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_sales
            FROM orders

            JOIN beats
                ON beats.id = orders.beat_id

            WHERE
                beats.producer_id = ?
                AND orders.status = 'completed'
            """,
            (producer["id"],),
        ).fetchone()

        earnings_row = conn.execute(
            """
            SELECT
                COALESCE(
                    SUM(producer_payout),
                    0
                ) AS total_earnings

            FROM orders

            JOIN beats
                ON beats.id = orders.beat_id

            WHERE
                beats.producer_id = ?
                AND orders.status = 'completed'
            """,
            (producer["id"],),
        ).fetchone()

        # IMPORTANT:
        # totals is required by your existing admin.html.
        totals = {
            "total_sales": int(
                sales_row["total_sales"] or 0
            ),

            "total_earnings": int(
                earnings_row["total_earnings"] or 0
            ),

            "available_balance": int(
                wallet["available_balance"] or 0
            ),

            "pending_withdrawal": int(
                wallet["pending_withdrawal"] or 0
            ),

            "total_withdrawn": int(
                wallet["total_withdrawn"] or 0
            ),
        }

        conn.commit()

    finally:
        conn.close()

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "producer": producer,
            "wallet": wallet,
            "totals": totals,
            "beats": beats,
            "orders": orders,
            "withdrawals": withdrawals,
        },
    )


# =============================================================================
# UPDATE PROFILE
# =============================================================================

@app.post("/admin/profile")
def update_profile(
    name: str = Form(...),
    bio: str = Form(""),
    phone: str = Form(""),
    payout_phone: str = Form(""),
    producer=Depends(auth.require_producer),
):
    name = clean_text(name, 100)
    bio = clean_text(bio, 2000)
    phone = clean_text(phone, 30)
    payout_phone = clean_text(
        payout_phone,
        30,
    )

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Name is required.",
        )

    if payout_phone:
        try:
            payout_phone = mpesa.normalize_phone(
                payout_phone
            )
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    conn = get_db()

    try:
        conn.execute(
            """
            UPDATE producers
            SET
                name = ?,
                bio = ?,
                phone = ?,
                payout_phone = ?
            WHERE id = ?
            """,
            (
                name,
                bio,
                phone,
                payout_phone,
                producer["id"],
            ),
        )

        conn.commit()

    finally:
        conn.close()

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# =============================================================================
# UPLOAD BEAT
# =============================================================================

@app.post("/admin/beat")
def upload_beat(
    title: str = Form(...),
    genre: str = Form(""),
    bpm: int = Form(None),
    price: int = Form(...),
    is_hot_pick: str = Form("0"),
    cover: UploadFile = File(...),
    audio: UploadFile = File(...),
    producer=Depends(auth.require_producer),
):
    title = clean_text(title, 200)
    genre = clean_text(genre, 100)

    if not title:
        raise HTTPException(
            status_code=400,
            detail="Beat title is required.",
        )

    validate_price(price)
    validate_bpm(bpm)

    hot_pick = (
        str(is_hot_pick).strip().lower()
        in {"1", "true", "yes", "on"}
    )

    cover_path = None
    audio_path = None

    try:
        cover_path = save_upload(
            cover,
            COVER_DIR,
            "/static/uploads/covers",
            ALLOWED_COVER_EXTENSIONS,
            MAX_COVER_SIZE,
        )

        audio_path = save_upload(
            audio,
            AUDIO_DIR,
            "/static/uploads/audio",
            ALLOWED_AUDIO_EXTENSIONS,
            MAX_AUDIO_SIZE,
        )

        conn = get_db()

        try:
            conn.execute(
                """
                INSERT INTO beats (
                    producer_id,
                    title,
                    genre,
                    bpm,
                    price,
                    cover_path,
                    audio_path,
                    is_hot_pick
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    producer["id"],
                    title,
                    genre,
                    bpm,
                    price,
                    cover_path,
                    audio_path,
                    int(hot_pick),
                ),
            )

            conn.commit()

        finally:
            conn.close()

    except Exception:
        if cover_path:
            delete_uploaded_path(cover_path)

        if audio_path:
            delete_uploaded_path(audio_path)

        raise

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# =============================================================================
# HOT PICK
# =============================================================================

@app.post("/admin/beat/{beat_id}/hot-pick")
def toggle_hot_pick(
    beat_id: int,
    is_hot_pick: str = Form("0"),
    producer=Depends(auth.require_producer),
):
    hot_pick = (
        str(is_hot_pick).strip().lower()
        in {"1", "true", "yes", "on"}
    )

    conn = get_db()

    try:
        result = conn.execute(
            """
            UPDATE beats
            SET is_hot_pick = ?
            WHERE
                id = ?
                AND producer_id = ?
            """,
            (
                int(hot_pick),
                beat_id,
                producer["id"],
            ),
        )

        conn.commit()

    finally:
        conn.close()

    if result.rowcount != 1:
        raise HTTPException(
            status_code=404,
            detail="Beat not found.",
        )

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# =============================================================================
# DELETE BEAT
# =============================================================================

@app.post("/admin/beat/{beat_id}/delete")
def delete_beat(
    beat_id: int,
    producer=Depends(auth.require_producer),
):
    conn = get_db()

    try:
        beat = conn.execute(
            """
            SELECT *
            FROM beats
            WHERE
                id = ?
                AND producer_id = ?
            """,
            (
                beat_id,
                producer["id"],
            ),
        ).fetchone()

        if not beat:
            raise HTTPException(
                status_code=404,
                detail="Beat not found.",
            )

        purchase_exists = conn.execute(
            """
            SELECT 1
            FROM orders
            WHERE beat_id = ?
            LIMIT 1
            """,
            (beat_id,),
        ).fetchone()

        if purchase_exists:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This beat has order records "
                    "and cannot be deleted."
                ),
            )

        conn.execute(
            """
            DELETE FROM beats
            WHERE id = ?
            """,
            (beat_id,),
        )

        conn.commit()

    finally:
        conn.close()

    delete_uploaded_path(
        beat["cover_path"]
    )

    delete_uploaded_path(
        beat["audio_path"]
    )

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# =============================================================================
# PAYMENT SPLIT
# =============================================================================

def apply_sale_once(
    conn,
    order_id,
):
    order = conn.execute(
        """
        SELECT
            orders.*,
            beats.title AS beat_title,
            beats.producer_id,

            producers.name AS producer_name,
            producers.email AS producer_email,
            producers.phone AS producer_phone

        FROM orders

        JOIN beats
            ON beats.id = orders.beat_id

        JOIN producers
            ON producers.id = beats.producer_id

        WHERE orders.id = ?
        """,
        (order_id,),
    ).fetchone()

    if not order:
        return None

    if order["status"] != "completed":
        return None

    if order["split_applied_at"] is not None:
        return None

    gross_amount = int(order["amount"])

    platform_fee = round(
        gross_amount
        * PLATFORM_COMMISSION_RATE
        / 100
    )

    producer_payout = (
        gross_amount
        - platform_fee
    )

    download_token = secrets.token_urlsafe(32)

    locked = conn.execute(
        """
        UPDATE orders
        SET
            platform_fee = ?,
            producer_payout = ?,
            commission_rate_locked = ?,
            download_token = ?,
            split_applied_at = datetime('now')
        WHERE
            id = ?
            AND status = 'completed'
            AND split_applied_at IS NULL
        """,
        (
            platform_fee,
            producer_payout,
            PLATFORM_COMMISSION_RATE,
            download_token,
            order_id,
        ),
    )

    if locked.rowcount != 1:
        return None

    producer_id = order["producer_id"]

    ensure_wallet(
        conn,
        producer_id,
    )

    conn.execute(
        """
        UPDATE producer_wallets
        SET
            available_balance =
                available_balance + ?,
            total_earnings =
                total_earnings + ?,
            updated_at = datetime('now')
        WHERE producer_id = ?
        """,
        (
            producer_payout,
            producer_payout,
            producer_id,
        ),
    )

    conn.execute(
        """
        INSERT INTO wallet_transactions (
            producer_id,
            order_id,
            transaction_type,
            amount,
            reference
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            producer_id,
            order_id,
            "sale_credit",
            producer_payout,
            f"ORDER-{order_id}",
        ),
    )

    conn.execute(
        """
        INSERT OR IGNORE INTO platform_ledger (
            order_id,
            gross_amount,
            platform_fee,
            producer_credit
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            order_id,
            gross_amount,
            platform_fee,
            producer_payout,
        ),
    )

    return {
        "producer_id": producer_id,
        "producer_name": order["producer_name"],
        "producer_email": order["producer_email"],
        "producer_phone": order["producer_phone"],
        "beat_title": order["beat_title"],
        "gross_amount": gross_amount,
        "platform_fee": platform_fee,
        "producer_payout": producer_payout,
    }


def complete_order(
    conn,
    order_id,
    receipt=None,
):
    conn.execute(
        """
        UPDATE orders
        SET
            status = 'completed',
            mpesa_receipt =
                COALESCE(?, mpesa_receipt),
            completed_at =
                COALESCE(
                    completed_at,
                    datetime('now')
                ),
            failure_reason = NULL
        WHERE
            id = ?
            AND status IN ('pending', 'completed')
        """,
        (
            receipt,
            order_id,
        ),
    )

    return apply_sale_once(
        conn,
        order_id,
    )


def fail_order(
    conn,
    order_id,
    reason,
):
    conn.execute(
        """
        UPDATE orders
        SET
            status = 'failed',
            failure_reason = ?
        WHERE
            id = ?
            AND status = 'pending'
        """,
        (
            (reason or "")[:500],
            order_id,
        ),
    )


# =============================================================================
# CHECKOUT
# =============================================================================

@app.post("/checkout/{beat_id}")
def checkout(
    beat_id: int,
    phone: str = Form(...),
):
    try:
        normalized_phone = mpesa.normalize_phone(phone)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    conn = get_db()

    try:
        beat = conn.execute(
            """
            SELECT *
            FROM beats
            WHERE id = ?
            """,
            (beat_id,),
        ).fetchone()

        if not beat:
            raise HTTPException(
                status_code=404,
                detail="Beat not found.",
            )

        amount = int(beat["price"])
        validate_price(amount)

        cursor = conn.execute(
            """
            INSERT INTO orders (
                beat_id,
                buyer_phone,
                amount,
                status
            )
            VALUES (?, ?, ?, 'pending')
            """,
            (
                beat_id,
                normalized_phone,
                amount,
            ),
        )

        order_id = cursor.lastrowid
        conn.commit()

    finally:
        conn.close()

    try:
        result = mpesa.stk_push(
            phone=normalized_phone,
            amount=amount,
            account_ref=f"BEAT{beat_id}",
            description=beat["title"],
        )
    except Exception as exc:
        conn = get_db()

        try:
            fail_order(
                conn,
                order_id,
                str(exc),
            )
            conn.commit()
        finally:
            conn.close()

        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    checkout_request_id = result.get(
        "checkout_request_id"
    )

    if not checkout_request_id:
        conn = get_db()

        try:
            fail_order(
                conn,
                order_id,
                "No checkout request ID returned.",
            )
            conn.commit()
        finally:
            conn.close()

        raise HTTPException(
            status_code=502,
            detail=(
                "M-Pesa STK Push could not "
                "be initiated."
            ),
        )

    conn = get_db()

    try:
        conn.execute(
            """
            UPDATE orders
            SET checkout_request_id = ?
            WHERE
                id = ?
                AND status = 'pending'
            """,
            (
                checkout_request_id,
                order_id,
            ),
        )

        conn.commit()

    finally:
        conn.close()

    if result.get("simulated"):
        threading.Thread(
            target=simulate_confirm,
            args=(order_id,),
            daemon=True,
        ).start()

    return JSONResponse(
        {
            "order_id": order_id,
            "status": "pending",
        }
    )


# =============================================================================
# SIMULATED PAYMENT
# =============================================================================

def simulate_confirm(order_id):
    time.sleep(3)

    conn = get_db()

    try:
        complete_order(
            conn,
            order_id,
            f"SIM-{order_id}",
        )

        conn.commit()

    finally:
        conn.close()


# =============================================================================
# PAYMENT STATUS
# =============================================================================

@app.get("/order/{order_id}/status")
def order_status(order_id: int):
    conn = get_db()

    try:
        order = conn.execute(
            """
            SELECT
                id,
                status,
                download_token
            FROM orders
            WHERE id = ?
            """,
            (order_id,),
        ).fetchone()

    finally:
        conn.close()

    if not order:
        raise HTTPException(
            status_code=404,
            detail="Order not found.",
        )

    result = {
        "order_id": order["id"],
        "status": order["status"],
    }

    if (
        order["status"] == "completed"
        and order["download_token"]
    ):
        result["download_token"] = (
            order["download_token"]
        )

    return result


# =============================================================================
# M-PESA CALLBACK
# =============================================================================

@app.post("/mpesa/callback")
async def mpesa_callback(request: Request):
    try:
        payload = await request.json()

        callback = payload["Body"]["stkCallback"]

        checkout_id = callback[
            "CheckoutRequestID"
        ]

        result_code = int(
            callback["ResultCode"]
        )

        result_desc = str(
            callback.get(
                "ResultDesc",
                "",
            )
        )

    except Exception:
        return {
            "ResultCode": 0,
            "ResultDesc": "Accepted",
        }

    receipt = None

    for item in (
        callback.get(
            "CallbackMetadata",
            {},
        ).get(
            "Item",
            [],
        )
    ):
        if (
            item.get("Name")
            == "MpesaReceiptNumber"
        ):
            receipt = item.get("Value")
            break

    conn = get_db()

    try:
        order = conn.execute(
            """
            SELECT id
            FROM orders
            WHERE checkout_request_id = ?
            """,
            (checkout_id,),
        ).fetchone()

        if not order:
            conn.commit()

            return {
                "ResultCode": 0,
                "ResultDesc": "Accepted",
            }

        if result_code == 0:
            complete_order(
                conn,
                order["id"],
                receipt,
            )
        else:
            fail_order(
                conn,
                order["id"],
                result_desc,
            )

        conn.commit()

    finally:
        conn.close()

    return {
        "ResultCode": 0,
        "ResultDesc": "Accepted",
    }


# =============================================================================
# REQUEST WITHDRAWAL
# =============================================================================

@app.post("/admin/withdraw")
def request_withdrawal(
    amount: int = Form(...),
    producer=Depends(auth.require_producer),
):
    if amount < MIN_WITHDRAWAL:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Minimum withdrawal is "
                f"KES {MIN_WITHDRAWAL:,}."
            ),
        )

    conn = get_db()

    try:
        producer_row = conn.execute(
            """
            SELECT payout_phone
            FROM producers
            WHERE id = ?
            """,
            (producer["id"],),
        ).fetchone()

        payout_phone = (
            producer_row["payout_phone"]
            if producer_row
            else ""
        )

        if not payout_phone:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Add your M-Pesa payout phone "
                    "number first."
                ),
            )

        conn.execute("BEGIN IMMEDIATE")

        wallet = get_wallet(
            conn,
            producer["id"],
        )

        if (
            not wallet
            or int(wallet["available_balance"])
            < amount
        ):
            conn.rollback()

            raise HTTPException(
                status_code=400,
                detail="Insufficient balance.",
            )

        updated = conn.execute(
            """
            UPDATE producer_wallets
            SET
                available_balance =
                    available_balance - ?,
                pending_withdrawal =
                    pending_withdrawal + ?,
                updated_at = datetime('now')
            WHERE
                producer_id = ?
                AND available_balance >= ?
            """,
            (
                amount,
                amount,
                producer["id"],
                amount,
            ),
        )

        if updated.rowcount != 1:
            conn.rollback()

            raise HTTPException(
                status_code=409,
                detail=(
                    "Balance changed. "
                    "Please try again."
                ),
            )

        cursor = conn.execute(
            """
            INSERT INTO withdrawals (
                producer_id,
                amount,
                phone,
                status
            )
            VALUES (?, ?, ?, 'processing')
            """,
            (
                producer["id"],
                amount,
                payout_phone,
            ),
        )

        withdrawal_id = cursor.lastrowid

        conn.execute(
            """
            INSERT INTO wallet_transactions (
                producer_id,
                withdrawal_id,
                transaction_type,
                amount,
                reference
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                producer["id"],
                withdrawal_id,
                "withdrawal_requested",
                -amount,
                f"WITHDRAWAL-{withdrawal_id}",
            ),
        )

        conn.commit()

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

        raise

    finally:
        conn.close()

    try:
        result = mpesa.initiate_producer_payout(
            phone=payout_phone,
            amount=amount,
            reference=f"WD{withdrawal_id}",
        )

    except Exception as exc:
        return_failed_withdrawal(
            withdrawal_id,
            producer["id"],
            amount,
            str(exc),
        )

        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    if result.get("simulated"):
        complete_withdrawal(
            withdrawal_id,
            producer["id"],
            amount,
            result.get("reference"),
        )

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# =============================================================================
# COMPLETE WITHDRAWAL
# =============================================================================

def complete_withdrawal(
    withdrawal_id,
    producer_id,
    amount,
    reference=None,
):
    conn = get_db()

    try:
        conn.execute("BEGIN IMMEDIATE")

        updated = conn.execute(
            """
            UPDATE withdrawals
            SET
                status = 'completed',
                payout_reference = ?,
                completed_at = datetime('now')
            WHERE
                id = ?
                AND status = 'processing'
            """,
            (
                reference,
                withdrawal_id,
            ),
        )

        if updated.rowcount != 1:
            conn.rollback()
            return False

        conn.execute(
            """
            UPDATE producer_wallets
            SET
                pending_withdrawal =
                    CASE
                        WHEN pending_withdrawal >= ?
                        THEN pending_withdrawal - ?
                        ELSE 0
                    END,
                total_withdrawn =
                    total_withdrawn + ?,
                updated_at = datetime('now')
            WHERE producer_id = ?
            """,
            (
                amount,
                amount,
                amount,
                producer_id,
            ),
        )

        conn.execute(
            """
            INSERT INTO wallet_transactions (
                producer_id,
                withdrawal_id,
                transaction_type,
                amount,
                reference
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                producer_id,
                withdrawal_id,
                "withdrawal_completed",
                amount,
                reference,
            ),
        )

        conn.commit()
        return True

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


# =============================================================================
# RETURN FAILED WITHDRAWAL MONEY
# =============================================================================

def return_failed_withdrawal(
    withdrawal_id,
    producer_id,
    amount,
    reason,
):
    conn = get_db()

    try:
        conn.execute("BEGIN IMMEDIATE")

        updated = conn.execute(
            """
            UPDATE withdrawals
            SET
                status = 'failed',
                failure_reason = ?
            WHERE
                id = ?
                AND status = 'processing'
            """,
            (
                (reason or "")[:500],
                withdrawal_id,
            ),
        )

        if updated.rowcount != 1:
            conn.rollback()
            return False

        conn.execute(
            """
            UPDATE producer_wallets
            SET
                available_balance =
                    available_balance + ?,
                pending_withdrawal =
                    CASE
                        WHEN pending_withdrawal >= ?
                        THEN pending_withdrawal - ?
                        ELSE 0
                    END,
                updated_at = datetime('now')
            WHERE producer_id = ?
            """,
            (
                amount,
                amount,
                amount,
                producer_id,
            ),
        )

        conn.execute(
            """
            INSERT INTO wallet_transactions (
                producer_id,
                withdrawal_id,
                transaction_type,
                amount,
                reference
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                producer_id,
                withdrawal_id,
                "withdrawal_failed_return",
                amount,
                f"WITHDRAWAL-{withdrawal_id}",
            ),
        )

        conn.commit()
        return True

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


# =============================================================================
# PROTECTED DOWNLOAD
# =============================================================================

@app.get("/download/{token}")
def download(token: str):
    if not token or len(token) < 20:
        raise HTTPException(
            status_code=403,
            detail="Invalid download link.",
        )

    conn = get_db()

    try:
        row = conn.execute(
            """
            SELECT
                orders.status,
                beats.title,
                beats.audio_path

            FROM orders

            JOIN beats
                ON beats.id = orders.beat_id

            WHERE orders.download_token = ?
            """,
            (token,),
        ).fetchone()

    finally:
        conn.close()

    if (
        not row
        or row["status"] != "completed"
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid download link.",
        )

    file_path = get_safe_audio_file(
        row["audio_path"]
    )

    extension = file_path.suffix.lower()

    media_types = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
    }

    safe_name = (
        row["title"]
        .replace("/", "-")
        .replace("\\", "-")
    )

    return FileResponse(
        path=str(file_path),
        filename=f"{safe_name}{extension}",
        media_type=media_types.get(
            extension,
            "application/octet-stream",
        ),
    )
