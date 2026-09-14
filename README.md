# LocalLoop

LocalLoop is a runnable marketplace MVP for independent local delivery. It includes customer, driver, business and admin roles; delivery posting/claiming; lifecycle state transitions; pricing; a platform-fee ledger; driver balances; ratings; disputes; notifications; and an operator dashboard.

## Run now

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000

Admin seeding is controlled by `LOCALLOOP_ADMIN_EMAIL` and `LOCALLOOP_ADMIN_PASSWORD`. No hard-coded public admin password is stored in source.

## Included workflows

- Customer registration/login and delivery posting
- Business accounts that can dispatch deliveries
- Driver onboarding, online/offline status, open-job feed, claiming and status updates
- Atomic delivery claiming to reduce double-assignment
- Quote engine: base fare + distance + surge + platform fee
- Delivery event audit trail
- Driver earning ledger and payout balance
- Platform revenue ledger
- In-app notifications
- Customer cancellation rules
- Delivery proof text field
- Ratings/reputation data model
- Dispute intake data model
- Promo-code schema
- Admin KPI dashboard and live delivery stream
- JSON APIs for current user, deliveries and notifications
- SQLite persistence, Docker support and responsive UI

## Render deployment

This repository includes `render.yaml` for a free public preview. The free preview stores SQLite at `/tmp/localloop.db`, so data may reset when a free service restarts or redeploys. Before commercial use, migrate persistence to PostgreSQL and connect real payment, routing, identity, notification, backup, and security providers.

Start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Health check: `/health`
