"""Classification inference backed by a saved, leak-free sklearn pipeline."""
from functools import lru_cache

from ml.pipelines import classify_result, load_artifact


@lru_cache(maxsize=1)
def _get_model():
    return load_artifact("classification")


def classify_rent_details(features: dict) -> dict:
    # The API/UI may send a legacy rent field; intentionally drop it.
    safe = {key: value for key, value in features.items() if key != "rent"}
    return classify_result(safe, _get_model())


def classify_rent(features: dict) -> str:
    """Backward-compatible category-only helper."""
    return classify_rent_details(features)["rent_category"]
