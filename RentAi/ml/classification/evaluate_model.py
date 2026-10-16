"""Read the classifier's actual held-out metrics from the training report."""
import json

from ml.pipelines import ARTIFACT_DIR


def get_evaluation_results() -> dict:
    path = ARTIFACT_DIR / "model_report.json"
    if not path.exists():
        raise FileNotFoundError("ML evaluation report is missing. Run `python -m ml.train` first.")
    report = json.loads(path.read_text(encoding="utf-8"))
    try:
        result = report["classification"]["test_metrics"]
    except KeyError as exc:
        raise ValueError("The training report does not contain classification test metrics.") from exc
    per_class = result.get("per_class", {})
    labels = result.get("labels", [])
    return {
        "accuracy": result["accuracy"],
        "precision_macro": per_class.get("macro avg", {}).get("precision", 0.0),
        "recall_macro": per_class.get("macro avg", {}).get("recall", 0.0),
        "f1_macro": result["macro_f1"],
        "f1_weighted": result["weighted_f1"],
        "per_class": {label: per_class.get(label, {}) for label in labels},
        "labels": labels,
        "confusion_matrix": result["confusion_matrix"],
        "training_records": result["train_records"],
        "test_records": result["test_records"],
        "model_name": report["classification"]["selected_model"],
        "class_distribution": result["class_distribution"],
        "scenario": result["scenario"],
        "feature_importance": result.get("feature_importance", []),
    }


def evaluate_model() -> dict:
    result = get_evaluation_results()
    print(f"Selected model: {result['model_name']}")
    print(f"Accuracy: {result['accuracy']:.4f}; macro F1: {result['f1_macro']:.4f}; weighted F1: {result['f1_weighted']:.4f}")
    print("Confusion matrix (rows=actual, columns=predicted):")
    print(result["confusion_matrix"])
    return result


if __name__ == "__main__":
    evaluate_model()
