# Beat Hub

Clean replacement project.

## Render
Build command: `pip install -r requirements.txt`
Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

Set `SESSION_SECRET` in Render.

M-Pesa:
- `MPESA_MODE=mock` works immediately for testing.
- Set `MPESA_MODE=live` only after adding Daraja credentials and completing the callback URL setup.

The 10% platform fee is applied internally. Producers see only their net available balance.
