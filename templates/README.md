# BeatHub — The Home of Beats

Production-ready application structure for beats, Hot Picks, session booking, calendar availability, booking chat/proposals, automatic 10% platform accounting, producer balances, downloads and password recovery.

## Render
Build: `pip install -r requirements.txt`
Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`

## Required
`SESSION_SECRET`, `SUPER_ADMIN_USERNAME`, `SUPER_ADMIN_PASSWORD`, `SUPER_ADMIN_PAYOUT_PHONE`

## Password reset email
`APP_BASE_URL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`

## Payments
Default is `MPESA_MODE=mock`. The remaining production payment dependency is live Safaricom Daraja STK/B2C implementation and callback verification.
