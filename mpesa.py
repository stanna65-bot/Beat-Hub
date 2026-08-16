import os,uuid

def normalize_phone(phone):
    digits="".join(c for c in (phone or "") if c.isdigit())
    if digits.startswith("0") and len(digits)==10: digits="254"+digits[1:]
    elif digits.startswith("7") and len(digits)==9: digits="254"+digits
    if not(digits.startswith("2547") and len(digits)==12): raise ValueError("Enter a valid Kenyan M-Pesa number.")
    return digits

def mode():
    return os.getenv("MPESA_MODE","mock").lower()

def stk_push(phone,amount,account_ref,description):
    phone=normalize_phone(phone)
    if mode()=="mock": return {"checkout_request_id":"MOCK-"+uuid.uuid4().hex,"simulated":True}
    raise RuntimeError("Live Safaricom Daraja integration is pending. Do not set MPESA_MODE=live until the live API implementation and credentials are added.")

def initiate_producer_payout(phone,amount,reference):
    phone=normalize_phone(phone)
    if mode()=="mock": return {"reference":"MOCK-PAYOUT-"+uuid.uuid4().hex,"simulated":True}
    raise RuntimeError("Live Safaricom B2C integration is pending.")

def initiate_platform_payout(phone,amount,reference):
    return initiate_producer_payout(phone,amount,reference)
