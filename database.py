import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "beatstore.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "producer"


def unique_slug(conn, name: str) -> str:
    base = slugify(name)
    slug = base
    i = 2
    while conn.execute("SELECT 1 FROM producers WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base}-{i}"
        i += 1
    return slug


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS producers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,          -- used in the shareable link: /p/<slug>
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT 'Your Name',
            bio TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            profile_photo TEXT DEFAULT '',
            payout_phone TEXT DEFAULT '',       -- M-Pesa number the producer gets paid out to
            commission_rate REAL DEFAULT 10.0,  -- platform's cut, in percent (10.0 = 10%)
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS beats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producer_id INTEGER NOT NULL REFERENCES producers(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            genre TEXT DEFAULT '',
            bpm INTEGER,
            price INTEGER NOT NULL,
            cover_path TEXT NOT NULL,
            audio_path TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            beat_id INTEGER NOT NULL REFERENCES beats(id),
            buyer_phone TEXT NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',      -- pending | completed | failed
            checkout_request_id TEXT,
            mpesa_receipt TEXT,
            download_token TEXT UNIQUE,                    -- unguessable token, set once payment completes
            platform_fee INTEGER DEFAULT 0,               -- your cut, in KES, locked in at payment time
            producer_payout INTEGER DEFAULT 0,             -- producer's cut, in KES
            payout_status TEXT NOT NULL DEFAULT 'unpaid',  -- unpaid | paid_out
            payout_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_beats_producer ON beats(producer_id);
        CREATE INDEX IF NOT EXISTS idx_orders_beat ON orders(beat_id);
        """
    )
    conn.commit()
    conn.close()
