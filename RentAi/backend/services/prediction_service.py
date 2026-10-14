"""Train and use the rent model in memory for API predictions."""

from functools import lru_cache

import pandas as pd

from ml.regression.rent_model import encode_features, train_model


@lru_cache(maxsize=1)
def _get_model():
    """Train once per API process and retain the fitted model in memory."""
    return train_model()


def predict_rent(features: dict) -> float:
    """Predict monthly rent after encoding categorical request features."""
    model, feature_columns = _get_model()
    sample = pd.DataFrame([features])
    sample = encode_features(sample, feature_columns)
    return float(model.predict(sample)[0])
