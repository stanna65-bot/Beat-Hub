import os,secrets,threading,time,uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path
from fastapi import FastAPI,Request,Form,UploadFile,File,HTTPException,Depends
from fastapi.responses import RedirectResponse,FileResponse,JSONResponse,Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import auth,mpesa
from database import get_db,init_db,unique_slug

BASE=Path(__file__).resolve().parent; STATIC=BASE/"static"; COVERS=STATIC/"uploads/covers"; AUDIO=STATIC/"uploads/audio"
for p in (COVERS,AUDIO):p.mkdir(parents=True,exist_ok=True)
FEE_RATE=10
ALLOWED_COVERS={".jpg",".jpeg",".png",".webp"}; ALLOWED_AUDIO={".mp3",".wav",".m4a"}
MAX_COVER=10*1024*1024; MAX_AUDIO=100*1024*1024

app=FastAPI(title="BeatHub")
app.add_middleware(SessionMiddleware,secret_key=os.getenv("SESSION_SECRET") or secrets.token_urlsafe(48),same_site="lax",https_only=os.getenv("SESSION_HTTPS_ONLY","false").lower()=="true")
app.mount("/static",StaticFiles(directory=str(STATIC)),name="static")
templates=Jinja2Templates(directory=str(BASE/"templates"))
init_db()

def save_file(upload,folder,prefix,allowed,max_bytes):
    if not upload or not upload.filename:raise HTTPException(400,"File is required.")
    ext=Path(upload.filename).suffix.lower()
    if ext not in allowed:raise HTTPException(400,"Unsupported file type.")
    target=folder/(uuid.uuid4().hex+ext); total=0
    try:
        with target.open("wb") as f:
            while chunk:=upload.file.read(1024*1024):
                total+=len(chunk)
                if total>max_bytes:raise HTTPException(413,"File is too large.")
                f.write(chunk)
    except Exception:
        target.unlink(missing_ok=True);raise
    return f"{prefix}/{target.name}"

def ensure_wallet(c,pid):c.execute("INSERT OR IGNORE INTO producer_wallets(producer_id) VALUES(?)",(pid,))
def admin_phone():
    return mpesa.normalize_phone(os.getenv("SUPER_ADMIN_PAYOUT_PHONE","")) if os.getenv("SUPER_ADMIN_PAYOUT_PHONE") else ""
def render(name,request,**ctx):
    ctx.update(request=request,producer=auth.current_producer(request),super_admin=auth.is_super_admin(request))
    return templates.TemplateResponse(name,ctx)

@app.api_route("/health",methods=["GET","HEAD"])
def health():return Response("OK")
@app.api_route("/",methods=["HEAD"])
def root_head():return Response(status_code=200)
@app.get("/")
def home(request:Request):
    c=get_db()
    try:hot=c.execute("SELECT b.*,p.name producer_name,p.slug producer_slug FROM beats b JOIN producers p ON p.id=b.producer_id WHERE b.is_hot_pick=1 ORDER BY b.created_at DESC LIMIT 8").fetchall()
    finally:c.close()
    return render("home.html",request,error=None,hot_beats=hot)

@app.get("/terms")
def terms(request:Request):return render("terms.html",request)
@app.get("/signup")
def signup_page(request:Request):
    return RedirectResponse("/admin",303) if auth.current_producer(request) else render("signup.html",request,error=None)
@app.post("/signup")
def signup(request:Request,name:str=Form(...),email:str=Form(...),password:str=Form(...)):
    name=name.strip();email=email.strip().lower()
    if not name or len(password)<8 or "@" not in email:return render("signup.html",request,error="Use a name, valid email and password of at least 8 characters.")
    c=get_db()
    try:
        if c.execute("SELECT 1 FROM producers WHERE email=?",(email,)).fetchone():return render("signup.html",request,error="Email already exists.")
        cur=c.execute("INSERT INTO producers(slug,email,password_hash,name) VALUES(?,?,?,?)",(unique_slug(c,name),email,auth.hash_password(password),name));pid=cur.lastrowid;ensure_wallet(c,pid);c.commit()
    finally:c.close()
    request.session.clear();request.session["producer_id"]=pid
    return RedirectResponse("/admin",303)

@app.get("/login")
def login_page(request:Request):
    return RedirectResponse("/admin",303) if auth.current_producer(request) else render("login.html",request,error=None)
@app.post("/login")
def login(request:Request,email:str=Form(...),password:str=Form(...)):
    c=get_db()
    try:p=c.execute("SELECT * FROM producers WHERE email=?",(email.strip().lower(),)).fetchone()
    finally:c.close()
    if not p or not auth.verify_password(password,p["password_hash"]):return render("login.html",request,error="Incorrect email or password.")
    request.session.clear();request.session["producer_id"]=p["id"];return RedirectResponse("/admin",303)
@app.post("/logout")
def logout(request:Request):request.session.clear();return RedirectResponse("/",303)

@app.get("/forgot-password")
def forgot_page(request:Request):return render("forgot_password.html",request,message=None)
@app.post("/forgot-password")
def forgot(request:Request,email:str=Form(...)):
    email=email.strip().lower(); c=get_db(); token=None
    try:
        p=c.execute("SELECT id FROM producers WHERE email=?",(email,)).fetchone()
        if p:
            token=auth.new_token(); expiry=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()
            c.execute("UPDATE password_reset_tokens SET used_at=CURRENT_TIMESTAMP WHERE producer_id=? AND used_at IS NULL",(p["id"],))
            c.execute("INSERT INTO password_reset_tokens(producer_id,token_hash,expires_at) VALUES(?,?,?)",(p["id"],auth.token_hash(token),expiry));c.commit()
    finally:c.close()
    # Email delivery intentionally left configurable; link is never exposed unless development mode is enabled.
    msg="If that account exists, a password reset request has been created."
    if os.getenv("DEV_SHOW_RESET_LINK","false").lower()=="true" and token: msg += f" Development reset link: /reset-password/{token}"
    return render("forgot_password.html",request,message=msg)

@app.get("/reset-password/{token}")
def reset_page(request:Request,token:str):return render("reset_password.html",request,token=token,error=None)
@app.post("/reset-password/{token}")
def reset_password(request:Request,token:str,password:str=Form(...),confirm_password:str=Form(...)):
    if len(password)<8 or password!=confirm_password:return render("reset_password.html",request,token=token,error="Passwords must match and be at least 8 characters.")
    c=get_db()
    try:
        row=c.execute("SELECT * FROM password_reset_tokens WHERE token_hash=? AND used_at IS NULL ORDER BY id DESC LIMIT 1",(auth.token_hash(token),)).fetchone()
        if not row or datetime.fromisoformat(row["expires_at"]).replace(tzinfo=timezone.utc)<datetime.now(timezone.utc):return render("reset_password.html",request,token=token,error="This reset link is invalid or expired.")
        c.execute("UPDATE producers SET password_hash=? WHERE id=?",(auth.hash_password(password),row["producer_id"]))
        c.execute("UPDATE password_reset_tokens SET used_at=CURRENT_TIMESTAMP WHERE id=?",(row["id"],));c.commit()
    finally:c.close()
    return RedirectResponse("/login",303)

@app.get("/p/{slug}")
def feed(request:Request,slug:str):
    c=get_db()
    try:
        p=c.execute("SELECT * FROM producers WHERE slug=?",(slug,)).fetchone()
        if not p:raise HTTPException(404,"Producer not found")
        beats=c.execute("SELECT * FROM beats WHERE producer_id=? ORDER BY is_hot_pick DESC,created_at DESC",(p["id"],)).fetchall()
    finally:c.close()
    return render("feed.html",request,producer=p,beats=beats)

@app.get("/p/{slug}/beat/{beat_id}")
def beat_detail(request:Request,slug:str,beat_id:int):
    c=get_db()
    try:
        p=c.execute("SELECT * FROM producers WHERE slug=?",(slug,)).fetchone()
        beat=c.execute("SELECT * FROM beats WHERE id=? AND producer_id=?",(beat_id,p["id"] if p else -1)).fetchone()
    finally:c.close()
    if not p or not beat:raise HTTPException(404,"Beat not found")
    return render("beat.html",request,producer=p,beat=beat)

@app.get("/admin")
def admin(request:Request,producer=Depends(auth.require_producer)):
    c=get_db()
    try:
        ensure_wallet(c,producer["id"]);wallet=c.execute("SELECT * FROM producer_wallets WHERE producer_id=?",(producer["id"],)).fetchone()
        beats=c.execute("SELECT * FROM beats WHERE producer_id=? ORDER BY is_hot_pick DESC,created_at DESC",(producer["id"],)).fetchall()
        orders=c.execute("SELECT o.*,b.title beat_title FROM orders o JOIN beats b ON b.id=o.beat_id WHERE b.producer_id=? ORDER BY o.created_at DESC LIMIT 50",(producer["id"],)).fetchall()
        withdrawals=c.execute("SELECT * FROM withdrawals WHERE producer_id=? ORDER BY requested_at DESC LIMIT 30",(producer["id"],)).fetchall()
        totals={"total_sales":sum(1 for o in orders if o["status"]=="completed"),"total_earnings":wallet["total_earnings"],"available_balance":wallet["available_balance"],"pending_withdrawal":wallet["pending_withdrawal"],"total_withdrawn":wallet["total_withdrawn"]}
    finally:c.close()
    return render("admin.html",request,wallet=wallet,totals=totals,beats=beats,orders=orders,withdrawals=withdrawals)

@app.post("/admin/profile")
def profile(name:str=Form(...),bio:str=Form(""),phone:str=Form(""),payout_phone:str=Form(""),producer=Depends(auth.require_producer)):
    payout=mpesa.normalize_phone(payout_phone) if payout_phone.strip() else ""
    c=get_db()
    try:c.execute("UPDATE producers SET name=?,bio=?,phone=?,payout_phone=? WHERE id=?",(name.strip()[:100],bio.strip()[:2000],phone.strip()[:30],payout,producer["id"]));c.commit()
    finally:c.close()
    return RedirectResponse("/admin",303)

@app.post("/admin/beat")
def add_beat(title:str=Form(...),genre:str=Form(""),bpm:str=Form(""),price:int=Form(...),is_hot_pick:str=Form("0"),cover:UploadFile=File(...),audio:UploadFile=File(...),producer=Depends(auth.require_producer)):
    if not title.strip() or not 1<=price<=10_000_000:raise HTTPException(400,"Invalid title or price.")
    bpm=bpm.strip(); bpm_value=None
    if bpm:
        try:bpm_value=int(bpm)
        except ValueError:raise HTTPException(400,"BPM must be a whole number.")
        if not 20<=bpm_value<=400:raise HTTPException(400,"BPM must be between 20 and 400.")
    hot=1 if str(is_hot_pick).lower() in ("1","true","on","yes") else 0
    cp=save_file(cover,COVERS,"/static/uploads/covers",ALLOWED_COVERS,MAX_COVER)
    try:ap=save_file(audio,AUDIO,"/static/uploads/audio",ALLOWED_AUDIO,MAX_AUDIO)
    except Exception:(BASE/cp.lstrip("/")).unlink(missing_ok=True);raise
    c=get_db()
    try:c.execute("INSERT INTO beats(producer_id,title,genre,bpm,price,cover_path,audio_path,is_hot_pick) VALUES(?,?,?,?,?,?,?,?)",(producer["id"],title.strip()[:200],genre.strip()[:100],bpm_value,price,cp,ap,hot));c.commit()
    finally:c.close()
    return RedirectResponse("/admin",303)

@app.post("/admin/beat/{beat_id}/hot-pick")
def hot_pick(beat_id:int,is_hot_pick:str=Form("0"),producer=Depends(auth.require_producer)):
    hot=1 if str(is_hot_pick).lower() in ("1","true","on","yes") else 0;c=get_db()
    try:r=c.execute("UPDATE beats SET is_hot_pick=? WHERE id=? AND producer_id=?",(hot,beat_id,producer["id"]));c.commit()
    finally:c.close()
    if not r.rowcount:raise HTTPException(404,"Beat not found")
    return RedirectResponse("/admin",303)

def apply_split(c,order_id):
    o=c.execute("SELECT o.*,b.producer_id FROM orders o JOIN beats b ON b.id=o.beat_id WHERE o.id=?",(order_id,)).fetchone()
    if not o or o["status"]!="completed" or o["split_applied_at"]:return False
    gross=int(o["amount"]);fee=round(gross*FEE_RATE/100);net=gross-fee;token=secrets.token_urlsafe(32)
    r=c.execute("UPDATE orders SET platform_fee=?,producer_payout=?,commission_rate_locked=?,download_token=?,split_applied_at=CURRENT_TIMESTAMP WHERE id=? AND split_applied_at IS NULL",(fee,net,FEE_RATE,token,order_id))
    if not r.rowcount:return False
    ensure_wallet(c,o["producer_id"])
    c.execute("UPDATE producer_wallets SET available_balance=available_balance+?,total_earnings=total_earnings+?,updated_at=CURRENT_TIMESTAMP WHERE producer_id=?",(net,net,o["producer_id"]))
    c.execute("UPDATE platform_wallet SET available_balance=available_balance+?,total_earnings=total_earnings+?,updated_at=CURRENT_TIMESTAMP WHERE id=1",(fee,fee))
    c.execute("INSERT OR IGNORE INTO platform_ledger(order_id,gross_amount,platform_fee,producer_credit) VALUES(?,?,?,?)",(order_id,gross,fee,net))
    return True

def complete_order(order_id,receipt):
    c=get_db()
    try:
        c.execute("BEGIN IMMEDIATE");c.execute("UPDATE orders SET status='completed',mpesa_receipt=COALESCE(?,mpesa_receipt),completed_at=COALESCE(completed_at,CURRENT_TIMESTAMP) WHERE id=? AND status IN ('pending','completed')",(receipt,order_id));apply_split(c,order_id);c.commit()
    except Exception:c.rollback();raise
    finally:c.close()

@app.post("/checkout/{beat_id}")
def checkout(beat_id:int,phone:str=Form(...)):
    phone=mpesa.normalize_phone(phone);c=get_db()
    try:
        beat=c.execute("SELECT * FROM beats WHERE id=?",(beat_id,)).fetchone()
        if not beat:raise HTTPException(404,"Beat not found")
        oid=c.execute("INSERT INTO orders(beat_id,buyer_phone,amount) VALUES(?,?,?)",(beat_id,phone,beat["price"])).lastrowid;c.commit()
    finally:c.close()
    try:r=mpesa.stk_push(phone,beat["price"],f"BEAT{beat_id}",beat["title"])
    except Exception as e:
        c=get_db()
        try:c.execute("UPDATE orders SET status='failed',failure_reason=? WHERE id=?",(str(e)[:500],oid));c.commit()
        finally:c.close()
        raise HTTPException(502,str(e))
    c=get_db()
    try:c.execute("UPDATE orders SET checkout_request_id=? WHERE id=?",(r["checkout_request_id"],oid));c.commit()
    finally:c.close()
    if r.get("simulated"):threading.Thread(target=lambda:(time.sleep(2),complete_order(oid,f"MOCK-{oid}")),daemon=True).start()
    return JSONResponse({"order_id":oid,"status":"pending"})

@app.get("/order/{order_id}/status")
def order_status(order_id:int):
    c=get_db()
    try:o=c.execute("SELECT status,download_token FROM orders WHERE id=?",(order_id,)).fetchone()
    finally:c.close()
    if not o:raise HTTPException(404,"Order not found")
    return {"status":o["status"],"download_token":o["download_token"] if o["status"]=="completed" else None}

def request_producer_withdrawal(c,pid,amount,phone):
    c.execute("BEGIN IMMEDIATE");r=c.execute("UPDATE producer_wallets SET available_balance=available_balance-?,pending_withdrawal=pending_withdrawal+? WHERE producer_id=? AND available_balance>=?",(amount,amount,pid,amount))
    if not r.rowcount:raise HTTPException(400,"Insufficient available balance.")
    wid=c.execute("INSERT INTO withdrawals(producer_id,amount,phone,status) VALUES(?,?,?,'pending')",(pid,amount,phone)).lastrowid;c.commit();return wid

@app.post("/admin/withdraw")
def withdraw(amount:int=Form(...),producer=Depends(auth.require_producer)):
    if amount<10:raise HTTPException(400,"Minimum withdrawal is KES 10.")
    c=get_db()
    try:
        p=c.execute("SELECT payout_phone FROM producers WHERE id=?",(producer["id"],)).fetchone()
        if not p["payout_phone"]:raise HTTPException(400,"Add a payout M-Pesa number first.")
        wid=request_producer_withdrawal(c,producer["id"],amount,p["payout_phone"])
    except Exception:
        try:c.rollback()
        except:pass
        raise
    finally:c.close()
    try:r=mpesa.initiate_producer_payout(p["payout_phone"],amount,f"WD{wid}")
    except Exception as e:
        c=get_db()
        try:c.execute("BEGIN IMMEDIATE");c.execute("UPDATE withdrawals SET status='failed',failure_reason=? WHERE id=?",(str(e)[:500],wid));c.execute("UPDATE producer_wallets SET available_balance=available_balance+?,pending_withdrawal=pending_withdrawal-? WHERE producer_id=?",(amount,amount,producer["id"]));c.commit()
        finally:c.close()
        raise HTTPException(502,str(e))
    if r.get("simulated"):
        c=get_db()
        try:c.execute("BEGIN IMMEDIATE");c.execute("UPDATE withdrawals SET status='completed',payout_reference=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",(r["reference"],wid));c.execute("UPDATE producer_wallets SET pending_withdrawal=pending_withdrawal-?,total_withdrawn=total_withdrawn+? WHERE producer_id=?",(amount,amount,producer["id"]));c.commit()
        finally:c.close()
    return RedirectResponse("/admin",303)

@app.get("/super-admin/login")
def super_login_page(request:Request):return render("super_admin_login.html",request,error=None)
@app.post("/super-admin/login")
def super_login(request:Request,username:str=Form(...),password:str=Form(...)):
    good=secrets.compare_digest(username,os.getenv("SUPER_ADMIN_USERNAME","admin")) and secrets.compare_digest(password,os.getenv("SUPER_ADMIN_PASSWORD",""))
    if not good:return render("super_admin_login.html",request,error="Invalid credentials.")
    request.session["super_admin"]=True;return RedirectResponse("/super-admin",303)
@app.post("/super-admin/logout")
def super_logout(request:Request):request.session.pop("super_admin",None);return RedirectResponse("/",303)

@app.get("/super-admin")
def super_admin(request:Request):
    auth.require_super_admin(request);c=get_db()
    try:
        wallet=c.execute("SELECT * FROM platform_wallet WHERE id=1").fetchone()
        recent=c.execute("SELECT pl.*,o.mpesa_receipt,b.title,p.name producer_name FROM platform_ledger pl JOIN orders o ON o.id=pl.order_id JOIN beats b ON b.id=o.beat_id JOIN producers p ON p.id=b.producer_id ORDER BY pl.created_at DESC LIMIT 100").fetchall()
        withdrawals=c.execute("SELECT * FROM platform_withdrawals ORDER BY requested_at DESC LIMIT 50").fetchall()
        totals={"gross_sales":sum(x["gross_amount"] for x in recent),"platform_earnings":wallet["total_earnings"],"available_balance":wallet["available_balance"],"pending_withdrawal":wallet["pending_withdrawal"],"total_withdrawn":wallet["total_withdrawn"]}
    finally:c.close()
    return render("super_admin.html",request,wallet=wallet,totals=totals,recent=recent,withdrawals=withdrawals,payout_phone=admin_phone())

@app.post("/super-admin/withdraw")
def super_withdraw(request:Request,amount:int=Form(...)):
    auth.require_super_admin(request)
    if amount<10:raise HTTPException(400,"Minimum withdrawal is KES 10.")
    phone=admin_phone()
    if not phone:raise HTTPException(400,"SUPER_ADMIN_PAYOUT_PHONE is not configured.")
    c=get_db()
    try:
        c.execute("BEGIN IMMEDIATE");r=c.execute("UPDATE platform_wallet SET available_balance=available_balance-?,pending_withdrawal=pending_withdrawal+? WHERE id=1 AND available_balance>=?",(amount,amount,amount))
        if not r.rowcount:raise HTTPException(400,"Insufficient platform balance.")
        wid=c.execute("INSERT INTO platform_withdrawals(amount,phone,status) VALUES(?,?, 'pending')",(amount,phone)).lastrowid;c.commit()
    except Exception:
        try:c.rollback()
        except:pass
        raise
    finally:c.close()
    try:r=mpesa.initiate_platform_payout(phone,amount,f"ADMINWD{wid}")
    except Exception as e:
        c=get_db()
        try:c.execute("BEGIN IMMEDIATE");c.execute("UPDATE platform_withdrawals SET status='failed',failure_reason=? WHERE id=?",(str(e)[:500],wid));c.execute("UPDATE platform_wallet SET available_balance=available_balance+?,pending_withdrawal=pending_withdrawal-? WHERE id=1",(amount,amount));c.commit()
        finally:c.close()
        raise HTTPException(502,str(e))
    if r.get("simulated"):
        c=get_db()
        try:c.execute("BEGIN IMMEDIATE");c.execute("UPDATE platform_withdrawals SET status='completed',payout_reference=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",(r["reference"],wid));c.execute("UPDATE platform_wallet SET pending_withdrawal=pending_withdrawal-?,total_withdrawn=total_withdrawn+? WHERE id=1",(amount,amount));c.commit()
        finally:c.close()
    return RedirectResponse("/super-admin",303)

@app.get("/download/{token}")
def download(token:str):
    c=get_db()
    try:r=c.execute("SELECT o.status,b.audio_path FROM orders o JOIN beats b ON b.id=o.beat_id WHERE o.download_token=?",(token,)).fetchone()
    finally:c.close()
    if not r or r["status"]!="completed":raise HTTPException(403,"Invalid download link.")
    path=(BASE/r["audio_path"].lstrip("/")).resolve()
    if AUDIO.resolve() not in path.parents or not path.is_file():raise HTTPException(404,"File unavailable.")
    return FileResponse(str(path),filename=path.name)

@app.post("/mpesa/callback")
async def callback(request:Request):
    return {"ResultCode":0,"ResultDesc":"Callback endpoint reserved for the live Safaricom integration."}
