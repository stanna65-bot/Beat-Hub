import hashlib,hmac,os,secrets
from fastapi import HTTPException,Request
from database import get_db

ITERATIONS=300_000
def hash_password(password):
    salt=os.urandom(16)
    digest=hashlib.pbkdf2_hmac("sha256",password.encode(),salt,ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"

def verify_password(password,stored):
    try:
        salt_hex,digest_hex=stored.split("$",1)
        got=hashlib.pbkdf2_hmac("sha256",password.encode(),bytes.fromhex(salt_hex),ITERATIONS).hex()
        return hmac.compare_digest(got,digest_hex)
    except Exception:return False

def current_producer(request):
    pid=request.session.get("producer_id")
    if not pid:return None
    conn=get_db()
    try:return conn.execute("SELECT * FROM producers WHERE id=?",(pid,)).fetchone()
    finally:conn.close()

def require_producer(request):
    p=current_producer(request)
    if not p: raise HTTPException(401,"Login required")
    return p

def is_super_admin(request):
    return bool(request.session.get("super_admin"))

def require_super_admin(request):
    if not is_super_admin(request): raise HTTPException(401,"Super admin login required")

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()

def new_token():
    return secrets.token_urlsafe(32)
