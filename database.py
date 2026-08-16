import re
import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).parent / "beatstore.db"


# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


# ---------------------------------------------------------------------------
# Producer slug helpers
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        (name or "").lower(),
    ).strip("-")

    return slug or "producer"


def unique_slug(conn, name: str) -> str:
    base = slugify(name)
    slug = base
    number = 2

    while conn.execute(
        "SELECT 1 FROM producers WHERE slug = ?",
        (slug,),
    ).fetchone():
        slug = f"{base}-{number}"
        number += 1

    return slug


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------

def _column_exists(conn, table: str, column: str) -> bool:
    columns = conn.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    return any(
        row["name"] == column
        for row in columns
    )


def _add_column_if_missing(
    conn,
    table: str,
    column: str,
    definition: str,
):
    if not _column_exists(conn, table, column):
        conn.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN {column} {definition}"
        )


# ---------------------------------------------------------------------------
# Database initialization
# ---------------------------------------------------------------------------

def init_db():
    conn = get_db()

    try:
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

                created_at TEXT NOT NULL
                    DEFAULT (datetime('now'))
            );


            CREATE TABLE IF NOT EXISTS beats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                producer_id INTEGER NOT NULL
                    REFERENCES producers(id)
                    ON DELETE CASCADE,

                title TEXT NOT NULL,
                genre TEXT DEFAULT '',
                bpm INTEGER,
                price INTEGER NOT NULL,

                cover_path TEXT NOT NULL,
                audio_path TEXT NOT NULL,

                is_hot_pick INTEGER NOT NULL DEFAULT 0,

                created_at TEXT NOT NULL
                    DEFAULT (datetime('now'))
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

                created_at TEXT NOT NULL
                    DEFAULT (datetime('now')),

                completed_at TEXT
            );


            /*
            Producer wallet.

            available_balance:
                Money the producer can currently withdraw.

            pending_withdrawal:
                Money reserved for a withdrawal request currently
                being processed.

            total_earnings:
                Total NET earnings ever credited to the producer.

            total_withdrawn:
                Total money successfully paid to producer.
            */
            CREATE TABLE IF NOT EXISTS producer_wallets (
                producer_id INTEGER PRIMARY KEY
                    REFERENCES producers(id)
                    ON DELETE CASCADE,

                available_balance INTEGER NOT NULL DEFAULT 0,

                pending_withdrawal INTEGER NOT NULL DEFAULT 0,

                total_earnings INTEGER NOT NULL DEFAULT 0,

                total_withdrawn INTEGER NOT NULL DEFAULT 0,

                updated_at TEXT NOT NULL
                    DEFAULT (datetime('now'))
            );


            /*
            Wallet ledger.

            This provides an audit trail for every wallet movement.

            Examples:
                sale_credit
                withdrawal_requested
                withdrawal_completed
                withdrawal_failed_return
            */
            CREATE TABLE IF NOT EXISTS wallet_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                producer_id INTEGER NOT NULL
                    REFERENCES producers(id)
                    ON DELETE CASCADE,

                order_id INTEGER
                    REFERENCES orders(id),

                withdrawal_id INTEGER,

                transaction_type TEXT NOT NULL,

                amount INTEGER NOT NULL,

                reference TEXT,

                created_at TEXT NOT NULL
                    DEFAULT (datetime('now'))
            );


            /*
            Withdrawal records.

            requested
            processing
            completed
            failed
            cancelled
            */
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                producer_id INTEGER NOT NULL
                    REFERENCES producers(id)
                    ON DELETE CASCADE,

                amount INTEGER NOT NULL,

                phone TEXT NOT NULL,

                status TEXT NOT NULL DEFAULT 'requested',

                payout_reference TEXT,

                failure_reason TEXT,

                requested_at TEXT NOT NULL
                    DEFAULT (datetime('now')),

                completed_at TEXT
            );


            /*
            Platform ledger.

            Every successful order records the platform fee here.
            This is internal and not shown as a deduction on the
            producer's main wallet screen.
            */
            CREATE TABLE IF NOT EXISTS platform_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                order_id INTEGER NOT NULL UNIQUE
                    REFERENCES orders(id),

                gross_amount INTEGER NOT NULL,

                platform_fee INTEGER NOT NULL,

                producer_credit INTEGER NOT NULL,

                created_at TEXT NOT NULL
                    DEFAULT (datetime('now'))
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

            CREATE INDEX IF NOT EXISTS idx_wallet_transactions_producer
                ON wallet_transactions(producer_id);

            CREATE INDEX IF NOT EXISTS idx_wallet_transactions_order
                ON wallet_transactions(order_id);

            CREATE INDEX IF NOT EXISTS idx_withdrawals_producer
                ON withdrawals(producer_id);

            CREATE INDEX IF NOT EXISTS idx_withdrawals_status
                ON withdrawals(status);
            """
        )

        # -------------------------------------------------------------------
        # Safe migrations for databases created by earlier versions
        # -------------------------------------------------------------------

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

    finally:
        conn.close()
