import base64
import os
import re
import time
from datetime import datetime

import requests


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SIMULATE = (
    os.getenv(
        "MPESA_SIMULATE",
        "true",
    )
    .strip()
    .lower()
    != "false"
)

CONSUMER_KEY = os.getenv(
    "MPESA_CONSUMER_KEY",
    "",
).strip()

CONSUMER_SECRET = os.getenv(
    "MPESA_CONSUMER_SECRET",
    "",
).strip()

SHORTCODE = os.getenv(
    "MPESA_SHORTCODE",
    "174379",
).strip()

PASSKEY = os.getenv(
    "MPESA_PASSKEY",
    "",
).strip()

CALLBACK_URL = os.getenv(
    "MPESA_CALLBACK_URL",
    "",
).strip()

BASE_URL = os.getenv(
    "MPESA_BASE_URL",
    "https://sandbox.safaricom.co.ke",
).rstrip("/")


PHONE_PATTERN = re.compile(
    r"^(?:\+?254|0)?7\d{8}$"
)


class MpesaError(Exception):
    pass


# ---------------------------------------------------------------------------
# Phone normalization
# ---------------------------------------------------------------------------

def normalize_phone(phone: str) -> str:
    """
    Converts:

        0712345678
        712345678
        254712345678
        +254712345678

    into:

        254712345678
    """

    if not isinstance(phone, str):
        raise MpesaError(
            "Invalid M-Pesa phone number."
        )

    value = (
        phone.strip()
        .replace(" ", "")
        .replace("-", "")
    )

    if not PHONE_PATTERN.fullmatch(value):
        raise MpesaError(
            "Enter a valid Kenyan M-Pesa number, "
            "for example 0712345678."
        )

    if value.startswith("+254"):
        value = value[1:]

    if value.startswith("0"):
        value = "254" + value[1:]

    if value.startswith("7"):
        value = "254" + value

    if not re.fullmatch(
        r"2547\d{8}",
        value,
    ):
        raise MpesaError(
            "Invalid M-Pesa phone number."
        )

    return value


# ---------------------------------------------------------------------------
# Daraja authentication
# ---------------------------------------------------------------------------

def _get_access_token() -> str:
    try:
        response = requests.get(
            (
                f"{BASE_URL}/oauth/v1/"
                f"generate?grant_type=client_credentials"
            ),
            auth=(
                CONSUMER_KEY,
                CONSUMER_SECRET,
            ),
            timeout=20,
        )

        response.raise_for_status()

        data = response.json()

        token = data.get(
            "access_token"
        )

        if not token:
            raise MpesaError(
                "M-Pesa did not return an access token."
            )

        return token

    except requests.RequestException as exc:
        raise MpesaError(
            "Could not connect to M-Pesa."
        ) from exc


def _password_and_timestamp():
    timestamp = datetime.now().strftime(
        "%Y%m%d%H%M%S"
    )

    raw = (
        f"{SHORTCODE}"
        f"{PASSKEY}"
        f"{timestamp}"
    )

    password = base64.b64encode(
        raw.encode("utf-8")
    ).decode("utf-8")

    return password, timestamp


# ---------------------------------------------------------------------------
# STK Push
# ---------------------------------------------------------------------------

def stk_push(
    phone: str,
    amount: int,
    account_ref: str,
    description: str,
):
    normalized_phone = normalize_phone(phone)

    if (
        not isinstance(amount, int)
        or amount <= 0
    ):
        raise MpesaError(
            "Invalid payment amount."
        )

    # ---------------------------------------------------------------
    # Simulation mode
    # ---------------------------------------------------------------

    if SIMULATE:
        fake_id = (
            f"SIM-{int(time.time() * 1000)}"
            f"-{os.urandom(4).hex()}"
        )

        return {
            "checkout_request_id": fake_id,
            "simulated": True,
            "raw": {},
        }

    # ---------------------------------------------------------------
    # Production validation
    # ---------------------------------------------------------------

    if not all(
        [
            CONSUMER_KEY,
            CONSUMER_SECRET,
            SHORTCODE,
            PASSKEY,
            CALLBACK_URL,
        ]
    ):
        raise MpesaError(
            "M-Pesa is not configured correctly."
        )

    token = _get_access_token()

    password, timestamp = (
        _password_and_timestamp()
    )

    payload = {
        "BusinessShortCode": SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType":
            "CustomerPayBillOnline",
        "Amount": amount,
        "PartyA": normalized_phone,
        "PartyB": SHORTCODE,
        "PhoneNumber": normalized_phone,
        "CallBackURL": CALLBACK_URL,
        "AccountReference":
            str(account_ref)[:12],
        "TransactionDesc":
            str(description)[:20],
    }

    headers = {
        "Authorization":
            f"Bearer {token}",
        "Content-Type":
            "application/json",
    }

    try:
        response = requests.post(
            (
                f"{BASE_URL}/mpesa/stkpush/"
                f"v1/processrequest"
            ),
            json=payload,
            headers=headers,
            timeout=20,
        )

        response.raise_for_status()

        data = response.json()

    except requests.RequestException as exc:
        raise MpesaError(
            "M-Pesa STK Push could not be initiated."
        ) from exc

    checkout_request_id = data.get(
        "CheckoutRequestID"
    )

    if not checkout_request_id:
        error_message = (
            data.get("errorMessage")
            or data.get("ResponseDescription")
            or "M-Pesa rejected the payment request."
        )

        raise MpesaError(
            str(error_message)
        )

    return {
        "checkout_request_id":
            checkout_request_id,
        "simulated":
            False,
        "raw":
            data,
    }


# ---------------------------------------------------------------------------
# Producer payout placeholder
# ---------------------------------------------------------------------------

def initiate_producer_payout(
    phone: str,
    amount: int,
    reference: str,
):
    """
    This function deliberately does NOT pretend that a real payout
    happened in production.

    In simulation mode, it returns a simulated successful reference.

    For production, connect this function to your approved Daraja B2C
    payout/disbursement flow and only mark the withdrawal completed
    after the actual provider callback confirms success.
    """

    normalized_phone = normalize_phone(phone)

    if (
        not isinstance(amount, int)
        or amount <= 0
    ):
        raise MpesaError(
            "Invalid withdrawal amount."
        )

    if SIMULATE:
        return {
            "accepted": True,
            "simulated": True,
            "reference": (
                f"SIM-PAYOUT-"
                f"{reference}-"
                f"{os.urandom(4).hex()}"
            ),
        }

    raise MpesaError(
        "Automatic producer payouts are not yet "
        "configured for production."
    )
