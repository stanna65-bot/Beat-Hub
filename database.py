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
