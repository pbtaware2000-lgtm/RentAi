# RentAI — Rental Intelligence

RentAI combines rental property analysis with three reproducible ML workflows: monthly rent regression, rent-band classification, and property clustering with similar-listing search. The Streamlit app is the demonstration interface; FastAPI serves the same saved models to the app and other clients.

## Setup and application

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Train artifacts once after setup or whenever the dataset or feature logic changes:

```powershell
python -m ml.train --eda
```

This writes fitted joblib files and a JSON report under `artifacts/`. The report includes a SHA-256 dataset fingerprint; inference refuses artifacts trained against a different dataset. The original CSV is never modified. EDA images are written to `artifacts/eda/`.

Run the API and Streamlit in separate persistent terminals:

```powershell
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8001
python -m streamlit run app.py --server.port 8501
```

For deployment, set `RENTAI_API_URL` to the HTTPS URL that the Streamlit server can reach, and start the API as a persistent service. The localhost default is for a single-machine local setup.

The existing SQLite database remains at `data/rentai.sqlite3` by default. `RENTAI_DATABASE_PATH` can point elsewhere. Authentication, role management, sessions, and application records remain separate from model training.

## Dataset and data policy

Default source: `data/rentai_dataset.csv`, resolved relative to the repository. The inspected CSV contains 10,000 rows and 33 columns: property identifier, city/locality, property type and physical features, distances, dates, rent, rent category, deposit/maintenance, and legacy cluster ID. Key observed coverage: 8 cities, 47 localities, 4 property types, and 3 furnishing states. Rent is represented as monthly INR in the existing data/UI convention; observed rent range is ₹7,000–₹101,949. `rent_category` has four balanced labels (2,500 each). `cluster_id` is `-1` for every record and is not a useful label. The full field-by-field quality report is generated with each training run.

The data appears synthetic. Evaluation describes only this dataset and should not be interpreted as a real rental market guarantee. Validation reports missingness, duplicate rows, numeric distributions/skew/outlier counts, unique values, categorical frequencies, coverage, and non-positive rent/area. The validator does not alter the source. Regression training excludes rows with missing/non-numeric/non-positive rent; it does not trim high or unusual positive rents. Fitted imputers and encoders handle feature missingness. `property_id`, `rent_category`, and `cluster_id` are excluded from regression inputs. Classification excludes rent, deposit, maintenance, IDs, and cluster labels because the intended scenario predicts a rent band before the amount is known. The constant legacy cluster label is excluded from unsupervised clustering.

## ML architecture and evaluation

`ml/data_validation.py` contains reusable quality summaries and plot generation. `ml/pipelines.py` builds scikit-learn preprocessing/estimator pipelines, evaluates candidates, persists artifacts, and provides inference helpers. `ml/train.py` is the explicit training command. Training is not run from API requests or Streamlit reruns.

| Workflow | Candidates | Evaluation and selection |
| --- | --- | --- |
| Regression (`rent`) | Median DummyRegressor, Ridge, RandomForestRegressor, GradientBoostingRegressor, and XGBoost Regressor when importable | City-grouped train/validation/test split when at least five cities are available. Select by validation MAE, then evaluate the chosen pipeline once on the held-out test set. Report MAE, RMSE, R², median absolute error, and test error by city/locality/type/rent band where group size is at least 10. |
| Classification (`rent_category`) | Prior DummyClassifier, LogisticRegression, DecisionTreeClassifier, RandomForestClassifier, GradientBoostingClassifier | Stratified holdout and validation splits. Select by validation macro-F1, then report accuracy, macro/weighted F1, per-class precision/recall, and confusion matrix. |
| Clustering | K-Means, K=2–8 | Compare sampled silhouette, repeated-run adjusted Rand stability, full-data inertia, and cluster sizes; prefer stable solutions and reject tiny clusters when possible, then fit the selected K to all rows and report actual cluster profiles. |

Preprocessing is stored with each supervised estimator: median/mode imputation, unknown-safe one-hot encoding, and numerical scaling for linear models. Unseen categories are accepted. Classification scores are labeled raw and are not calibrated. Clustering uses city and property type as low-cardinality categorical features; standardizing the one-hot matrix gives those dimensions meaningful influence, and the resulting groups partly reflect city/type structure. Similar listings use Euclidean distance after the fitted clustering imputer, one-hot encoder, and scaler; property ID lookup excludes the reference row, and optional city/locality/type/rent filters are applied to results.

### Measured results on the inspected dataset

Training completed on dataset SHA-256 `8519ac388c286cb2dc802dec14da25feef6b779c737e4966ba68de6d33ded9ce`, random seed 42, on 2026-10-16 UTC. Metrics are from the generated `artifacts/model_report.json`; files are ignored by Git.

Regression split was city-grouped: 4,992 training, 2,513 validation, and 2,495 test records. This tests held-out cities from the same eight-city dataset. Ridge won validation by MAE, but generalization dropped substantially on test, which is an important limitation.

| Regression candidate | Validation MAE (INR) | Validation RMSE (INR) | Validation R² |
| --- | ---: | ---: | ---: |
| Median dummy | 6,509.34 | 8,208.97 | -0.118 |
| Ridge (selected) | 2,590.89 | 3,272.72 | 0.822 |
| Random forest | 4,401.20 | 5,405.46 | 0.515 |
| Gradient boosting | 4,399.27 | 5,478.10 | 0.502 |
| XGBoost | 3,433.57 | 4,280.77 | 0.696 |

Held-out Ridge test results: **MAE ₹5,589.996**, **RMSE ₹6,941.815**, **R² 0.3585**, median absolute error ₹4,739.59. The difference between validation and test indicates location shift; treat estimates cautiously. Diagnostics are saved in `artifacts/eda/regression_actual_predicted_residuals.png`.

Classification selected LogisticRegression using validation macro-F1. On its stratified 2,000-record test set: **accuracy 0.7275**, **macro-F1 0.7294**, weighted-F1 0.7294. Each class had 500 test records.

| Class | Precision | Recall | F1 |
| --- | ---: | ---: | ---: |
| High | 0.6464 | 0.6580 | 0.6521 |
| Low | 0.8233 | 0.7640 | 0.7925 |
| Medium | 0.5857 | 0.6220 | 0.6033 |
| Premium | 0.8730 | 0.8660 | 0.8695 |

The class errors are material, especially for Medium. Categories are predicted without rent, so these results measure pre-rent property-band classification, not categorization of a known asking rent.

K-Means selected **K=5**, silhouette 0.1604 and repeated-run mean adjusted Rand stability 0.8006. K=8 had higher silhouette (0.2624) but stability 0.7865, below the 0.80 stability threshold used for selection. The selected cluster sizes range from 863 to 4,138. Their profiles show distinct rent/property segments, with city and property type as common characteristics:

| Cluster | Listings | Median rent (INR/month) | Median area (sq ft) | Median beds | Most common city | Most common type |
| ---: | ---: | ---: | ---: | ---: | --- | --- |
| 0 | 863 | 10,919 | 619 | 1 | Pune | Studio |
| 1 | 4,138 | 16,378 | 1,057 | 2 | Ahmedabad | Apartment |
| 2 | 2,780 | 24,614.5 | 1,769 | 3 | Pune | Apartment |
| 3 | 1,062 | 39,290.5 | 1,342 | 2 | Mumbai | Apartment |
| 4 | 1,157 | 20,502 | 1,260 | 2 | Kolkata | Independent House |

The test-set rent screen compared dataset rent with held-out predictions: 589 records were over 10% above the estimate, 1,342 were over 10% below, and 564 were within 10%. These are model-screening flags only, not verified mispricing. Full profiles, candidate inertia/silhouette/stability, and error breakdowns are in `artifacts/model_report.json`.

Results are reproducible for this exact dataset fingerprint and software environment. Candidate selection is based on validation only; the test split is used once for final metrics. Retraining overwrites the ignored model artifacts and reports, so archive the JSON report with its dataset fingerprint if you need experiment history.

### Training, report, and tests

```powershell
python -m ml.train --eda
python -m ml.regression.evaluate_model
python -m ml.classification.evaluate_model
python -m unittest discover -s tests -v
```

Artifacts are trusted local files. Do not load joblib files from untrusted sources. Retrain when the CSV fingerprint changes or training code, feature selection, or preprocessing changes. Compare new report results using the same dataset fingerprint and split methodology.

## FastAPI contracts

All ML routes use saved artifacts, loaded lazily and cached once per process. Missing or stale artifacts return HTTP 503; malformed requests are rejected by Pydantic.

```http
POST /ml/rent-prediction
Content-Type: application/json
```

```json
{"city":"Pune","locality":"Aundh","property_type":"Apartment","furnishing_status":"Semi-Furnished","area_sqft":900,"bedrooms":2,"bathrooms":1,"floor":3,"distance_to_city_center_km":4,"age_of_property":5,"parking":1}
```

Response includes `predicted_rent`, `currency` (`INR per month`), `model_name`, input features, test metrics, and limitations.

```http
POST /ml/rent-classification
```

Send city and property characteristics (for example `city`, `locality`, `property_type`, `area_sqft`, `bedrooms`, `bathrooms`, and `furnishing_status`). The optional legacy `rent` input is ignored; it is never passed to the classifier. Response includes category, raw class scores, model name, and evaluation results.

```http
POST /ml/similar-properties
```

Example ID lookup: `{"property_id":"<dataset property_id>","limit":5,"city":"Pune"}`. Or provide `features` instead of `property_id`; optional `locality`, `property_type`, `min_rent`, and `max_rent` filters are available. Results are actual CSV records with `similarity_distance`.

## Owner listing bookings and payments

Renters can request bookings only for published owner listings whose owner has explicitly marked them vacant. Dataset/CSV properties are not part of this API. The owner must accept a request before the renter can pay. Acceptance does not count as payment. The renter's initial payable amount is the saved monthly rent plus the listing's optional security deposit; the terms are copied into the booking at request time.

Payment checkout uses a server-created Razorpay Payment Link. Set `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, and `RAZORPAY_WEBHOOK_SECRET` in the API server environment. Configure a Razorpay webhook to `POST /bookings/razorpay/webhook` for `payment_link.paid` and `payment.failed`; use the same webhook secret. The API independently fetches the Payment Link and payment, checks captured state and amount, and confirms the booking only after a valid signed webhook. Browser redirects do not confirm payment. Do not expose any Razorpay secret to Streamlit.

Only one accepted or confirmed booking can reserve a listing at a time. Listings must be explicitly marked vacant; a confirmed booking changes the listing occupancy to occupied. Since the current listing model has no lease end date, an accepted booking reserves the listing while awaiting payment; failed payments can be retried, but accepted requests cannot currently be cancelled or expired. If a payment is captured after availability has changed, RentAI records the verified payment and flags the booking for manual reconciliation rather than claiming the booking is confirmed.

The renter and owner booking pages show status changes and durable in-app notifications. Email/SMS notices, cancellation/refund handling, and manual reconciliation tooling are not configured. Payment tests mock all gateway requests and do not initiate charges.

## Known limitations

- Dataset-driven metrics may not transfer to live listings or future markets; the dataset appears synthetic and balanced.
- City-grouped regression tests generalization to held-out cities from this dataset, not arbitrary locations.
- Category labels are learned from the existing column; if that target is changed or removed, the classification pipeline requires a reviewed target strategy before training.
- Classifier raw scores are not calibrated probabilities.
- Similarity depends on the selected features and one-hot distance weighting; use the returned distance as a ranking aid, not an absolute match probability.
- Explainability currently reports model-native feature importance or coefficient magnitude where available. SHAP is not included because compatibility/performance could not be confirmed in this environment.
