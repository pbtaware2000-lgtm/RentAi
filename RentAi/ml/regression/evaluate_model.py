"""Read actual held-out regression results from the saved training report."""
import json

from ml.pipelines import ARTIFACT_DIR


def evaluate_model() -> dict:
    report_path = ARTIFACT_DIR / "model_report.json"
    if not report_path.exists():
        raise FileNotFoundError("ML evaluation report is missing. Run `python -m ml.train` first.")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    try:
        result = report["regression"]
        metrics = result["test_metrics"]
    except KeyError as exc:
        raise ValueError("The saved report does not include regression test metrics.") from exc
    response = {key: metrics[key] for key in ("mae", "rmse", "r2", "median_absolute_error")}
    response.update({key: result[key] for key in ("train_records", "validation_records", "test_records", "split_method", "selected_model", "currency")})
    response["validation_comparison"] = result["validation_comparison"]
    response["feature_importance"] = metrics.get("feature_importance", [])
    response["listing_rent_screen"] = metrics.get("listing_rent_screen", {})
    return response


if __name__ == "__main__":
    result = evaluate_model()
    print(f"Selected model: {result['selected_model']}")
    print(f"Held-out test records: {result['test_records']:,}")
    print(f"MAE: {result['mae']:,.2f} INR/month")
    print(f"RMSE: {result['rmse']:,.2f} INR/month")
    print(f"R²: {result['r2']:.4f}")
