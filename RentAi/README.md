# RentAI - Smart Rental Price & Property Analysis System

RentAI uses SQLite for account authentication, role assignments, sessions, and application records. The default database is `data/rentai.sqlite3`; Python's standard library provides the SQLite driver. Property analysis and listing search continue to use `data/rentai_dataset.csv`.

## Setup

```powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`RENTAI_DATABASE_PATH` can point to another SQLite file. `RENTAI_API_URL` defaults to the local FastAPI server. Start the API and Streamlit in separate terminals:

```powershell
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8001
python -m streamlit run app.py --server.port 8501
```

Public registration creates Owner, Renter, or Broker accounts directly in SQLite. Passwords are stored as salted PBKDF2 hashes. Create the first Admin locally with `python -m backend.role_provisioning create-admin`; it prompts for the email, name, and password and refuses if an Admin already exists. Admins can assign later roles through the app. SQLite sessions expire after 30 days.

## Data

The SQLite database is created automatically on first launch. It contains `users`, `sessions`, `application_records`, and `application_images` tables. Keep the database file local and back it up with the application. Existing cloud accounts and records are not imported automatically.

Property endpoints read the CSV dataset. The API provides property browsing, search, comparison, and ML endpoints documented at `/docs`. Image binary hosting remains with the configured image provider; metadata is stored in SQLite.

## Owner notification email and portfolio summaries

Owner notification preferences remain in the authenticated `owner_settings` application record in SQLite. SMTP credentials are read by the backend and notification worker only; do not put them in Streamlit secrets or frontend code. Add these names to the existing `.env` without replacing its other settings:

```dotenv
RENTAI_EMAIL_SMTP_HOST=smtp.example.com
RENTAI_EMAIL_SMTP_PORT=587
RENTAI_EMAIL_SMTP_USERNAME=your-smtp-user
RENTAI_EMAIL_SMTP_PASSWORD=your-smtp-app-password
RENTAI_EMAIL_FROM=rentai@example.com
RENTAI_EMAIL_SMTP_SECURITY=starttls
```

The first five values are required. `RENTAI_EMAIL_SMTP_SECURITY` is optional and defaults to `starttls`; use `ssl` for providers configured for implicit TLS, commonly port 465. Use a provider issued SMTP/app password, not a personal account password. The application reports missing variable **names** only and never logs credential values. No email is sent until SMTP is configured and the independent worker is running.

Run the API and worker as separate supervised processes from the project directory (separate terminals during local development):

```powershell
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8001
python -m backend.notification_worker
```

The worker checks due notifications every 30 seconds. Weekly summaries are scheduled Monday at 09:00 UTC, monthly summaries on the first day of the month at 09:00 UTC. If the worker starts after that time, it schedules the current period when it next runs. Keep the worker running in deployment; run it as a separate supervised process alongside the API. `python -m backend.notification_worker --dry-run` schedules due summaries and prints outbox counts without sending. To verify SMTP deliberately, run `python -m backend.notification_worker --test-email your-address@example.com`; it sends only to the explicitly supplied address and contains no Owner or renter data.

Notification creation is committed with the related enquiry/status update. The SQLite outbox deduplicates event creation and summary periods, retries transient SMTP failures with backoff, and uses a stable Message-ID. SMTP does not provide an atomic transaction with SQLite, so a process crash after the SMTP server accepts a message but before the worker records success can result in a repeated delivery on recovery; no SMTP-only implementation can guarantee exactly-once inbox delivery across that failure window.
