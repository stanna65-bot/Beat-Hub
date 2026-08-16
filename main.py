import os, secrets, threading, time, uuid
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException, Depends
from fastapi.responses import RedirectResponse, FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import auth, mpesa
from database import get_db, init_db, unique_slug

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
COVERS = STATIC / "uploads/covers"
AUDIO = STATIC / "uploads/audio"
for p in (COVERS, AUDIO): p.mkdir(parents=True, exist_ok=True)

FEE_RATE = 10.0
ALLOWED_COVERS = {".jpg",".jpeg",".png",".webp"}
ALLOWED_AUDIO = {".mp3",".wav",".m4a"}
MAX_COVER = 10 * 1024 * 1024
MAX_AUDIO = 100 * 1024 * 1024

app = FastAPI(title="Beat Hub")
secret = os.getenv("SESSION_SECRET") or secrets.token_urlsafe(48)
app.add_middleware(SessionMiddleware, secret_key=secret, same_site="lax", https_only=os.getenv("SESSION_HTTPS_ONLY","false").lower()=="true")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
templates = Jinja2Templates(directory=str(BASE/"templates"))
init_db()

def save_file(upload, folder, prefix, allowed, max_bytes):
    if not upload or not upload.filename: raise HTTPException(400, "File is required.")
    ext = Path(upload.filename).suffix.lower()
    if ext not in allowed: raise HTTPException(400, "Unsupported file type.")
    name = uuid.uuid4().hex + ext
    target = folder / name
    total = 0
    with target.open("wb") as f:
        while True:
            chunk = upload.file.read(1024*1024)
            if not chunk: break
            total += len(chunk)
            if total > max_bytes:
                f.close(); target.unlink(missing_ok=True); raise HTTPException(413, "File is too large.")
            f.write(chunk)
    return f"{prefix}/{name}"

def ensure_wallet(conn, pid):
    conn.execute("INSERT OR IGNORE INTO producer_wallets(producer_id) VALUES(?)", (pid,))

def page_error(request, message, code=400):
    return templates.TemplateResponse("home.html", {"request":request,"producer":auth.current_producer(request),"error":message}, status_code=code)

@app.api_route("/health", methods=["GET","HEAD"])
def health(): return Response("OK")

@app.api_route("/", methods=["HEAD"])
def root_head(): return Response(status_code=200)

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request":request,"producer":auth.current_producer(request),"error":None})

@app.get("/terms")
def terms(request: Request):
    return templates.TemplateResponse("terms.html", {"request":request,"producer":auth.current_producer(request)})

@app.get("/signup")
def signup_page(request: Request):
    return RedirectResponse("/admin",303) if auth.current_producer(request) else templates.TemplateResponse("signup.html",{"request":request,"error":None})

@app.post("/signup")
def signup(request: Request, name:str=Form(...), email:str=Form(...), password:str=Form(...)):
    name=name.strip(); email=email.strip().lower()
    if not name or len(password)<8 or "@" not in email: return page_error(request,"Use a name, valid email and password of at least 8 characters.")
    conn=get_db()
    try:
        if conn.execute("SELECT 1 FROM producers WHERE email=?", (email,)).fetchone():
            return templates.TemplateResponse("signup.html",{"request":request,"error":"Email already exists."},status_code=409)
        cur=conn.execute("INSERT INTO producers(slug,email,password_hash,name) VALUES(?,?,?,?)",(unique_slug(conn,name),email,auth.hash_password(password),name))
        pid=cur.lastrowid; ensure_wallet(conn,pid); conn.commit()
    finally: conn.close()
    request.session.clear(); request.session["producer_id"]=pid
    return RedirectResponse("/admin",303)

@app.get("/login")
def login_page(request: Request):
    return RedirectResponse("/admin",303) if auth.current_producer(request) else templates.TemplateResponse("login.html",{"request":request,"error":None})

@app.post("/login")
def login(request: Request,email:str=Form(...),password:str=Form(...)):
    conn=get_db()
    try: p=conn.execute("SELECT * FROM producers WHERE email=?", (email.strip().lower(),)).fetchone()
    finally: conn.close()
    if not p or not auth.verify_password(password,p["password_hash"]):
        return templates.TemplateResponse("login.html",{"request":request,"error":"Incorrect email or password."},status_code=401)
    request.session.clear(); request.session["producer_id"]=p["id"]
    return RedirectResponse("/admin",303)

@app.post("/logout")
def logout(request: Request):
    request.session.clear(); return RedirectResponse("/",303)

@app.get("/p/{slug}")
def feed(request: Request,slug:str):
    conn=get_db()
    try:
        p=conn.execute("SELECT * FROM producers WHERE slug=?",(slug,)).fetchone()
        if not p: raise HTTPException(404,"Producer not found")
        beats=conn.execute("SELECT * FROM beats WHERE producer_id=? ORDER BY is_hot_pick DESC,created_at DESC",(p["id"],)).fetchall()
    finally: conn.close()
    return templates.TemplateResponse("feed.html",{"request":request,"producer":p,"beats":beats})

@app.get("/p/{slug}/beat/{beat_id}")
def beat_detail(request: Request,slug:str,beat_id:int):
    conn=get_db()
    try:
        p=conn.execute("SELECT * FROM producers WHERE slug=?",(slug,)).fetchone()
        beat=conn.execute("SELECT * FROM beats WHERE id=? AND producer_id=?",(beat_id,p["id"] if p else -1)).fetchone()
    finally: conn.close()
    if not p or not beat: raise HTTPException(404,"Beat not found")
    return templates.TemplateResponse("beat.html",{"request":request,"producer":p,"beat":beat})

@app.get("/admin")
def admin(request: Request, producer=Depends(auth.require_producer)):
    conn=get_db()
    try:
        ensure_wallet(conn,producer["id"])
        wallet=conn.execute("SELECT * FROM producer_wallets WHERE producer_id=?",(producer["id"],)).fetchone()
        beats=conn.execute("SELECT * FROM beats WHERE producer_id=? ORDER BY is_hot_pick DESC,created_at DESC",(producer["id"],)).fetchall()
        orders=conn.execute("""SELECT o.*,b.title beat_title FROM orders o JOIN beats b ON b.id=o.beat_id
                               WHERE b.producer_id=? ORDER BY o.created_at DESC LIMIT 50""",(producer["id"],)).fetchall()
        withdrawals=conn.execute("SELECT * FROM withdrawals WHERE producer_id=? ORDER BY requested_at DESC LIMIT 20",(producer["id"],)).fetchall()
        sales=conn.execute("""SELECT COUNT(*) n FROM orders o JOIN beats b ON b.id=o.beat_id
                              WHERE b.producer_id=? AND o.status='completed'""",(producer["id"],)).fetchone()["n"]
        totals={"total_sales":sales,"total_earnings":wallet["total_earnings"],"available_balance":wallet["available_balance"],
                "pending_withdrawal":wallet["pending_withdrawal"],"total_withdrawn":wallet["total_withdrawn"]}
        conn.commit()
    finally: conn.close()
    return templates.TemplateResponse("admin.html",{"request":request,"producer":producer,"wallet":wallet,"totals":totals,"beats":beats,"orders":orders,"withdrawals":withdrawals})

@app.post("/admin/profile")
def profile(name:str=Form(...),bio:str=Form(""),phone:str=Form(""),payout_phone:str=Form(""),producer=Depends(auth.require_producer)):
    if payout_phone:
        try: payout_phone=mpesa.normalize_phone(payout_phone)
        except Exception as e: raise HTTPException(400,str(e))
    conn=get_db()
    try:
        conn.execute("UPDATE producers SET name=?,bio=?,phone=?,payout_phone=? WHERE id=?",(name.strip()[:100],bio.strip()[:2000],phone.strip()[:30],payout_phone,producer["id"])); conn.commit()
    finally: conn.close()
    return RedirectResponse("/admin",303)

@app.post("/admin/beat")
def add_beat(title:str=Form(...),genre:str=Form(""),bpm:int=Form(None),price:int=Form(...),is_hot_pick:str=Form("0"),cover:UploadFile=File(...),audio:UploadFile=File(...),producer=Depends(auth.require_producer)):
    if not title.strip() or not 1<=price<=10_000_000: raise HTTPException(400,"Invalid title or price.")
    if bpm is not None and not 20<=bpm<=400: raise HTTPException(400,"BPM must be 20-400.")
    hot=1 if str(is_hot_pick).lower() in ("1","true","on","yes") else 0
    cp=save_file(cover,COVERS,"/static/uploads/covers",ALLOWED_COVERS,MAX_COVER)
    try:
        ap=save_file(audio,AUDIO,"/static/uploads/audio",ALLOWED_AUDIO,MAX_AUDIO)
        conn=get_db()
        try:
            conn.execute("INSERT INTO beats(producer_id,title,genre,bpm,price,cover_path,audio_path,is_hot_pick) VALUES(?,?,?,?,?,?,?,?)",(producer["id"],title.strip()[:200],genre.strip()[:100],bpm,price,cp,ap,hot)); conn.commit()
        finally: conn.close()
    except Exception:
        (BASE/cp.lstrip("/")).unlink(missing_ok=True); raise
    return RedirectResponse("/admin",303)

@app.post("/admin/beat/{beat_id}/hot-pick")
def hot_pick(beat_id:int,is_hot_pick:str=Form("0"),producer=Depends(auth.require_producer)):
    hot=1 if str(is_hot_pick).lower() in ("1","true","on","yes") else 0
    conn=get_db()
    try:
        r=conn.execute("UPDATE beats SET is_hot_pick=? WHERE id=? AND producer_id=?",(hot,beat_id,producer["id"])); conn.commit()
    finally: conn.close()
    if not r.rowcount: raise HTTPException(404,"Beat not found")
    return RedirectResponse("/admin",303)

def apply_split(conn,order_id):
    order=conn.execute("""SELECT o.*,b.producer_id,b.title FROM orders o JOIN beats b ON b.id=o.beat_id WHERE o.id=?""",(order_id,)).fetchone()
    if not order or order["status"]!="completed" or order["split_applied_at"]: return False
    gross=int(order["amount"]); fee=round(gross*FEE_RATE/100); payout=gross-fee; token=secrets.token_urlsafe(32)
    r=conn.execute("""UPDATE orders SET platform_fee=?,producer_payout=?,commission_rate_locked=?,download_token=?,split_applied_at=CURRENT_TIMESTAMP
                      WHERE id=? AND status='completed' AND split_applied_at IS NULL""",(fee,payout,FEE_RATE,token,order_id))
    if not r.rowcount: return False
    ensure_wallet(conn,order["producer_id"])
    conn.execute("""UPDATE producer_wallets SET available_balance=available_balance+?,total_earnings=total_earnings+?,updated_at=CURRENT_TIMESTAMP WHERE producer_id=?""",(payout,payout,order["producer_id"]))
    conn.execute("INSERT INTO wallet_transactions(producer_id,order_id,transaction_type,amount,reference) VALUES(?,?,?,?,?)",(order["producer_id"],order_id,"sale_credit",payout,f"ORDER-{order_id}"))
    conn.execute("INSERT OR IGNORE INTO platform_ledger(order_id,gross_amount,platform_fee,producer_credit) VALUES(?,?,?,?)",(order_id,gross,fee,payout))
    return True

def complete_order(order_id,receipt):
    conn=get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""UPDATE orders SET status='completed',mpesa_receipt=COALESCE(?,mpesa_receipt),completed_at=COALESCE(completed_at,CURRENT_TIMESTAMP)
                        WHERE id=? AND status IN ('pending','completed')""",(receipt,order_id))
        apply_split(conn,order_id); conn.commit()
    except Exception: conn.rollback(); raise
    finally: conn.close()

@app.post("/checkout/{beat_id}")
def checkout(beat_id:int,phone:str=Form(...)):
    try: phone=mpesa.normalize_phone(phone)
    except Exception as e: raise HTTPException(400,str(e))
    conn=get_db()
    try:
        beat=conn.execute("SELECT * FROM beats WHERE id=?",(beat_id,)).fetchone()
        if not beat: raise HTTPException(404,"Beat not found")
        cur=conn.execute("INSERT INTO orders(beat_id,buyer_phone,amount) VALUES(?,?,?)",(beat_id,phone,beat["price"])); oid=cur.lastrowid; conn.commit()
    finally: conn.close()
    try: result=mpesa.stk_push(phone,beat["price"],f"BEAT{beat_id}",beat["title"])
    except Exception as e:
        conn=get_db(); conn.execute("UPDATE orders SET status='failed',failure_reason=? WHERE id=?",(str(e)[:500],oid)); conn.commit(); conn.close(); raise HTTPException(502,str(e))
    conn=get_db()
    try: conn.execute("UPDATE orders SET checkout_request_id=? WHERE id=?",(result["checkout_request_id"],oid)); conn.commit()
    finally: conn.close()
    if result.get("simulated"): threading.Thread(target=lambda:(time.sleep(2),complete_order(oid,f"MOCK-{oid}")),daemon=True).start()
    return JSONResponse({"order_id":oid,"status":"pending"})

@app.get("/order/{order_id}/status")
def order_status(order_id:int):
    conn=get_db()
    try: o=conn.execute("SELECT status,download_token FROM orders WHERE id=?",(order_id,)).fetchone()
    finally: conn.close()
    if not o: raise HTTPException(404,"Order not found")
    return {"status":o["status"],"download_token":o["download_token"] if o["status"]=="completed" else None}

@app.post("/admin/withdraw")
def withdraw(amount:int=Form(...),producer=Depends(auth.require_producer)):
    if amount<10: raise HTTPException(400,"Minimum withdrawal is KES 10.")
    conn=get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        p=conn.execute("SELECT payout_phone FROM producers WHERE id=?",(producer["id"],)).fetchone()
        if not p["payout_phone"]: raise HTTPException(400,"Add a payout M-Pesa number first.")
        ensure_wallet(conn,producer["id"])
        r=conn.execute("""UPDATE producer_wallets SET available_balance=available_balance-?,pending_withdrawal=pending_withdrawal+?,updated_at=CURRENT_TIMESTAMP
                          WHERE producer_id=? AND available_balance>=?""",(amount,amount,producer["id"],amount))
        if not r.rowcount: raise HTTPException(400,"Insufficient available balance.")
        cur=conn.execute("INSERT INTO withdrawals(producer_id,amount,phone) VALUES(?,?,?)",(producer["id"],amount,p["payout_phone"])); wid=cur.lastrowid
        conn.execute("INSERT INTO wallet_transactions(producer_id,withdrawal_id,transaction_type,amount,reference) VALUES(?,?,?,?,?)",(producer["id"],wid,"withdrawal_requested",-amount,f"WD-{wid}")); conn.commit()
    except Exception: conn.rollback(); raise
    finally: conn.close()
    try: result=mpesa.initiate_producer_payout(p["payout_phone"],amount,f"WD{wid}")
    except Exception as e:
        conn=get_db()
        try:
            conn.execute("BEGIN IMMEDIATE"); conn.execute("UPDATE withdrawals SET status='failed',failure_reason=? WHERE id=?",(str(e)[:500],wid))
            conn.execute("UPDATE producer_wallets SET available_balance=available_balance+?,pending_withdrawal=pending_withdrawal-? WHERE producer_id=?",(amount,amount,producer["id"])); conn.commit()
        finally: conn.close()
        raise HTTPException(502,str(e))
    if result.get("simulated"):
        conn=get_db()
        try:
            conn.execute("BEGIN IMMEDIATE"); conn.execute("UPDATE withdrawals SET status='completed',payout_reference=?,completed_at=CURRENT_TIMESTAMP WHERE id=? AND status='processing'",(result["reference"],wid))
            conn.execute("UPDATE producer_wallets SET pending_withdrawal=pending_withdrawal-?,total_withdrawn=total_withdrawn+? WHERE producer_id=?",(amount,amount,producer["id"])); conn.commit()
        finally: conn.close()
    return RedirectResponse("/admin",303)

@app.get("/download/{token}")
def download(token:str):
    conn=get_db()
    try: row=conn.execute("""SELECT o.status,b.title,b.audio_path FROM orders o JOIN beats b ON b.id=o.beat_id WHERE o.download_token=?""",(token,)).fetchone()
    finally: conn.close()
    if not row or row["status"]!="completed": raise HTTPException(403,"Invalid download link.")
    path=(BASE/row["audio_path"].lstrip("/")).resolve()
    if AUDIO.resolve() not in path.parents or not path.is_file(): raise HTTPException(404,"File unavailable.")
    return FileResponse(str(path),filename=path.name)

@app.post("/mpesa/callback")
async def callback(request:Request):
    return {"ResultCode":0,"ResultDesc":"Accepted"}
