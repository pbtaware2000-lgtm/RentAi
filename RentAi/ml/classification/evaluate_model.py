"""Evaluate the production rent-category KNN on a held-out dataset split."""

from functools import lru_cache

import numpy as np
import pandas as pd

from backend.services.classification_service import (
	FEATURES,
	_classify_against_training,
)
from backend.services.property_service import load_properties
from ml.regression.rent_model import train_test_split


EVALUATION_LABELS = ("High", "Low", "Medium", "Premium")


@lru_cache(maxsize=1)
def get_evaluation_results() -> dict[str, object]:
    """Compute and cache metrics for the held-out split for UI and CLI use."""
    data = load_properties()
    target = "rent_category"
    feature_columns = [*FEATURES, "city", "locality", "property_type"]
    required = [*FEATURES, "city", "property_type", target]
    if "locality" not in data.columns:
        data["locality"] = ""
    data["locality"] = data["locality"].fillna("").astype(str).str.strip()
    missing_columns = sorted(set(required) - set(data.columns))
    if missing_columns:
        raise ValueError(f"Classification dataset is missing columns: {missing_columns}")

    data = data.dropna(subset=required)
    if data.empty:
        raise ValueError("Classification dataset contains no complete labeled rows")

    # Match the API's shuffled 80/20 split. The locality comparison uses the
    # production classifier helper, while numeric ranges are fitted on train.
    x_train, x_test, y_train, y_test = train_test_split(
        data[feature_columns], data[target], test_size=0.2, random_state=42
    )
    training = x_train.copy()
    training[target] = y_train
    numeric = training.loc[:, FEATURES].to_numpy(dtype=float)
    minima = numeric.min(axis=0)
    ranges = np.ptp(numeric, axis=0)
    ranges[ranges == 0] = 1.0

    predictions = np.array([
		_classify_against_training(row.to_dict(), training, minima, ranges)
		for _, row in x_test.iterrows()
	], dtype=object)
    actual = y_test.to_numpy(dtype=object)

    labels = list(EVALUATION_LABELS)
    unexpected_labels = (set(actual) | set(predictions)) - set(labels)
    labels.extend(sorted(unexpected_labels))
    confusion = np.zeros((len(labels), len(labels)), dtype=int)
    label_indices = {label: index for index, label in enumerate(labels)}
    for truth, predicted in zip(actual, predictions):
        confusion[label_indices[truth], label_indices[predicted]] += 1

    per_class_precision = []
    per_class_recall = []
    for index in range(len(labels)):
        true_positive = int(confusion[index, index])
        predicted_count = int(confusion[:, index].sum())
        actual_count = int(confusion[index, :].sum())
        per_class_precision.append(true_positive / predicted_count if predicted_count else 0.0)
        per_class_recall.append(true_positive / actual_count if actual_count else 0.0)

    precision = float(np.mean(per_class_precision))
    recall = float(np.mean(per_class_recall))
    f1_per_class = [
        (2 * p * r / (p + r)) if (p + r) else 0.0
        for p, r in zip(per_class_precision, per_class_recall)
    ]
    f1 = float(np.mean(f1_per_class))
    accuracy = float(np.mean(predictions == actual))

    return {
        "accuracy": accuracy,
        "precision_macro": precision,
        "recall_macro": recall,
        "f1_macro": f1,
        "labels": labels,
        "confusion_matrix": confusion,
        "training_records": len(x_train),
        "test_records": len(x_test),
    }


def evaluate_model() -> dict[str, object]:
    """Print and return the shared held-out evaluation result."""
    results = get_evaluation_results()
    print("RentAI classification evaluation (held-out test set)")
    print(f"Training records: {results['training_records']:,}")
    print(f"Test records:     {results['test_records']:,}")
    print("Averaging:        macro (unweighted mean across classes)")
    print(f"Accuracy:         {results['accuracy']:.6f}")
    print(f"Precision:        {results['precision_macro']:.6f}")
    print(f"Recall:           {results['recall_macro']:.6f}")
    print(f"F1-score:         {results['f1_macro']:.6f}")
    print("Confusion matrix (rows=actual, columns=predicted):")
    print("Labels: " + ", ".join(results["labels"]))
    print(results["confusion_matrix"])
    return results


if __name__ == "__main__":
    evaluate_model()
