"""Compatibility constants for the public rent-prediction feature contract.

Production training and inference live in :mod:`ml.pipelines`; this module no
longer contains a second handwritten estimator or trains on import/request.
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = PROJECT_ROOT / "data" / "rentai_dataset.csv"
TARGET = "rent"
CATEGORICAL_FEATURES = ["city", "locality", "property_type", "furnishing_status"]
FEATURES = CATEGORICAL_FEATURES + [
    "area_sqft", "bedrooms", "bathrooms", "floor", "distance_to_city_center_km",
    "age_of_property", "parking",
]
