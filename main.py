import os
import secrets
import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException, Depends
from fastapi.responses import RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import auth
import mpesa
from database import get_db, init_db, unique_slug

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"

app = FastAPI(title="Beat Store")

SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    # Fine for local dev; every restart invalidates existing sessions.
    # ALWAYS set SESSION_SECRET as a real env var in production.
    SESSION_SECRET = secrets.token_hex(32)
    print("WARNING: SESSION_SECRET not set — using a random one-off key. "
          "Set SESSION_SECRET in your environment before deploying.")

app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax", https_only=os.getenv("SESSION_HTTPS_ONLY", "true").lower() == "true")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

init_db()


def save_upload(upload: UploadFile, subfolder: str) -> str:
    ext = Path(upload.filename).suffix
    fname = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / subfolder / fname
    with dest.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    return f"/static/uploads/{subfolder}/{fname}"


# ---------------------------------------------------------------------------
# Auth - signup / login / logout
# ---------------------------------------------------------------------------
@app.get("/")
def home(request: Request):
    producer = auth.current_producer(request)
    return templates.TemplateResponse("home.html", {"request": request, "producer": producer})


@app.get("/signup")
def signup_page(request: Request):
    if auth.current_producer(request):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse("signup.html", {"request": request, "error": None})


@app.post("/signup")
def signup(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    email = email.strip().lower()
    if len(password) < 8:
        return templates.TemplateResponse(
            "signup.html", {"request": request, "error": "Password must be at least 8 characters."}
        )
    conn = get_db()
    existing = conn.execute("SELECT 1 FROM producers WHERE email = ?", (email,)).fetchone()
    if existing:
        conn.close()
        return templates.TemplateResponse(
            "signup.html", {"request": request, "error": "An account with that email already exists."}
        )
    slug = unique_slug(conn, name)
    password_hash = auth.hash_password(password)
    cur = conn.execute(
        "INSERT INTO producers (slug, email, password_hash, name) VALUES (?,?,?,?)",
        (slug, email, password_hash, name),
    )
    producer_id = cur.lastrowid
    conn.commit()
    conn.close()
    request.session["producer_id"] = producer_id
    return RedirectResponse("/admin", status_code=303)


@app.get("/login")
def login_page(request: Request):
    if auth.current_producer(request):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    conn = get_db()
    producer = conn.execute("SELECT * FROM producers WHERE email = ?", (email,)).fetchone()
    conn.close()
    if not producer or not auth.verify_password(password, producer["password_hash"]):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Incorrect email or password."}
        )
    request.session["producer_id"] = producer["id"]
    return RedirectResponse("/admin", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# Public feed - this is the link each producer shares on Instagram
# ---------------------------------------------------------------------------
@app.get("/p/{slug}")
def feed(request: Request, slug: str):
    conn = get_db()
    producer = conn.execute("SELECT * FROM producers WHERE slug = ?", (slug,)).fetchone()
    if not producer:
        conn.close()
        raise HTTPException(404, "Producer not found")
    beats = conn.execute(
        "SELECT * FROM beats WHERE producer_id = ? ORDER BY created_at DESC", (producer["id"],)
    ).fetchall()
    conn.close()
    return templates.TemplateResponse(
        "feed.html", {"request": request, "producer": producer, "beats": beats}
    )


@app.get("/p/{slug}/beat/{beat_id}")
def beat_detail(request: Request, slug: str, beat_id: int):
    conn = get_db()
    producer = conn.execute("SELECT * FROM producers WHERE slug = ?", (slug,)).fetchone()
    if not producer:
        conn.close()
        raise HTTPException(404, "Producer not found")
    beat = conn.execute(
        "SELECT * FROM beats WHERE id = ? AND producer_id = ?", (beat_id, producer["id"])
    ).fetchone()
    conn.close()
    if not beat:
        raise HTTPException(404, "Beat not found")
    return templates.TemplateResponse(
        "beat.html", {"request": request, "beat": beat, "producer": producer}
    )


# ---------------------------------------------------------------------------
# Admin - each producer only ever sees and edits their own data. Every route
# below depends on auth.require_producer, which 303-redirects to /login if
# there's no valid session, and every query is scoped by that producer's id
# so one producer can never read or modify another's beats/orders/payout info.
# ---------------------------------------------------------------------------
@app.get("/admin")
def admin_page(request: Request, producer=Depends(auth.require_producer)):
    conn = get_db()
    beats = conn.execute(
        "SELECT * FROM beats WHERE producer_id = ? ORDER BY created_at DESC", (producer["id"],)
    ).fetchall()
    orders = conn.execute(
        """SELECT orders.*, beats.title as beat_title FROM orders
           JOIN beats ON beats.id = orders.beat_id
           WHERE beats.producer_id = ?
           ORDER BY orders.created_at DESC LIMIT 20""",
        (producer["id"],),
    ).fetchall()
    totals = conn.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN orders.payout_status='unpaid' AND orders.status='completed' THEN orders.producer_payout ELSE 0 END), 0) as owed,
             COALESCE(SUM(CASE WHEN orders.status='completed' THEN orders.platform_fee ELSE 0 END), 0) as your_earnings,
             COALESCE(SUM(CASE WHEN orders.status='completed' THEN orders.amount ELSE 0 END), 0) as total_sales
           FROM orders JOIN beats ON beats.id = orders.beat_id
           WHERE beats.producer_id = ?""",
        (producer["id"],),
    ).fetchone()
    conn.close()
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "producer": producer,
            "beats": beats,
            "orders": orders,
            "totals": totals,
        },
    )


@app.post("/admin/profile")
def update_profile(
    name: str = Form(...),
    bio: str = Form(""),
    phone: str = Form(""),
    payout_phone: str = Form(""),
    commission_rate: float = Form(10.0),
    profile_photo: UploadFile = File(None),
    producer=Depends(auth.require_producer),
):
    commission_rate = max(0.0, min(100.0, commission_rate))
    conn = get_db()
    if profile_photo and profile_photo.filename:
        photo_path = save_upload(profile_photo, "covers")
        conn.execute(
            "UPDATE producers SET name=?, bio=?, phone=?, payout_phone=?, commission_rate=?, profile_photo=? WHERE id=?",
            (name, bio, phone, payout_phone, commission_rate, photo_path, producer["id"]),
        )
    else:
        conn.execute(
            "UPDATE producers SET name=?, bio=?, phone=?, payout_phone=?, commission_rate=? WHERE id=?",
            (name, bio, phone, payout_phone, commission_rate, producer["id"]),
        )
    conn.commit()
    conn.close()
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# Payouts - track what the producer is owed and mark it paid once you've
# sent it (manually, via M-Pesa send-money, till, or however). Automating
# this via Daraja's B2C API is a natural next step once that's approved.
# ---------------------------------------------------------------------------
@app.post("/admin/payout/{order_id}/mark_paid")
def mark_payout_paid(order_id: int, producer=Depends(auth.require_producer)):
    conn = get_db()
    conn.execute(
        """UPDATE orders SET payout_status='paid_out', payout_at=datetime('now')
           WHERE id=? AND status='completed'
           AND beat_id IN (SELECT id FROM beats WHERE producer_id=?)""",
        (order_id, producer["id"]),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/beat")
def upload_beat(
    title: str = Form(...),
    genre: str = Form(""),
    bpm: int = Form(None),
    price: int = Form(...),
    cover: UploadFile = File(...),
    audio: UploadFile = File(...),
    producer=Depends(auth.require_producer),
):
    cover_path = save_upload(cover, "covers")
    audio_path = save_upload(audio, "audio")
    conn = get_db()
    conn.execute(
        "INSERT INTO beats (producer_id, title, genre, bpm, price, cover_path, audio_path) VALUES (?,?,?,?,?,?,?)",
        (producer["id"], title, genre, bpm, price, cover_path, audio_path),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/beat/{beat_id}/delete")
def delete_beat(beat_id: int, producer=Depends(auth.require_producer)):
    conn = get_db()
    conn.execute("DELETE FROM beats WHERE id = ? AND producer_id = ?", (beat_id, producer["id"]))
    conn.commit()
    conn.close()
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# Checkout - STK push
# ---------------------------------------------------------------------------
@app.post("/checkout/{beat_id}")
def checkout(beat_id: int, phone: str = Form(...)):
    conn = get_db()
    beat = conn.execute("SELECT * FROM beats WHERE id = ?", (beat_id,)).fetchone()
    if not beat:
        conn.close()
        raise HTTPException(404, "Beat not found")

    result = mpesa.stk_push(
        phone=phone,
        amount=beat["price"],
        account_ref=f"BEAT{beat_id}",
        description=beat["title"],
    )

    cur = conn.execute(
        "INSERT INTO orders (beat_id, buyer_phone, amount, checkout_request_id) VALUES (?,?,?,?)",
        (beat_id, phone, beat["price"], result.get("checkout_request_id")),
    )
    order_id = cur.lastrowid
    conn.commit()
    conn.close()

    if result.get("simulated"):
        # Auto-complete the "payment" after a couple of seconds in a
        # background thread so the demo flow works with zero setup.
        threading.Thread(target=_simulate_confirm, args=(order_id,), daemon=True).start()

    return JSONResponse({"order_id": order_id})


def _apply_split(conn, order_id: int, amount: int, producer_id: int):
    """Calculate and lock in the platform fee / producer payout for an order,
    using the commission_rate set on the producer at the time of payment.
    Also stamps an unguessable download_token so /download can't be
    enumerated via sequential order ids."""
    producer = conn.execute("SELECT commission_rate FROM producers WHERE id = ?", (producer_id,)).fetchone()
    rate = producer["commission_rate"] if producer else 10.0
    platform_fee = round(amount * rate / 100)
    producer_payout = amount - platform_fee
    token = secrets.token_urlsafe(24)
    conn.execute(
        "UPDATE orders SET platform_fee=?, producer_payout=?, download_token=? WHERE id=?",
        (platform_fee, producer_payout, token, order_id),
    )


def _simulate_confirm(order_id: int):
    time.sleep(3)
    conn = get_db()
    order = conn.execute(
        """SELECT orders.amount, beats.producer_id FROM orders
           JOIN beats ON beats.id = orders.beat_id WHERE orders.id = ?""",
        (order_id,),
    ).fetchone()
    conn.execute(
        "UPDATE orders SET status='completed', mpesa_receipt=? WHERE id=?",
        (f"SIM{order_id}RECEIPT", order_id),
    )
    if order:
        _apply_split(conn, order_id, order["amount"], order["producer_id"])
    conn.commit()
    conn.close()


@app.get("/order/{order_id}/status")
def order_status(order_id: int):
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    conn.close()
    if not order:
        raise HTTPException(404, "Order not found")
    resp = {"status": order["status"], "order_id": order_id}
    if order["status"] == "completed":
        resp["download_token"] = order["download_token"]
    return resp


@app.post("/mpesa/callback")
async def mpesa_callback(request: Request):
    """Real Daraja hits this once SIMULATE = False. Adjust field parsing
    if you change the payload shape; this matches the standard STK push
    callback structure."""
    payload = await request.json()
    try:
        stk = payload["Body"]["stkCallback"]
        checkout_id = stk["CheckoutRequestID"]
        result_code = stk["ResultCode"]
    except (KeyError, TypeError):
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    conn = get_db()
    if result_code == 0:
        receipt = None
        for item in stk.get("CallbackMetadata", {}).get("Item", []):
            if item.get("Name") == "MpesaReceiptNumber":
                receipt = item.get("Value")
        conn.execute(
            "UPDATE orders SET status='completed', mpesa_receipt=? WHERE checkout_request_id=?",
            (receipt, checkout_id),
        )
        row = conn.execute(
            """SELECT orders.id, orders.amount, beats.producer_id FROM orders
               JOIN beats ON beats.id = orders.beat_id
               WHERE orders.checkout_request_id=?""",
            (checkout_id,),
        ).fetchone()
        if row:
            _apply_split(conn, row["id"], row["amount"], row["producer_id"])
    else:
        conn.execute(
            "UPDATE orders SET status='failed' WHERE checkout_request_id=?",
            (checkout_id,),
        )
    conn.commit()
    conn.close()
    return {"ResultCode": 0, "ResultDesc": "Accepted"}


@app.get("/download/{token}")
def download(token: str):
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE download_token = ?", (token,)).fetchone()
    if not order or order["status"] != "completed":
        conn.close()
        raise HTTPException(403, "Invalid or expired download link")
    beat = conn.execute("SELECT * FROM beats WHERE id = ?", (order["beat_id"],)).fetchone()
    conn.close()
    file_path = BASE_DIR / beat["audio_path"].lstrip("/")
    return FileResponse(file_path, filename=f"{beat['title']}.mp3", media_type="audio/mpeg")
