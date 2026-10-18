"""Load the saved regression pipeline once per API process."""

from functools import lru_cache

from ml.pipelines import load_artifact, predict_rent_result


@lru_cache(maxsize=1)
def _get_model():
    """Load, never train, the model once per API process."""
    return load_artifact("regression")


def predict_rent(features: dict) -> float:
    """Backwards-compatible scalar prediction helper."""
    return float(predict_rent_result(features, _get_model())["predicted_rent"])


def predict_rent_details(features: dict) -> dict:
    return predict_rent_result(features, _get_model())
