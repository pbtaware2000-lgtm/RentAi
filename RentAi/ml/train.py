"""Run reproducible RentAI model training: python -m ml.train"""
import argparse
import json

from ml.data_validation import DATASET_PATH, generate_eda_plots, load_dataset
from ml.pipelines import ARTIFACT_DIR, train_all


def main():
    parser = argparse.ArgumentParser(description="Train and evaluate RentAI ML pipelines.")
    parser.add_argument("--dataset", default=str(DATASET_PATH))
    parser.add_argument("--artifacts", default=str(ARTIFACT_DIR))
    parser.add_argument("--eda", action="store_true", help="Also write EDA plots to artifacts/eda.")
    args = parser.parse_args()
    report = train_all(args.dataset, args.artifacts)
    if args.eda:
        plots = generate_eda_plots(load_dataset(args.dataset), f"{args.artifacts}/eda")
        report["eda_plots"] = plots
    compact = {"trained_at_utc": report.get("trained_at_utc"), "dataset_sha256": report.get("dataset_sha256")}
    if report.get("regression"):
        reg = report["regression"]
        compact["regression"] = {"selected_model": reg["selected_model"], "test_metrics": {k: reg["test_metrics"][k] for k in ("mae", "rmse", "r2", "median_absolute_error")}, "records": {k: reg[k] for k in ("train_records", "validation_records", "test_records")}}
    if report.get("classification"):
        cls = report["classification"]
        compact["classification"] = {"selected_model": cls["selected_model"], "test_metrics": {k: cls["test_metrics"][k] for k in ("accuracy", "macro_f1", "weighted_f1", "test_records")}}
    if report.get("clustering"):
        cl = report["clustering"]
        compact["clustering"] = {"selected_k": cl["selected_k"], "candidate_metrics": [{k: row[k] for k in ("k", "silhouette", "stability_ari", "inertia")} for row in cl["candidate_metrics"]]}
    print(json.dumps(compact, indent=2, default=str))
    print(f"Full report: {args.artifacts}/model_report.json")


if __name__ == "__main__":
    main()
