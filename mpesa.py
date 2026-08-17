import os
import uuid
import base64a
import hashlib
from datetime import datetime

import requests


# =========================================================
# CONFIGURATION
# =========================================================

MPESA_MODE = os.getenv("MPESA_MODE", "live").lower()

MPESA_CONSUMER_KEY = os.getenv("MPESA_CONSUMER_KEY", "")
MPESA_CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET", "")

MPESA_SHORTCODE = os.getenv("MPESA_SHORTCODE", "")
MPESA_PASSKEY = os.getenv("MPESA_PASSKEY", "")

MPESA_STK_CALLBACK_URL = os.getenv(
    "MPESA_STK_CALLBACK_URL",
    ""
)

MPESA_B2C_INITIATOR_NAME = os.getenv(
    "MPESA_B2C_INITIATOR_NAME",
    ""
)

MPESA_B2C_SECURITY_CREDENTIAL = os.getenv(
    "MPESA_B2C_SECURITY_CREDENTIAL",
    ""
)

MPESA_B2C_COMMAND_ID = os.getenv(
    "MPESA_B2C_COMMAND_ID",
    "BusinessPayment"
)

MPESA_B2C_RESULT_URL = os.getenv(
    "MPESA_B2C_RESULT_URL",
    ""
)

MPESA_B2C_TIMEOUT_URL = os.getenv(
    "MPESA_B2C_TIMEOUT_URL",
    ""
)

MPESA_ENVIRONMENT = os.getenv(
    "MPESA_ENVIRONMENT",
    "production"
).lower()


# =========================================================
# API URLS
# =========================================================

if MPESA_ENVIRONMENT == "sandbox":
    MPESA_BASE_URL = "https://sandbox.safaricom.co.ke"
else:
    MPESA_BASE_URL = "https://api.safaricom.co.ke"


OAUTH_URL = (
    f"{MPESA_BASE_URL}/oauth/v1/generate"
    "?grant_type=client_credentials"
)

STK_PUSH_URL = (
    f"{MPESA_BASE_URL}/mpesa/stkpush/v1/processrequest"
)

B2C_URL = (
    f"{MPESA_BASE_URL}/mpesa/b2c/v1/paymentrequest"
)


# =========================================================
# PHONE NUMBER
# =========================================================

def normalize_phone(phone):
    digits = "".join(
        c for c in (phone or "")
        if c.isdigit()
    )

    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]

    elif digits.startswith("7") and len(digits) == 9:
        digits = "254" + digits

    elif digits.startswith("254") and len(digits) == 12:
        pass

    else:
        raise ValueError(
            "Enter a valid Kenyan M-Pesa number."
        )

    if not (
        digits.startswith("2547")
        and len(digits) == 12
    ):
        raise ValueError(
            "Enter a valid Kenyan M-Pesa number."
        )

    return digits


# =========================================================
# VALIDATION
# =========================================================

def _require(value, name):
    if not value:
        raise RuntimeError(
            f"Missing required M-Pesa configuration: {name}"
        )


def _validate_stk_config():
    _require(
        MPESA_CONSUMER_KEY,
        "MPESA_CONSUMER_KEY"
    )

    _require(
        MPESA_CONSUMER_SECRET,
        "MPESA_CONSUMER_SECRET"
    )

    _require(
        MPESA_SHORTCODE,
        "MPESA_SHORTCODE"
    )

    _require(
        MPESA_PASSKEY,
        "MPESA_PASSKEY"
    )

    _require(
        MPESA_STK_CALLBACK_URL,
        "MPESA_STK_CALLBACK_URL"
    )


def _validate_b2c_config():
    _require(
        MPESA_CONSUMER_KEY,
        "MPESA_CONSUMER_KEY"
    )

    _require(
        MPESA_CONSUMER_SECRET,
        "MPESA_CONSUMER_SECRET"
    )

    _require(
        MPESA_B2C_INITIATOR_NAME,
        "MPESA_B2C_INITIATOR_NAME"
    )

    _require(
        MPESA_B2C_SECURITY_CREDENTIAL,
        "MPESA_B2C_SECURITY_CREDENTIAL"
    )

    _require(
        MPESA_B2C_RESULT_URL,
        "MPESA_B2C_RESULT_URL"
    )

    _require(
        MPESA_B2C_TIMEOUT_URL,
        "MPESA_B2C_TIMEOUT_URL"
    )


# =========================================================
# OAUTH ACCESS TOKEN
# =========================================================

def _get_access_token():
    _require(
        MPESA_CONSUMER_KEY,
        "MPESA_CONSUMER_KEY"
    )

    _require(
        MPESA_CONSUMER_SECRET,
        "MPESA_CONSUMER_SECRET"
    )

    credentials = (
        f"{MPESA_CONSUMER_KEY}:"
        f"{MPESA_CONSUMER_SECRET}"
    )

    encoded = base64.b64encode(
        credentials.encode("utf-8")
    ).decode("utf-8")

    headers = {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    response = requests.get(
        OAUTH_URL,
        headers=headers,
        timeout=30
    )

    if response.status_code != 200:
        raise RuntimeError(
            "M-Pesa OAuth failed: "
            f"{response.status_code} "
            f"{response.text}"
        )

    data = response.json()

    token = data.get("access_token")

    if not token:
        raise RuntimeError(
            "M-Pesa OAuth response did not contain "
            "an access token."
        )

    return token


# =========================================================
# STK PASSWORD
# =========================================================

def _stk_password():
    timestamp = datetime.now().strftime(
        "%Y%m%d%H%M%S"
    )

    raw = (
        f"{MPESA_SHORTCODE}"
        f"{MPESA_PASSKEY}"
        f"{timestamp}"
    )

    password = base64.b64encode(
        raw.encode("utf-8")
    ).decode("utf-8")

    return password, timestamp


# =========================================================
# STK PUSH
# =========================================================

def stk_push(
    phone,
    amount,
    account_ref,
    description
):
    phone = normalize_phone(phone)

    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise ValueError(
            "M-Pesa amount must be a valid number."
        )

    if amount <= 0:
        raise ValueError(
            "M-Pesa amount must be greater than zero."
        )

    if MPESA_MODE == "mock":
        return {
            "checkout_request_id":
                "MOCK-" + uuid.uuid4().hex,
            "merchant_request_id":
                "MOCK-MERCHANT-" + uuid.uuid4().hex,
            "simulated": True
        }

    _validate_stk_config()

    token = _get_access_token()

    password, timestamp = _stk_password()

    payload = {
        "BusinessShortCode":
            MPESA_SHORTCODE,

        "Password":
            password,

        "Timestamp":
            timestamp,

        "TransactionType":
            "CustomerPayBillOnline",

        "Amount":
            amount,

        "PartyA":
            phone,

        "PartyB":
            MPESA_SHORTCODE,

        "PhoneNumber":
            phone,

        "CallBackURL":
            MPESA_STK_CALLBACK_URL,

        "AccountReference":
            str(account_ref)[:12],

        "TransactionDesc":
            str(description)[:20]
    }

    headers = {
        "Authorization":
            f"Bearer {token}",

        "Content-Type":
            "application/json"
    }

    response = requests.post(
        STK_PUSH_URL,
        json=payload,
        headers=headers,
        timeout=30
    )

    try:
        data = response.json()
    except ValueError:
        data = {
            "raw_response":
                response.text
        }

    if response.status_code != 200:
        raise RuntimeError(
            "M-Pesa STK Push failed: "
            f"{response.status_code} "
            f"{data}"
        )

    if data.get("ResponseCode") not in (
        None,
        "0",
        0
    ):
        raise RuntimeError(
            "M-Pesa rejected STK Push: "
            f"{data}"
        )

    checkout_request_id = data.get(
        "CheckoutRequestID"
    )

    if not checkout_request_id:
        raise RuntimeError(
            "M-Pesa did not return "
            "CheckoutRequestID."
        )

    return {
        "checkout_request_id":
            checkout_request_id,

        "merchant_request_id":
            data.get("MerchantRequestID"),

        "response_code":
            data.get("ResponseCode"),

        "response_description":
            data.get("ResponseDescription"),

        "customer_message":
            data.get("CustomerMessage"),

        "simulated":
            False
    }


# =========================================================
# B2C PAYOUT
# =========================================================

def b2c_payout(
    phone,
    amount,
    reference
):
    phone = normalize_phone(phone)

    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise ValueError(
            "M-Pesa payout amount must be a valid number."
        )

    if amount <= 0:
        raise ValueError(
            "M-Pesa payout amount must be greater than zero."
        )

    if MPESA_MODE == "mock":
        return {
            "reference":
                "MOCK-PAYOUT-" + uuid.uuid4().hex,

            "conversation_id":
                "MOCK-CONVERSATION-" + uuid.uuid4().hex,

            "simulated":
                True
        }

    _validate_b2c_config()

    token = _get_access_token()

    payload = {
        "InitiatorName":
            MPESA_B2C_INITIATOR_NAME,

        "SecurityCredential":
            MPESA_B2C_SECURITY_CREDENTIAL,

        "CommandID":
            MPESA_B2C_COMMAND_ID,

        "Amount":
            amount,

        "PartyA":
            MPESA_SHORTCODE,

        "PartyB":
            phone,

        "Remarks":
            str(reference)[:100],

        "QueueTimeOutURL":
            MPESA_B2C_TIMEOUT_URL,

        "ResultURL":
            MPESA_B2C_RESULT_URL,

        "Occasion":
            str(reference)[:100]
    }

    headers = {
        "Authorization":
            f"Bearer {token}",

        "Content-Type":
            "application/json"
    }

    response = requests.post(
        B2C_URL,
        json=payload,
        headers=headers,
        timeout=30
    )

    try:
        data = response.json()
    except ValueError:
        data = {
            "raw_response":
                response.text
        }

    if response.status_code != 200:
        raise RuntimeError(
            "M-Pesa B2C payout failed: "
            f"{response.status_code} "
            f"{data}"
        )

    response_code = data.get(
        "ResponseCode"
    )

    if response_code not in (
        None,
        "0",
        0
    ):
        raise RuntimeError(
            "M-Pesa rejected B2C payout: "
            f"{data}"
        )

    return {
        "reference":
            data.get(
                "OriginatorConversationID"
            ),

        "conversation_id":
            data.get(
                "ConversationID"
            ),

        "response_code":
            response_code,

        "response_description":
            data.get(
                "ResponseDescription"
            ),

        "simulated":
            False
    }


# =========================================================
# COMPATIBILITY FUNCTIONS
# =========================================================

def initiate_producer_payout(
    phone,
    amount,
    reference
):
    return b2c_payout(
        phone,
        amount,
        reference
    )


def initiate_platform_payout(
    phone,
    amount,
    reference
):
    return b2c_payout(
        phone,
        amount,
        reference
    )
