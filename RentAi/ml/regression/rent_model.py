"""Train an in-memory rent regression model from the project CSV."""

from pathlib import Path

import pandas as pd
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = PROJECT_ROOT / "data" / "rentai_dataset.csv"
TARGET = "rent"
CATEGORICAL_FEATURES = ["city", "locality", "property_type", "furnishing_status"]
FEATURES = CATEGORICAL_FEATURES + [
    "area_sqft",
    "bedrooms",
    "bathrooms",
    "floor",
    "distance_to_city_center_km",
    "age_of_property",
    "parking",
]


def train_test_split(features, target, test_size=0.2, random_state=42):
    """Split aligned Pandas data into shuffled training and test sets."""
    shuffled = features.sample(frac=1, random_state=random_state)
    train_count = int(len(shuffled) * (1 - test_size))
    x_train = shuffled.iloc[:train_count]
    x_test = shuffled.iloc[train_count:]
    y_train = target.loc[x_train.index]
    y_test = target.loc[x_test.index]
    return x_train, x_test, y_train, y_test


class LinearRegressionModel:
    """Basic least-squares linear regression using NumPy."""

    def fit(self, features, target):
        x = np.asarray(features, dtype=float)
        y = np.asarray(target, dtype=float)
        x_with_intercept = np.column_stack((np.ones(len(x)), x))
        self.coefficients = np.linalg.lstsq(x_with_intercept, y, rcond=None)[0]
        return self

    def predict(self, features):
        x = np.asarray(features, dtype=float)
        x_with_intercept = np.column_stack((np.ones(len(x)), x))
        return x_with_intercept @ self.coefficients


def encode_features(features: pd.DataFrame, feature_columns: list[str] | None = None) -> pd.DataFrame:
    """Encode model inputs consistently and add rent-relevant nonlinear terms."""
    encoded = pd.get_dummies(
        features, columns=CATEGORICAL_FEATURES, dtype=float
    )

    area = pd.to_numeric(encoded["area_sqft"], errors="coerce")
    for category in ("locality", "property_type"):
        category_columns = [
            column for column in encoded.columns
            if column.startswith(f"{category}_")
        ]
        for column in category_columns:
            encoded[f"area_x_{column}"] = area * encoded[column]

    encoded["area_squared"] = area.pow(2) / 1000.0
    encoded["area_x_bedrooms"] = area * pd.to_numeric(
        encoded["bedrooms"], errors="coerce"
    )
    if feature_columns is not None:
        encoded = encoded.reindex(columns=feature_columns, fill_value=0)
    return encoded


def train_model() -> tuple[LinearRegressionModel, list[str]]:
    """Split the CSV data, encode categories, and fit a rent model in memory."""
    data = pd.read_csv(DATASET_PATH)
    missing_columns = sorted(set(FEATURES + [TARGET]) - set(data.columns))
    if missing_columns:
        raise ValueError(f"Dataset is missing required columns: {missing_columns}")

    data = data.dropna(subset=FEATURES + [TARGET])
    if data.empty:
        raise ValueError("Dataset has no complete records for rent model training")

    x_train, _, y_train, _ = train_test_split(
        data[FEATURES], data[TARGET], test_size=0.2, random_state=42
    )
    x_train = encode_features(x_train)
    feature_columns = x_train.columns.tolist()
    model = LinearRegressionModel().fit(x_train, y_train)
    return model, feature_columns
