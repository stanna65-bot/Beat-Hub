import os
import uuid
from datetime import datetime


MPESA_MODE = os.getenv("MPESA_MODE", "mock").lower()


def normalize_phone(phone: str) -> str:
    if not phone:
        return ""

    phone = phone.strip().replace(" ", "").replace("-", "")

    if phone.startswith("+"):
        phone = phone[1:]

    if phone.startswith("07") or phone.startswith("01"):
        phone = "254" + phone[1:]

    return phone


def initiate_stk_push(
    phone: str,
    amount: float,
    account_reference: str,
    description: str = "BeatHub Payment",
):
    """
    Mock STK Push.

    Replace this function with the live Safaricom Daraja
    implementation when you are ready for real M-Pesa testing.
    """

    phone = normalize_phone(phone)

    if MPESA_MODE != "live":
        return {
            "success": True,
            "mode": "mock",
            "checkout_request_id": f"MOCK-STK-{uuid.uuid4().hex[:16]}",
            "merchant_request_id": f"MOCK-MERCHANT-{uuid.uuid4().hex[:16]}",
            "phone": phone,
            "amount": float(amount),
            "account_reference": account_reference,
            "description": description,
            "message": "Mock STK Push accepted.",
        }

    raise RuntimeError(
        "Live M-Pesa is not configured yet. "
        "Set MPESA_MODE=live after configuring Safaricom Daraja."
    )


def initiate_b2c_payment(
    phone: str,
    amount: float,
    reference: str,
    remarks: str = "BeatHub Withdrawal",
):
    """
    Mock B2C payout.

    This is intentionally the only external limitation remaining.
    Replace with the live Safaricom B2C implementation before
    production withdrawals.
    """

    phone = normalize_phone(phone)

    if MPESA_MODE != "live":
        return {
            "success": True,
            "mode": "mock",
            "conversation_id": f"MOCK-B2C-{uuid.uuid4().hex[:16]}",
            "originator_conversation_id": f"MOCK-ORIGINATOR-{uuid.uuid4().hex[:16]}",
            "transaction_reference": f"MOCK-MPESA-{uuid.uuid4().hex[:16]}",
            "phone": phone,
            "amount": float(amount),
            "reference": reference,
            "remarks": remarks,
            "timestamp": datetime.utcnow().isoformat(),
            "message": "Mock B2C payout accepted.",
        }

    raise RuntimeError(
        "Live M-Pesa B2C is not configured yet. "
        "Set MPESA_MODE=live after configuring Safaricom Daraja."
    )
