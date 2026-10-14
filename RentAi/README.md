# RentAI - Smart Rental Price & Property Analysis System

RentAI uses `data/rentai_dataset.csv` for property analysis and Firebase Authentication with Cloud Firestore for role-protected application records. The Streamlit UI and CSV-backed FastAPI property endpoints remain available independently of the Firebase-backed account features. Rent prediction trains a basic NumPy linear regression model in memory when first requested, with Pandas handling categorical feature encoding.

## Environment

Create and activate the project virtual environment in Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` once and fill in the Firebase Web API key from the Firebase Console. The local `.env` is ignored by Git. Keep the Web API key distinct from the Firebase Admin service-account JSON; the latter stays outside this repository. RentAI loads this same root `.env` for Streamlit and FastAPI, while nonempty process environment variables take precedence.

For the local setup described here, configure `FIREBASE_PROJECT_ID=rentai-62ac9`, `FIREBASE_CREDENTIALS_PATH=C:/Users/Pavan/RentAI-Secrets/rentai.json`, and `RENTAI_API_URL=http://127.0.0.1:8001` in `.env`. Obtain `FIREBASE_WEB_API_KEY` from Firebase Console → Project settings → General → Your apps. Because the key was reported exposed, rotate it in Google Cloud Console and update only the ignored local `.env` before using it again.

Start FastAPI and Streamlit in separate PowerShell windows from the project root:

```powershell
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8001
python -m streamlit run app.py --server.port 8501
```

Run project commands after activating the virtual environment. Firebase-backed authentication reports safe configuration errors when required values are missing or malformed; the CSV and ML endpoints remain usable independently of Firebase configuration.

## Role provisioning

The local role-provisioning client reuses Firebase Email/Password sign-in and the authenticated FastAPI endpoints. It prompts for the password without echoing it, and never prints ID or refresh tokens. To bootstrap, temporarily set `RENTAI_BOOTSTRAP_ADMIN_UIDS` to the trusted operator's Firebase UID in the FastAPI launch environment, restart FastAPI, then run `python -m backend.role_provisioning bootstrap`. The backend validates that the token belongs to the allowlisted UID and that no first Admin has already been bootstrapped. After success, remove the allowlist from the shell or service configuration used to launch FastAPI and restart it. Then an Admin can assign a role with `python -m backend.role_provisioning grant <TARGET_FIREBASE_UID> --role renter` (replace `renter` with an allowed role as appropriate). Never add an account to the allowlist based only on an email address.
The CSV contains 10,000 property records and includes city, locality, property attributes, rent, rent category, and cluster ID columns.

## Property API

Run the API with `python -m uvicorn backend.main:app --reload`. Interactive API documentation is available at `/docs`.

- `GET /properties`, `/properties/count`, and `/properties/{property_id}` provide listing, count, and detail operations.
- `GET /properties/search` supports city, locality, rent range, bedroom count, property type, sorting, and pagination.
- `GET /properties/compare?property_ids=ID1&property_ids=ID2` compares two to four distinct records.
- `POST /ml/rent-prediction` trains and uses an in-memory model from the CSV on first use in each API process. Categorical fields are encoded with Pandas `get_dummies`; NumPy solves the linear regression coefficients.
- `POST /ml/rent-classification` and `/ml/similar-properties` define future integration contracts and return 501 until those models are implemented.

## Development phases

1. Virtual environment and project structure
2. Streamlit UI
3. FastAPI backend
4. CSV loading and data operations
5. Preprocessing and exploratory data analysis
6. Regression models
7. Classification models
8. Clustering models
9. FastAPI and machine-learning integration
10. Analytics, insights, and reports
11. Testing and final integration

Phase 1, the Streamlit UI, CSV-backed property APIs, and in-memory rent prediction are implemented. Rent classification and clustering inference remain unconnected.
