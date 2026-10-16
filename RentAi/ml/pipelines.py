"""Reproducible training, evaluation, persistence, and inference for RentAI ML."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import dump, load
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor, GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (accuracy_score, classification_report, confusion_matrix,
    adjusted_rand_score, f1_score, mean_absolute_error, mean_squared_error, median_absolute_error, r2_score,
    silhouette_score)
from sklearn.model_selection import GroupShuffleSplit, train_test_split
from sklearn.neighbors import NearestNeighbors
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.cluster import KMeans

from ml.data_validation import DATASET_PATH, PROJECT_ROOT, load_dataset, validate_dataset

ARTIFACT_DIR = PROJECT_ROOT / "artifacts"
RANDOM_SEED = 42
REGRESSION_TARGET = "rent"
CLASSIFICATION_TARGET = "rent_category"
DROP_FEATURES = {"property_id", "rent", "rent_category", "cluster_id", "security_deposit", "maintenance"}
REGRESSION_FEATURES = ("city", "locality", "property_type", "area_sqft", "bedrooms", "bathrooms", "balcony", "floor", "total_floors", "furnishing_status", "parking", "lift", "gym", "security", "power_backup", "swimming_pool", "air_conditioning", "water_supply", "gated_community", "age_of_property", "distance_to_city_center_km", "distance_to_metro_km", "distance_to_school_km", "distance_to_hospital_km", "distance_to_it_park_km")
CLASSIFICATION_FEATURES = tuple(c for c in REGRESSION_FEATURES if c != "rent")
CLUSTER_FEATURES = ("rent", "area_sqft", "bedrooms", "bathrooms", "balcony", "property_type", "furnishing_status", "city")


def _features(data: pd.DataFrame, candidates: tuple[str, ...]) -> list[str]:
    return [c for c in candidates if c in data.columns and c not in DROP_FEATURES]


def _pipeline(data: pd.DataFrame, features: list[str], estimator, *, scale: bool = False) -> Pipeline:
    numeric = [c for c in features if pd.api.types.is_numeric_dtype(data[c])]
    categorical = [c for c in features if c not in numeric]
    num_steps = [("imputer", SimpleImputer(strategy="median"))]
    if scale:
        num_steps.append(("scaler", StandardScaler()))
    transformers = []
    if numeric:
        from sklearn.pipeline import make_pipeline
        transformers.append(("numeric", make_pipeline(*[step[1] for step in num_steps]), numeric))
    if categorical:
        from sklearn.pipeline import make_pipeline
        transformers.append(("categorical", make_pipeline(SimpleImputer(strategy="most_frequent"), OneHotEncoder(handle_unknown="ignore")), categorical))
    prep = ColumnTransformer(transformers, remainder="drop", sparse_threshold=0.0)
    return Pipeline([("preprocessor", prep), ("model", estimator)])


def _fingerprint(path: Path = DATASET_PATH) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regression_split(data: pd.DataFrame):
    """Hold out whole city/locality groups when there are enough groups."""
    groups = data["city"].astype(str) if "city" in data and data["city"].nunique() >= 5 else None
    if groups is None:
        train_idx, rest_idx = train_test_split(np.arange(len(data)), test_size=.3, random_state=RANDOM_SEED)
        val_idx, test_idx = train_test_split(rest_idx, test_size=2/3, random_state=RANDOM_SEED)
        return train_idx, val_idx, test_idx, "random 70/10/20 split"
    first = GroupShuffleSplit(n_splits=1, test_size=.2, random_state=RANDOM_SEED)
    train_val_idx, test_idx = next(first.split(data, groups=groups))
    second = GroupShuffleSplit(n_splits=1, test_size=.25, random_state=RANDOM_SEED + 1)
    train_rel, val_rel = next(second.split(data.iloc[train_val_idx], groups=groups.iloc[train_val_idx]))
    return train_val_idx[train_rel], train_val_idx[val_rel], test_idx, "city-grouped train/validation/test (unseen-city generalization; only observed cities)"


def _jsonable(value):
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (np.floating,)): return float(value)
    if isinstance(value, np.ndarray): return value.tolist()
    if isinstance(value, dict): return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_jsonable(v) for v in value]
    return value


def _metric_regression(y, pred):
    return {"mae": float(mean_absolute_error(y, pred)), "rmse": float(np.sqrt(mean_squared_error(y, pred))),
        "r2": float(r2_score(y, pred)), "median_absolute_error": float(median_absolute_error(y, pred))}


def _feature_importance(pipeline: Pipeline, limit: int = 20) -> list[dict]:
    """Expose model-native importance/weight magnitudes when available."""
    estimator = pipeline.named_steps["model"]
    if hasattr(estimator, "feature_importances_"):
        values = np.asarray(estimator.feature_importances_, dtype=float)
    elif hasattr(estimator, "coef_"):
        values = np.asarray(estimator.coef_, dtype=float)
        values = np.mean(np.abs(values), axis=0) if values.ndim > 1 else np.abs(values)
    else:
        return []
    try:
        names = pipeline.named_steps["preprocessor"].get_feature_names_out()
    except (AttributeError, ValueError):
        names = [f"feature_{i}" for i in range(len(values))]
    indices = np.argsort(values)[::-1][:limit]
    return [{"feature": str(names[i]), "importance": float(values[i])} for i in indices if i < len(names)]


def _save_regression_diagnostics(actual, predicted, directory: Path) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    directory.mkdir(parents=True, exist_ok=True)
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    residual = actual - predicted
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].scatter(actual, predicted, alpha=.35, s=12)
    low, high = min(actual.min(), predicted.min()), max(actual.max(), predicted.max())
    axes[0].plot([low, high], [low, high], "--", color="black")
    axes[0].set(xlabel="Actual rent (INR/month)", ylabel="Predicted rent (INR/month)", title="Held-out actual vs predicted")
    axes[1].scatter(predicted, residual, alpha=.35, s=12)
    axes[1].axhline(0, linestyle="--", color="black")
    axes[1].set(xlabel="Predicted rent (INR/month)", ylabel="Residual (actual − predicted)", title="Held-out residuals")
    fig.tight_layout()
    fig.savefig(directory / "regression_actual_predicted_residuals.png", dpi=140)
    plt.close(fig)
    return ["regression_actual_predicted_residuals.png"]


def _cluster_profile(data: pd.DataFrame) -> list[dict]:
    profiles = []
    for label, rows in data.groupby("cluster", sort=True):
        item = {"cluster": int(label), "properties": int(len(rows))}
        for col in ("rent", "area_sqft", "bedrooms", "bathrooms"):
            if col in rows:
                values = pd.to_numeric(rows[col], errors="coerce")
                item[f"{col}_median"] = float(values.median()) if values.notna().any() else None
                item[f"{col}_mean"] = float(values.mean()) if values.notna().any() else None
        for col in ("city", "locality", "property_type", "furnishing_status"):
            if col in rows:
                item[f"common_{col}"] = rows[col].dropna().astype(str).value_counts().head(3).to_dict()
        profiles.append(item)
    return profiles


def train_all(dataset_path: str | Path = DATASET_PATH, artifact_dir: str | Path = ARTIFACT_DIR) -> dict:
    """Train candidates, select via validation only, evaluate once on held-out test."""
    source_path = Path(dataset_path)
    if not source_path.is_absolute():
        source_path = PROJECT_ROOT / source_path
    source_path = source_path.resolve()
    data = load_dataset(source_path)
    try:
        dataset_reference = str(source_path.relative_to(PROJECT_ROOT))
    except ValueError:
        dataset_reference = str(source_path)
    output = Path(artifact_dir)
    output.mkdir(parents=True, exist_ok=True)
    fingerprint = _fingerprint(source_path)
    trained_at = datetime.now(timezone.utc).isoformat()
    sklearn_version = __import__("sklearn").__version__
    summary = {"trained_at_utc": trained_at, "random_seed": RANDOM_SEED,
        "dataset": str(source_path), "dataset_sha256": fingerprint, "dataset_quality": validate_dataset(data),
        "regression": {}, "classification": {}, "clustering": {}}

    # Regression: invalid/non-positive targets are excluded explicitly; other unusual values remain.
    if REGRESSION_TARGET in data:
        reg = data.copy()
        reg[REGRESSION_TARGET] = pd.to_numeric(reg[REGRESSION_TARGET], errors="coerce")
        reg = reg.loc[reg[REGRESSION_TARGET].notna() & (reg[REGRESSION_TARGET] > 0)].reset_index(drop=True)
        reg_features = _features(reg, REGRESSION_FEATURES)
        if len(reg_features) and len(reg) >= 30:
            train_i, val_i, test_i, split_desc = _regression_split(reg)
            X, y = reg[reg_features], reg[REGRESSION_TARGET]
            candidates = {
                "dummy_median": _pipeline(reg, reg_features, DummyRegressor(strategy="median")),
                "ridge": _pipeline(reg, reg_features, Ridge(alpha=10.0), scale=True),
                "random_forest": _pipeline(reg, reg_features, RandomForestRegressor(n_estimators=250, min_samples_leaf=2, max_features=.8, n_jobs=-1, random_state=RANDOM_SEED)),
                "gradient_boosting": _pipeline(reg, reg_features, GradientBoostingRegressor(n_estimators=120, max_depth=2, learning_rate=.06, random_state=RANDOM_SEED)),
            }
            try:
                from xgboost import XGBRegressor
                candidates["xgboost"] = _pipeline(reg, reg_features, XGBRegressor(
                    n_estimators=200, max_depth=5, learning_rate=.05, subsample=.8,
                    colsample_bytree=.8, reg_lambda=2.0, objective="reg:squarederror",
                    n_jobs=-1, random_state=RANDOM_SEED, verbosity=0))
            except ImportError:
                pass
            val_metrics = {}
            fitted = {}
            for name, model in candidates.items():
                model.fit(X.iloc[train_i], y.iloc[train_i])
                val_metrics[name] = _metric_regression(y.iloc[val_i], model.predict(X.iloc[val_i]))
                fitted[name] = model
            selected = min(val_metrics, key=lambda name: val_metrics[name]["mae"])
            model = fitted[selected]
            # Refit selected pipeline on train+validation only, then inspect test once.
            train_val = np.concatenate([train_i, val_i])
            model.fit(X.iloc[train_val], y.iloc[train_val])
            test_pred = model.predict(X.iloc[test_i])
            test_result = _metric_regression(y.iloc[test_i], test_pred)
            test_result["feature_importance"] = _feature_importance(model)
            actual_test = y.iloc[test_i].to_numpy(dtype=float)
            estimate_floor = np.maximum(test_pred, 1.0)
            test_result["listing_rent_screen"] = {
                "above_estimate_by_more_than_10pct": int(np.sum(actual_test > estimate_floor * 1.10)),
                "below_estimate_by_more_than_10pct": int(np.sum(actual_test < estimate_floor * .90)),
                "within_10pct": int(np.sum((actual_test >= estimate_floor * .90) & (actual_test <= estimate_floor * 1.10))),
                "records": int(len(actual_test)),
                "threshold": "Dataset rent compared with held-out predicted rent; 10% is a screening threshold, not verified overpricing/underpricing.",
            }
            diagnostic_plots = _save_regression_diagnostics(y.iloc[test_i], test_pred, output / "eda")
            error_frame = reg.iloc[test_i][[c for c in ("city", "locality", "property_type") if c in reg]].copy()
            error_frame["rent_range"] = pd.cut(y.iloc[test_i], bins=[0, 15000, 25000, 40000, np.inf], labels=["<=15k", "15k-25k", "25k-40k", ">40k"])
            error_frame["absolute_error"] = np.abs(test_pred - y.iloc[test_i].to_numpy())
            breakdown = {}
            for col in [c for c in ("city", "locality", "property_type", "rent_range") if c in error_frame]:
                grouped = error_frame.groupby(col, observed=True)["absolute_error"].agg(["count", "mean"]).query("count >= 10")
                breakdown[col] = {str(k): {"count": int(v["count"]), "mae": float(v["mean"])} for k, v in grouped.iterrows()}
            summary["regression"] = {"selected_model": selected, "validation_comparison": val_metrics,
                "test_metrics": test_result, "test_error_breakdown": breakdown,
                "train_records": int(len(train_i)), "validation_records": int(len(val_i)), "test_records": int(len(test_i)),
                "split_method": split_desc, "target_filter": "Rows with missing, non-numeric, or non-positive rent excluded; no other target outlier filtering.",
                "features": reg_features, "currency": "INR per month", "diagnostic_plots": diagnostic_plots,
                "model_parameters": _jsonable(model.named_steps["model"].get_params(deep=False))}
            dump({"pipeline": model, "features": reg_features, "model_name": selected, "metrics": summary["regression"], "dataset_sha256": fingerprint, "dataset_path": dataset_reference, "random_seed": RANDOM_SEED, "trained_at_utc": trained_at, "sklearn_version": sklearn_version}, output / "regression.joblib")

    # Classification scenario: estimate rent band before knowing rent; rent and all derived IDs are excluded.
    if CLASSIFICATION_TARGET in data:
        clf = data.dropna(subset=[CLASSIFICATION_TARGET]).copy()
        clf[CLASSIFICATION_TARGET] = clf[CLASSIFICATION_TARGET].astype(str)
        clf_features = _features(clf, CLASSIFICATION_FEATURES)
        if len(clf_features) and clf[CLASSIFICATION_TARGET].nunique() >= 2:
            X, y = clf[clf_features], clf[CLASSIFICATION_TARGET]
            counts = y.value_counts()
            stratify = y if counts.min() >= 2 else None
            X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=.2, random_state=RANDOM_SEED, stratify=stratify)
            x_fit, x_val, y_fit, y_val = train_test_split(X_train, y_train, test_size=.2, random_state=RANDOM_SEED + 1, stratify=y_train if y_train.value_counts().min() >= 2 else None)
            candidates = {
                "dummy_prior": _pipeline(clf, clf_features, DummyClassifier(strategy="prior")),
                "logistic_regression": _pipeline(clf, clf_features, LogisticRegression(max_iter=1000, class_weight="balanced", random_state=RANDOM_SEED), scale=True),
                "decision_tree": _pipeline(clf, clf_features, __import__("sklearn.tree", fromlist=["DecisionTreeClassifier"]).DecisionTreeClassifier(min_samples_leaf=5, class_weight="balanced", random_state=RANDOM_SEED)),
                "random_forest": _pipeline(clf, clf_features, RandomForestClassifier(n_estimators=250, min_samples_leaf=2, class_weight="balanced_subsample", n_jobs=-1, random_state=RANDOM_SEED)),
                "gradient_boosting": _pipeline(clf, clf_features, GradientBoostingClassifier(n_estimators=120, max_depth=2, learning_rate=.06, random_state=RANDOM_SEED)),
            }
            val = {}
            fitted = {}
            for name, model in candidates.items():
                model.fit(x_fit, y_fit)
                pred = model.predict(x_val)
                val[name] = {"accuracy": float(accuracy_score(y_val, pred)), "macro_f1": float(f1_score(y_val, pred, average="macro", zero_division=0)), "weighted_f1": float(f1_score(y_val, pred, average="weighted", zero_division=0))}
                fitted[name] = model
            selected = max(val, key=lambda n: (val[n]["macro_f1"], val[n]["accuracy"]))
            model = fitted[selected]
            model.fit(X_train, y_train)
            pred = model.predict(X_test)
            labels = list(model.named_steps["model"].classes_)
            metrics = {"accuracy": float(accuracy_score(y_test, pred)), "macro_f1": float(f1_score(y_test, pred, average="macro", zero_division=0)),
                "weighted_f1": float(f1_score(y_test, pred, average="weighted", zero_division=0)),
                "per_class": classification_report(y_test, pred, labels=labels, output_dict=True, zero_division=0),
                "labels": labels, "confusion_matrix": confusion_matrix(y_test, pred, labels=labels).tolist(),
                "test_records": int(len(y_test)), "train_records": int(len(X_train)),
                "class_distribution": {str(k): int(v) for k, v in y.value_counts().items()},
                "scenario": "Predict rent category from property and location characteristics before the rent is known; rent, rent-derived financial fields, property_id, and existing cluster_id are excluded.",
                "features": clf_features, "random_seed": RANDOM_SEED,
                "model_parameters": _jsonable(model.named_steps["model"].get_params(deep=False))}
            metrics["feature_importance"] = _feature_importance(model)
            summary["classification"] = {"selected_model": selected, "validation_comparison": val, "test_metrics": metrics}
            dump({"pipeline": model, "features": clf_features, "model_name": selected, "metrics": metrics, "dataset_sha256": fingerprint, "dataset_path": dataset_reference, "random_seed": RANDOM_SEED, "trained_at_utc": trained_at, "sklearn_version": sklearn_version}, output / "classification.joblib")

    # Clustering: fit actual property features, never the constant legacy cluster_id.
    # Rent is a valid clustering signal, but this selector must not apply the
    # supervised-target exclusion used by classification/regression.
    cluster_features = [c for c in CLUSTER_FEATURES if c in data.columns]
    if len(cluster_features) >= 2 and len(data) >= 20:
        cluster_data = data.copy().reset_index(drop=True)
        cluster_pipe_prep = _pipeline(cluster_data, cluster_features, "passthrough")
        transformed = cluster_pipe_prep.named_steps["preprocessor"].fit_transform(cluster_data[cluster_features])
        if hasattr(transformed, "toarray"):
            transformed = transformed.toarray()
        transformed = StandardScaler().fit_transform(transformed)
        sample = transformed
        if len(sample) > 2500:
            rng = np.random.default_rng(RANDOM_SEED)
            sample = sample[rng.choice(len(sample), 2500, replace=False)]
        candidates = []
        for k in range(2, min(9, len(data) - 1)):
            km = KMeans(n_clusters=k, n_init=10, random_state=RANDOM_SEED)
            km.fit(transformed)
            sil = float(silhouette_score(sample, km.predict(sample)))
            stability_runs = [km.predict(sample)]
            for stability_seed in (RANDOM_SEED + 101, RANDOM_SEED + 202):
                stability_model = KMeans(n_clusters=k, n_init=5, random_state=stability_seed).fit(sample)
                stability_runs.append(stability_model.predict(sample))
            stability = float(np.mean([adjusted_rand_score(stability_runs[i], stability_runs[j])
                for i in range(len(stability_runs)) for j in range(i + 1, len(stability_runs))]))
            candidates.append({"k": k, "silhouette": sil, "stability_ari": stability, "inertia": float(km.inertia_), "cluster_sizes": np.bincount(km.labels_, minlength=k).tolist()})
        # Favor interpretable, populated clusters while maximizing validation silhouette.
        viable = [r for r in candidates if min(r["cluster_sizes"]) >= max(10, int(.005 * len(data)))] or candidates
        stable_candidates = [row for row in viable if row["stability_ari"] >= .80]
        selected = max(stable_candidates or viable, key=lambda row: row["silhouette"])
        k = selected["k"]
        km = KMeans(n_clusters=k, n_init=20, random_state=RANDOM_SEED).fit(transformed)
        cluster_data["cluster"] = km.labels_
        nearest = NearestNeighbors(n_neighbors=min(21, len(data)), metric="euclidean", algorithm="brute").fit(transformed)
        cluster_scaler = StandardScaler().fit(cluster_pipe_prep.named_steps["preprocessor"].transform(cluster_data[cluster_features]))
        cluster_artifact = {"preprocessor": cluster_pipe_prep.named_steps["preprocessor"], "scaler": cluster_scaler,
            "kmeans": km, "neighbors": nearest, "transformed": transformed.astype(np.float32),
            "properties": cluster_data, "features": cluster_features, "candidate_metrics": candidates,
            "selected_k": k, "profiles": _cluster_profile(cluster_data), "dataset_sha256": fingerprint, "dataset_path": dataset_reference, "random_seed": RANDOM_SEED, "trained_at_utc": trained_at, "sklearn_version": sklearn_version, "model_parameters": _jsonable(km.get_params(deep=False))}
        dump(cluster_artifact, output / "clustering.joblib", compress=3)
        summary["clustering"] = {"selected_k": k, "candidate_metrics": candidates, "profiles": cluster_artifact["profiles"],
            "features": cluster_features, "random_seed": RANDOM_SEED, "model_parameters": cluster_artifact["model_parameters"], "similarity": "Euclidean distance after fitted imputation, one-hot encoding, and standardization; neighbor search excludes the queried row and may filter within the top 100 candidates."}

    summary["runtime"] = {"sklearn": sklearn_version, "pandas": pd.__version__, "numpy": np.__version__}
    if summary["regression"].get("validation_comparison"):
        pd.DataFrame.from_dict(summary["regression"]["validation_comparison"], orient="index").rename_axis("model").reset_index().to_csv(output / "regression_model_comparison.csv", index=False)
    (output / "model_report.json").write_text(json.dumps(_jsonable(summary), indent=2), encoding="utf-8")
    return summary


def load_artifact(name: str, artifact_dir: str | Path = ARTIFACT_DIR) -> dict:
    path = Path(artifact_dir) / f"{name}.joblib"
    if not path.exists():
        raise FileNotFoundError(f"The {name} model artifact is missing. Train models with `python -m ml.train`.")
    artifact = load(path)
    if not isinstance(artifact, dict):
        raise ValueError(f"The {name} model artifact is incompatible with the current rental dataset. Retrain models with `python -m ml.train`.")
    dataset_path = Path(artifact.get("dataset_path", DATASET_PATH))
    if not dataset_path.is_absolute():
        dataset_path = PROJECT_ROOT / dataset_path
    if artifact.get("dataset_sha256") != _fingerprint(dataset_path):
        raise ValueError(f"The {name} model artifact is incompatible with the current rental dataset. Retrain models with `python -m ml.train`.")
    return artifact


def predict_rent_result(features: dict, artifact: dict | None = None) -> dict:
    artifact = artifact or load_artifact("regression")
    frame = pd.DataFrame([{name: features.get(name) for name in artifact["features"]}])
    prediction = float(artifact["pipeline"].predict(frame)[0])
    return {"predicted_rent": max(0.0, prediction), "currency": "INR per month", "model_name": artifact["model_name"], "features": features,
        "limitations": "Estimate reflects the training data and may be unreliable for unusual properties or locations with sparse examples.",
        "test_metrics": artifact["metrics"].get("test_metrics", {})}


def classify_result(features: dict, artifact: dict | None = None) -> dict:
    artifact = artifact or load_artifact("classification")
    frame = pd.DataFrame([{name: features.get(name) for name in artifact["features"]}])
    model = artifact["pipeline"]
    label = str(model.predict(frame)[0])
    probabilities = model.predict_proba(frame)[0] if hasattr(model, "predict_proba") else None
    classes = model.named_steps["model"].classes_
    return {"rent_category": label, "probabilities": {str(c): float(p) for c, p in zip(classes, probabilities)} if probabilities is not None else None,
        "probability_type": "uncalibrated model scores" if probabilities is not None else None, "model_name": artifact["model_name"], "features": features,
        "test_metrics": artifact["metrics"]}


def recommend_similar(property_id: str | None = None, features: dict | None = None, limit: int = 5, filters: dict | None = None, artifact: dict | None = None) -> list[dict]:
    artifact = artifact or load_artifact("clustering")
    records = artifact["properties"]
    model_input = artifact["preprocessor"]
    scaler = artifact["scaler"]
    exclude = None
    if property_id is not None:
        if "property_id" not in records:
            raise ValueError("Dataset has no property_id column; provide property characteristics.")
        matches = records.index[records["property_id"].astype(str) == str(property_id)].tolist()
        if not matches:
            raise KeyError("Property ID was not found in the rental dataset.")
        exclude = matches[0]
        query = records.loc[[exclude], artifact["features"]]
    elif features is not None:
        query = pd.DataFrame([{name: features.get(name) for name in artifact["features"]}])
    else:
        raise ValueError("Provide a property_id or property characteristics.")
    query_transformed = scaler.transform(model_input.transform(query)).astype(np.float32)
    distances, indices = artifact["neighbors"].kneighbors(query_transformed, n_neighbors=len(records))
    found = []
    filters = filters or {}
    for distance, index in zip(distances[0], indices[0]):
        if index == exclude:
            continue
        row = records.iloc[int(index)]
        if filters.get("city") and str(row.get("city", "")).casefold() != str(filters["city"]).casefold(): continue
        if filters.get("locality") and str(row.get("locality", "")).casefold() != str(filters["locality"]).casefold(): continue
        if filters.get("property_type") and str(row.get("property_type", "")).casefold() != str(filters["property_type"]).casefold(): continue
        rent = pd.to_numeric(pd.Series([row.get("rent")]), errors="coerce").iloc[0]
        if filters.get("min_rent") is not None and (pd.isna(rent) or rent < filters["min_rent"]): continue
        if filters.get("max_rent") is not None and (pd.isna(rent) or rent > filters["max_rent"]): continue
        item = row.to_dict()
        item["similarity_distance"] = float(distance)
        found.append(item)
        if len(found) >= limit: break
    return found

