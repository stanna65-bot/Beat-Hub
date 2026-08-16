"""
Minimal Daraja (M-Pesa) STK Push helper.

Two modes, controlled by SIMULATE below:
- SIMULATE = True  -> no real network calls. Perfect for demoing the full
  buy flow locally before you have real/sandbox credentials handy or when
  your network can't reach Safaricom's servers. It auto-marks the order as
  "completed" after a couple of seconds, just like a real payment would.
- SIMULATE = False -> real calls to the Daraja sandbox (or prod, once you
  swap the base URL and credentials). Requires:
    1. An app on https://developer.safaricom.co.ke with Consumer Key/Secret
    2. Your Till/Paybill shortcode + Lipa Na M-Pesa passkey
    3. A publicly reachable CALLBACK_URL (use ngrok while testing locally,
       Daraja cannot call back into localhost)

Fill in the CONFIG block once you have real credentials, flip SIMULATE to
False, and nothing else in the app needs to change.
"""

import base64
import os
import time
from datetime import datetime

import requests

# ---------------------------------------------------------------------------
# CONFIG - all real credentials come from environment variables so nothing
# secret ever lives in source control or gets baked into a deployed image.
# Set these on your host (Render/Railway/Fly/etc.) before flipping SIMULATE
# off. See README.md for the full Daraja setup checklist.
# ---------------------------------------------------------------------------
SIMULATE = os.getenv("MPESA_SIMULATE", "true").lower() != "false"

CONSUMER_KEY = os.getenv("MPESA_CONSUMER_KEY", "")
CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET", "")
SHORTCODE = os.getenv("MPESA_SHORTCODE", "174379")  # Daraja sandbox default test shortcode
PASSKEY = os.getenv("MPESA_PASSKEY", "")
CALLBACK_URL = os.getenv("MPESA_CALLBACK_URL", "")  # must be a public HTTPS URL

BASE_URL = os.getenv("MPESA_BASE_URL", "https://sandbox.safaricom.co.ke")  # api.safaricom.co.ke for production

if not SIMULATE and not all([CONSUMER_KEY, CONSUMER_SECRET, PASSKEY, CALLBACK_URL]):
    raise RuntimeError(
        "MPESA_SIMULATE is false but one or more of MPESA_CONSUMER_KEY, "
        "MPESA_CONSUMER_SECRET, MPESA_PASSKEY, MPESA_CALLBACK_URL is missing. "
        "Set them as environment variables before going live."
    )


def _get_access_token():
    resp = requests.get(
        f"{BASE_URL}/oauth/v1/generate?grant_type=client_credentials",
        auth=(CONSUMER_KEY, CONSUMER_SECRET),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _password_and_timestamp():
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    raw = f"{SHORTCODE}{PASSKEY}{timestamp}"
    password = base64.b64encode(raw.encode()).decode()
    return password, timestamp


def stk_push(phone: str, amount: int, account_ref: str, description: str):
    """
    Kick off an STK push. Returns a dict with at least:
      { "checkout_request_id": str | None, "simulated": bool }
    """
    if SIMULATE:
        # Fake a CheckoutRequestID; the app will auto-complete this order
        # shortly after, see simulate_confirm() below.
        fake_id = f"SIM-{int(time.time() * 1000)}"
        return {"checkout_request_id": fake_id, "simulated": True}

    token = _get_access_token()
    password, timestamp = _password_and_timestamp()

    # Daraja expects phone in 2547XXXXXXXX format
    phone = phone.strip().replace("+", "")
    if phone.startswith("0"):
        phone = "254" + phone[1:]

    payload = {
        "BusinessShortCode": SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": amount,
        "PartyA": phone,
        "PartyB": SHORTCODE,
        "PhoneNumber": phone,
        "CallBackURL": CALLBACK_URL,
        "AccountReference": account_ref[:12],
        "TransactionDesc": description[:20],
    }
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.post(
        f"{BASE_URL}/mpesa/stkpush/v1/processrequest",
        json=payload,
        headers=headers,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "checkout_request_id": data.get("CheckoutRequestID"),
        "simulated": False,
        "raw": data,
    }
