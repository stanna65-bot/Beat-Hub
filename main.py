import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import (
    FastAPI, Request, Form, UploadFile, File,
    HTTPException, Depends
)
from fastapi.responses import RedirectResponse, FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import auth
import mpesa
from database import get_db, init_db, unique_slug


BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
COVERS = STATIC / "uploads" / "covers"
AUDIO = STATIC / "uploads" / "audio"

for folder in (COVERS, AUDIO):
    folder.mkdir(parents=True, exist_ok=True)

FEE_RATE = 10

ALLOWED_COVERS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_AUDIO = {".mp3", ".wav", ".m4a"}

MAX_COVER = 10 * 1024 * 1024
MAX_AUDIO = 100 * 1024 * 1024


app = FastAPI(title="BeatHub")

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET") or secrets.token_urlsafe(48),
    same_site="lax",
    https_only=os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true",
)

app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

templates = Jinja2Templates(directory=str(BASE / "templates"))

init_db()


def save_file(upload, folder, prefix, allowed_extensions, max_bytes):
    if not upload or not upload.filename:
        raise HTTPException(status_code=400, detail="File is required.")

    extension = Path(upload.filename).suffix.lower()

    if extension not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Unsupported file type.")

    filename = f"{uuid.uuid4().hex}{extension}"
    target = folder / filename
    total = 0

    try:
        with target.open("wb") as file:
            while True:
                chunk = upload.file.read(1024 * 1024)

                if not chunk:
                    break

                total += len(chunk)

                if total > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail="File is too large."
                    )

                file.write(chunk)

    except Exception:
        target.unlink(missing_ok=True)
        raise

    return f"{prefix}/{filename}"


def ensure_wallet(conn, producer_id):
    conn.execute(
        """
        INSERT OR IGNORE INTO producer_wallets(producer_id)
        VALUES(?)
        """,
        (producer_id,),
    )


def get_admin_phone():
    phone = os.getenv("SUPER_ADMIN_PAYOUT_PHONE", "").strip()

    if not phone:
        return ""

    return mpesa.normalize_phone(phone)


def render_template(template_name, request: Request, **context):
    context["request"] = request
    context["producer"] = auth.current_producer(request)
    context["super_admin"] = auth.is_super_admin(request)

    return templates.TemplateResponse(
        template_name,
        context,
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
@app.head("/health")
def health():
    return Response(content="OK", status_code=200)


@app.head("/")
def root_head():
    return Response(status_code=200)


# ============================================================
# PUBLIC
# ============================================================

@app.get("/")
def home(request: Request):

    conn = get_db()

    try:
        hot_beats = conn.execute(
            """
            SELECT
                b.*,
                p.name AS producer_name,
                p.slug AS producer_slug
            FROM beats b
            JOIN producers p ON p.id = b.producer_id
            WHERE b.is_hot_pick = 1
            ORDER BY b.created_at DESC
            LIMIT 8
            """
        ).fetchall()

    finally:
        conn.close()

    return render_template(
        "home.html",
        request,
        hot_beats=hot_beats,
        error=None,
    )


@app.get("/terms")
def terms(request: Request):
    return render_template("terms.html", request)


# ============================================================
# SIGNUP
# ============================================================

@app.get("/signup")
def signup_page(request: Request):

    if auth.current_producer(request):
        return RedirectResponse("/admin", status_code=303)

    return render_template(
        "signup.html",
        request,
        error=None,
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
        return render_template(
            "signup.html",
            request,
            error="Name is required.",
        )

    if len(password) < 8:
        return render_template(
            "signup.html",
            request,
            error="Password must be at least 8 characters.",
        )

    if "@" not in email:
        return render_template(
            "signup.html",
            request,
            error="Enter a valid email address.",
        )

    conn = get_db()

    try:

        exists = conn.execute(
            "SELECT 1 FROM producers WHERE email = ?",
            (email,),
        ).fetchone()

        if exists:
            return render_template(
                "signup.html",
                request,
                error="Email already exists.",
            )

        slug = unique_slug(conn, name)

        cursor = conn.execute(
            """
            INSERT INTO producers(
                slug,
                email,
                password_hash,
                name
            )
            VALUES(?, ?, ?, ?)
            """,
            (
                slug,
                email,
                auth.hash_password(password),
                name,
            ),
        )

        producer_id = cursor.lastrowid

        ensure_wallet(conn, producer_id)

        conn.commit()

    finally:
        conn.close()

    request.session.clear()
    request.session["producer_id"] = producer_id

    return RedirectResponse("/admin", status_code=303)


# ============================================================
# LOGIN
# ============================================================

@app.get("/login")
def login_page(request: Request):

    if auth.current_producer(request):
        return RedirectResponse("/admin", status_code=303)

    return render_template(
        "login.html",
        request,
        error=None,
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
            "SELECT * FROM producers WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()

    finally:
        conn.close()

    if not producer or not auth.verify_password(
        password,
        producer["password_hash"],
    ):
        return render_template(
            "login.html",
            request,
            error="Incorrect email or password.",
        )

    request.session.clear()
    request.session["producer_id"] = producer["id"]

    return RedirectResponse("/admin", status_code=303)


@app.post("/logout")
def logout(request: Request):

    request.session.clear()

    return RedirectResponse("/", status_code=303)


# ============================================================
# FORGOT PASSWORD
# ============================================================

@app.get("/forgot-password")
def forgot_password_page(request: Request):

    return render_template(
        "forgot_password.html",
        request,
        message=None,
    )


@app.post("/forgot-password")
def forgot_password(
    request: Request,
    email: str = Form(...),
):

    email = email.strip().lower()

    conn = get_db()
    token = None

    try:

        producer = conn.execute(
            """
            SELECT id
            FROM producers
            WHERE email = ?
            """,
            (email,),
        ).fetchone()

        if producer:

            token = auth.new_token()

            expires_at = (
                datetime.now(timezone.utc)
                + timedelta(minutes=30)
            ).isoformat()

            conn.execute(
                """
                UPDATE password_reset_tokens
                SET used_at = CURRENT_TIMESTAMP
                WHERE producer_id = ?
                AND used_at IS NULL
                """,
                (producer["id"],),
            )

            conn.execute(
                """
                INSERT INTO password_reset_tokens(
                    producer_id,
                    token_hash,
                    expires_at
                )
                VALUES(?, ?, ?)
                """,
                (
                    producer["id"],
                    auth.token_hash(token),
                    expires_at,
                ),
            )

            conn.commit()

    finally:
        conn.close()

    message = (
        "If that account exists, a password reset request has been created."
    )

    if (
        os.getenv("DEV_SHOW_RESET_LINK", "false").lower() == "true"
        and token
    ):
        message += f" Development reset link: /reset-password/{token}"

    return render_template(
        "forgot_password.html",
        request,
        message=message,
    )


@app.get("/reset-password/{token}")
def reset_password_page(
    request: Request,
    token: str,
):

    return render_template(
        "reset_password.html",
        request,
        token=token,
        error=None,
    )


@app.post("/reset-password/{token}")
def reset_password(
    request: Request,
    token: str,
    password: str = Form(...),
    confirm_password: str = Form(...),
):

    if password != confirm_password:
        return render_template(
            "reset_password.html",
            request,
            token=token,
            error="Passwords do not match.",
        )

    if len(password) < 8:
        return render_template(
            "reset_password.html",
            request,
            token=token,
            error="Password must be at least 8 characters.",
        )

    conn = get_db()

    try:

        row = conn.execute(
            """
            SELECT *
            FROM password_reset_tokens
            WHERE token_hash = ?
            AND used_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (auth.token_hash(token),),
        ).fetchone()

        if not row:
            return render_template(
                "reset_password.html",
                request,
                token=token,
                error="This reset link is invalid or expired.",
            )

        expiry = datetime.fromisoformat(row["expires_at"])

        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        if expiry < datetime.now(timezone.utc):
            return render_template(
                "reset_password.html",
                request,
                token=token,
                error="This reset link is invalid or expired.",
            )

        conn.execute(
            """
            UPDATE producers
            SET password_hash = ?
            WHERE id = ?
            """,
            (
                auth.hash_password(password),
                row["producer_id"],
            ),
        )

        conn.execute(
            """
            UPDATE password_reset_tokens
            SET used_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (row["id"],),
        )

        conn.commit()

    finally:
        conn.close()

    return RedirectResponse("/login", status_code=303)


# ============================================================
# PRODUCER PUBLIC PAGE
# ============================================================

@app.get("/p/{slug}")
def producer_page(
    request: Request,
    slug: str,
):

    conn = get_db()

    try:

        producer = conn.execute(
            "SELECT * FROM producers WHERE slug = ?",
            (slug,),
        ).fetchone()

        if not producer:
            raise HTTPException(
                status_code=404,
                detail="Producer not found",
            )

        beats = conn.execute(
            """
            SELECT *
            FROM beats
            WHERE producer_id = ?
            ORDER BY is_hot_pick DESC, created_at DESC
            """,
            (producer["id"],),
        ).fetchall()

    finally:
        conn.close()

    return render_template(
        "feed.html",
        request,
        producer=producer,
        beats=beats,
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
            "SELECT * FROM producers WHERE slug = ?",
            (slug,),
        ).fetchone()

        if not producer:
            raise HTTPException(
                status_code=404,
                detail="Producer not found",
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
            detail="Beat not found",
        )

    return render_template(
        "beat.html",
        request,
        producer=producer,
        beat=beat,
    )


# ============================================================
# PRODUCER DASHBOARD
# ============================================================

@app.get("/admin")
def admin_dashboard(
    request: Request,
    producer=Depends(auth.require_producer),
):

    conn = get_db()

    try:

        ensure_wallet(conn, producer["id"])

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
            ORDER BY is_hot_pick DESC, created_at DESC
            """,
            (producer["id"],),
        ).fetchall()

        orders = conn.execute(
            """
            SELECT
                o.*,
                b.title AS beat_title
            FROM orders o
            JOIN beats b ON b.id = o.beat_id
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
            LIMIT 30
            """,
            (producer["id"],),
        ).fetchall()

        totals = {
            "total_sales": sum(
                1 for order in orders
                if order["status"] == "completed"
            ),
            "total_earnings": wallet["total_earnings"],
            "available_balance": wallet["available_balance"],
            "pending_withdrawal": wallet["pending_withdrawal"],
            "total_withdrawn": wallet["total_withdrawn"],
        }

    finally:
        conn.close()

    return render_template(
        "admin.html",
        request,
        wallet=wallet,
        totals=totals,
        beats=beats,
        orders=orders,
        withdrawals=withdrawals,
    )


# ============================================================
# PRODUCER PROFILE
# ============================================================

@app.post("/admin/profile")
def update_profile(
    request: Request,
    name: str = Form(...),
    bio: str = Form(""),
    phone: str = Form(""),
    payout_phone: str = Form(""),
    producer=Depends(auth.require_producer),
):

    normalized_payout_phone = ""

    if payout_phone.strip():
        normalized_payout_phone = mpesa.normalize_phone(
            payout_phone
        )

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
                name.strip()[:100],
                bio.strip()[:2000],
                phone.strip()[:30],
                normalized_payout_phone,
                producer["id"],
            ),
        )

        conn.commit()

    finally:
        conn.close()

    return RedirectResponse("/admin", status_code=303)


# ============================================================
# ADD BEAT
# ============================================================

@app.post("/admin/beat")
def add_beat(
    request: Request,
    title: str = Form(...),
    genre: str = Form(""),
    bpm: str = Form(""),
    price: int = Form(...),
    is_hot_pick: str = Form("0"),
    cover: UploadFile = File(...),
    audio: UploadFile = File(...),
    producer=Depends(auth.require_producer),
):

    if not title.strip():
        raise HTTPException(
            status_code=400,
            detail="Title is required.",
        )

    if price < 1 or price > 10_000_000:
        raise HTTPException(
            status_code=400,
            detail="Invalid price.",
        )

    bpm_value = None

    bpm = bpm.strip()

    if bpm:
        try:
            bpm_value = int(bpm)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="BPM must be a whole number.",
            )

        if bpm_value < 20 or bpm_value > 400:
            raise HTTPException(
                status_code=400,
                detail="BPM must be between 20 and 400.",
            )

    hot_pick = 1 if str(is_hot_pick).lower() in (
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

    except Exception:

        (BASE / cover_path.lstrip("/")).unlink(
            missing_ok=True
        )

        raise

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
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                producer["id"],
                title.strip()[:200],
                genre.strip()[:100],
                bpm_value,
                price,
                cover_path,
                audio_path,
                hot_pick,
            ),
        )

        conn.commit()

    finally:
        conn.close()

    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/beat/{beat_id}/hot-pick")
def update_hot_pick(
    request: Request,
    beat_id: int,
    is_hot_pick: str = Form("0"),
    producer=Depends(auth.require_producer),
):

    hot_pick = 1 if str(is_hot_pick).lower() in (
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
                hot_pick,
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
            detail="Beat not found",
        )

    return RedirectResponse("/admin", status_code=303)


# ============================================================
# FINANCIAL SPLIT
# ============================================================

def apply_split(conn, order_id):

    order = conn.execute(
        """
        SELECT
            o.*,
            b.producer_id
        FROM orders o
        JOIN beats b ON b.id = o.beat_id
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
    platform_fee = round(gross * FEE_RATE / 100)
    producer_credit = gross - platform_fee

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
        AND split_applied_at IS NULL
        """,
        (
            platform_fee,
            producer_credit,
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
            updated_at = CURRENT_TIMESTAMP
        WHERE producer_id = ?
        """,
        (
            producer_credit,
            producer_credit,
            order["producer_id"],
        ),
    )

    conn.execute(
        """
        UPDATE platform_wallet
        SET
            available_balance =
                available_balance + ?,
            total_earnings =
                total_earnings + ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 1
        """,
        (
            platform_fee,
            platform_fee,
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
        VALUES(?, ?, ?, ?)
        """,
        (
            order_id,
            gross,
            platform_fee,
            producer_credit,
        ),
    )

    return True


def complete_order(order_id, receipt):

    conn = get_db()

    try:

        conn.execute("BEGIN IMMEDIATE")

        conn.execute(
            """
            UPDATE orders
            SET
                status = 'completed',
                mpesa_receipt = COALESCE(
                    ?,
                    mpesa_receipt
                ),
                completed_at = COALESCE(
                    completed_at,
                    CURRENT_TIMESTAMP
                )
            WHERE id = ?
            AND status IN ('pending', 'completed')
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
    request: Request,
    beat_id: int,
    phone: str = Form(...),
):

    phone = mpesa.normalize_phone(phone)

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
                detail="Beat not found",
            )

        cursor = conn.execute(
            """
            INSERT INTO orders(
                beat_id,
                buyer_phone,
                amount
            )
            VALUES(?, ?, ?)
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


@app.get("/order/{order_id}/status")
def order_status(
    request: Request,
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
            detail="Order not found",
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
# PRODUCER WITHDRAWAL
# ============================================================

def create_producer_withdrawal(
    conn,
    producer_id,
    amount,
    phone,
):

    conn.execute("BEGIN IMMEDIATE")

    result = conn.execute(
        """
        UPDATE producer_wallets
        SET
            available_balance =
                available_balance - ?,
            pending_withdrawal =
                pending_withdrawal + ?
        WHERE producer_id = ?
        AND available_balance >= ?
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
            status_code=400,
            detail="Insufficient available balance.",
        )

    cursor = conn.execute(
        """
        INSERT INTO withdrawals(
            producer_id,
            amount,
            phone,
            status
        )
        VALUES(?, ?, ?, 'pending')
        """,
        (
            producer_id,
            amount,
            phone,
        ),
    )

    withdrawal_id = cursor.lastrowid

    conn.commit()

    return withdrawal_id


@app.post("/admin/withdraw")
def producer_withdraw(
    request: Request,
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

        profile = conn.execute(
            """
            SELECT payout_phone
            FROM producers
            WHERE id = ?
            """,
            (producer["id"],),
        ).fetchone()

        if not profile["payout_phone"]:
            raise HTTPException(
                status_code=400,
                detail="Add a payout M-Pesa number first.",
            )

        withdrawal_id = create_producer_withdrawal(
            conn,
            producer["id"],
            amount,
            profile["payout_phone"],
        )

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
            profile["payout_phone"],
            amount,
            f"WD{withdrawal_id}",
        )

    except Exception as error:

        conn = get_db()

        try:

            conn.execute("BEGIN IMMEDIATE")

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

            conn.execute("BEGIN IMMEDIATE")

            conn.execute(
                """
                UPDATE withdrawals
                SET
                    status = 'completed',
                    payout_reference = ?,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
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
# SUPER ADMIN LOGIN
# ============================================================

@app.get("/super-admin/login")
def super_admin_login_page(request: Request):

    return render_template(
        "super_admin_login.html",
        request,
        error=None,
    )


@app.post("/super-admin/login")
def super_admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):

    expected_username = os.getenv(
        "SUPER_ADMIN_USERNAME",
        "admin",
    )

    expected_password = os.getenv(
        "SUPER_ADMIN_PASSWORD",
        "",
    )

    username_ok = secrets.compare_digest(
        username,
        expected_username,
    )

    password_ok = bool(expected_password) and secrets.compare_digest(
        password,
        expected_password,
    )

    if not (username_ok and password_ok):
        return render_template(
            "super_admin_login.html",
            request,
            error="Invalid credentials.",
        )

    request.session["super_admin"] = True

    return RedirectResponse(
        "/super-admin",
        status_code=303,
    )


@app.post("/super-admin/logout")
def super_admin_logout(request: Request):

    request.session.pop("super_admin", None)

    return RedirectResponse(
        "/",
        status_code=303,
    )


# ============================================================
# SUPER ADMIN DASHBOARD
# ============================================================

@app.get("/super-admin")
def super_admin_dashboard(request: Request):

    auth.require_super_admin(request)

    conn = get_db()

    try:

        wallet = conn.execute(
            """
            SELECT *
            FROM platform_wallet
            WHERE id = 1
            """
        ).fetchone()

        recent = conn.execute(
            """
            SELECT
                pl.*,
                o.mpesa_receipt,
                b.title,
                p.name AS producer_name
            FROM platform_ledger pl
            JOIN orders o ON o.id = pl.order_id
            JOIN beats b ON b.id = o.beat_id
            JOIN producers p ON p.id = b.producer_id
            ORDER BY pl.created_at DESC
            LIMIT 100
            """
        ).fetchall()

        withdrawals = conn.execute(
            """
            SELECT *
            FROM platform_withdrawals
            ORDER BY requested_at DESC
            LIMIT 50
            """
        ).fetchall()

        gross_sales = sum(
            row["gross_amount"]
            for row in recent
        )

        totals = {
            "gross_sales": gross_sales,
            "platform_earnings": wallet["total_earnings"],
            "available_balance": wallet["available_balance"],
            "pending_withdrawal": wallet["pending_withdrawal"],
            "total_withdrawn": wallet["total_withdrawn"],
        }

    finally:
        conn.close()

    return render_template(
        "super_admin.html",
        request,
        wallet=wallet,
        totals=totals,
        recent=recent,
        withdrawals=withdrawals,
        payout_phone=get_admin_phone(),
    )


# ============================================================
# SUPER ADMIN WITHDRAWAL
# ============================================================

@app.post("/super-admin/withdraw")
def super_admin_withdraw(
    request: Request,
    amount: int = Form(...),
):

    auth.require_super_admin(request)

    if amount < 10:
        raise HTTPException(
            status_code=400,
            detail="Minimum withdrawal is KES 10.",
        )

    phone = get_admin_phone()

    if not phone:
        raise HTTPException(
            status_code=400,
            detail="SUPER_ADMIN_PAYOUT_PHONE is not configured.",
        )

    conn = get_db()

    try:

        conn.execute("BEGIN IMMEDIATE")

        result = conn.execute(
            """
            UPDATE platform_wallet
            SET
                available_balance =
                    available_balance - ?,
                pending_withdrawal =
                    pending_withdrawal + ?
            WHERE id = 1
            AND available_balance >= ?
            """,
            (
                amount,
                amount,
                amount,
            ),
        )

        if not result.rowcount:
            raise HTTPException(
                status_code=400,
                detail="Insufficient platform balance.",
            )

        cursor = conn.execute(
            """
            INSERT INTO platform_withdrawals(
                amount,
                phone,
                status
            )
            VALUES(?, ?, 'pending')
            """,
            (
                amount,
                phone,
            ),
        )

        withdrawal_id = cursor.lastrowid

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

        result = mpesa.initiate_platform_payout(
            phone,
            amount,
            f"ADMINWD{withdrawal_id}",
        )

    except Exception as error:

        conn = get_db()

        try:

            conn.execute("BEGIN IMMEDIATE")

            conn.execute(
                """
                UPDATE platform_withdrawals
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
                UPDATE platform_wallet
                SET
                    available_balance =
                        available_balance + ?,
                    pending_withdrawal =
                        pending_withdrawal - ?
                WHERE id = 1
                """,
                (
                    amount,
                    amount,
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

            conn.execute("BEGIN IMMEDIATE")

            conn.execute(
                """
                UPDATE platform_withdrawals
                SET
                    status = 'completed',
                    payout_reference = ?,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    result["reference"],
                    withdrawal_id,
                ),
            )

            conn.execute(
                """
                UPDATE platform_wallet
                SET
                    pending_withdrawal =
                        pending_withdrawal - ?,
                    total_withdrawn =
                        total_withdrawn + ?
                WHERE id = 1
                """,
                (
                    amount,
                    amount,
                ),
            )

            conn.commit()

        finally:
            conn.close()

    return RedirectResponse(
        "/super-admin",
        status_code=303,
    )


# ============================================================
# DOWNLOAD
# ============================================================

@app.get("/download/{token}")
def download(
    request: Request,
    token: str,
):

    conn = get_db()

    try:

        result = conn.execute(
            """
            SELECT
                o.status,
                b.audio_path
            FROM orders o
            JOIN beats b ON b.id = o.beat_id
            WHERE o.download_token = ?
            """,
            (token,),
        ).fetchone()

    finally:
        conn.close()

    if not result or result["status"] != "completed":
        raise HTTPException(
            status_code=403,
            detail="Invalid download link.",
        )

    path = (
        BASE
        / result["audio_path"].lstrip("/")
    ).resolve()

    if AUDIO.resolve() not in path.parents:
        raise HTTPException(
            status_code=403,
            detail="Invalid file path.",
        )

    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="File unavailable.",
        )

    return FileResponse(
        str(path),
        filename=path.name,
    )


# ============================================================
# M-PESA CALLBACK PLACEHOLDER
# ============================================================

@app.post("/mpesa/callback")
async def mpesa_callback(request: Request):

    return {
        "ResultCode": 0,
        "ResultDesc": (
            "Callback endpoint reserved for "
            "the live Safaricom integration."
        ),
    }
