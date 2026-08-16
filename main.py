import os
import secrets
import threading
import time
import uuid
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
    JSONResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import auth
import mpesa
from database import get_db, init_db, unique_slug


# ============================================================
# PATHS AND CONFIGURATION
# ============================================================

BASE = Path(__file__).resolve().parent

STATIC = BASE / "static"
COVERS = STATIC / "uploads" / "covers"
AUDIO = STATIC / "uploads" / "audio"

COVERS.mkdir(parents=True, exist_ok=True)
AUDIO.mkdir(parents=True, exist_ok=True)

FEE_RATE = 10.0

ALLOWED_COVERS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_AUDIO = {".mp3", ".wav", ".m4a"}

MAX_COVER = 10 * 1024 * 1024
MAX_AUDIO = 100 * 1024 * 1024


# ============================================================
# APP
# ============================================================

app = FastAPI(title="Beat Hub")

secret = os.getenv("SESSION_SECRET") or secrets.token_urlsafe(48)

app.add_middleware(
    SessionMiddleware,
    secret_key=secret,
    same_site="lax",
    https_only=os.getenv(
        "SESSION_HTTPS_ONLY",
        "false"
    ).lower() == "true",
)

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC)),
    name="static",
)

templates = Jinja2Templates(
    directory=str(BASE / "templates")
)

init_db()


# ============================================================
# HELPERS
# ============================================================

def save_file(
    upload: UploadFile,
    folder: Path,
    prefix: str,
    allowed_extensions: set,
    max_bytes: int,
):
    if not upload or not upload.filename:
        raise HTTPException(
            status_code=400,
            detail="File is required.",
        )

    extension = Path(upload.filename).suffix.lower()

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type.",
        )

    filename = f"{uuid.uuid4().hex}{extension}"
    target = folder / filename

    total_size = 0

    try:
        with target.open("wb") as output_file:
            while True:
                chunk = upload.file.read(1024 * 1024)

                if not chunk:
                    break

                total_size += len(chunk)

                if total_size > max_bytes:
                    output_file.close()
                    target.unlink(missing_ok=True)

                    raise HTTPException(
                        status_code=413,
                        detail="File is too large.",
                    )

                output_file.write(chunk)

    except HTTPException:
        raise

    except Exception:
        target.unlink(missing_ok=True)

        raise HTTPException(
            status_code=500,
            detail="Could not save uploaded file.",
        )

    return f"{prefix}/{filename}"


def ensure_wallet(conn, producer_id: int):
    conn.execute(
        """
        INSERT OR IGNORE INTO producer_wallets(producer_id)
        VALUES(?)
        """,
        (producer_id,),
    )


def page_error(
    request: Request,
    message: str,
    code: int = 400,
):
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "producer": auth.current_producer(request),
            "error": message,
        },
        status_code=code,
    )


# ============================================================
# HEALTH
# ============================================================

@app.api_route(
    "/health",
    methods=["GET", "HEAD"],
)
def health():
    return Response("OK")


@app.api_route(
    "/",
    methods=["HEAD"],
)
def root_head():
    return Response(status_code=200)


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "producer": auth.current_producer(request),
            "error": None,
        },
    )


@app.get("/terms")
def terms(request: Request):
    return templates.TemplateResponse(
        "terms.html",
        {
            "request": request,
            "producer": auth.current_producer(request),
        },
    )


# ============================================================
# SIGNUP
# ============================================================

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
    name = name.strip()
    email = email.strip().lower()

    if not name:
        return page_error(
            request,
            "Producer name is required.",
        )

    if "@" not in email:
        return page_error(
            request,
            "Enter a valid email address.",
        )

    if len(password) < 8:
        return page_error(
            request,
            "Password must be at least 8 characters.",
        )

    conn = get_db()

    try:
        existing = conn.execute(
            """
            SELECT 1
            FROM producers
            WHERE email = ?
            """,
            (email,),
        ).fetchone()

        if existing:
            return templates.TemplateResponse(
                "signup.html",
                {
                    "request": request,
                    "error": "Email already exists.",
                },
                status_code=409,
            )

        slug = unique_slug(
            conn,
            name,
        )

        cursor = conn.execute(
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


# ============================================================
# LOGIN
# ============================================================

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
    conn = get_db()

    try:
        producer = conn.execute(
            """
            SELECT *
            FROM producers
            WHERE email = ?
            """,
            (email.strip().lower(),),
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
                "error": "Incorrect email or password.",
            },
            status_code=401,
        )

    request.session.clear()
    request.session["producer_id"] = producer["id"]

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# ============================================================
# LOGOUT
# ============================================================

@app.post("/logout")
def logout(request: Request):
    request.session.clear()

    return RedirectResponse(
        "/",
        status_code=303,
    )


# ============================================================
# PUBLIC PRODUCER STORE
# ============================================================

@app.get("/p/{slug}")
def feed(
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
                created_at DESC
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


# ============================================================
# BEAT DETAILS
# ============================================================

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
            WHERE id = ?
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


# ============================================================
# ADMIN DASHBOARD
# ============================================================

@app.get("/admin")
def admin(
    request: Request,
    producer=Depends(auth.require_producer),
):
    conn = get_db()

    try:
        ensure_wallet(
            conn,
            producer["id"],
        )

        wallet = conn.execute(
            """
            SELECT *
            FROM producer_wallets
            WHERE producer_id = ?
            """,
            (producer["id"],),
        ).fetchone()

        beats = conn.execute(
            """
            SELECT *
            FROM beats
            WHERE producer_id = ?
            ORDER BY
                is_hot_pick DESC,
                created_at DESC
            """,
            (producer["id"],),
        ).fetchall()

        orders = conn.execute(
            """
            SELECT
                o.*,
                b.title AS beat_title
            FROM orders o
            JOIN beats b
                ON b.id = o.beat_id
            WHERE b.producer_id = ?
            ORDER BY o.created_at DESC
            LIMIT 50
            """,
            (producer["id"],),
        ).fetchall()

        withdrawals = conn.execute(
            """
            SELECT *
            FROM withdrawals
            WHERE producer_id = ?
            ORDER BY requested_at DESC
            LIMIT 20
            """,
            (producer["id"],),
        ).fetchall()

        sales = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM orders o
            JOIN beats b
                ON b.id = o.beat_id
            WHERE b.producer_id = ?
            AND o.status = 'completed'
            """,
            (producer["id"],),
        ).fetchone()["n"]

        totals = {
            "total_sales": sales,
            "total_earnings": wallet["total_earnings"],
            "available_balance": wallet["available_balance"],
            "pending_withdrawal": wallet["pending_withdrawal"],
            "total_withdrawn": wallet["total_withdrawn"],
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


# ============================================================
# UPDATE PROFILE
# ============================================================

@app.post("/admin/profile")
def profile(
    name: str = Form(...),
    bio: str = Form(""),
    phone: str = Form(""),
    payout_phone: str = Form(""),
    producer=Depends(auth.require_producer),
):
    name = name.strip()

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Producer name is required.",
        )

    if payout_phone.strip():
        try:
            payout_phone = mpesa.normalize_phone(
                payout_phone
            )

        except Exception as error:
            raise HTTPException(
                status_code=400,
                detail=str(error),
            )

    else:
        payout_phone = ""

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
                name[:100],
                bio.strip()[:2000],
                phone.strip()[:30],
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


# ============================================================
# UPLOAD BEAT
# BPM FIXED: EMPTY BPM IS NOW ACCEPTED
# ============================================================

@app.post("/admin/beat")
def add_beat(
    title: str = Form(...),
    genre: str = Form(""),
    bpm: str = Form(""),
    price: int = Form(...),
    is_hot_pick: str = Form("0"),
    cover: UploadFile = File(...),
    audio: UploadFile = File(...),
    producer=Depends(auth.require_producer),
):
    title = title.strip()
    genre = genre.strip()

    if not title:
        raise HTTPException(
            status_code=400,
            detail="Beat title is required.",
        )

    if not 1 <= price <= 10_000_000:
        raise HTTPException(
            status_code=400,
            detail="Price must be between KES 1 and KES 10,000,000.",
        )

    # --------------------------------------------------------
    # BPM FIX
    # Empty input "" becomes None instead of causing
    # FastAPI int_parsing validation error.
    # --------------------------------------------------------

    bpm = bpm.strip()

    if bpm == "":
        bpm_value = None

    else:
        try:
            bpm_value = int(bpm)

        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="BPM must be a valid whole number.",
            )

        if not 20 <= bpm_value <= 400:
            raise HTTPException(
                status_code=400,
                detail="BPM must be between 20 and 400.",
            )

    hot = 1 if str(
        is_hot_pick
    ).lower() in (
        "1",
        "true",
        "on",
        "yes",
    ) else 0

    cover_path = save_file(
        cover,
        COVERS,
        "/static/uploads/covers",
        ALLOWED_COVERS,
        MAX_COVER,
    )

    try:
        audio_path = save_file(
            audio,
            AUDIO,
            "/static/uploads/audio",
            ALLOWED_AUDIO,
            MAX_AUDIO,
        )

        conn = get_db()

        try:
            conn.execute(
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
                    title[:200],
                    genre[:100],
                    bpm_value,
                    price,
                    cover_path,
                    audio_path,
                    hot,
                ),
            )

            conn.commit()

        finally:
            conn.close()

    except Exception:
        (BASE / cover_path.lstrip("/")).unlink(
            missing_ok=True
        )
        raise

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# ============================================================
# HOT PICK
# ============================================================

@app.post("/admin/beat/{beat_id}/hot-pick")
def hot_pick(
    beat_id: int,
    is_hot_pick: str = Form("0"),
    producer=Depends(auth.require_producer),
):
    hot = 1 if str(
        is_hot_pick
    ).lower() in (
        "1",
        "true",
        "on",
        "yes",
    ) else 0

    conn = get_db()

    try:
        result = conn.execute(
            """
            UPDATE beats
            SET is_hot_pick = ?
            WHERE id = ?
            AND producer_id = ?
            """,
            (
                hot,
                beat_id,
                producer["id"],
            ),
        )

        conn.commit()

    finally:
        conn.close()

    if not result.rowcount:
        raise HTTPException(
            status_code=404,
            detail="Beat not found.",
        )

    return RedirectResponse(
        "/admin",
        status_code=303,
    )


# ============================================================
# APPLY AUTOMATIC SPLIT
# ============================================================

def apply_split(
    conn,
    order_id: int,
):
    order = conn.execute(
        """
        SELECT
            o.*,
            b.producer_id,
            b.title
        FROM orders o
        JOIN beats b
            ON b.id = o.beat_id
        WHERE o.id = ?
        """,
        (order_id,),
    ).fetchone()

    if not order:
        return False

    if order["status"] != "completed":
        return False

    if order["split_applied_at"]:
        return False

    gross = int(order["amount"])
    platform_fee = round(
        gross * FEE_RATE / 100
    )
    producer_payout = gross - platform_fee
    download_token = secrets.token_urlsafe(32)

    result = conn.execute(
        """
        UPDATE orders
        SET
            platform_fee = ?,
            producer_payout = ?,
            commission_rate_locked = ?,
            download_token = ?,
            split_applied_at = CURRENT_TIMESTAMP
        WHERE id = ?
        AND status = 'completed'
        AND split_applied_at IS NULL
        """,
        (
            platform_fee,
            producer_payout,
            FEE_RATE,
            download_token,
            order_id,
        ),
    )

    if not result.rowcount:
        return False

    ensure_wallet(
        conn,
        order["producer_id"],
    )

    conn.execute(
        """
        UPDATE producer_wallets
        SET
            available_balance =
                available_balance + ?,
            total_earnings =
                total_earnings + ?,
            updated_at =
                CURRENT_TIMESTAMP
        WHERE producer_id = ?
        """,
        (
            producer_payout,
            producer_payout,
            order["producer_id"],
        ),
    )

    conn.execute(
        """
        INSERT INTO wallet_transactions(
            producer_id,
            order_id,
            transaction_type,
            amount,
            reference
        )
        VALUES(?,?,?,?,?)
        """,
        (
            order["producer_id"],
            order_id,
            "sale_credit",
            producer_payout,
            f"ORDER-{order_id}",
        ),
    )

    conn.execute(
        """
        INSERT OR IGNORE INTO platform_ledger(
            order_id,
            gross_amount,
            platform_fee,
            producer_credit
        )
        VALUES(?,?,?,?)
        """,
        (
            order_id,
            gross,
            platform_fee,
            producer_payout,
        ),
    )

    return True


# ============================================================
# COMPLETE ORDER
# ============================================================

def complete_order(
    order_id: int,
    receipt: str,
):
    conn = get_db()

    try:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

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
                        CURRENT_TIMESTAMP
                    )
            WHERE id = ?
            AND status IN (
                'pending',
                'completed'
            )
            """,
            (
                receipt,
                order_id,
            ),
        )

        apply_split(
            conn,
            order_id,
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


# ============================================================
# CHECKOUT
# ============================================================

@app.post("/checkout/{beat_id}")
def checkout(
    beat_id: int,
    phone: str = Form(...),
):
    try:
        phone = mpesa.normalize_phone(
            phone
        )

    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

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

        cursor = conn.execute(
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
                beat["price"],
            ),
        )

        order_id = cursor.lastrowid

        conn.commit()

    finally:
        conn.close()

    try:
        result = mpesa.stk_push(
            phone,
            beat["price"],
            f"BEAT{beat_id}",
            beat["title"],
        )

    except Exception as error:
        conn = get_db()

        try:
            conn.execute(
                """
                UPDATE orders
                SET
                    status = 'failed',
                    failure_reason = ?
                WHERE id = ?
                """,
                (
                    str(error)[:500],
                    order_id,
                ),
            )

            conn.commit()

        finally:
            conn.close()

        raise HTTPException(
            status_code=502,
            detail=str(error),
        )

    conn = get_db()

    try:
        conn.execute(
            """
            UPDATE orders
            SET checkout_request_id = ?
            WHERE id = ?
            """,
            (
                result["checkout_request_id"],
                order_id,
            ),
        )

        conn.commit()

    finally:
        conn.close()

    if result.get("simulated"):
        threading.Thread(
            target=lambda: (
                time.sleep(2),
                complete_order(
                    order_id,
                    f"MOCK-{order_id}",
                ),
            ),
            daemon=True,
        ).start()

    return JSONResponse(
        {
            "order_id": order_id,
            "status": "pending",
        }
    )


# ============================================================
# ORDER STATUS
# ============================================================

@app.get("/order/{order_id}/status")
def order_status(
    order_id: int,
):
    conn = get_db()

    try:
        order = conn.execute(
            """
            SELECT
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

    return {
        "status": order["status"],
        "download_token": (
            order["download_token"]
            if order["status"] == "completed"
            else None
        ),
    }


# ============================================================
# WITHDRAWAL
# ============================================================

@app.post("/admin/withdraw")
def withdraw(
    amount: int = Form(...),
    producer=Depends(auth.require_producer),
):
    if amount < 10:
        raise HTTPException(
            status_code=400,
            detail="Minimum withdrawal is KES 10.",
        )

    conn = get_db()

    try:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        producer_data = conn.execute(
            """
            SELECT payout_phone
            FROM producers
            WHERE id = ?
            """,
            (producer["id"],),
        ).fetchone()

        if not producer_data["payout_phone"]:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Add a payout M-Pesa number first."
                ),
            )

        ensure_wallet(
            conn,
            producer["id"],
        )

        result = conn.execute(
            """
            UPDATE producer_wallets
            SET
                available_balance =
                    available_balance - ?,
                pending_withdrawal =
                    pending_withdrawal + ?,
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE producer_id = ?
            AND available_balance >= ?
            """,
            (
                amount,
                amount,
                producer["id"],
                amount,
            ),
        )

        if not result.rowcount:
            raise HTTPException(
                status_code=400,
                detail="Insufficient available balance.",
            )

        cursor = conn.execute(
            """
            INSERT INTO withdrawals(
                producer_id,
                amount,
                phone
            )
            VALUES(?,?,?)
            """,
            (
                producer["id"],
                amount,
                producer_data["payout_phone"],
            ),
        )

        withdrawal_id = cursor.lastrowid

        conn.execute(
            """
            INSERT INTO wallet_transactions(
                producer_id,
                withdrawal_id,
                transaction_type,
                amount,
                reference
            )
            VALUES(?,?,?,?,?)
            """,
            (
                producer["id"],
                withdrawal_id,
                "withdrawal_requested",
                -amount,
                f"WD-{withdrawal_id}",
            ),
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    try:
        result = mpesa.initiate_producer_payout(
            producer_data["payout_phone"],
            amount,
            f"WD{withdrawal_id}",
        )

    except Exception as error:
        conn = get_db()

        try:
            conn.execute(
                "BEGIN IMMEDIATE"
            )

            conn.execute(
                """
                UPDATE withdrawals
                SET
                    status = 'failed',
                    failure_reason = ?
                WHERE id = ?
                """,
                (
                    str(error)[:500],
                    withdrawal_id,
                ),
            )

            conn.execute(
                """
                UPDATE producer_wallets
                SET
                    available_balance =
                        available_balance + ?,
                    pending_withdrawal =
                        pending_withdrawal - ?
                WHERE producer_id = ?
                """,
                (
                    amount,
                    amount,
                    producer["id"],
                ),
            )

            conn.commit()

        finally:
            conn.close()

        raise HTTPException(
            status_code=502,
            detail=str(error),
        )

    if result.get("simulated"):
        conn = get_db()

        try:
            conn.execute(
                "BEGIN IMMEDIATE"
            )

            conn.execute(
                """
                UPDATE withdrawals
                SET
                    status = 'completed',
                    payout_reference = ?,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                AND status = 'processing'
                """,
                (
                    result["reference"],
                    withdrawal_id,
                ),
            )

            conn.execute(
                """
                UPDATE producer_wallets
                SET
                    pending_withdrawal =
                        pending_withdrawal - ?,
                    total_withdrawn =
                        total_withdrawn + ?
                WHERE producer_id = ?
                """,
                (
                    amount,
                    amount,
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


# ============================================================
# PROTECTED DOWNLOAD
# ============================================================

@app.get("/download/{token}")
def download(
    token: str,
):
    conn = get_db()

    try:
        row = conn.execute(
            """
            SELECT
                o.status,
                b.title,
                b.audio_path
            FROM orders o
            JOIN beats b
                ON b.id = o.beat_id
            WHERE o.download_token = ?
            """,
            (token,),
        ).fetchone()

    finally:
        conn.close()

    if not row or row["status"] != "completed":
        raise HTTPException(
            status_code=403,
            detail="Invalid download link.",
        )

    path = (
        BASE / row["audio_path"].lstrip("/")
    ).resolve()

    if (
        AUDIO.resolve() not in path.parents
        or not path.is_file()
    ):
        raise HTTPException(
            status_code=404,
            detail="File unavailable.",
        )

    return FileResponse(
        str(path),
        filename=path.name,
    )


# ============================================================
# M-PESA CALLBACK
# ============================================================

@app.post("/mpesa/callback")
async def callback(
    request: Request,
):
    # Live Daraja callback processing can be added here
    # when MPESA_MODE is changed from mock to live.
    return {
        "ResultCode": 0,
        "ResultDesc": "Accepted",
    }
