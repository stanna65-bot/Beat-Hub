import sqlite3, re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "beat_hub.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def _add_column(conn, table, column, definition):
    cols={r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

def init_db():
    conn=get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS producers (
      id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL, name TEXT NOT NULL, bio TEXT NOT NULL DEFAULT '',
      phone TEXT NOT NULL DEFAULT '', payout_phone TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS beats (
      id INTEGER PRIMARY KEY AUTOINCREMENT, producer_id INTEGER NOT NULL REFERENCES producers(id) ON DELETE CASCADE,
      title TEXT NOT NULL, genre TEXT NOT NULL DEFAULT '', bpm INTEGER, price INTEGER NOT NULL CHECK(price>0),
      cover_path TEXT NOT NULL, audio_path TEXT NOT NULL, is_hot_pick INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS orders (
      id INTEGER PRIMARY KEY AUTOINCREMENT, beat_id INTEGER NOT NULL REFERENCES beats(id), buyer_phone TEXT NOT NULL,
      amount INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
      checkout_request_id TEXT UNIQUE, mpesa_receipt TEXT, platform_fee INTEGER NOT NULL DEFAULT 0,
      producer_payout INTEGER NOT NULL DEFAULT 0, commission_rate_locked REAL, split_applied_at TEXT,
      download_token TEXT UNIQUE, failure_reason TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS producer_wallets (
      producer_id INTEGER PRIMARY KEY REFERENCES producers(id) ON DELETE CASCADE,
      available_balance INTEGER NOT NULL DEFAULT 0, pending_withdrawal INTEGER NOT NULL DEFAULT 0,
      total_earnings INTEGER NOT NULL DEFAULT 0, total_withdrawn INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS withdrawals (
      id INTEGER PRIMARY KEY AUTOINCREMENT, producer_id INTEGER NOT NULL REFERENCES producers(id),
      amount INTEGER NOT NULL CHECK(amount>0), phone TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending', payout_reference TEXT, failure_reason TEXT,
      requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS platform_ledger (
      id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id),
      gross_amount INTEGER NOT NULL, platform_fee INTEGER NOT NULL, producer_credit INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS platform_wallet (
      id INTEGER PRIMARY KEY CHECK(id=1), available_balance INTEGER NOT NULL DEFAULT 0,
      pending_withdrawal INTEGER NOT NULL DEFAULT 0, total_earnings INTEGER NOT NULL DEFAULT 0,
      total_withdrawn INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    INSERT OR IGNORE INTO platform_wallet(id) VALUES(1);
    CREATE TABLE IF NOT EXISTS platform_withdrawals (
      id INTEGER PRIMARY KEY AUTOINCREMENT, amount INTEGER NOT NULL CHECK(amount>0), phone TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending', payout_reference TEXT, failure_reason TEXT,
      requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS password_reset_tokens (
      id INTEGER PRIMARY KEY AUTOINCREMENT, producer_id INTEGER NOT NULL REFERENCES producers(id) ON DELETE CASCADE,
      token_hash TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL, used_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS admin_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, reference TEXT, details TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)
    _add_column(conn,"orders","failure_reason","TEXT")
    _add_column(conn,"orders","completed_at","TEXT")
    conn.commit(); conn.close()

def slugify(value):
    value=re.sub(r"[^a-z0-9]+","-",(value or "").lower()).strip("-")
    return value or "producer"

def unique_slug(conn,name):
    base=slugify(name); slug=base; n=2
    while conn.execute("SELECT 1 FROM producers WHERE slug=?",(slug,)).fetchone():
        slug=f"{base}-{n}"; n+=1
    return slug
