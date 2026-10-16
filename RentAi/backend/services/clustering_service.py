"""Persisted clustering and fitted-space similar-property recommendations."""
from functools import lru_cache

from ml.pipelines import load_artifact, recommend_similar


@lru_cache(maxsize=1)
def _get_model():
    return load_artifact("clustering")


def find_similar_properties(property_id: str | None = None, limit: int = 5, filters: dict | None = None, features: dict | None = None) -> list[dict] | None:
    try:
        return recommend_similar(property_id=property_id, features=features, limit=limit, filters=filters, artifact=_get_model())
    except KeyError:
        return None


def cluster_analytics() -> dict:
    artifact = _get_model()
    return {"selected_k": artifact["selected_k"], "candidate_metrics": artifact["candidate_metrics"], "profiles": artifact["profiles"], "features": artifact["features"]}
