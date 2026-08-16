import os
import secrets
import smtplib
import ssl
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException, Depends
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

for p in (COVERS, AUDIO):
    p.mkdir(parents=True, exist_ok=True)

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


# ============================================================
# HELPERS
# ============================================================

def save_file(upload, folder, prefix, allowed, max_bytes):
    if not upload or not upload.filename:
        raise HTTPException(status_code=400, detail="File is required.")

    ext = Path(upload.filename).suffix.lower()

    if ext not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported file type.")

    target = folder / f"{uuid.uuid4().hex}{ext}"
    total = 0

    try:
        with target.open("wb") as f:
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

                f.write(chunk)

    except Exception:
        target.unlink(missing_ok=True)
        raise

    return f"{prefix}/{target.name}"


def ensure_wallet(c, producer_id):
    c.execute(
        "INSERT OR IGNORE INTO producer_wallets(producer_id) VALUES(?)",
        (producer_id,)
    )


def admin_phone():
    phone = os.getenv("SUPER_ADMIN_PAYOUT_PHONE", "").strip()

    if not phone:
        return ""

    return mpesa.normalize_phone(phone)


def render(name, request, **ctx):
    ctx.update(
        request=request,
        producer=auth.current_producer(request),
        super_admin=auth.is_super_admin(request),
    )

    return templates.TemplateResponse(name, ctx)


def app_base_url(request: Request):
    configured = os.getenv("APP_BASE_URL", "").strip().rstrip("/")

    if configured:
        return configured

    return str(request.base_url).rstrip("/")


# ============================================================
# EMAIL
# ============================================================

def send_password_reset_email(to_email: str, reset_url: str):
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM", "").strip()
    smtp_from_name = os.getenv("SMTP_FROM_NAME", "BeatHub").strip()

    if not smtp_host or not smtp_username or not smtp_password or not smtp_from:
        raise RuntimeError(
            "Password reset email is not configured. "
            "Set SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD and SMTP_FROM."
        )

    message = EmailMessage()

    message["Subject"] = "Reset your BeatHub password"
    message["From"] = f"{smtp_from_name} <{smtp_from}>"
    message["To"] = to_email

    message.set_content(
        f"""Hello,

We received a request to reset your BeatHub password.

Use this link to create a new password:

{reset_url}

This link expires in 30 minutes and can only be used once.

If you did not request a password reset, you can safely ignore this email.

BeatHub
"""
    )

    use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() == "true"

    if use_ssl:
        context = ssl.create_default_context()

        with smtplib.SMTP_SSL(
            smtp_host,
            smtp_port,
            context=context,
            timeout=20,
        ) as server:
            server.login(smtp_username, smtp_password)
            server.send_message(message)

    else:
        context = ssl.create_default_context()

        with smtplib.SMTP(
            smtp_host,
            smtp_port,
            timeout=20,
        ) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(smtp_username, smtp_password)
            server.send_message(message)


# ============================================================
# HEALTH
# ============================================================

@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return Response("OK", status_code=200)


@app.api_route("/", methods=["HEAD"])
def root_head():
    return Response(status_code=200)


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home(request: Request):
    c = get_db()

    try:
        hot = c.execute(
            """
            SELECT b.*, p.name AS producer_name, p.slug AS producer_slug
            FROM beats b
            JOIN producers p ON p.id = b.producer_id
            WHERE b.is_hot_pick = 1
            ORDER BY b.created_at DESC
            LIMIT 8
            """
        ).fetchall()

    finally:
        c.close()

    return render(
        "home.html",
        request,
        error=None,
        hot_beats=hot,
    )


@app.get("/terms")
def terms(request: Request):
    return render("terms.html", request)


# ============================================================
# SIGNUP
# ============================================================

@app.get("/signup")
def signup_page(request: Request):
    if auth.current_producer(request):
        return RedirectResponse("/admin", status_code=303)

    return render("signup.html", request, error=None)


@app.post("/signup")
def signup(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    name = name.strip()
    email = email.strip().lower()

    if not name or len(password) < 8 or "@" not in email:
        return render(
            "signup.html",
            request,
            error="Use a name, valid email and password of at least 8 characters."
        )

    c = get_db()

    try:
        existing = c.execute(
            "SELECT 1 FROM producers WHERE email=?",
            (email,)
        ).fetchone()

        if existing:
            return render(
                "signup.html",
                request,
                error="Email already exists."
            )

        cur = c.execute(
            """
            INSERT INTO producers(slug, email, password_hash, name)
            VALUES(?, ?, ?, ?)
            """,
            (
                unique_slug(c, name),
                email,
                auth.hash_password(password),
                name,
            )
        )

        producer_id = cur.lastrowid

        ensure_wallet(c, producer_id)

        c.commit()

    finally:
        c.close()

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

    return render("login.html", request, error=None)


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    c = get_db()

    try:
        p = c.execute(
            "SELECT * FROM producers WHERE email=?",
            (email.strip().lower(),)
        ).fetchone()

    finally:
        c.close()

    if not p or not auth.verify_password(password, p["password_hash"]):
        return render(
            "login.html",
            request,
            error="Incorrect email or password."
        )

    request.session.clear()
    request.session["producer_id"] = p["id"]

    return RedirectResponse("/admin", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()

    return RedirectResponse("/", status_code=303)


# ============================================================
# FORGOT PASSWORD
# ============================================================

@app.get("/forgot-password")
def forgot_page(request: Request):
    return render(
        "forgot_password.html",
        request,
        message=None,
        error=None,
    )


@app.post("/forgot-password")
def forgot(
    request: Request,
    email: str = Form(...),
):
    email = email.strip().lower()

    if not email or "@" not in email:
        return render(
            "forgot_password.html",
            request,
            message=None,
            error="Enter a valid email address."
        )

    c = get_db()

    try:
        producer = c.execute(
            "SELECT id, email, name FROM producers WHERE email=?",
            (email,)
        ).fetchone()

        if not producer:
            return render(
                "forgot_password.html",
                request,
                message="If an account exists for that email, a reset link has been sent.",
                error=None,
            )

        token = auth.new_token()

        expiry = (
            datetime.now(timezone.utc)
            + timedelta(minutes=30)
        ).isoformat()

        c.execute(
            """
            UPDATE password_reset_tokens
            SET used_at=CURRENT_TIMESTAMP
            WHERE producer_id=?
            AND used_at IS NULL
            """,
            (producer["id"],)
        )

        c.execute(
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
                expiry,
            )
        )

        c.commit()

    finally:
        c.close()

    reset_url = (
        f"{app_base_url(request)}"
        f"/reset-password/{token}"
    )

    try:
        send_password_reset_email(
            producer["email"],
            reset_url,
        )

    except Exception:
        # Remove the newly created reset token if delivery failed.
        c = get_db()

        try:
            c.execute(
                """
                DELETE FROM password_reset_tokens
                WHERE token_hash=?
                """,
                (auth.token_hash(token),)
            )
            c.commit()

        finally:
            c.close()

        return render(
            "forgot_password.html",
            request,
            message=None,
            error=(
                "We could not send the reset email right now. "
                "Please try again later."
            )
        )

    return render(
        "forgot_password.html",
        request,
        message="If an account exists for that email, a reset link has been sent.",
        error=None,
    )


@app.get("/reset-password/{token}")
def reset_page(
    request: Request,
    token: str,
):
    return render(
        "reset_password.html",
        request,
        token=token,
        error=None,
        message=None,
    )


@app.post("/reset-password/{token}")
def reset_password(
    request: Request,
    token: str,
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    if password != confirm_password:
        return render(
            "reset_password.html",
            request,
            token=token,
            error="Passwords do not match.",
            message=None,
        )

    if len(password) < 8:
        return render(
            "reset_password.html",
            request,
            token=token,
            error="Password must be at least 8 characters.",
            message=None,
        )

    c = get_db()

    try:
        row = c.execute(
            """
            SELECT *
            FROM password_reset_tokens
            WHERE token_hash=?
            AND used_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (auth.token_hash(token),)
        ).fetchone()

        if not row:
            return render(
                "reset_password.html",
                request,
                token=token,
                error="This reset link is invalid or has already been used.",
                message=None,
            )

        expiry = datetime.fromisoformat(row["expires_at"])

        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        if expiry < datetime.now(timezone.utc):
            return render(
                "reset_password.html",
                request,
                token=token,
                error="This reset link has expired. Please request a new one.",
                message=None,
            )

        c.execute(
            """
            UPDATE producers
            SET password_hash=?
            WHERE id=?
            """,
            (
                auth.hash_password(password),
                row["producer_id"],
            )
        )

        c.execute(
            """
            UPDATE password_reset_tokens
            SET used_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (row["id"],)
        )

        c.commit()

    finally:
        c.close()

    return RedirectResponse(
        "/login?reset=success",
        status_code=303,
    )


# ============================================================
# PRODUCER PUBLIC PAGE
# ============================================================

@app.get("/p/{slug}")
def feed(request: Request, slug: str):
    c = get_db()

    try:
        p = c.execute(
            "SELECT * FROM producers WHERE slug=?",
            (slug,)
        ).fetchone()

        if not p:
            raise HTTPException(status_code=404, detail="Producer not found")

        beats = c.execute(
            """
            SELECT *
            FROM beats
            WHERE producer_id=?
            ORDER BY is_hot_pick DESC, created_at DESC
            """,
            (p["id"],)
        ).fetchall()

    finally:
        c.close()

    return render(
        "feed.html",
        request,
        producer=p,
        beats=beats,
    )


@app.get("/p/{slug}/beat/{beat_id}")
def beat_detail(
    request: Request,
    slug: str,
    beat_id: int,
):
    c = get_db()

    try:
        p = c.execute(
            "SELECT * FROM producers WHERE slug=?",
            (slug,)
        ).fetchone()

        beat = c.execute(
            """
            SELECT *
            FROM beats
            WHERE id=?
            AND producer_id=?
            """,
            (beat_id, p["id"] if p else -1)
        ).fetchone()

    finally:
        c.close()

    if not p or not beat:
        raise HTTPException(status_code=404, detail="Beat not found")

    return render(
        "beat.html",
        request,
        producer=p,
        beat=beat,
    )


# ============================================================
# PRODUCER DASHBOARD
# ============================================================

@app.get("/admin")
def admin(
    request: Request,
    producer=Depends(auth.require_producer),
):
    c = get_db()

    try:
        ensure_wallet(c, producer["id"])

        wallet = c.execute(
            """
            SELECT *
            FROM producer_wallets
            WHERE producer_id=?
            """,
            (producer["id"],)
        ).fetchone()

        beats = c.execute(
            """
            SELECT *
            FROM beats
            WHERE producer_id=?
            ORDER BY is_hot_pick DESC, created_at DESC
            """,
            (producer["id"],)
        ).fetchall()

        orders = c.execute(
            """
            SELECT o.*, b.title AS beat_title
            FROM orders o
            JOIN beats b ON b.id=o.beat_id
            WHERE b.producer_id=?
            ORDER BY o.created_at DESC
            LIMIT 50
            """,
            (producer["id"],)
        ).fetchall()

        withdrawals = c.execute(
            """
            SELECT *
            FROM withdrawals
            WHERE producer_id=?
            ORDER BY requested_at DESC
            LIMIT 30
            """,
            (producer["id"],)
        ).fetchall()

        totals = {
            "total_sales": sum(
                1 for o in orders
                if o["status"] == "completed"
            ),
            "total_earnings": wallet["total_earnings"],
            "available_balance": wallet["available_balance"],
            "pending_withdrawal": wallet["pending_withdrawal"],
            "total_withdrawn": wallet["total_withdrawn"],
        }

    finally:
        c.close()

    return render(
        "admin.html",
        request,
        wallet=wallet,
        totals=totals,
        beats=beats,
        orders=orders,
        withdrawals=withdrawals,
    )


@app.post("/admin/profile")
def update_profile(
    request: Request,
    name: str = Form(...),
    bio: str = Form(""),
    phone: str = Form(""),
    payout_phone: str = Form(""),
    producer=Depends(auth.require_producer),
):
    normalized_payout = ""

    if payout_phone.strip():
        normalized_payout = mpesa.normalize_phone(payout_phone)

    c = get_db()

    try:
        c.execute(
            """
            UPDATE producers
            SET name=?, bio=?, phone=?, payout_phone=?
            WHERE id=?
            """,
            (
                name.strip()[:100],
                bio.strip()[:2000],
                phone.strip()[:30],
                normalized_payout,
                producer["id"],
            )
        )

        c.commit()

    finally:
        c.close()

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
        raise HTTPException(status_code=400, detail="Title is required.")

    if price < 1 or price > 10_000_000:
        raise HTTPException(status_code=400, detail="Invalid price.")

    bpm_value = None
    bpm = bpm.strip()

    if bpm:
        try:
            bpm_value = int(bpm)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="BPM must be a whole number."
            )

        if bpm_value < 20 or bpm_value > 400:
            raise HTTPException(
                status_code=400,
                detail="BPM must be between 20 and 400."
            )

    hot_pick = 1 if str(is_hot_pick).lower() in (
        "1", "true", "on", "yes"
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
        (BASE / cover_path.lstrip("/")).unlink(missing_ok=True)
        raise

    c = get_db()

    try:
        c.execute(
            """
            INSERT INTO beats(
                producer_id, title, genre, bpm, price,
                cover_path, audio_path, is_hot_pick
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
            )
        )

        c.commit()

    finally:
        c.close()

    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/beat/{beat_id}/hot-pick")
def update_hot_pick(
    request: Request,
    beat_id: int,
    is_hot_pick: str = Form("0"),
    producer=Depends(auth.require_producer),
):
    hot_pick = 1 if str(is_hot_pick).lower() in (
        "1", "true", "on", "yes"
    ) else 0

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
                hot_pick,
                beat_id,
                producer["id"],
            )
        )

        c.commit()

    finally:
        c.close()

    if not result.rowcount:
        raise HTTPException(status_code=404, detail="Beat not found")

    return RedirectResponse("/admin", status_code=303)


# ============================================================
# FINANCIAL SPLIT
# ============================================================

def apply_split(c, order_id):
    order = c.execute(
        """
        SELECT o.*, b.producer_id
        FROM orders o
        JOIN beats b ON b.id=o.beat_id
        WHERE o.id=?
        """,
        (order_id,)
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

    result = c.execute(
        """
        UPDATE orders
        SET platform_fee=?,
            producer_payout=?,
            commission_rate_locked=?,
            download_token=?,
            split_applied_at=CURRENT_TIMESTAMP
        WHERE id=?
        AND split_applied_at IS NULL
        """,
        (
            platform_fee,
            producer_credit,
            FEE_RATE,
            download_token,
            order_id,
        )
    )

    if not result.rowcount:
        return False

    ensure_wallet(c, order["producer_id"])

    c.execute(
        """
        UPDATE producer_wallets
        SET available_balance=available_balance+?,
            total_earnings=total_earnings+?,
            updated_at=CURRENT_TIMESTAMP
        WHERE producer_id=?
        """,
        (
            producer_credit,
            producer_credit,
            order["producer_id"],
        )
    )

    c.execute(
        """
        UPDATE platform_wallet
        SET available_balance=available_balance+?,
            total_earnings=total_earnings+?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=1
        """,
        (
            platform_fee,
            platform_fee,
        )
    )

    c.execute(
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
        )
    )

    return True


def complete_order(order_id, receipt):
    c = get_db()

    try:
        c.execute("BEGIN IMMEDIATE")

        c.execute(
            """
            UPDATE orders
            SET status='completed',
                mpesa_receipt=COALESCE(?, mpesa_receipt),
                completed_at=COALESCE(completed_at, CURRENT_TIMESTAMP)
            WHERE id=?
            AND status IN ('pending', 'completed')
            """,
            (
                receipt,
                order_id,
            )
        )

        apply_split(c, order_id)

        c.commit()

    except Exception:
        c.rollback()
        raise

    finally:
        c.close()


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

    c = get_db()

    try:
        beat = c.execute(
            "SELECT * FROM beats WHERE id=?",
            (beat_id,)
        ).fetchone()

        if not beat:
            raise HTTPException(status_code=404, detail="Beat not found")

        cur = c.execute(
            """
            INSERT INTO orders(beat_id, buyer_phone, amount)
            VALUES(?, ?, ?)
            """,
            (
                beat_id,
                phone,
                beat["price"],
            )
        )

        order_id = cur.lastrowid
        c.commit()

    finally:
        c.close()

    try:
        result = mpesa.stk_push(
            phone,
            beat["price"],
            f"BEAT{beat_id}",
            beat["title"],
        )

    except Exception as error:
        c = get_db()

        try:
            c.execute(
                """
                UPDATE orders
                SET status='failed',
                    failure_reason=?
                WHERE id=?
                """,
                (
                    str(error)[:500],
                    order_id,
                )
            )
            c.commit()

        finally:
            c.close()

        raise HTTPException(status_code=502, detail=str(error))

    c = get_db()

    try:
        c.execute(
            """
            UPDATE orders
            SET checkout_request_id=?
            WHERE id=?
            """,
            (
                result["checkout_request_id"],
                order_id,
            )
        )
        c.commit()

    finally:
        c.close()

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

    return JSONResponse({
        "order_id": order_id,
        "status": "pending",
    })


@app.get("/order/{order_id}/status")
def order_status(
    request: Request,
    order_id: int,
):
    c = get_db()

    try:
        order = c.execute(
            """
            SELECT status, download_token
            FROM orders
            WHERE id=?
            """,
            (order_id,)
        ).fetchone()

    finally:
        c.close()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

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

def create_producer_withdrawal(c, producer_id, amount, phone):
    c.execute("BEGIN IMMEDIATE")

    result = c.execute(
        """
        UPDATE producer_wallets
        SET available_balance=available_balance-?,
            pending_withdrawal=pending_withdrawal+?
        WHERE producer_id=?
        AND available_balance>=?
        """,
        (
            amount,
            amount,
            producer_id,
            amount,
        )
    )

    if not result.rowcount:
        raise HTTPException(
            status_code=400,
            detail="Insufficient available balance."
        )

    cur = c.execute(
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
        )
    )

    withdrawal_id = cur.lastrowid
    c.commit()

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
            detail="Minimum withdrawal is KES 10."
        )

    c = get_db()

    try:
        profile = c.execute(
            """
            SELECT payout_phone
            FROM producers
            WHERE id=?
            """,
            (producer["id"],)
        ).fetchone()

        if not profile["payout_phone"]:
            raise HTTPException(
                status_code=400,
                detail="Add a payout M-Pesa number first."
            )

        withdrawal_id = create_producer_withdrawal(
            c,
            producer["id"],
            amount,
            profile["payout_phone"],
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
        result = mpesa.initiate_producer_payout(
            profile["payout_phone"],
            amount,
            f"WD{withdrawal_id}",
        )

    except Exception as error:
        c = get_db()

        try:
            c.execute("BEGIN IMMEDIATE")

            c.execute(
                """
                UPDATE withdrawals
                SET status='failed',
                    failure_reason=?
                WHERE id=?
                """,
                (
                    str(error)[:500],
                    withdrawal_id,
                )
            )

            c.execute(
                """
                UPDATE producer_wallets
                SET available_balance=available_balance+?,
                    pending_withdrawal=pending_withdrawal-?
                WHERE producer_id=?
                """,
                (
                    amount,
                    amount,
                    producer["id"],
                )
            )

            c.commit()

        finally:
            c.close()

        raise HTTPException(status_code=502, detail=str(error))

    if result.get("simulated"):
        c = get_db()

        try:
            c.execute("BEGIN IMMEDIATE")

            c.execute(
                """
                UPDATE withdrawals
                SET status='completed',
                    payout_reference=?,
                    completed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    result["reference"],
                    withdrawal_id,
                )
            )

            c.execute(
                """
                UPDATE producer_wallets
                SET pending_withdrawal=pending_withdrawal-?,
                    total_withdrawn=total_withdrawn+?
                WHERE producer_id=?
                """,
                (
                    amount,
                    amount,
                    producer["id"],
                )
            )

            c.commit()

        finally:
            c.close()

    return RedirectResponse("/admin", status_code=303)


# ============================================================
# SUPER ADMIN
# ============================================================

@app.get("/super-admin/login")
def super_admin_login_page(request: Request):
    return render(
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

    password_ok = (
        bool(expected_password)
        and secrets.compare_digest(
            password,
            expected_password,
        )
    )

    if not (username_ok and password_ok):
        return render(
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

    return RedirectResponse("/", status_code=303)


@app.get("/super-admin")
def super_admin_dashboard(request: Request):
    auth.require_super_admin(request)

    c = get_db()

    try:
        wallet = c.execute(
            "SELECT * FROM platform_wallet WHERE id=1"
        ).fetchone()

        recent = c.execute(
            """
            SELECT pl.*, o.mpesa_receipt,
                   b.title,
                   p.name AS producer_name
            FROM platform_ledger pl
            JOIN orders o ON o.id=pl.order_id
            JOIN beats b ON b.id=o.beat_id
            JOIN producers p ON p.id=b.producer_id
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
        c.close()

    return render(
        "super_admin.html",
        request,
        wallet=wallet,
        totals=totals,
        recent=recent,
        withdrawals=withdrawals,
        payout_phone=admin_phone(),
    )


@app.post("/super-admin/withdraw")
def super_admin_withdraw(
    request: Request,
    amount: int = Form(...),
):
    auth.require_super_admin(request)

    if amount < 10:
        raise HTTPException(
            status_code=400,
            detail="Minimum withdrawal is KES 10."
        )

    phone = admin_phone()

    if not phone:
        raise HTTPException(
            status_code=400,
            detail="SUPER_ADMIN_PAYOUT_PHONE is not configured."
        )

    c = get_db()

    try:
        c.execute("BEGIN IMMEDIATE")

        result = c.execute(
            """
            UPDATE platform_wallet
            SET available_balance=available_balance-?,
                pending_withdrawal=pending_withdrawal+?
            WHERE id=1
            AND available_balance>=?
            """,
            (
                amount,
                amount,
                amount,
            )
        )

        if not result.rowcount:
            raise HTTPException(
                status_code=400,
                detail="Insufficient platform balance."
            )

        cur = c.execute(
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
            )
        )

        withdrawal_id = cur.lastrowid
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
        result = mpesa.initiate_platform_payout(
            phone,
            amount,
            f"ADMINWD{withdrawal_id}",
        )

    except Exception as error:
        c = get_db()

        try:
            c.execute("BEGIN IMMEDIATE")

            c.execute(
                """
                UPDATE platform_withdrawals
                SET status='failed',
                    failure_reason=?
                WHERE id=?
                """,
                (
                    str(error)[:500],
                    withdrawal_id,
                )
            )

            c.execute(
                """
                UPDATE platform_wallet
                SET available_balance=available_balance+?,
                    pending_withdrawal=pending_withdrawal-?
                WHERE id=1
                """,
                (
                    amount,
                    amount,
                )
            )

            c.commit()

        finally:
            c.close()

        raise HTTPException(status_code=502, detail=str(error))

    if result.get("simulated"):
        c = get_db()

        try:
            c.execute("BEGIN IMMEDIATE")

            c.execute(
                """
                UPDATE platform_withdrawals
                SET status='completed',
                    payout_reference=?,
                    completed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    result["reference"],
                    withdrawal_id,
                )
            )

            c.execute(
                """
                UPDATE platform_wallet
                SET pending_withdrawal=pending_withdrawal-?,
                    total_withdrawn=total_withdrawn+?
                WHERE id=1
                """,
                (
                    amount,
                    amount,
                )
            )

            c.commit()

        finally:
            c.close()

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
    c = get_db()

    try:
        result = c.execute(
            """
            SELECT o.status, b.audio_path
            FROM orders o
            JOIN beats b ON b.id=o.beat_id
            WHERE o.download_token=?
            """,
            (token,)
        ).fetchone()

    finally:
        c.close()

    if not result or result["status"] != "completed":
        raise HTTPException(
            status_code=403,
            detail="Invalid download link."
        )

    path = (
        BASE / result["audio_path"].lstrip("/")
    ).resolve()

    if AUDIO.resolve() not in path.parents:
        raise HTTPException(
            status_code=403,
            detail="Invalid file path."
        )

    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="File unavailable."
        )

    return FileResponse(
        str(path),
        filename=path.name,
    )


# ============================================================
# M-PESA CALLBACK
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
