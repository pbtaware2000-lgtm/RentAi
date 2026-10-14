"""CSV-backed K-nearest-neighbors rent category classification."""

from functools import lru_cache

import numpy as np

from backend.services.property_service import load_properties


FEATURES = ("rent", "area_sqft")
NEIGHBOR_COUNT = 5
CATEGORICAL_MISMATCH_PENALTY = 0.25
LOCALITY_MISMATCH_PENALTY = 0.0001


@lru_cache(maxsize=1)
def _training_data():
    """Load labeled examples and feature ranges once per API process."""
    dataset = load_properties()
    if "locality" not in dataset.columns:
        dataset["locality"] = ""
    required = [*FEATURES, "city", "property_type", "rent_category"]
    missing = set(required).difference(dataset.columns)
    if missing:
        raise ValueError(f"Classification dataset is missing columns: {sorted(missing)}")

    dataset = dataset.dropna(subset=required)
    if dataset.empty:
        raise ValueError("Classification dataset contains no complete labeled rows")

    dataset["locality"] = dataset["locality"].fillna("").astype(str).str.strip()

    numeric = dataset.loc[:, FEATURES].to_numpy(dtype=float)
    minima = numeric.min(axis=0)
    ranges = np.ptp(numeric, axis=0)
    ranges[ranges == 0] = 1.0
    return dataset, minima, ranges


def _classify_against_training(features: dict, dataset, minima: np.ndarray,
                               ranges: np.ndarray) -> str:
    """Apply the production distance and majority-vote logic to training rows."""
    query_numeric = np.array([features[name] for name in FEATURES], dtype=float)
    normalized_query = (query_numeric - minima) / ranges
    training_numeric = (dataset.loc[:, FEATURES].to_numpy(dtype=float) - minima) / ranges

    distances = np.sum((training_numeric - normalized_query) ** 2, axis=1)
    distances += (dataset["city"].to_numpy() != features["city"]) * CATEGORICAL_MISMATCH_PENALTY
    distances += (dataset["property_type"].to_numpy() != features["property_type"]) * CATEGORICAL_MISMATCH_PENALTY

    locality = features.get("locality")
    if locality is not None and str(locality).strip():
        training_localities = dataset["locality"].fillna("").astype(str).to_numpy()
        distances += (training_localities != str(locality).strip()) * LOCALITY_MISMATCH_PENALTY

    nearest_indices = np.argpartition(distances, min(NEIGHBOR_COUNT - 1, len(distances) - 1))[:NEIGHBOR_COUNT]
    nearest_indices = nearest_indices[np.argsort(distances[nearest_indices])]
    labels = dataset["rent_category"].to_numpy()[nearest_indices]
    votes = {}
    for index, label in zip(nearest_indices, labels):
        votes[label] = (votes.get(label, (0, float("inf")))[0] + 1,
                        min(votes.get(label, (0, float("inf")))[1], distances[index]))
    return max(votes, key=lambda label: (votes[label][0], -votes[label][1]))


def classify_rent(features: dict) -> str:
    """Predict a category; locality is optional for older API callers."""
    dataset, minima, ranges = _training_data()
    return _classify_against_training(features, dataset, minima, ranges)
