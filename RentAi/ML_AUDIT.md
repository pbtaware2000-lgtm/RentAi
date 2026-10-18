# RentAI ML audit

## Scope and dataset observed

Audit performed against the checked-in project structure and `data/rentai_dataset.csv`. The CSV contains 10,000 rows and 33 columns. It includes `property_id`, location (`city`, `locality`), property characteristics, distances, `rent`, `rent_category`, and `cluster_id`. The rent values are positive (observed range ₹7,000–₹101,949); there are eight cities, 47 localities, four property types, and three furnishing states. `rent_category` has four equally represented labels (2,500 each). `cluster_id` is `-1` for all 10,000 rows and is unusable as a learned target. `property_id` is unique. No missing values were observed in the inspected key fields.

The dataset appears synthetically balanced; metrics on it must not be presented as real-market performance. Its currency convention is INR per month, corroborated by the existing UI and rent values. Recheck the complete schema and quality summary whenever the CSV is replaced.

## Findings before implementation

- Regression used a handwritten least-squares estimator and one-hot encoded the full dataset before fitting. Inference retrained the model on each API process startup and had no saved artifact or model comparison.
- The regression input set was hardcoded and required every listed column. There was no fitted imputation pipeline or metadata/fingerprint check.
- Classification used a custom nearest-neighbor vote that included actual `rent`, which reveals the rent-derived category and is leakage. It had no fitted classifier artifact or raw class probability response.
- Similar-property results used hand-coded range normalization and categorical mismatch weights, did not impute safely, and did not use a persisted fitted transformation. It was separate from the app's interactive clustering analysis.
- The active ML routes did not contain HTTP 501 or literal mock predictions; the issue was that their outputs came from weak, inconsistent estimators without persisted fitted preprocessing or comparable model evaluation.
- The existing cluster label is constant noise, so clustering must be trained from property features. Existing K-Means and DBSCAN UI helpers do not persist models or evaluate cluster quality.
- The repo has multiple frontend ML pages, API routes, and regression/classification tests. Existing contracts include `/ml/rent-prediction`, `/ml/rent-classification`, and `/ml/similar-properties`; preserve those paths.
- Dataset loading in the property service is project-relative, but ML training and inference need one shared loader and fitted feature schema.

## Implementation approach

Add a reusable validation/EDA report and reproducible training command that fits preprocessing and estimators together, keeps rent out of category inputs, evaluates candidate models on held-out data, and saves trusted joblib pipelines plus JSON metrics/metadata under `artifacts/`. Regression uses a location-grouped holdout where feasible; classification uses a stratified holdout. Clustering selects K using silhouette, inertia, cluster sizes, and profiles, and the same fitted transformation powers nearest-neighbor recommendations. API services load artifacts lazily once per process and report missing/incompatible artifacts as service errors rather than inventing predictions. Update the existing UI/API contracts without changing authentication or SQLite.

## Verification

The bundled venv initially lacked scikit-learn; it was installed into the project venv and added to `requirements.txt`. Training completed on the inspected CSV and generated `artifacts/model_report.json` plus the fitted pipelines. Actual scores and the material regression validation-to-test drop are documented in the README and generated report. Focused ML/prediction tests passed. The full suite still has failures in existing auth/config/image tests outside the ML changes; details are in the final task summary.
