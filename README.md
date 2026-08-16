# BeatHub — Merged Final Build

This build combines the **BeatHub visual identity/design** from the updated replacement with the newer **session booking, availability, booking messages/proposals and expanded wallet/accounting functionality**.

## Included
- Producer registration, login and password recovery
- Public producer stores and beat catalogue
- Beat uploads, Hot Picks, previews and protected downloads
- 10% platform commission accounting
- Producer wallet and producer M-Pesa withdrawal flow
- Session services with duration, price and location
- Weekly producer availability and live free-slot calculation
- Session booking with payment hold and overlap protection
- Booking status, messaging and alternative-time proposals
- Platform wallet combining beat and session commissions
- Super Admin control room with commission ledger
- Super Admin platform-balance withdrawal to M-Pesa
- Failure-safe wallet reservation/refund handling
- Database upgrade path for the older platform-ledger format
- M-Pesa mock mode for application testing

## M-Pesa
The only intentionally pending external integration is the **live Safaricom Daraja implementation**. Keep:

`MPESA_MODE=mock`

until the live STK Push and B2C credentials/callback implementation is installed.

## Environment
```text
SESSION_SECRET=change-this
MPESA_MODE=mock
SUPER_ADMIN_USERNAME=admin
SUPER_ADMIN_PASSWORD=change-this
SUPER_ADMIN_PAYOUT_PHONE=2547XXXXXXXX
DEV_SHOW_RESET_LINK=false
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=
APP_BASE_URL=
```

For production, use a persistent database, HTTPS, a strong session secret, real SMTP credentials and real Safaricom Daraja credentials.
