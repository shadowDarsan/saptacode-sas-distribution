# AegisSaS · Autonomous Distribution & Service-as-a-Software Engine

Production-grade autonomous lead harvester, security/QA pre-auditor (`Obscura` + `REA` SHA-256 Evidence Ledger), synthetic persona pitch evaluator (`MiroFish` pattern), and Amazon SES HTTP `POST` webhook dispatcher.

## 1. Quick Start (Local)
```powershell
python cli.py start
# Open http://127.0.0.1:8080
```

## 2. Deploying to Railway
1. Push this directory to a private GitHub repository and create a new Railway service from the repo.
2. In Railway **Settings -> Volumes**, mount a persistent volume at **`/data`**.
3. In Railway **Variables**, set:
   - `ADMIN_PASSWORD`: Strong password to lock the operator dashboard (`/`).
   - `SES_WEBHOOK_URL`: Your Amazon SES microservice HTTP `POST` endpoint.
   - `SES_WEBHOOK_API_KEY`: Your Amazon SES API bearer token / secret.
   - `PUBLIC_BASE_URL`: Your public Railway domain (e.g., `https://audit.yourdomain.com`).
   - `STRIPE_CHECKOUT_URL`: Your live Stripe Payment Link for the `$299` 7-Day Pilot.
   - `RAILWAY_VOLUME_MOUNT_PATH`: `/data`
