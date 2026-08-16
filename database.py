import os
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.exc import IntegrityError


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./beat_hub.db")

# Render/Railway/Postgres compatibility
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    phone = Column(String(30), nullable=True)
    role = Column(String(50), default="producer", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    beats = relationship(
        "Beat",
        back_populates="producer",
        cascade="all, delete-orphan",
    )

    services = relationship(
        "Service",
        back_populates="producer",
        cascade="all, delete-orphan",
    )

    bookings = relationship(
        "Booking",
        foreign_keys="Booking.producer_id",
        back_populates="producer",
    )

    wallet = relationship(
        "Wallet",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )


class Beat(Base):
    __tablename__ = "beats"

    id = Column(Integer, primary_key=True, index=True)
    producer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    genre = Column(String(100), nullable=True)
    bpm = Column(Integer, nullable=True)
    price = Column(Numeric(12, 2), default=0, nullable=False)
    audio_url = Column(String(500), nullable=True)
    cover_url = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_featured = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    producer = relationship("User", back_populates="beats")


class Service(Base):
    __tablename__ = "services"

    id = Column(Integer, primary_key=True, index=True)
    producer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)
    price = Column(Numeric(12, 2), default=0, nullable=False)
    duration_minutes = Column(Integer, default=60, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    producer = relationship("User", back_populates="services")
    bookings = relationship("Booking", back_populates="service")


class Availability(Base):
    __tablename__ = "availability"

    id = Column(Integer, primary_key=True, index=True)
    producer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    day_of_week = Column(Integer, nullable=False)
    start_time = Column(String(10), nullable=False)
    end_time = Column(String(10), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    producer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False)

    booking_date = Column(DateTime, nullable=False)
    notes = Column(Text, nullable=True)

    amount = Column(Numeric(12, 2), default=0, nullable=False)

    status = Column(
        String(50),
        default="pending",
        nullable=False,
    )

    payment_status = Column(
        String(50),
        default="pending",
        nullable=False,
    )

    proposed_time = Column(DateTime, nullable=True)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    producer = relationship(
        "User",
        foreign_keys=[producer_id],
        back_populates="bookings",
    )

    customer = relationship(
        "User",
        foreign_keys=[customer_id],
    )

    service = relationship(
        "Service",
        back_populates="bookings",
    )


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    receiver_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        unique=True,
        nullable=False,
    )

    balance = Column(
        Numeric(14, 2),
        default=Decimal("0.00"),
        nullable=False,
    )

    pending_balance = Column(
        Numeric(14, 2),
        default=Decimal("0.00"),
        nullable=False,
    )

    total_earned = Column(
        Numeric(14, 2),
        default=Decimal("0.00"),
        nullable=False,
    )

    total_withdrawn = Column(
        Numeric(14, 2),
        default=Decimal("0.00"),
        nullable=False,
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    user = relationship(
        "User",
        back_populates="wallet",
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    reference = Column(
        String(150),
        unique=True,
        nullable=False,
        index=True,
    )

    transaction_type = Column(
        String(100),
        nullable=False,
    )

    amount = Column(
        Numeric(14, 2),
        nullable=False,
    )

    status = Column(
        String(50),
        default="pending",
        nullable=False,
    )

    description = Column(Text, nullable=True)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


class PlatformWallet(Base):
    __tablename__ = "platform_wallet"

    id = Column(Integer, primary_key=True)

    balance = Column(
        Numeric(14, 2),
        default=Decimal("0.00"),
        nullable=False,
    )

    pending_balance = Column(
        Numeric(14, 2),
        default=Decimal("0.00"),
        nullable=False,
    )

    total_earned = Column(
        Numeric(14, 2),
        default=Decimal("0.00"),
        nullable=False,
    )

    total_withdrawn = Column(
        Numeric(14, 2),
        default=Decimal("0.00"),
        nullable=False,
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id = Column(Integer, primary_key=True, index=True)

    reference = Column(
        String(150),
        unique=True,
        nullable=False,
        index=True,
    )

    entry_type = Column(
        String(100),
        nullable=False,
    )

    source_type = Column(
        String(100),
        nullable=True,
    )

    source_id = Column(
        Integer,
        nullable=True,
    )

    producer_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
    )

    gross_amount = Column(
        Numeric(14, 2),
        default=Decimal("0.00"),
        nullable=False,
    )

    producer_amount = Column(
        Numeric(14, 2),
        default=Decimal("0.00"),
        nullable=False,
    )

    platform_amount = Column(
        Numeric(14, 2),
        default=Decimal("0.00"),
        nullable=False,
    )

    status = Column(
        String(50),
        default="completed",
        nullable=False,
    )

    description = Column(Text, nullable=True)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


class Withdrawal(Base):
    __tablename__ = "withdrawals"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
    )

    amount = Column(
        Numeric(14, 2),
        nullable=False,
    )

    phone = Column(
        String(30),
        nullable=False,
    )

    withdrawal_type = Column(
        String(50),
        default="producer",
        nullable=False,
    )

    reference = Column(
        String(150),
        unique=True,
        nullable=False,
        index=True,
    )

    status = Column(
        String(50),
        default="pending",
        nullable=False,
    )

    mpesa_reference = Column(
        String(150),
        nullable=True,
    )

    failure_reason = Column(
        Text,
        nullable=True,
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    completed_at = Column(
        DateTime,
        nullable=True,
    )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    Base.metadata.create_all(bind=engine)


def get_or_create_wallet(db, user_id):
    wallet = (
        db.query(Wallet)
        .filter(Wallet.user_id == user_id)
        .first()
    )

    if wallet:
        return wallet

    wallet = Wallet(
        user_id=user_id,
        balance=Decimal("0.00"),
        pending_balance=Decimal("0.00"),
        total_earned=Decimal("0.00"),
        total_withdrawn=Decimal("0.00"),
    )

    db.add(wallet)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        wallet = (
            db.query(Wallet)
            .filter(Wallet.user_id == user_id)
            .first()
        )

    return wallet


def get_platform_wallet(db):
    wallet = db.query(PlatformWallet).first()

    if wallet:
        return wallet

    wallet = PlatformWallet(
        balance=Decimal("0.00"),
        pending_balance=Decimal("0.00"),
        total_earned=Decimal("0.00"),
        total_withdrawn=Decimal("0.00"),
    )

    db.add(wallet)
    db.commit()
    db.refresh(wallet)

    return wallet
