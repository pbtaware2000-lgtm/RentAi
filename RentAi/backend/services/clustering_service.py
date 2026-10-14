"""Recommend listings using nearest-neighbor distances over property features."""

import numpy as np

from backend.services.property_service import load_properties


NUMERIC_FEATURES = (
    "rent",
    "area_sqft",
    "bedrooms",
    "bathrooms",
    "distance_to_city_center_km",
    "age_of_property",
)
CATEGORICAL_WEIGHTS = {
    "city": 0.5,
    "locality": 0.75,
    "property_type": 0.75,
    "furnishing_status": 0.25,
}


def find_similar_properties(property_id: str, limit: int = 5) -> list[dict] | None:
    """Return the closest dataset listings, excluding the reference listing."""
    dataset = load_properties()
    required = {"property_id", *NUMERIC_FEATURES, *CATEGORICAL_WEIGHTS}
    missing_columns = required.difference(dataset.columns)
    if missing_columns:
        raise ValueError(f"Similarity dataset is missing columns: {sorted(missing_columns)}")

    matches = dataset.index[dataset["property_id"] == property_id]
    if len(matches) == 0:
        return None

    reference_index = matches[0]
    reference = dataset.loc[reference_index]
    candidates = dataset.drop(index=reference_index).copy()
    if candidates.empty:
        return []

    numeric_values = dataset.loc[:, NUMERIC_FEATURES].astype(float)
    ranges = numeric_values.max() - numeric_values.min()
    ranges = ranges.mask(ranges == 0, 1.0)
    differences = (candidates.loc[:, NUMERIC_FEATURES].astype(float) - reference.loc[list(NUMERIC_FEATURES)].astype(float)) / ranges
    distances = np.square(differences.to_numpy()).sum(axis=1)

    for column, weight in CATEGORICAL_WEIGHTS.items():
        distances += (candidates[column].to_numpy() != reference[column]) * weight

    candidates["_similarity_distance"] = distances
    return candidates.sort_values("_similarity_distance", kind="stable").head(limit).drop(columns="_similarity_distance").to_dict(orient="records")
