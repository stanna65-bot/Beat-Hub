import os,secrets,threading,time,uuid,smtplib,ssl,re,sqlite3
from datetime import datetime,timedelta,timezone,date,time as dtime
from email.message import EmailMessage
from pathlib import Path
from fastapi import FastAPI,Request,Form,UploadFile,File,HTTPException,Depends
from fastapi.responses import RedirectResponse,FileResponse,JSONResponse,Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import auth,mpesa
from database import get_db,init_db,unique_slug
BASE=Path(__file__).resolve().parent; STATIC=BASE/'static'; COVERS=STATIC/'uploads/covers'; AUDIO=STATIC/'uploads/audio'
for p in(COVERS,AUDIO):p.mkdir(parents=True,exist_ok=True)
FEE_RATE=max(0,min(100,int(os.getenv('PLATFORM_COMMISSION_RATE','10'))))
app=FastAPI(title='BeatHub - The Home of Beats'); app.add_middleware(SessionMiddleware,secret_key=os.getenv('SESSION_SECRET') or secrets.token_urlsafe(48),same_site='lax',https_only=os.getenv('SESSION_HTTPS_ONLY','false').lower()=='true');app.mount('/static',StaticFiles(directory=str(STATIC)),name='static');templates=Jinja2Templates(directory=str(BASE/'templates'));init_db()
def render(n,r,**k):k.update(request=r,producer=auth.current_producer(r),super_admin=auth.is_super_admin(r));return templates.TemplateResponse(n,k)
def now():return datetime.now(timezone.utc)
def iso(dt):return dt.astimezone(timezone.utc).isoformat()
def parse_iso(v):return datetime.fromisoformat(v.replace('Z','+00:00')).astimezone(timezone.utc)
def ensure_wallet(c,pid):c.execute('INSERT OR IGNORE INTO producer_wallets(producer_id) VALUES(?)',(pid,))
def app_url(r):return os.getenv('APP_BASE_URL','').rstrip('/') or str(r.base_url).rstrip('/')
def send_reset(to,url):
 h=os.getenv('SMTP_HOST','');u=os.getenv('SMTP_USERNAME','');pw=os.getenv('SMTP_PASSWORD','');fr=os.getenv('SMTP_FROM','')
 if not all((h,u,pw,fr)):raise RuntimeError('Email is not configured.')
 m=EmailMessage();m['Subject']='Reset your BeatHub password';m['From']=fr;m['To']=to;m.set_content(f'Use this secure link to reset your BeatHub password. It expires in 30 minutes:\n\n{url}')
 port=int(os.getenv('SMTP_PORT','587'))
 with smtplib.SMTP(h,port,timeout=20) as s:s.starttls(context=ssl.create_default_context());s.login(u,pw);s.send_message(m)
def save_file(up,folder,prefix,allowed,maxb):
 if not up or not up.filename:raise HTTPException(400,'File is required.')
 ext=Path(up.filename).suffix.lower()
 if ext not in allowed:raise HTTPException(400,'Unsupported file type.')
 path=folder/(uuid.uuid4().hex+ext);n=0
 try:
  with path.open('wb') as f:
   while True:
    ch=up.file.read(1024*1024)
    if not ch:break
    n+=len(ch)
    if n>maxb:raise HTTPException(413,'File too large.')
    f.write(ch)
 except Exception:path.unlink(missing_ok=True);raise
 return prefix+'/'+path.name
@app.api_route('/health',methods=['GET','HEAD'])
def health():return Response('OK')
@app.api_route('/',methods=['HEAD'])
def head():return Response(status_code=200)
@app.get('/')
def home(r:Request):
 c=get_db()
 try:
  hot=c.execute('SELECT b.*,p.name producer_name,p.slug producer_slug FROM beats b JOIN producers p ON p.id=b.producer_id WHERE b.is_hot_pick=1 ORDER BY b.created_at DESC LIMIT 8').fetchall(); services=c.execute("SELECT s.*,p.name producer_name,p.slug producer_slug FROM session_services s JOIN producers p ON p.id=s.producer_id WHERE s.active=1 ORDER BY s.created_at DESC LIMIT 6").fetchall()
 finally:c.close()
 return render('home.html',r,hot_beats=hot,services=services)
@app.get('/terms')
def terms(r:Request):return render('terms.html',r)
@app.get('/signup')
def signup_page(r:Request):return RedirectResponse('/admin',303) if auth.current_producer(r) else render('signup.html',r,error=None)
@app.post('/signup')
def signup(r:Request,name:str=Form(...),email:str=Form(...),password:str=Form(...)):
 name=name.strip();email=email.strip().lower()
 if not name or len(password)<8 or '@' not in email:return render('signup.html',r,error='Use a valid email and a password of at least 8 characters.')
 c=get_db()
 try:
  if c.execute('SELECT 1 FROM producers WHERE email=?',(email,)).fetchone():return render('signup.html',r,error='Email already exists.')
  pid=c.execute('INSERT INTO producers(slug,email,password_hash,name) VALUES(?,?,?,?)',(unique_slug(c,name),email,auth.hash_password(password),name)).lastrowid;ensure_wallet(c,pid);c.commit()
 finally:c.close()
 r.session.clear();r.session['producer_id']=pid;return RedirectResponse('/admin',303)
@app.get('/login')
def login_page(r:Request):return RedirectResponse('/admin',303) if auth.current_producer(r) else render('login.html',r,error=None)
@app.post('/login')
def login(r:Request,email:str=Form(...),password:str=Form(...)):
 c=get_db()
 try:p=c.execute('SELECT * FROM producers WHERE email=?',(email.strip().lower(),)).fetchone()
 finally:c.close()
 if not p or not auth.verify_password(password,p['password_hash']):return render('login.html',r,error='Incorrect email or password.')
 r.session.clear();r.session['producer_id']=p['id'];return RedirectResponse('/admin',303)
@app.post('/logout')
def logout(r:Request):r.session.clear();return RedirectResponse('/',303)
@app.get('/forgot-password')
def forgot_page(r:Request):return render('forgot_password.html',r,error=None,message=None)
@app.post('/forgot-password')
def forgot(r:Request,email:str=Form(...)):
 email=email.strip().lower();msg='If an account exists for that email, a reset link has been sent.';token=None;p=None;c=get_db()
 try:
  p=c.execute('SELECT id,email FROM producers WHERE email=?',(email,)).fetchone()
  if p:
   token=auth.new_token();c.execute('UPDATE password_reset_tokens SET used_at=CURRENT_TIMESTAMP WHERE producer_id=? AND used_at IS NULL',(p['id'],));c.execute('INSERT INTO password_reset_tokens(producer_id,token_hash,expires_at) VALUES(?,?,?)',(p['id'],auth.token_hash(token),iso(now()+timedelta(minutes=30))));c.commit()
 finally:c.close()
 if p:
  try:send_reset(p['email'],app_url(r)+'/reset-password/'+token)
  except Exception:return render('forgot_password.html',r,error='Reset email could not be sent. Please try again later.',message=None)
 return render('forgot_password.html',r,error=None,message=msg)
@app.get('/reset-password/{token}')
def reset_page(r:Request,token:str):return render('reset_password.html',r,token=token,error=None)
@app.post('/reset-password/{token}')
def reset(r:Request,token:str,password:str=Form(...),confirm_password:str=Form(...)):
 if len(password)<8 or password!=confirm_password:return render('reset_password.html',r,token=token,error='Passwords must match and be at least 8 characters.')
 c=get_db()
 try:
  x=c.execute('SELECT * FROM password_reset_tokens WHERE token_hash=? AND used_at IS NULL',(auth.token_hash(token),)).fetchone()
  if not x or parse_iso(x['expires_at'])<now():return render('reset_password.html',r,token=token,error='This reset link is invalid or expired.')
  c.execute('UPDATE producers SET password_hash=? WHERE id=?',(auth.hash_password(password),x['producer_id']));c.execute('UPDATE password_reset_tokens SET used_at=CURRENT_TIMESTAMP WHERE id=?',(x['id'],));c.commit()
 finally:c.close()
 return RedirectResponse('/login',303)
@app.get('/p/{slug}')
def feed(r:Request,slug:str):
 c=get_db()
 try:
  p=c.execute('SELECT * FROM producers WHERE slug=?',(slug,)).fetchone()
  if not p:raise HTTPException(404,'Producer not found')
  beats=c.execute('SELECT * FROM beats WHERE producer_id=? ORDER BY is_hot_pick DESC,created_at DESC',(p['id'],)).fetchall();services=c.execute('SELECT * FROM session_services WHERE producer_id=? AND active=1 ORDER BY created_at DESC',(p['id'],)).fetchall()
 finally:c.close()
 return render('feed.html',r,profile=p,beats=beats,services=services)
@app.get('/p/{slug}/beat/{beat_id}')
def beat(r:Request,slug:str,beat_id:int):
 c=get_db()
 try:p=c.execute('SELECT * FROM producers WHERE slug=?',(slug,)).fetchone();b=c.execute('SELECT * FROM beats WHERE id=?',(beat_id,)).fetchone()
 finally:c.close()
 if not p or not b or b['producer_id']!=p['id']:raise HTTPException(404,'Beat not found')
 return render('beat.html',r,profile=p,beat=b)
@app.get('/admin')
def admin(r:Request,producer=Depends(auth.require_producer)):
 c=get_db()
 try:
  ensure_wallet(c,producer['id']);w=c.execute('SELECT * FROM producer_wallets WHERE producer_id=?',(producer['id'],)).fetchone();beats=c.execute('SELECT * FROM beats WHERE producer_id=? ORDER BY created_at DESC',(producer['id'],)).fetchall();services=c.execute('SELECT * FROM session_services WHERE producer_id=? ORDER BY created_at DESC',(producer['id'],)).fetchall();avail=c.execute('SELECT * FROM producer_availability WHERE producer_id=? ORDER BY weekday',(producer['id'],)).fetchall();bookings=c.execute("SELECT b.*,s.title service_title FROM session_bookings b JOIN session_services s ON s.id=b.service_id WHERE b.producer_id=? ORDER BY b.start_at DESC LIMIT 50",(producer['id'],)).fetchall();withdrawals=c.execute('SELECT * FROM withdrawals WHERE producer_id=? ORDER BY requested_at DESC LIMIT 20',(producer['id'],)).fetchall()
 finally:c.close()
 return render('admin.html',r,wallet=w,beats=beats,services=services,availability=avail,bookings=bookings,withdrawals=withdrawals,totals={'available_balance':w['available_balance'],'total_earnings':w['total_earnings'],'total_withdrawn':w['total_withdrawn']})
@app.post('/admin/profile')
def profile(r:Request,name:str=Form(...),bio:str=Form(''),phone:str=Form(''),payout_phone:str=Form(''),producer=Depends(auth.require_producer)):
 pp=mpesa.normalize_phone(payout_phone) if payout_phone.strip() else '' ;c=get_db()
 try:c.execute('UPDATE producers SET name=?,bio=?,phone=?,payout_phone=? WHERE id=?',(name.strip()[:100],bio.strip()[:2000],phone.strip()[:30],pp,producer['id']));c.commit()
 finally:c.close()
 return RedirectResponse('/admin',303)
@app.post('/admin/beat')
def add_beat(r:Request,title:str=Form(...),genre:str=Form(''),bpm:str=Form(''),price:int=Form(...),is_hot_pick:str=Form('0'),cover:UploadFile=File(...),audio:UploadFile=File(...),producer=Depends(auth.require_producer)):
 if price<1:raise HTTPException(400,'Invalid price.')
 bpmv=int(bpm) if bpm.strip() else None
 cp=save_file(cover,COVERS,'/static/uploads/covers',{'.jpg','.jpeg','.png','.webp'},10*1024*1024)
 try:ap=save_file(audio,AUDIO,'/static/uploads/audio',{'.mp3','.wav','.m4a'},100*1024*1024)
 except Exception:(BASE/cp.lstrip('/')).unlink(missing_ok=True);raise
 c=get_db()
 try:c.execute('INSERT INTO beats(producer_id,title,genre,bpm,price,cover_path,audio_path,is_hot_pick) VALUES(?,?,?,?,?,?,?,?)',(producer['id'],title.strip()[:200],genre.strip()[:100],bpmv,price,cp,ap,1 if is_hot_pick.lower() in('1','on','true') else 0));c.commit()
 finally:c.close()
 return RedirectResponse('/admin',303)
@app.post('/admin/beat/{beat_id}/hot-pick')
def hot_pick(beat_id:int,is_hot_pick:str=Form('0'),producer=Depends(auth.require_producer)):
    hot=1 if str(is_hot_pick).lower() in ('1','true','on','yes') else 0
    c=get_db()
    try: r=c.execute('UPDATE beats SET is_hot_pick=? WHERE id=? AND producer_id=?',(hot,beat_id,producer['id'])); c.commit()
    finally: c.close()
    if not r.rowcount: raise HTTPException(404,'Beat not found')
    return RedirectResponse('/admin',303)

@app.post('/admin/service')
def add_service(r:Request,title:str=Form(...),description:str=Form(''),duration_minutes:int=Form(...),price:int=Form(...),location:str=Form(''),producer=Depends(auth.require_producer)):
 if not 15<=duration_minutes<=720 or price<1:raise HTTPException(400,'Invalid service details.')
 c=get_db()
 try:c.execute('INSERT INTO session_services(producer_id,title,description,duration_minutes,price,location) VALUES(?,?,?,?,?,?)',(producer['id'],title.strip()[:100],description.strip()[:1000],duration_minutes,price,location.strip()[:200]));c.commit()
 finally:c.close()
 return RedirectResponse('/admin',303)
@app.post('/admin/availability')
def availability(r:Request,weekday:int=Form(...),start_time:str=Form(...),end_time:str=Form(...),slot_minutes:int=Form(60),producer=Depends(auth.require_producer)):
 if not(0<=weekday<=6 and 15<=slot_minutes<=240 and start_time<end_time):raise HTTPException(400,'Invalid availability.')
 c=get_db()
 try:c.execute('INSERT INTO producer_availability(producer_id,weekday,start_time,end_time,slot_minutes) VALUES(?,?,?,?,?) ON CONFLICT(producer_id,weekday) DO UPDATE SET start_time=excluded.start_time,end_time=excluded.end_time,slot_minutes=excluded.slot_minutes',(producer['id'],weekday,start_time,end_time,slot_minutes));c.commit()
 finally:c.close()
 return RedirectResponse('/admin',303)
def request_producer_withdrawal(c,pid,amount,phone):
    c.execute('BEGIN IMMEDIATE')
    r=c.execute('UPDATE producer_wallets SET available_balance=available_balance-?,pending_withdrawal=pending_withdrawal+?,updated_at=CURRENT_TIMESTAMP WHERE producer_id=? AND available_balance>=?',(amount,amount,pid,amount))
    if not r.rowcount: raise HTTPException(400,'Insufficient available balance.')
    wid=c.execute("INSERT INTO withdrawals(producer_id,amount,phone,status) VALUES(?,?,?,'pending')",(pid,amount,phone)).lastrowid
    c.commit(); return wid

@app.post('/admin/withdraw')
def withdraw(amount:int=Form(...),producer=Depends(auth.require_producer)):
    if amount<10: raise HTTPException(400,'Minimum withdrawal amount is 10.')
    c=get_db()
    try:
        p=c.execute('SELECT payout_phone FROM producers WHERE id=?',(producer['id'],)).fetchone()
        if not p or not p['payout_phone']: raise HTTPException(400,'Add a payout number first.')
        wid=request_producer_withdrawal(c,producer['id'],amount,p['payout_phone'])
    except Exception:
        try:c.rollback()
        except:pass
        raise
    finally:c.close()
    try: res=mpesa.initiate_producer_payout(p['payout_phone'],amount,f'WD{wid}')
    except Exception as e:
        c=get_db(); c.execute('BEGIN IMMEDIATE'); c.execute("UPDATE withdrawals SET status='failed',failure_reason=? WHERE id=?",(str(e)[:500],wid)); c.execute('UPDATE producer_wallets SET available_balance=available_balance+?,pending_withdrawal=pending_withdrawal-?,updated_at=CURRENT_TIMESTAMP WHERE producer_id=?',(amount,amount,producer['id'])); c.commit(); c.close(); raise HTTPException(502,str(e))
    if res.get('simulated'):
        c=get_db(); c.execute('BEGIN IMMEDIATE'); c.execute("UPDATE withdrawals SET status='completed',payout_reference=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",(res['reference'],wid)); c.execute('UPDATE producer_wallets SET pending_withdrawal=pending_withdrawal-?,total_withdrawn=total_withdrawn+?,updated_at=CURRENT_TIMESTAMP WHERE producer_id=?',(amount,amount,producer['id'])); c.commit(); c.close()
    return RedirectResponse('/admin',303)

def split(c,kind,id,producer_id,amount):
    # One transaction may only be split once. This protects both wallets and
    # the platform ledger from duplicate callbacks/retries.
    amount=int(amount)
    if amount<=0:
        raise HTTPException(400,'Transaction amount must be greater than zero.')

    fee=round(amount*FEE_RATE/100)
    net=amount-fee

    res=c.execute(
        '''
        INSERT OR IGNORE INTO platform_ledger
            (source_type,source_id,gross_amount,platform_fee,producer_credit)
        VALUES
            (?,?,?,?,?)
        ''',
        (kind,id,amount,fee,net)
    )

    if not res.rowcount:
        return None

    ensure_wallet(c,producer_id)

    c.execute(
        '''
        UPDATE producer_wallets
        SET
            available_balance=available_balance+?,
            total_earnings=total_earnings+?,
            updated_at=CURRENT_TIMESTAMP
        WHERE producer_id=?
        ''',
        (net,net,producer_id)
    )

    c.execute(
        '''
        UPDATE platform_wallet
        SET
            available_balance=available_balance+?,
            total_earnings=total_earnings+?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=1
        ''',
        (fee,fee)
    )

    return fee,net
@app.post('/checkout/{beat_id}')
def checkout(beat_id:int,phone:str=Form(...)):
    try: phone=mpesa.normalize_phone(phone)
    except ValueError as e: raise HTTPException(400,str(e))
    c=get_db()
    try:
        b=c.execute('SELECT * FROM beats WHERE id=?',(beat_id,)).fetchone()
        if not b: raise HTTPException(404,'Beat not found')
        oid=c.execute('INSERT INTO orders(beat_id,buyer_phone,amount) VALUES(?,?,?)',(beat_id,phone,b['price'])).lastrowid
        c.commit()
    finally: c.close()
    try: res=mpesa.stk_push(phone,b['price'],f'BEAT{beat_id}',b['title'])
    except Exception as e:
        c=get_db(); c.execute("UPDATE orders SET status='failed',failure_reason=? WHERE id=?",(str(e)[:500],oid)); c.commit(); c.close()
        raise HTTPException(502,str(e))
    c=get_db(); c.execute('UPDATE orders SET checkout_request_id=? WHERE id=?',(res['checkout_request_id'],oid)); c.commit(); c.close()
    if res.get('simulated'): threading.Thread(target=lambda:(time.sleep(1),complete_beat(oid)),daemon=True).start()
    return {'order_id':oid,'status':'pending'}

def complete_beat(oid):
    c=get_db()
    try:
        c.execute('BEGIN IMMEDIATE')
        o=c.execute('SELECT o.*,b.producer_id FROM orders o JOIN beats b ON b.id=o.beat_id WHERE o.id=?',(oid,)).fetchone()
        if not o or o['status'] not in ('pending','completed'): c.rollback(); return
        x=split(c,'beat',oid,o['producer_id'],o['amount'])
        c.execute("UPDATE orders SET status='completed',completed_at=COALESCE(completed_at,CURRENT_TIMESTAMP) WHERE id=?",(oid,))
        if x: c.execute('UPDATE orders SET platform_fee=?,producer_payout=?,commission_rate_locked=?,split_applied_at=CURRENT_TIMESTAMP,download_token=COALESCE(download_token,?) WHERE id=?',(x[0],x[1],FEE_RATE,secrets.token_urlsafe(32),oid))
        c.commit()
    except Exception:
        c.rollback(); raise
    finally: c.close()

@app.get('/order/{oid}/status')
def order_status(oid:int):
 c=get_db();o=c.execute('SELECT status,download_token FROM orders WHERE id=?',(oid,)).fetchone();c.close();
 if not o:raise HTTPException(404,'Order not found')
 return {'status':o['status'],'download_token':o['download_token'] if o['status']=='completed' else None}
# sessions
@app.get('/sessions/{service_id}/book')
def book_page(r:Request,service_id:int):
 c=get_db();s=c.execute('SELECT s.*,p.name producer_name,p.slug producer_slug FROM session_services s JOIN producers p ON p.id=s.producer_id WHERE s.id=? AND s.active=1',(service_id,)).fetchone();c.close()
 if not s:raise HTTPException(404,'Service not found')
 return render('book_session.html',r,service=s)
def slot_free(c,pid,start,end,ignore=None):
 q="SELECT 1 FROM session_bookings WHERE producer_id=? AND status IN ('pending','paid','confirmed') AND (hold_expires_at IS NULL OR hold_expires_at>?) AND start_at<? AND end_at>?";args=[pid,iso(now()),iso(end),iso(start)]
 if ignore:q+=' AND id<>?';args.append(ignore)
 return not c.execute(q,args).fetchone()
@app.get('/api/services/{sid}/slots')
def slots(sid:int,day:str):
 d=date.fromisoformat(day);c=get_db();s=c.execute('SELECT * FROM session_services WHERE id=? AND active=1',(sid,)).fetchone()
 if not s:c.close();raise HTTPException(404,'Service not found')
 a=c.execute('SELECT * FROM producer_availability WHERE producer_id=? AND weekday=?',(s['producer_id'],d.weekday())).fetchone()
 if not a:c.close();return []
 cur=datetime.combine(d,dtime.fromisoformat(a['start_time']),tzinfo=timezone.utc);endday=datetime.combine(d,dtime.fromisoformat(a['end_time']),tzinfo=timezone.utc);dur=timedelta(minutes=s['duration_minutes']);out=[]
 while cur+dur<=endday:
  if cur>now() and slot_free(c,s['producer_id'],cur,cur+dur):out.append({'start_at':iso(cur),'end_at':iso(cur+dur)})
  cur+=timedelta(minutes=a['slot_minutes'])
 c.close();return out
@app.post('/sessions/{sid}/book')
def create_booking(sid:int,client_name:str=Form(...),client_phone:str=Form(...),client_email:str=Form(''),start_at:str=Form(...)):
    try: phone=mpesa.normalize_phone(client_phone)
    except ValueError as e: raise HTTPException(400,str(e))
    start=parse_iso(start_at); c=get_db()
    try:
        c.execute('BEGIN IMMEDIATE')
        # Expired payment holds no longer block a slot.
        c.execute("UPDATE session_bookings SET status='cancelled',cancelled_at=CURRENT_TIMESTAMP WHERE producer_id IN (SELECT producer_id FROM session_services WHERE id=?) AND status='pending' AND hold_expires_at IS NOT NULL AND hold_expires_at<=?",(sid,iso(now())))
        s=c.execute('SELECT * FROM session_services WHERE id=? AND active=1',(sid,)).fetchone()
        if not s: raise HTTPException(404,'Service not found')
        end=start+timedelta(minutes=s['duration_minutes'])
        if start<=now() or not slot_free(c,s['producer_id'],start,end): raise HTTPException(409,'That time is no longer available.')
        bid=c.execute('INSERT INTO session_bookings(producer_id,service_id,client_name,client_phone,client_email,start_at,end_at,amount,status,hold_expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(s['producer_id'],sid,client_name.strip()[:100],phone,client_email.strip()[:200],iso(start),iso(end),s['price'],'pending',iso(now()+timedelta(minutes=10)))).lastrowid
        c.commit()
    except sqlite3.IntegrityError:
        c.rollback(); raise HTTPException(409,'That time is no longer available.')
    finally: c.close()
    try: res=mpesa.stk_push(phone,s['price'],f'SESSION{bid}',s['title'])
    except Exception as e:
        c=get_db(); c.execute("UPDATE session_bookings SET status='cancelled',cancelled_at=CURRENT_TIMESTAMP WHERE id=?",(bid,)); c.commit(); c.close(); raise HTTPException(502,str(e))
    c=get_db(); c.execute('UPDATE session_bookings SET checkout_request_id=? WHERE id=?',(res['checkout_request_id'],bid)); c.commit(); c.close()
    if res.get('simulated'): threading.Thread(target=lambda:(time.sleep(1),complete_session(bid)),daemon=True).start()
    return {'booking_id':bid,'status':'pending'}

def complete_session(bid):
 c=get_db()
 try:
  c.execute('BEGIN IMMEDIATE');b=c.execute('SELECT * FROM session_bookings WHERE id=?',(bid,)).fetchone()
  if not b or b['status']!='pending':c.rollback();return
  x=split(c,'session',bid,b['producer_id'],b['amount']);c.execute("UPDATE session_bookings SET status='paid',paid_at=CURRENT_TIMESTAMP,hold_expires_at=NULL,platform_fee=?,producer_payout=?,split_applied_at=CURRENT_TIMESTAMP WHERE id=?",(x[0],x[1],bid));c.commit()
 except:c.rollback();raise
 finally:c.close()
@app.get('/booking/{bid}')
def booking_page(r:Request,bid:int):
 c=get_db();b=c.execute('SELECT b.*,s.title service_title,p.name producer_name,p.slug FROM session_bookings b JOIN session_services s ON s.id=b.service_id JOIN producers p ON p.id=b.producer_id WHERE b.id=?',(bid,)).fetchone();msgs=c.execute('SELECT * FROM booking_messages WHERE booking_id=? ORDER BY id',(bid,)).fetchall();props=c.execute('SELECT * FROM booking_proposals WHERE booking_id=? AND confirmed_at IS NULL AND declined_at IS NULL ORDER BY id DESC',(bid,)).fetchall();c.close()
 if not b:raise HTTPException(404,'Booking not found')
 return render('booking.html',r,booking=b,messages=msgs,proposals=props)
def booking_actor(r,b):
    p=auth.current_producer(r)
    if p and b and p['id']==b['producer_id']: return 'producer'
    # Client access is intentionally limited to the current booking session. A real
    # public client account can be added later without changing the booking model.
    return 'client'

@app.post('/booking/{bid}/message')
def message(r:Request,bid:int,body:str=Form(...)):
    c=get_db(); b=c.execute('SELECT * FROM session_bookings WHERE id=?',(bid,)).fetchone()
    if not b: c.close(); raise HTTPException(404,'Booking not found')
    body=body.strip()
    if not body: c.close(); raise HTTPException(400,'Message cannot be empty.')
    role=booking_actor(r,b); c.execute('INSERT INTO booking_messages(booking_id,sender_role,body) VALUES(?,?,?)',(bid,role,body[:2000])); c.commit(); c.close(); return RedirectResponse('/booking/'+str(bid),303)

@app.post('/booking/{bid}/propose')
def propose(r:Request,bid:int,start_at:str=Form(...)):
    c=get_db(); b=c.execute('SELECT * FROM session_bookings WHERE id=?',(bid,)).fetchone()
    if not b: c.close(); raise HTTPException(404,'Booking not found')
    role=booking_actor(r,b)
    st=parse_iso(start_at)
    if st<=now(): c.close(); raise HTTPException(400,'Proposed time must be in the future.')
    en=st+(parse_iso(b['end_at'])-parse_iso(b['start_at']))
    if not slot_free(c,b['producer_id'],st,en,ignore=bid): c.close(); raise HTTPException(409,'That proposed time is unavailable.')
    c.execute('INSERT INTO booking_proposals(booking_id,proposed_start_at,proposed_end_at,proposed_by) VALUES(?,?,?,?)',(bid,iso(st),iso(en),role)); c.commit(); c.close(); return RedirectResponse('/booking/'+str(bid),303)

@app.post('/booking/{bid}/proposal/{pid}/confirm')
def confirm_proposal(r:Request,bid:int,pid:int):
    c=get_db()
    try:
        c.execute('BEGIN IMMEDIATE'); b=c.execute('SELECT * FROM session_bookings WHERE id=?',(bid,)).fetchone(); pr=c.execute('SELECT * FROM booking_proposals WHERE id=? AND booking_id=? AND confirmed_at IS NULL AND declined_at IS NULL',(pid,bid)).fetchone()
        if not b or not pr: raise HTTPException(404,'Proposal not found')
        actor=booking_actor(r,b)
        # Only the other party may accept a proposal; the proposer cannot self-confirm it.
        if actor==pr['proposed_by']: raise HTTPException(403,'The other party must confirm this proposal.')
        st=parse_iso(pr['proposed_start_at']); en=parse_iso(pr['proposed_end_at'])
        if not slot_free(c,b['producer_id'],st,en,ignore=bid): raise HTTPException(409,'That proposed time is no longer available.')
        c.execute('UPDATE session_bookings SET start_at=?,end_at=?,status=CASE WHEN status="paid" THEN "confirmed" ELSE status END WHERE id=?',(iso(st),iso(en),bid)); c.execute('UPDATE booking_proposals SET confirmed_at=CURRENT_TIMESTAMP WHERE id=?',(pid,)); c.commit()
    except Exception:
        try:c.rollback()
        except:pass
        raise
    finally:c.close()
    return RedirectResponse('/booking/'+str(bid),303)

@app.get('/booking/{bid}/status')
def booking_status(bid:int):
 c=get_db();b=c.execute('SELECT status FROM session_bookings WHERE id=?',(bid,)).fetchone();c.close();return {'status':b['status']} if b else (_ for _ in ()).throw(HTTPException(404,'Booking not found'))
def admin_phone():
    raw=os.getenv('SUPER_ADMIN_PAYOUT_PHONE','').strip()
    if not raw: return ''
    try: return mpesa.normalize_phone(raw)
    except ValueError: return ''

@app.get('/super-admin/login')
def super_login_page(r:Request):
    if auth.is_super_admin(r):
        return RedirectResponse('/super-admin',303)
    return render('super_admin_login.html',r,error=None)

@app.post('/super-admin/login')
def super_login(r:Request,username:str=Form(...),password:str=Form(...)):
    configured_username=os.getenv('SUPER_ADMIN_USERNAME','').strip()
    configured_password=os.getenv('SUPER_ADMIN_PASSWORD','')

    if not configured_username or not configured_password:
        return render(
            'super_admin_login.html',
            r,
            error='Super Admin credentials are not configured on the server.'
        )

    good=(
        secrets.compare_digest(username.strip(),configured_username)
        and secrets.compare_digest(password,configured_password)
    )

    if not good:
        return render('super_admin_login.html',r,error='Invalid credentials.')

    r.session.clear()
    r.session['super_admin']=True
    r.session['role']='super_admin'
    r.session['super_admin_login_at']=datetime.now(timezone.utc).isoformat()

    return RedirectResponse('/super-admin',303)


@app.post('/super-admin/logout')
def super_logout(r:Request):
    r.session.clear()
    return RedirectResponse('/',303)


@app.get('/super-admin')
def super_admin(r:Request):
    auth.require_super_admin(r)
    c=get_db()

    try:
        wallet=c.execute(
            'SELECT * FROM platform_wallet WHERE id=1'
        ).fetchone()

        if not wallet:
            c.execute('INSERT OR IGNORE INTO platform_wallet(id) VALUES(1)')
            c.commit()
            wallet=c.execute(
                'SELECT * FROM platform_wallet WHERE id=1'
            ).fetchone()

        # Aggregate from the complete platform ledger. Do not limit financial
        # totals to the latest page of transactions.
        summary=c.execute("""
            SELECT
                COALESCE(SUM(gross_amount),0) AS gross_sales,
                COALESCE(SUM(platform_fee),0) AS platform_earnings,
                COALESCE(SUM(producer_credit),0) AS producer_earnings,
                COUNT(*) AS completed_transactions
            FROM platform_ledger
        """).fetchone()

        beat_summary=c.execute("""
            SELECT
                COALESCE(SUM(gross_amount),0) AS gross,
                COALESCE(SUM(platform_fee),0) AS fee,
                COUNT(*) AS count
            FROM platform_ledger
            WHERE source_type='beat'
        """).fetchone()

        session_summary=c.execute("""
            SELECT
                COALESCE(SUM(gross_amount),0) AS gross,
                COALESCE(SUM(platform_fee),0) AS fee,
                COUNT(*) AS count
            FROM platform_ledger
            WHERE source_type='session'
        """).fetchone()

        recent=c.execute("""
            SELECT
                pl.*,
                CASE
                    WHEN pl.source_type='beat' THEN b.title
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
                    WHEN pl.source_type='beat' THEN b.producer_id
                    ELSE sb.producer_id
                END
            ORDER BY pl.created_at DESC
            LIMIT 100
        """).fetchall()

        withdrawals=c.execute("""
            SELECT *
            FROM platform_withdrawals
            ORDER BY requested_at DESC
            LIMIT 50
        """).fetchall()

        pending_count=c.execute("""
            SELECT COUNT(*) AS count
            FROM platform_withdrawals
            WHERE status='pending'
        """).fetchone()['count']

        totals={
            'gross_sales':summary['gross_sales'],
            'platform_earnings':summary['platform_earnings'],
            'producer_earnings':summary['producer_earnings'],
            'completed_transactions':summary['completed_transactions'],
            'available_balance':wallet['available_balance'],
            'pending_withdrawal':wallet['pending_withdrawal'],
            'total_withdrawn':wallet['total_withdrawn'],
            'pending_withdrawals_count':pending_count,
            'beat_gross':beat_summary['gross'],
            'beat_fee':beat_summary['fee'],
            'beat_count':beat_summary['count'],
            'session_gross':session_summary['gross'],
            'session_fee':session_summary['fee'],
            'session_count':session_summary['count'],
            'commission_rate':FEE_RATE,
        }

    finally:
        c.close()

    return render(
        'super_admin.html',
        r,
        wallet=wallet,
        totals=totals,
        recent=recent,
        withdrawals=withdrawals,
        payout_phone=admin_phone()
    )


@app.post('/super-admin/withdraw')
def super_withdraw(r:Request,amount:int=Form(...)):
    auth.require_super_admin(r)

    if amount<10:
        raise HTTPException(
            400,
            'Minimum withdrawal amount is 10.'
        )

    phone=admin_phone()

    if not phone:
        raise HTTPException(
            400,
            'Configure a valid Super Admin payout number first.'
        )

    c=get_db()

    try:
        c.execute('BEGIN IMMEDIATE')

        row=c.execute("""
            SELECT available_balance
            FROM platform_wallet
            WHERE id=1
        """).fetchone()

        if not row:
            raise HTTPException(
                500,
                'Platform wallet is not available.'
            )

        if row['available_balance']<amount:
            raise HTTPException(
                400,
                'Insufficient available platform balance.'
            )

        wid=c.execute("""
            INSERT INTO platform_withdrawals
                (amount,phone,status)
            VALUES
                (?,?,'pending')
        """,(amount,phone)).lastrowid

        c.execute("""
            UPDATE platform_wallet
            SET
                available_balance=available_balance-?,
                pending_withdrawal=pending_withdrawal+?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=1
        """,(amount,amount))

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
        res=mpesa.initiate_platform_payout(
            phone,
            amount,
            f'ADMINWD{wid}'
        )
    except Exception as e:
        c=get_db()
        try:
            c.execute('BEGIN IMMEDIATE')

            c.execute("""
                UPDATE platform_withdrawals
                SET
                    status='failed',
                    failure_reason=?
                WHERE id=?
            """,(str(e)[:500],wid))

            c.execute("""
                UPDATE platform_wallet
                SET
                    available_balance=available_balance+?,
                    pending_withdrawal=pending_withdrawal-?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=1
            """,(amount,amount))

            c.commit()
        finally:
            c.close()

        raise HTTPException(
            502,
            'The payout provider could not process the withdrawal.'
        )

    # Mock mode completes immediately. Live mode remains pending until
    # the provider callback confirms the payout.
    if res.get('simulated'):
        c=get_db()

        try:
            c.execute('BEGIN IMMEDIATE')

            c.execute("""
                UPDATE platform_withdrawals
                SET
                    status='completed',
                    payout_reference=?,
                    completed_at=CURRENT_TIMESTAMP
                WHERE id=?
            """,(res['reference'],wid))

            c.execute("""
                UPDATE platform_wallet
                SET
                    pending_withdrawal=pending_withdrawal-?,
                    total_withdrawn=total_withdrawn+?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=1
            """,(amount,amount))

            c.commit()
        finally:
            c.close()

    return RedirectResponse('/super-admin',303)


@app.get('/download/{token}')
def download(token:str):
 c=get_db();x=c.execute('SELECT o.status,b.audio_path FROM orders o JOIN beats b ON b.id=o.beat_id WHERE o.download_token=?',(token,)).fetchone();c.close()
 if not x or x['status']!='completed':raise HTTPException(403,'Invalid download link.')
 p=(BASE/x['audio_path'].lstrip('/')).resolve()
 if not p.is_file() or AUDIO.resolve() not in p.parents:raise HTTPException(404,'File unavailable.')
 return FileResponse(p,filename=p.name)
@app.post('/mpesa/callback')
async def callback(r:Request):return {'ResultCode':0,'ResultDesc':'Live Safaricom callback integration pending.'}
