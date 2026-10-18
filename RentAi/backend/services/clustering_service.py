"""Persisted clustering and fitted-space similar-property recommendations."""
from functools import lru_cache

from ml.pipelines import load_artifact, recommend_similar


@lru_cache(maxsize=1)
def _get_model():
    return load_artifact("clustering")


def find_similar_properties(property_id: str | None = None, limit: int = 5, filters: dict | None = None, features: dict | None = None) -> list[dict] | None:
    try:
        return recommend_similar(property_id=property_id, features=features, limit=limit, filters=filters, artifact=_get_model())
    except KeyError:
        return None


def cluster_analytics() -> dict:
    artifact = _get_model()
    return {"selected_k": artifact["selected_k"], "candidate_metrics": artifact["candidate_metrics"], "profiles": artifact["profiles"], "features": artifact["features"]}

def _required_dataset(dataset):
    required = {"property_id", *NUMERIC_FEATURES, *CATEGORICAL_WEIGHTS}
    missing_columns = required.difference(dataset.columns)
    if missing_columns:
        raise ValueError(f"Similarity dataset is missing columns: {sorted(missing_columns)}")
    return dataset


def _nearest_properties(dataset, reference: dict, limit: int, exclude_index=None) -> list[dict]:
    candidates = dataset.drop(index=exclude_index).copy() if exclude_index is not None else dataset.copy()
    if candidates.empty:
        return []

    numeric_values = dataset.loc[:, NUMERIC_FEATURES].astype(float)
    ranges = numeric_values.max() - numeric_values.min()
    ranges = ranges.mask(ranges == 0, 1.0)
    reference_values = np.array([reference[name] for name in NUMERIC_FEATURES], dtype=float)
    differences = (candidates.loc[:, NUMERIC_FEATURES].astype(float) - reference_values) / ranges
    distances = np.square(differences.to_numpy()).sum(axis=1)

    for column, weight in CATEGORICAL_WEIGHTS.items():
        distances += (candidates[column].to_numpy() != reference[column]) * weight

    candidates["_similarity_distance"] = distances
    return candidates.sort_values("_similarity_distance", kind="stable").head(limit).drop(columns="_similarity_distance").to_dict(orient="records")


def find_similar_properties(property_id: str, limit: int = 5) -> list[dict] | None:
    """Return the closest dataset listings, excluding the reference listing."""
    dataset = _required_dataset(load_properties())

    matches = dataset.index[dataset["property_id"] == property_id]
    if len(matches) == 0:
        return None

    reference_index = matches[0]
    reference = dataset.loc[reference_index].to_dict()
    return _nearest_properties(dataset, reference, limit, exclude_index=reference_index)


def find_similar_properties_for_features(features: dict, limit: int = 5) -> list[dict]:
    """Rank CSV properties against an unsaved property feature profile."""
    dataset = _required_dataset(load_properties())
    required = {"city", "locality", "property_type", "furnishing_status", *NUMERIC_FEATURES}
    missing_features = required.difference(features)
    if missing_features:
        raise ValueError(f"Similarity features are missing: {sorted(missing_features)}")
    return _nearest_properties(dataset, features, limit)
