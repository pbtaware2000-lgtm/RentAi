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
