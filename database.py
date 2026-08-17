import sqlite3
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "beat_hub.db"


def get_db():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _table_exists(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    ).fetchone() is not None


def _columns(conn, table):
    return {
        r["name"]
        for r in conn.execute(
            f"PRAGMA table_info({table})"
        )
    }


def _add_column(conn, table, column, definition):
    if not _table_exists(conn, table):
        return

    if column not in _columns(conn, table):
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def slugify(value):
    value = re.sub(
        r"[^a-z0-9]+",
        "-",
        (value or "").casefold()
    ).strip("-")

    return value or "producer"


def unique_slug(conn, name):
    base = slugify(name)
    slug = base
    n = 2

    while conn.execute(
        "SELECT 1 FROM producers WHERE slug=?",
        (slug,)
    ).fetchone():
        slug = f"{base}-{n}"
        n += 1

    return slug


def _migrate_platform_ledger(conn):
    """
    Upgrade the original order-only ledger to the beat/session ledger.

    Existing ledger rows are retained as beat transactions.
    No balances are recalculated here.
    """

    if not _table_exists(conn, "platform_ledger"):
        conn.execute("""
            CREATE TABLE platform_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                gross_amount INTEGER NOT NULL,
                platform_fee INTEGER NOT NULL,
                producer_credit INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_type, source_id)
            )
        """)
        return

    cols = _columns(conn, "platform_ledger")

    if "source_type" in cols and "source_id" in cols:
        return

    conn.execute(
        "ALTER TABLE platform_ledger RENAME TO platform_ledger_legacy"
    )

    conn.execute("""
        CREATE TABLE platform_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            gross_amount INTEGER NOT NULL,
            platform_fee INTEGER NOT NULL,
            producer_credit INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_type, source_id)
        )
    """)

    legacy_cols = _columns(
        conn,
        "platform_ledger_legacy"
    )

    if "order_id" in legacy_cols:
        conn.execute("""
            INSERT OR IGNORE INTO platform_ledger(
                source_type,
                source_id,
                gross_amount,
                platform_fee,
                producer_credit,
                created_at
            )
            SELECT
                'beat',
                order_id,
                gross_amount,
                platform_fee,
                producer_credit,
                created_at
            FROM platform_ledger_legacy
            WHERE order_id IS NOT NULL
        """)


def init_db():
    conn = get_db()

    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS producers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            bio TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            payout_phone TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS beats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producer_id INTEGER NOT NULL
                REFERENCES producers(id)
                ON DELETE CASCADE,

            title TEXT NOT NULL,
            genre TEXT NOT NULL DEFAULT '',
            bpm INTEGER,
            price INTEGER NOT NULL CHECK(price>0),

            cover_path TEXT NOT NULL,
            audio_path TEXT NOT NULL,

            is_hot_pick INTEGER NOT NULL DEFAULT 0,

            license_type TEXT NOT NULL
                DEFAULT 'non_exclusive',

            status TEXT NOT NULL
                DEFAULT 'available',

            sold_at TEXT,
            sold_order_id INTEGER,

            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            beat_id INTEGER NOT NULL
                REFERENCES beats(id),

            buyer_phone TEXT NOT NULL,
            amount INTEGER NOT NULL,

            status TEXT NOT NULL
                DEFAULT 'pending',

            checkout_request_id TEXT UNIQUE,
            mpesa_receipt TEXT,

            platform_fee INTEGER NOT NULL DEFAULT 0,
            producer_payout INTEGER NOT NULL DEFAULT 0,

            commission_rate_locked REAL,
            split_applied_at TEXT,

            download_token TEXT UNIQUE,

            failure_reason TEXT,

            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS producer_wallets (
            producer_id INTEGER PRIMARY KEY
                REFERENCES producers(id)
                ON DELETE CASCADE,

            available_balance INTEGER NOT NULL DEFAULT 0,
            pending_withdrawal INTEGER NOT NULL DEFAULT 0,
            total_earnings INTEGER NOT NULL DEFAULT 0,
            total_withdrawn INTEGER NOT NULL DEFAULT 0,

            updated_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            producer_id INTEGER NOT NULL
                REFERENCES producers(id),

            amount INTEGER NOT NULL
                CHECK(amount>0),

            phone TEXT NOT NULL,

            status TEXT NOT NULL
                DEFAULT 'pending',

            payout_reference TEXT,
            failure_reason TEXT,

            requested_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS platform_wallet (
            id INTEGER PRIMARY KEY
                CHECK(id=1),

            available_balance INTEGER NOT NULL DEFAULT 0,
            pending_withdrawal INTEGER NOT NULL DEFAULT 0,
            total_earnings INTEGER NOT NULL DEFAULT 0,
            total_withdrawn INTEGER NOT NULL DEFAULT 0,

            updated_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP
        );

        INSERT OR IGNORE INTO platform_wallet(id)
        VALUES(1);

        CREATE TABLE IF NOT EXISTS platform_withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            amount INTEGER NOT NULL
                CHECK(amount>0),

            phone TEXT NOT NULL,

            status TEXT NOT NULL
                DEFAULT 'pending',

            payout_reference TEXT,
            failure_reason TEXT,

            requested_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            producer_id INTEGER NOT NULL
                REFERENCES producers(id)
                ON DELETE CASCADE,

            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,

            used_at TEXT,

            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS admin_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            event_type TEXT NOT NULL,
            reference TEXT,
            details TEXT,

            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS session_services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            producer_id INTEGER NOT NULL
                REFERENCES producers(id)
                ON DELETE CASCADE,

            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',

            duration_minutes INTEGER NOT NULL
                CHECK(duration_minutes BETWEEN 15 AND 720),

            price INTEGER NOT NULL
                CHECK(price>0),

            location TEXT NOT NULL DEFAULT '',

            active INTEGER NOT NULL DEFAULT 1,

            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS producer_availability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            producer_id INTEGER NOT NULL
                REFERENCES producers(id)
                ON DELETE CASCADE,

            weekday INTEGER NOT NULL
                CHECK(weekday BETWEEN 0 AND 6),

            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,

            slot_minutes INTEGER NOT NULL DEFAULT 60
                CHECK(slot_minutes BETWEEN 15 AND 240),

            UNIQUE(producer_id, weekday)
        );

        CREATE TABLE IF NOT EXISTS session_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            producer_id INTEGER NOT NULL
                REFERENCES producers(id)
                ON DELETE CASCADE,

            service_id INTEGER NOT NULL
                REFERENCES session_services(id),

            client_name TEXT NOT NULL,
            client_phone TEXT NOT NULL,
            client_email TEXT NOT NULL DEFAULT '',

            start_at TEXT NOT NULL,
            end_at TEXT NOT NULL,

            amount INTEGER NOT NULL
                CHECK(amount>0),

            status TEXT NOT NULL
                DEFAULT 'pending',

            hold_expires_at TEXT,

            checkout_request_id TEXT UNIQUE,

            paid_at TEXT,
            cancelled_at TEXT,

            platform_fee INTEGER NOT NULL DEFAULT 0,
            producer_payout INTEGER NOT NULL DEFAULT 0,

            split_applied_at TEXT,

            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS booking_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            booking_id INTEGER NOT NULL
                REFERENCES session_bookings(id)
                ON DELETE CASCADE,

            sender_role TEXT NOT NULL
                CHECK(sender_role IN ('producer','client')),

            body TEXT NOT NULL,

            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS booking_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            booking_id INTEGER NOT NULL
                REFERENCES session_bookings(id)
                ON DELETE CASCADE,

            proposed_start_at TEXT NOT NULL,
            proposed_end_at TEXT NOT NULL,

            proposed_by TEXT NOT NULL
                CHECK(proposed_by IN ('producer','client')),

            confirmed_at TEXT,
            declined_at TEXT,

            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # -------------------------------------------------
        # SAFE MIGRATIONS FOR OLDER BEATHUB DATABASES
        # -------------------------------------------------

        _add_column(
            conn,
            "orders",
            "failure_reason",
            "TEXT"
        )

        _add_column(
            conn,
            "orders",
            "completed_at",
            "TEXT"
        )

        # -------------------------------------------------
        # EXCLUSIVE / NON-EXCLUSIVE BEAT MIGRATION
        # -------------------------------------------------

        _add_column(
            conn,
            "beats",
            "license_type",
            "TEXT NOT NULL DEFAULT 'non_exclusive'"
        )

        _add_column(
            conn,
            "beats",
            "status",
            "TEXT NOT NULL DEFAULT 'available'"
        )

        _add_column(
            conn,
            "beats",
            "sold_at",
            "TEXT"
        )

        _add_column(
            conn,
            "beats",
            "sold_order_id",
            "INTEGER"
        )

        # Existing beats are preserved and remain available
        # as non-exclusive beats unless explicitly changed
        # by a future producer action.
        conn.execute("""
            UPDATE beats
            SET license_type='non_exclusive'
            WHERE license_type IS NULL
               OR license_type=''
        """)

        conn.execute("""
            UPDATE beats
            SET status='available'
            WHERE status IS NULL
               OR status=''
        """)

        # -------------------------------------------------
        # SESSION MIGRATIONS
        # -------------------------------------------------

        _add_column(
            conn,
            "session_services",
            "active",
            "INTEGER NOT NULL DEFAULT 1"
        )

        _add_column(
            conn,
            "session_services",
            "created_at",
            "TEXT"
        )

        _add_column(
            conn,
            "session_bookings",
            "client_email",
            "TEXT NOT NULL DEFAULT ''"
        )

        _add_column(
            conn,
            "session_bookings",
            "hold_expires_at",
            "TEXT"
        )

        _add_column(
            conn,
            "session_bookings",
            "checkout_request_id",
            "TEXT"
        )

        _add_column(
            conn,
            "session_bookings",
            "paid_at",
            "TEXT"
        )

        _add_column(
            conn,
            "session_bookings",
            "cancelled_at",
            "TEXT"
        )

        _add_column(
            conn,
            "session_bookings",
            "platform_fee",
            "INTEGER NOT NULL DEFAULT 0"
        )

        _add_column(
            conn,
            "session_bookings",
            "producer_payout",
            "INTEGER NOT NULL DEFAULT 0"
        )

        _add_column(
            conn,
            "session_bookings",
            "split_applied_at",
            "TEXT"
        )

        _add_column(
            conn,
            "session_bookings",
            "created_at",
            "TEXT"
        )

        # -------------------------------------------------
        # PLATFORM LEDGER
        # -------------------------------------------------

        _migrate_platform_ledger(conn)

        # -------------------------------------------------
        # INDEXES
        # -------------------------------------------------

        conn.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_beats_producer
            ON beats(producer_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_beats_status
            ON beats(status)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_beats_license_type
            ON beats(license_type)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_beats_sold_order
            ON beats(sold_order_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_services_producer
            ON session_services(producer_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_bookings_producer_start
            ON session_bookings(
                producer_id,
                start_at
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_booking_messages_booking
            ON booking_messages(
                booking_id,
                id
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_booking_proposals_booking
            ON booking_proposals(
                booking_id,
                id
            )
        """)

        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_session_checkout_request
            ON session_bookings(
                checkout_request_id
            )
            WHERE checkout_request_id IS NOT NULL
        """)

        conn.commit()

    finally:
        conn.close()
