import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "beatstore.db"


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "producer"


def unique_slug(conn, name: str) -> str:
    base = slugify(name)
    slug = base
    i = 2

    while conn.execute(
        "SELECT 1 FROM producers WHERE slug = ?",
        (slug,),
    ).fetchone():
        slug = f"{base}-{i}"
        i += 1

    return slug


def _column_exists(conn, table: str, column: str) -> bool:
    columns = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in columns)


def _add_column_if_missing(conn, table: str, column: str, definition: str):
    if not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    conn = get_db()

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS producers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT 'Your Name',
            bio TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            profile_photo TEXT DEFAULT '',
            payout_phone TEXT DEFAULT '',
            commission_rate REAL NOT NULL DEFAULT 10.0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS beats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producer_id INTEGER NOT NULL
                REFERENCES producers(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            genre TEXT DEFAULT '',
            bpm INTEGER,
            price INTEGER NOT NULL,
            cover_path TEXT NOT NULL,
            audio_path TEXT NOT NULL,
            is_hot_pick INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            beat_id INTEGER NOT NULL
                REFERENCES beats(id),
            buyer_phone TEXT NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            checkout_request_id TEXT,
            mpesa_receipt TEXT,
            download_token TEXT UNIQUE,
            platform_fee INTEGER NOT NULL DEFAULT 0,
            producer_payout INTEGER NOT NULL DEFAULT 0,
            commission_rate_locked REAL,
            split_applied_at TEXT,
            payout_status TEXT NOT NULL DEFAULT 'unpaid',
            payout_reference TEXT,
            payout_at TEXT,
            failure_reason TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_beats_producer
            ON beats(producer_id);

        CREATE INDEX IF NOT EXISTS idx_beats_hot_picks
            ON beats(producer_id, is_hot_pick);

        CREATE INDEX IF NOT EXISTS idx_orders_beat
            ON orders(beat_id);

        CREATE INDEX IF NOT EXISTS idx_orders_checkout_request
            ON orders(checkout_request_id);

        CREATE INDEX IF NOT EXISTS idx_orders_status
            ON orders(status);

        CREATE INDEX IF NOT EXISTS idx_orders_payout_status
            ON orders(payout_status);
        """
    )

    # Safe migrations for an existing database.
    _add_column_if_missing(
        conn,
        "beats",
        "is_hot_pick",
        "INTEGER NOT NULL DEFAULT 0",
    )

    _add_column_if_missing(
        conn,
        "orders",
        "commission_rate_locked",
        "REAL",
    )

    _add_column_if_missing(
        conn,
        "orders",
        "split_applied_at",
        "TEXT",
    )

    _add_column_if_missing(
        conn,
        "orders",
        "payout_reference",
        "TEXT",
    )

    _add_column_if_missing(
        conn,
        "orders",
        "failure_reason",
        "TEXT",
    )

    _add_column_if_missing(
        conn,
        "orders",
        "completed_at",
        "TEXT",
    )

    conn.commit()
    conn.close()
