# BeatHub updated replacement
Upload all contents of this folder to the GitHub repository root.

## Environment
SESSION_SECRET=...
MPESA_MODE=mock
SUPER_ADMIN_USERNAME=...
SUPER_ADMIN_PASSWORD=...
SUPER_ADMIN_PAYOUT_PHONE=2547XXXXXXXX
DEV_SHOW_RESET_LINK=false

## Implemented
Producer accounts, password recovery flow, public stores, uploads, optional BPM, Hot Picks, internal 10% platform accounting, producer wallet, platform wallet, super-admin dashboard, producer/admin withdrawal flows, protected downloads and duplicate split protection.

## Pending next upgrade
Only real Safaricom Daraja STK Push/B2C implementation and production credential configuration remain pending. Keep MPESA_MODE=mock until that implementation is added.

For real deployment, use a managed PostgreSQL database and a real transactional email provider for password reset delivery.
