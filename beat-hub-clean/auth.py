import hashlib, hmac, os
from fastapi import HTTPException, Request
from database import get_db

def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex() + "$" + digest.hex()

def verify_password(password, stored):
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False

def current_producer(request: Request):
    pid = request.session.get("producer_id")
    if not pid:
        return None
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM producers WHERE id=?", (pid,)).fetchone()
    finally:
        conn.close()

def require_producer(request: Request):
    producer = current_producer(request)
    if not producer:
        raise HTTPException(status_code=401, detail="Login required")
    return producer
