"""Evaluate the project rent model on its held-out CSV test split."""

import numpy as np
import pandas as pd

from ml.regression.rent_model import (
    DATASET_PATH,
    FEATURES,
    TARGET,
    LinearRegressionModel,
    encode_features,
    train_test_split,
)


def evaluate_model() -> dict[str, float]:
    """Fit on the 80% training split and report metrics on the untouched 20%."""
    data = pd.read_csv(DATASET_PATH)
    missing_columns = sorted(set(FEATURES + [TARGET]) - set(data.columns))
    if missing_columns:
        raise ValueError(f"Dataset is missing required columns: {missing_columns}")

    # Match train_model's missing-row policy so the API and evaluation use the
    # same eligible records. No test values are used to fit preprocessing.
    data = data.dropna(subset=FEATURES + [TARGET])
    if data.empty:
        raise ValueError("Dataset has no complete records for rent model evaluation")

    x_train, x_test, y_train, y_test = train_test_split(
        data[FEATURES], data[TARGET], test_size=0.2, random_state=42
    )

    x_train_encoded = encode_features(x_train)
    feature_columns = x_train_encoded.columns.tolist()
    # Align the test matrix to training-only dummy columns. Unseen categories
    # become all-zero dummy values, matching prediction_service behavior.
    x_test_encoded = encode_features(x_test, feature_columns)

    model = LinearRegressionModel().fit(x_train_encoded, y_train)
    predictions = model.predict(x_test_encoded)
    actual = y_test.to_numpy(dtype=float)
    errors = predictions - actual

    mae = float(np.mean(np.abs(errors)))
    squared_errors = np.square(errors)
    sse = float(np.sum(squared_errors))
    mse = float(np.mean(squared_errors))
    rmse = float(np.sqrt(mse))
    total_sum_squares = float(np.sum(np.square(actual - np.mean(actual))))
    r2 = (
        float(1.0 - sse / total_sum_squares)
        if total_sum_squares > 0
        else float("nan")
    )

    print("RentAI model evaluation (held-out test set)")
    print(f"Training records: {len(x_train):,}")
    print(f"Test records:     {len(x_test):,}")
    print(f"MAE:              {mae:,.2f} rupees (INR)")
    print(f"MSE:              {mse:,.2f} rupees squared (INR²)")
    print(f"RMSE:             {rmse:,.2f} rupees (INR)")
    print(f"R² score:         {r2:.6f}")
    print(f"SSE:              {sse:,.2f} rupees squared (INR²)")
    return {
        "mae": mae,
        "mse": mse,
        "rmse": rmse,
        "r2": r2,
        "sse": sse,
        "training_records": len(x_train),
        "test_records": len(x_test),
    }


if __name__ == "__main__":
    evaluate_model()
