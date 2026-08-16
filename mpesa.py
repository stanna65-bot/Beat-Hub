import os,uuid
def normalize_phone(phone):
 d=''.join(x for x in (phone or '') if x.isdigit())
 if d.startswith('0') and len(d)==10:d='254'+d[1:]
 elif d.startswith('7') and len(d)==9:d='254'+d
 if not(d.startswith('2547') and len(d)==12):raise ValueError('Enter a valid M-Pesa number.')
 return d
def mode():return os.getenv('MPESA_MODE','mock').lower()
def stk_push(phone,amount,account_ref,description):
 normalize_phone(phone)
 if mode()=='mock':return {'checkout_request_id':'MOCK-'+uuid.uuid4().hex,'simulated':True}
 raise RuntimeError('Live Safaricom Daraja integration is pending.')
def initiate_producer_payout(phone,amount,reference):
 normalize_phone(phone)
 if mode()=='mock':return {'reference':'MOCK-PAYOUT-'+uuid.uuid4().hex,'simulated':True}
 raise RuntimeError('Live Safaricom B2C integration is pending.')
def initiate_platform_payout(phone,amount,reference):return initiate_producer_payout(phone,amount,reference)
