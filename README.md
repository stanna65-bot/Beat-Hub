# Beat Store — multi-producer platform

Any producer signs up and gets their own shareable link + dashboard.
Artists browse beats, preview, and pay by M-Pesa. Each producer's cut and
your platform commission are tracked automatically per sale.

## Run it locally (2 minutes)

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
export SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
uvicorn main:app --reload
```

Open `http://127.0.0.1:8000/` — sign up as a producer, you'll land on
`/admin`. Your public page is `http://127.0.0.1:8000/p/<your-slug>` (shown
in the dashboard, ready to copy into an Instagram bio).

Each producer only ever sees and edits their own beats, orders, and payout
info — enforced server-side on every request, not just hidden in the UI.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `SESSION_SECRET` | **Yes, in production** | Signs login session cookies. Without it, a random key is generated on every restart and all producers get logged out each time you redeploy. Generate once, set it, never change it unless you want to force everyone to log in again. |
| `SESSION_HTTPS_ONLY` | No (default `true`) | Set to `false` only if testing over plain HTTP locally without TLS. Keep `true` in production — cookies won't be sent over HTTP otherwise. |
| `MPESA_SIMULATE` | No (default `true`) | `true` = fake payments that auto-complete in ~3s, no Safaricom account needed. Set to `false` to take real money — see below. |
| `MPESA_CONSUMER_KEY` / `MPESA_CONSUMER_SECRET` / `MPESA_PASSKEY` | Only when `MPESA_SIMULATE=false` | From your Daraja app. |
| `MPESA_SHORTCODE` | No (default `174379`, Daraja's shared sandbox code) | Your real Paybill/Till number in production. |
| `MPESA_CALLBACK_URL` | Only when `MPESA_SIMULATE=false` | Must be a **public HTTPS URL** ending in `/mpesa/callback`, e.g. `https://yourapp.com/mpesa/callback`. Safaricom cannot reach `localhost`. |
| `MPESA_BASE_URL` | No (default sandbox) | Set to `https://api.safaricom.co.ke` once you have production Daraja credentials. |

## Deploying

Any host that runs a long-lived Python process works — Render, Railway,
Fly.io, a VPS, etc. Static file hosts (Vercel/Netlify alone) won't work
since this needs a persistent process + writable disk for SQLite and
uploads.

1. Push this folder to a Git repo (private, since `beatstore.db` and
   uploaded audio will live alongside the code once running — add
   `beatstore.db` and `static/uploads/*` to `.gitignore` if you don't want
   them committed).
2. On your host, set `SESSION_SECRET` and the `MPESA_*` variables above.
3. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Once you have a real domain with HTTPS, set `MPESA_CALLBACK_URL`
   accordingly and flip `MPESA_SIMULATE=false`.
5. SQLite is fine at low-to-moderate volume across many producers. If you
   outgrow a single file (host doesn't give persistent disk, e.g. some
   serverless platforms), swap `database.py` for Postgres — the SQL is
   plain enough that the migration is mostly copy-paste.

## Revenue share (commission model)

Buyers pay the **full price into your account**, not the producer's —
that's what makes the split enforceable. Each producer sets their own
commission rate (yours) in their dashboard. When a payment confirms, the
split is locked in on that order:

- Your cut → `platform_fee`
- Producer's cut → `producer_payout`

Nothing is sent out automatically — each producer's dashboard shows a
running total of what's owed to them, and each completed order has a
**"Mark paid out"** button (with a confirmation prompt) for once you've
actually sent their share manually (bank transfer, M-Pesa send, till,
however you settle up).

**Automating the payout** (optional next step): Safaricom's Daraja B2C API
lets you programmatically send money to a phone number, which would let you
auto-pay each producer's cut the moment a sale completes. It's a separate
application + approval process on the Daraja portal from the STK push
credentials used to collect payment, since it involves money leaving an
account. Worth doing once volume justifies it.

## Security notes

- **Passwords** are hashed with PBKDF2-HMAC-SHA256 (260k iterations, salted
  per user) — nothing is stored in plaintext.
- **Sessions** are signed cookies (Starlette's `SessionMiddleware`); a
  producer can't forge another producer's session without `SESSION_SECRET`.
- **Every admin route** checks the logged-in producer's id against the
  resource being read or modified — one producer can never see or edit
  another's beats, orders, or payout number.
- **Downloads** are gated behind a random 32-character token generated only
  once payment completes (`secrets.token_urlsafe`), not a guessable
  sequential order id — so download links can't be enumerated.
- **Preview = full track** (still true here) — the audio player plays the
  whole file before purchase. For a real launch, generate a
  watermarked/30-second preview file and serve that on the beat page,
  keeping the clean file behind `/download/<token>`.
- Uploaded files are renamed to random UUIDs on save, so filenames from
  users never touch the filesystem path directly.

## What's still deliberately simple (fast-follow ideas)

- **No email verification on signup** — fine to launch with, add if fake
  signups become a problem.
- **No password reset flow** — add one before real producers rely on this
  (e.g. emailed reset link).
- **No rate limiting** on login/signup/checkout — add if abuse shows up.
- **Delivery = download link only** — WhatsApp auto-delivery is a natural
  next step (e.g. via Twilio) once this is validated.

## File structure

```
main.py          - all routes (auth, feed, admin, checkout, callback, download)
auth.py          - password hashing + session helpers
mpesa.py         - Daraja STK push + simulate mode
database.py      - SQLite schema, connection helper, slug generation
templates/       - home, signup, login, feed (public), beat (checkout), admin
static/uploads/  - cover images + audio files land here
```
