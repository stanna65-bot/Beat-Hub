import os
import uuid


def normalize_phone(phone: str) -> str:
    digits = "".join(
        character
        for character in (phone or "")
        if character.isdigit()
    )

    if (
        digits.startswith("0")
        and len(digits) == 10
    ):
        digits = "254" + digits[1:]

    elif (
        digits.startswith("7")
        and len(digits) == 9
    ):
        digits = "254" + digits

    if not (
        digits.startswith("2547")
        and len(digits) == 12
    ):
        raise ValueError(
            "Enter a valid M-Pesa payout number."
        )

    return digits


def mode() -> str:
    return os.getenv(
        "MPESA_MODE",
        "mock"
    ).strip().lower()


def stk_push(
    phone,
    amount,
    account_ref,
    description
):
    """
    Payment-provider adapter.

    mock:
        Allows complete application testing.

    live:
        Reserved for the real Safaricom Daraja
        STK implementation.
    """

    normalize_phone(phone)

    if mode() == "mock":
        return {
            "checkout_request_id":
                "MOCK-" + uuid.uuid4().hex,
            "simulated": True
        }

    raise RuntimeError(
        "Live M-Pesa STK integration is pending."
    )


def initiate_producer_payout(
    phone,
    amount,
    reference
):
    """
    Producer withdrawal adapter.

    Mock mode completes immediately so the wallet,
    ledger and withdrawal logic can be tested.
    """

    normalize_phone(phone)

    if mode() == "mock":
        return {
            "reference":
                "MOCK-PAYOUT-" + uuid.uuid4().hex,
            "simulated": True
        }

    raise RuntimeError(
        "Live M-Pesa B2C integration is pending."
    )


def initiate_platform_payout(
    phone,
    amount,
    reference
):
    """
    Super Admin platform-wallet withdrawal.

    The actual provider implementation will be
    connected here during the final M-Pesa step.
    """

    return initiate_producer_payout(
        phone,
        amount,
        reference
    )
