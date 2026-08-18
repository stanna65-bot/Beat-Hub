Pasted text(3).txt
Document
{"detail":"Not Found"} chat check this out and explain 

Good — /health working means the app is running. The remaining problem is logout/session handling.

Your source already has a /logout route, but we need to make the logout action match the browser request exactly.

Fix it directly

In main.py, replace your logout route with:

@app.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request):
    request.session.clear()


    response = RedirectResponse(
        url="/login",
        status_code=303
    )


    response.delete_cookie("session")
    response.delete_cookie("remember_email")


    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


    return response
Then check your admin template

Your logout button/form should be:

<form method="post" action="/logout">
    <button type="submit">Logout</button>
</form>

Do not use:

<a href="/logout">

unless you keep the GET support above—which we are doing anyway.

Then
Save main.py.
Commit.
Push to GitHub.
Deploy latest commit on Render.
Login.
Click Logout.

It should take you straight to:

/login

and the admin session should be cleared.

The existing code's intended logout behavior was already clearing the session and redirecting, so we're fixing the route to reliably handle both GET and POST rather than changing your authentication architecture.

Pasted text(4).txt
Document
now again i want the admin page to have a button nicely placed where an artist can upload multiple tracks as once,lets say an album and cver and price dont no downgrades

Yes. We can add this without removing or downgrading anything.

Your current admin page already has the single-track upload section at lines 236–349, with title, genre, BPM, price, cover and audio fields.

What you want is a new “Upload Album / Multiple Tracks” button/section that lets an artist:

Enter Album name
Upload one cover
Set album price
Select multiple audio tracks at once
Publish the whole album in one submission
Automatically use the uploaded track filenames as track names, while still allowing editing if we build the UI that way
Keep the existing single Beat / Track upload completely intact
Keep existing catalogue, payments, M-Pesa, sessions, wallets and admin functionality intact
Important compatibility point

The template alone is not enough to implement this properly. Your current form posts single-track data to:

/admin/beat

For a real multi-track album upload, we need to add a backend endpoint such as:

POST /admin/album

and make sure it works with your existing database schema, upload storage and catalogue tables.

So I don't want to invent a database structure and break BeatHub.

Send/upload the current main.py you are deploying now (the one where /health works). Then I'll give you the exact full replacement(s), with:

UPLOAD ALBUM

Album title → Cover → Price → Multiple tracks → Publish Album

while preserving everything currently working.

Pasted text(5).txt
Document
produce fll replacements of the pys and make sure they are all ok, no downgrades

Done. I inspected the current BeatHub files, kept the existing functionality, added the album system additively, and tested the replacements.

What is added
Upload Album section in Admin
Album title
Album genre
Album price
One album cover
Multiple audio tracks at once
Up to 50 tracks
Track names automatically taken from filenames
Album catalogue displayed in Admin
Public album page
Existing single-track upload remains intact
Existing beats, orders, wallets, sessions and other routes remain intact
/logout now supports GET + POST and redirects to /login
Database migration is additive — it does not delete existing data
Files

1. Full project replacement — easiest option

Download BeatHub full album replacement ZIP

Or individually:

Download updated main.py

Download updated database.py

Download auth.py

Download mpesa.py

Download updated admin.html

Download new album.html

Testing completed
PY_COMPILE_OK
ROUTE_IMPORT_OK
ALBUM_SCHEMA_OK

Confirmed routes:

/health                         GET, HEAD
/logout                         GET, POST
/admin/beat                     POST
/admin/album                    POST
/admin/album/{album_id}/publish POST
/p/{slug}/album/{album_id}      GET

The database test also confirmed the new albums and album_tracks tables work alongside the existing beats, orders, wallets, sessions and ledger tables.

Use the ZIP if you want the safest replacement. It contains the complete project structure, not just the changed snippets.

{% extends 'base.html' %}{% block title %}{{producer['name']}} — BeatHub{% endblock %}{% block body %}<div class="nav"><div><div class="muted" style="font:12px 'Space Mono'">PRODUCER CONTROL ROOM</div><div class="brand">{{producer['name']}}</div></div></div><div class="card" style="margin:22px 0"><div class="muted">YOUR PUBLIC STORE</div><div style="font-family:'Space Mono';color:var(--teal);margin-top:8px;word-break:break-all">{{request.url.scheme}}://{{request.url.netloc}}/p/{{producer['slug']}}</div></div><div style="display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:12px;margin:20px 0"><div class="card"><div class="muted">AVAILABLE BALANCE</div><h2>KES {{totals['available_balance']}}</h2><small class="muted">Ready for withdrawal</small></div><div class="card"><div class="muted">TOTAL EARNINGS</div><h2>KES {{totals['total_earnings']}}</h2><small class="muted">Your net earnings</small></div><div class="card"><div class="muted">WITHDRAWN</div><h2>KES {{totals['total_withdrawn']}}</h2><small class="muted">Paid to M-Pesa</small></div><div class="card"><div class="muted">PUBLIC PROFILE</div><h2>↗</h2><a class="btn alt" href="/p/{{producer['slug']}}">View store</a></div></div><div style="display:grid;grid-template-columns:1fr 1fr;gap:18px"><section class="card"><h2>Profile & payouts</h2><form action="/admin/profile" method="post"><label>Producer name</label><input name="name" value="{{producer['name']}}" required><label>Bio</label><textarea name="bio">{{producer['bio']}}</textarea><label>Phone</label><input name="phone" value="{{producer['phone']}}"><label>M-Pesa payout number</label><input name="payout_phone" value="{{producer['payout_phone']}}" placeholder="07XXXXXXXX"><button class="btn">Save profile</button></form><hr style="margin:25px 0"><h2>Withdraw to M-Pesa</h2><form action="/admin/withdraw" method="post"><label>Amount</label><input name="amount" type="number" min="10" required placeholder="KES"><button class="btn">Request withdrawal</button></form></section><section class="card"><div style="display:flex;justify-content:
{% extends 'base.html' %}
{% block title %}{{album['title']}} — BeatHub{% endblock %}
{% block body %}
<div class="nav"><div><div class="muted" style="font:12px 'Space Mono'">ALBUM</div><div class="brand">{{album['producer_name']}}</div></div><a class="btn alt" href="/p/{{album['producer_slug']}}">Back to store</a></div>
<section class="card" style="margin:22px 0;display:grid;grid-template-columns:minmax(220px,320px) 1fr;gap:24px;align-items:start">
<img src="{{album['cover_path']}}" alt="{{album['title']}} cover" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:14px">
<div><div class="muted">ALBUM</div><h1 style="margin:6px 0">{{album['title']}}</h1><p class="muted">{{album['genre']}} · {{tracks|length}} tracks · KES {{album['price']}}</p>
<div style="margin-top:20px">{% for t in tracks %}<div style="border-top:1px solid var(--line);padding:14px 0;display:grid;grid-template-columns:40px 1fr;gap:12px;align-items:center"><b>{{t['track_number']}}</b><div><b>{{t['title']}}</b><audio controls preload="none" src="{{t['audio_path']}}" style="width:100%;margin-top:8px"></audio></div></div>{% endfor %}</div></div>
</section>
{% endblock %}
BeatHub_main.py
Code
BeatHub_database.py
Code
BeatHub_auth.py
Code
BeatHub_mpesa.py
Code
BeatHub_admin.html
HTML
BeatHub_album.html
HTML
Show less

Library
/
BeatHub_database.py
import sqlite3,re
from pathlib import Path
BASE_DIR=Path(__file__).resolve().parent; DB_PATH=BASE_DIR/'beat_hub.db'
def get_db():
 c=sqlite3.connect(DB_PATH,timeout=30,isolation_level=None); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA journal_mode=WAL'); return c
def _add(c,t,col,typ):
 cols={x['name'] for x in c.execute(f'PRAGMA table_info({t})')}
 if col not in cols:c.execute(f'ALTER TABLE {t} ADD COLUMN {col} {typ}')
def init_db():
    conn=get_db()
    # Core tables. The schema is intentionally additive so an existing BeatHub database
    # can be upgraded without destroying producers, beats, orders or wallet balances.
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS producers(id INTEGER PRIMARY KEY AUTOINCREMENT,slug TEXT UNIQUE NOT NULL,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,name TEXT NOT NULL,bio TEXT NOT NULL DEFAULT '',phone TEXT NOT NULL DEFAULT '',payout_phone TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS beats(id INTEGER PRIMARY KEY AUTOINCREMENT,producer_id INTEGER NOT NULL REFERENCES producers(id) ON DELETE CASCADE,title TEXT NOT NULL,genre TEXT NOT NULL DEFAULT '',bpm INTEGER,price INTEGER NOT NULL CHECK(price>0),cover_path TEXT NOT NULL,audio_path TEXT NOT NULL,is_hot_pick INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS albums(id INTEGER PRIMARY KEY AUTOINCREMENT,producer_id INTEGER NOT NULL REFERENCES producers(id) ON DELETE CASCADE,title TEXT NOT NULL,genre TEXT NOT NULL DEFAULT '',price INTEGER NOT NULL CHECK(price>0),cover_path TEXT NOT NULL,is_published INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS album_tracks(id INTEGER PRIMARY KEY AUTOINCREMENT,album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,track_number INTEGER NOT NULL,title TEXT NOT NULL,audio_path TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(album_id,track_number));
    CREATE INDEX IF NOT EXISTS idx_albums_producer_created ON albums(producer_id,created_at);
    CREATE INDEX IF NOT EXISTS idx_album_tracks_album ON album_tracks(album_id,track_number);
    CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,beat_id INTEGER NOT NULL REFERENCES beats(id),buyer_phone TEXT NOT NULL,amount INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',checkout_request_id TEXT UNIQUE,mpesa_receipt TEXT,platform_fee INTEGER NOT NULL DEFAULT 0,producer_payout INTEGER NOT NULL DEFAULT 0,commission_rate_locked REAL,split_applied_at TEXT,download_token TEXT UNIQUE,failure_reason TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,completed_at TEXT);
    CREATE TABLE IF NOT EXISTS producer_wallets(producer_id INTEGER PRIMARY KEY REFERENCES producers(id) ON DELETE CASCADE,available_balance INTEGER NOT NULL DEFAULT 0,pending_withdrawal INTEGER NOT NULL DEFAULT 0,total_earnings INTEGER NOT NULL DEFAULT 0,total_withdrawn INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS withdrawals(id INTEGER PRIMARY KEY AUTOINCREMENT,producer_id INTEGER NOT NULL REFERENCES producers(id),amount INTEGER NOT NULL,phone TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',payout_reference TEXT,failure_reason TEXT,requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,completed_at TEXT);
    CREATE TABLE IF NOT EXISTS platform_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,source_type TEXT NOT NULL DEFAULT 'beat',source_id INTEGER NOT NULL,gross_amount INTEGER NOT NULL,platform_fee INTEGER NOT NULL,producer_credit INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(source_type,source_id));
    CREATE TABLE IF NOT EXISTS platform_wallet(id INTEGER PRIMARY KEY CHECK(id=1),available_balance INTEGER NOT NULL DEFAULT 0,pending_withdrawal INTEGER NOT NULL DEFAULT 0,total_earnings INTEGER NOT NULL DEFAULT 0,total_withdrawn INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    INSERT OR IGNORE INTO platform_wallet(id) VALUES(1);
    CREATE TABLE IF NOT EXISTS platform_withdrawals(id INTEGER PRIMARY KEY AUTOINCREMENT,amount INTEGER NOT NULL,phone TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',payout_reference TEXT,failure_reason TEXT,requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,completed_at TEXT);
    CREATE TABLE IF NOT EXISTS password_reset_tokens(id INTEGER PRIMARY KEY AUTOINCREMENT,producer_id INTEGER NOT NULL REFERENCES producers(id) ON DELETE CASCADE,token_hash TEXT NOT NULL UNIQUE,expires_at TEXT NOT NULL,used_at TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS session_services(id INTEGER PRIMARY KEY AUTOINCREMENT,producer_id INTEGER NOT NULL REFERENCES producers(id) ON DELETE CASCADE,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',duration_minutes INTEGER NOT NULL CHECK(duration_minutes BETWEEN 15 AND 720),price INTEGER NOT NULL CHECK(price>0),location TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS producer_availability(id INTEGER PRIMARY KEY AUTOINCREMENT,producer_id INTEGER NOT NULL REFERENCES producers(id) ON DELETE CASCADE,weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),start_time TEXT NOT NULL,end_time TEXT NOT NULL,slot_minutes INTEGER NOT NULL DEFAULT 60 CHECK(slot_minutes BETWEEN 15 AND 240),UNIQUE(producer_id,weekday));
    CREATE TABLE IF NOT EXISTS session_bookings(id INTEGER PRIMARY KEY AUTOINCREMENT,producer_id INTEGER NOT NULL REFERENCES producers(id),service_id INTEGER NOT NULL REFERENCES session_services(id),client_name TEXT NOT NULL,client_phone TEXT NOT NULL,client_email TEXT NOT NULL DEFAULT '',start_at TEXT NOT NULL,end_at TEXT NOT NULL,amount INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',checkout_request_id TEXT UNIQUE,mpesa_receipt TEXT,platform_fee INTEGER NOT NULL DEFAULT 0,producer_payout INTEGER NOT NULL DEFAULT 0,split_applied_at TEXT,hold_expires_at TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,paid_at TEXT,cancelled_at TEXT,UNIQUE(producer_id,start_at));
    CREATE TABLE IF NOT EXISTS booking_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,booking_id INTEGER NOT NULL REFERENCES session_bookings(id) ON DELETE CASCADE,sender_role TEXT NOT NULL CHECK(sender_role IN ('client','producer')),body TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS booking_proposals(id INTEGER PRIMARY KEY AUTOINCREMENT,booking_id INTEGER NOT NULL REFERENCES session_bookings(id) ON DELETE CASCADE,proposed_start_at TEXT NOT NULL,proposed_end_at TEXT NOT NULL,proposed_by TEXT NOT NULL,confirmed_at TEXT,declined_at TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS idx_bookings_producer_start ON session_bookings(producer_id,start_at);
    """)
    # Older releases used platform_ledger(order_id,...). Migrate that ledger to the
    # source_type/source_id format so beat and session revenue share one accounting path.
    cols={r["name"] for r in conn.execute("PRAGMA table_info(platform_ledger)")}
    if cols and "source_type" not in cols:
        conn.execute("ALTER TABLE platform_ledger RENAME TO platform_ledger_legacy")
        conn.execute("""CREATE TABLE platform_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,source_type TEXT NOT NULL DEFAULT 'beat',source_id INTEGER NOT NULL,gross_amount INTEGER NOT NULL,platform_fee INTEGER NOT NULL,producer_credit INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(source_type,source_id))""")
        conn.execute("""INSERT OR IGNORE INTO platform_ledger(source_type,source_id,gross_amount,platform_fee,producer_credit,created_at) SELECT 'beat',order_id,gross_amount,platform_fee,producer_credit,created_at FROM platform_ledger_legacy""")
        conn.execute("DROP TABLE platform_ledger_legacy")
    conn.commit()
    conn.close()

def slugify(v):
 v=re.sub(r'[^a-z0-9]+','-',(v or '').lower()).strip('-');return v or 'producer'
def unique_slug(c,name):
 base=slugify(name); s=base;n=2
 while c.execute('SELECT 1 FROM producers WHERE slug=?',(s,)).fetchone():s=f'{base}-{n}';n+=1
 return s
