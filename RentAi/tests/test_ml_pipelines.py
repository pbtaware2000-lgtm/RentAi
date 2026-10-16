"""Focused tests for fitted ML pipelines and leakage-safe feature policy."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler
from fastapi.testclient import TestClient
from joblib import dump

from backend.main import app
from ml.data_validation import load_dataset
from ml.data_validation import validate_dataset
from ml.pipelines import (
    CLASSIFICATION_FEATURES,
    _cluster_profile,
    _features,
    _pipeline,
    _fingerprint,
    DATASET_PATH,
    load_artifact,
    recommend_similar,
)


class MLPipelineTests(unittest.TestCase):
    def test_validation_reports_missing_duplicates_and_invalid_values(self):
        data = pd.DataFrame({"rent": [1000, -2, None], "area_sqft": [10, 0, 20], "city": ["A", "A", None]})
        report = validate_dataset(pd.concat([data, data.iloc[[0]]], ignore_index=True))
        self.assertEqual(report["rows"], 4)
        self.assertEqual(report["duplicate_rows"], 1)
        self.assertEqual(report["invalid_nonpositive"]["rent"], 2)
        self.assertEqual(report["city_coverage"], 1)

    def test_supervised_feature_schemas_exclude_leakage_columns(self):
        data = pd.DataFrame({name: [1, 2] for name in ("rent", "rent_category", "cluster_id", "property_id", "city", "locality", "area_sqft")})
        selected = _features(data, CLASSIFICATION_FEATURES)
        self.assertNotIn("rent", selected)
        self.assertNotIn("rent_category", selected)
        self.assertNotIn("cluster_id", selected)
        self.assertNotIn("property_id", selected)
        self.assertIn("city", selected)

    def test_pipeline_handles_missing_values_and_unseen_categories(self):
        data = pd.DataFrame({"area": [10.0, None, 30.0, 40.0], "city": ["A", "A", "B", "B"]})
        model = _pipeline(data, ["area", "city"], Ridge(), scale=True)
        model.fit(data.iloc[:3], [100.0, 120.0, 300.0])
        prediction = model.predict(pd.DataFrame({"area": [20.0], "city": ["unseen"]}))
        self.assertTrue(np.isfinite(prediction[0]))

    def test_cluster_profiles_use_observed_cluster_statistics(self):
        profiles = _cluster_profile(pd.DataFrame({"cluster": [0, 0, 1], "rent": [10, 30, 100], "city": ["A", "A", "B"]}))
        self.assertEqual(profiles[0]["properties"], 2)
        self.assertEqual(profiles[0]["rent_median"], 20)
        self.assertEqual(profiles[0]["common_city"], {"A": 2})

    def test_model_artifact_save_load_and_dataset_fingerprint_guard(self):
        payload = {"marker": "saved", "dataset_sha256": _fingerprint(), "dataset_path": str(DATASET_PATH)}
        with tempfile.TemporaryDirectory() as directory:
            dump(payload, Path(directory) / "unit.joblib")
            self.assertEqual(load_artifact("unit", directory)["marker"], "saved")
            with patch("ml.pipelines._fingerprint", return_value="different-dataset"):
                with self.assertRaisesRegex(ValueError, "incompatible"):
                    load_artifact("unit", directory)
            with self.assertRaises(FileNotFoundError):
                load_artifact("missing", directory)

    def test_similarity_excludes_the_reference_and_returns_real_records(self):
        records = pd.DataFrame({
            "property_id": ["p1", "p2", "p3"], "rent": [1000.0, 1100.0, 5000.0],
            "area_sqft": [10.0, 11.0, 90.0], "city": ["A", "A", "B"],
            "property_type": ["Flat", "Flat", "Villa"],
        })
        features = ["rent", "area_sqft", "city", "property_type"]
        prep = _pipeline(records, features, "passthrough").named_steps["preprocessor"]
        encoded = prep.fit_transform(records[features])
        scaler = StandardScaler().fit(encoded)
        matrix = scaler.transform(encoded)
        artifact = {"properties": records, "features": features, "preprocessor": prep, "scaler": scaler,
            "neighbors": NearestNeighbors(metric="euclidean").fit(matrix)}
        with patch("ml.pipelines.load_artifact", return_value=artifact):
            found = recommend_similar(property_id="p1", limit=1)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["property_id"], "p2")
        self.assertIn("similarity_distance", found[0])


class MLAPIIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_dataset()
        cls.row = cls.data.iloc[0]
        cls.client = TestClient(app)

    def test_rent_prediction_returns_saved_model_and_metrics(self):
        row = self.row
        payload = {"city": row.city, "locality": row.locality, "property_type": row.property_type,
            "furnishing_status": row.furnishing_status, "area_sqft": float(row.area_sqft),
            "bedrooms": int(row.bedrooms), "bathrooms": int(row.bathrooms), "floor": int(row.floor),
            "distance_to_city_center_km": float(row.distance_to_city_center_km),
            "age_of_property": int(row.age_of_property), "parking": int(row.parking)}
        response = self.client.post("/ml/rent-prediction", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreater(response.json()["predicted_rent"], 0)
        self.assertEqual(response.json()["currency"], "INR per month")
        self.assertIn("mae", response.json()["test_metrics"])

    def test_category_prediction_does_not_require_or_return_rent_as_feature(self):
        row = self.row
        response = self.client.post("/ml/rent-classification", json={
            "city": row.city, "locality": row.locality, "property_type": row.property_type,
            "area_sqft": float(row.area_sqft), "bedrooms": int(row.bedrooms),
            "bathrooms": int(row.bathrooms), "furnishing_status": row.furnishing_status,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(response.json()["rent_category"], {"Low", "Medium", "High", "Premium"})
        self.assertIn("probabilities", response.json())
        self.assertNotIn("rent", response.json()["features"])

    def test_similarity_endpoint_returns_other_real_properties(self):
        row = self.row
        response = self.client.post("/ml/similar-properties", json={
            "property_id": str(row.property_id), "limit": 3, "city": str(row.city),
        })
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertLessEqual(result["count"], 3)
        self.assertTrue(all(item["property_id"] != str(row.property_id) for item in result["properties"]))
        self.assertTrue(all("similarity_distance" in item for item in result["properties"]))


if __name__ == "__main__":
    unittest.main()
