"""Reusable, non-destructive validation and exploratory summaries for RentAI data."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = PROJECT_ROOT / "data" / "rentai_dataset.csv"
REQUIRED_FOR_ML = ("rent",)


def load_dataset(path: str | Path = DATASET_PATH) -> pd.DataFrame:
    """Load the original CSV from a project-relative default path."""
    path = Path(path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"Rental dataset not found: {path}")
    return pd.read_csv(path)


def validate_dataset(data: pd.DataFrame) -> dict:
    """Return a JSON-friendly quality report without mutating or filtering data."""
    numeric = data.select_dtypes(include="number")
    report = {
        "rows": int(len(data)),
        "columns": int(len(data.columns)),
        "duplicate_rows": int(data.duplicated().sum()),
        "duplicate_property_records": int(data.drop(columns=[c for c in ("property_id", "cluster_id") if c in data], errors="ignore").duplicated().sum()),
        "missing_by_column": {str(k): int(v) for k, v in data.isna().sum().items()},
        "unique_by_column": {str(k): int(v) for k, v in data.nunique(dropna=True).items()},
        "dtypes": {str(k): str(v) for k, v in data.dtypes.items()},
        "invalid_nonpositive": {},
        "numeric_summary": {},
        "categorical_frequencies": {},
        "target_leakage_columns": [c for c in ("rent", "rent_category", "cluster_id") if c in data],
    }
    for column in ("rent", "area_sqft"):
        if column in data:
            values = pd.to_numeric(data[column], errors="coerce")
            report["invalid_nonpositive"][column] = int((values <= 0).sum() + values.isna().sum())
    for column in numeric:
        values = pd.to_numeric(numeric[column], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if len(values):
            report["numeric_summary"][column] = {
                "mean": float(values.mean()), "median": float(values.median()),
                "std": float(values.std()), "skew": float(values.skew()),
                "p01": float(values.quantile(.01)), "p99": float(values.quantile(.99)),
                "iqr_outlier_count": int(((values < values.quantile(.25) - 1.5 * (values.quantile(.75)-values.quantile(.25))) | (values > values.quantile(.75) + 1.5 * (values.quantile(.75)-values.quantile(.25)))).sum()),
            }
    for column in data.select_dtypes(exclude="number"):
        report["categorical_frequencies"][column] = {str(k): int(v) for k, v in data[column].fillna("<missing>").astype(str).value_counts().head(100).items()}
    for column in ("city", "locality", "property_type", "furnishing_status", "rent_category", "cluster_id"):
        if column in data:
            report[f"{column}_coverage"] = int(data[column].nunique(dropna=True))
    return report


def generate_eda_plots(data: pd.DataFrame, output_dir: str | Path) -> list[str]:
    """Write useful data-derived plots; source data remains untouched."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    written = []
    numeric = data.select_dtypes(include="number")
    plots = []
    for column in ("rent", "area_sqft"):
        if column in data:
            plots.append((f"{column}_distribution.png", lambda c=column: sns.histplot(pd.to_numeric(data[c], errors="coerce").dropna(), bins=40)))
    if "city" in data and "rent" in data:
        plots.append(("rent_by_city.png", lambda: sns.boxplot(data=data, x="city", y="rent")))
    if "property_type" in data and "rent" in data:
        plots.append(("rent_by_property_type.png", lambda: sns.boxplot(data=data, x="property_type", y="rent")))
    if not numeric.empty:
        plots.append(("numeric_correlation.png", lambda: sns.heatmap(numeric.corr(), cmap="vlag", center=0)))
    plots.append(("missing_values.png", lambda: sns.heatmap(data.isna(), cbar=False, yticklabels=False)))
    for name, draw in plots:
        fig, ax = plt.subplots(figsize=(11, 6))
        plt.sca(ax)
        draw()
        ax.tick_params(axis="x", labelrotation=45)
        fig.tight_layout()
        fig.savefig(output / name, dpi=130)
        plt.close(fig)
        written.append(name)
    return written
