"""CSV-backed rent prediction integration tests."""

import math
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from backend.main import app
from backend.schemas.prediction_schema import RentPredictionRequest
from backend.services.prediction_service import _get_model, predict_rent
from frontend.data_loader import DATA_PATH, load_dataset
from ml.regression.rent_model import (
	DATASET_PATH,
	FEATURES,
	TARGET,
	LinearRegressionModel,
	encode_features,
	train_model,
	train_test_split,
)


class RentPredictionIntegrationTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.data = load_dataset()
		cls.complete_data = cls.data.dropna(subset=[*FEATURES, TARGET])

	@staticmethod
	def _features_from_row(row):
		return {
			key: row[key].item() if hasattr(row[key], "item") else row[key]
			for key in FEATURES
		}

	def test_loads_the_real_csv_dataset_and_rent_target(self):
		self.assertEqual(DATA_PATH.resolve(), DATASET_PATH.resolve())
		self.assertEqual(DATASET_PATH, DATA_PATH.parent / "rentai_dataset.csv")
		self.assertEqual(self.data.shape, (10_000, 33))
		self.assertIn(TARGET, self.data.columns)
		self.assertGreater(self.complete_data[TARGET].notna().sum(), 0)

	def test_valid_real_dataset_features_produce_monthly_rent(self):
		row = self.complete_data.iloc[0]
		predicted_rent = predict_rent(self._features_from_row(row))
		self.assertTrue(math.isfinite(predicted_rent))
		self.assertGreater(predicted_rent, 0)

	def test_model_fit_uses_only_the_training_partition(self):
		expected_train, expected_test, expected_train_target, _ = train_test_split(
			self.complete_data[FEATURES], self.complete_data[TARGET],
			test_size=0.2, random_state=42,
		)
		captured = {}

		def capture_fit(_model, features, target):
			captured["feature_indices"] = features.index.tolist()
			captured["target_indices"] = target.index.tolist()
			return _model

		with patch.object(
			LinearRegressionModel, "fit", autospec=True, side_effect=capture_fit
		) as fit:
			train_model()

		fit.assert_called_once()
		self.assertEqual(captured["feature_indices"], expected_train.index.tolist())
		self.assertEqual(captured["target_indices"], expected_train_target.index.tolist())
		self.assertTrue(set(captured["feature_indices"]).isdisjoint(expected_test.index))

	def test_prediction_contract_rejects_missing_and_invalid_inputs(self):
		row = self.complete_data.iloc[0]
		features = self._features_from_row(row)
		missing_area = {key: value for key, value in features.items() if key != "area_sqft"}
		invalid_area = {**features, "area_sqft": 0}
		blank_city = {**features, "city": "   "}
		for invalid_features in (missing_area, invalid_area, blank_city):
			with self.subTest(features=invalid_features), self.assertRaises(ValidationError):
				RentPredictionRequest.model_validate(invalid_features)

		response = TestClient(app).post("/ml/rent-prediction", json=missing_area)
		self.assertEqual(response.status_code, 422)

	def test_training_and_prediction_use_the_same_encoded_feature_columns(self):
		model, feature_columns = _get_model()
		x_train, _, _, _ = train_test_split(
			self.complete_data[FEATURES], self.complete_data[TARGET],
			test_size=0.2, random_state=42,
		)
		sample = x_train.iloc[0]
		features = self._features_from_row(sample)
		trained_sample = encode_features(pd.DataFrame([features]), feature_columns)
		self.assertEqual(trained_sample.columns.tolist(), feature_columns)
		self.assertEqual(model.coefficients.shape[0], len(feature_columns) + 1)

		with patch(
			"backend.services.prediction_service.encode_features", wraps=encode_features
		) as prediction_preprocessor:
			prediction = predict_rent(features)
		prediction_preprocessor.assert_called_once()
		self.assertEqual(prediction_preprocessor.call_args.args[1], feature_columns)
		self.assertTrue(math.isfinite(prediction))

	def test_owner_can_estimate_without_saving_and_rents_remain_separate(self):
		with patch(
			"frontend.pages.owner_sections.predict_rent", return_value=32_000.0
		) as estimate, patch(
			"frontend.pages.owner_sections.classify_rent", return_value="High"
		) as classify, patch(
			"frontend.pages.owner_sections.similar_properties_for_features",
			return_value=[{"property_id": "catalog-1", "city": "Pune", "locality": "Aundh", "property_type": "Apartment", "rent": 31_000, "area_sqft": 880, "bedrooms": 2}],
		) as similar, patch("frontend.pages.owner_sections.create") as create_record:
			app = AppTest.from_string(
				"import pandas as pd\n"
				"from frontend.pages.owner_sections import render_add_property\n"
				"render_add_property(pd.DataFrame({'furnishing_status':['Semi-Furnished']}))",
				default_timeout=30,
			).run()
			app.text_input(key="owner_add_title").set_value("Sunny Aundh apartment")
			app.text_input(key="owner_add_city").set_value("Pune")
			app.text_input(key="owner_add_locality").set_value("Aundh")
			app.number_input(key="owner_add_asking_rent").set_value(28_000)
			app.number_input(key="owner_add_area_sqft").set_value(900.0)
			app.number_input(key="owner_add_bedrooms").set_value(2)
			app.number_input(key="owner_add_bathrooms").set_value(1)
			app.number_input(key="owner_add_floor").set_value(3)
			app.number_input(key="owner_add_distance").set_value(4.0)
			app.number_input(key="owner_add_age").set_value(5)
			app.number_input(key="owner_add_parking").set_value(1)
			app.button(key="owner_estimate_rent").click().run()

		self.assertFalse(app.exception)
		expected_features = {
			"city": "Pune", "locality": "Aundh", "property_type": "Apartment",
			"furnishing_status": "Semi-Furnished", "area_sqft": 900.0,
			"bedrooms": 2, "bathrooms": 1, "floor": 3,
			"distance_to_city_center_km": 4.0, "age_of_property": 5, "parking": 1,
		}
		estimate.assert_called_once_with(expected_features)
		classify.assert_called_once_with({
			"rent": 32_000.0, "area_sqft": 900.0, "city": "Pune",
			"locality": "Aundh", "property_type": "Apartment",
		})
		self.assertEqual(similar.call_args.kwargs["limit"], 3)
		create_record.assert_not_called()
		metrics = {metric.label: metric.value for metric in app.metric}
		self.assertEqual(metrics["Monthly asking rent"], "₹28,000 INR")
		self.assertEqual(metrics["ML-predicted monthly rent"], "₹32,000 INR")
		self.assertEqual(metrics["Estimated rent category"], "High")
		self.assertTrue(any("Similar dataset properties" in item.value for item in app.markdown))

		with patch("frontend.pages.owner_sections.create") as saved_create, patch(
			"frontend.pages.owner_sections.st.rerun"
		):
			app.button(key="owner_save_property").click().run()
		self.assertFalse(app.exception)
		saved_create.assert_called_once()
		saved_listing = saved_create.call_args.args[1]
		self.assertEqual(saved_listing["rent"], 28_000)
		self.assertEqual(saved_listing["area_sqft"], 900.0)
		self.assertEqual(saved_listing["furnishing_status"], "Semi-Furnished")
		self.assertNotIn("ml_estimate", saved_listing)

	def test_renter_prediction_page_calls_model_and_displays_monthly_inr(self):
		sample = self._features_from_row(self.complete_data.iloc[0])
		page_data = pd.DataFrame([sample])
		with patch("frontend.pages.rent_prediction.predict_rent", return_value=24_567.0) as estimate:
			app = AppTest.from_string(
				"import pandas as pd\n"
				"from frontend.pages.renter_workspace import render_prediction\n"
				f"render_prediction(pd.DataFrame([{sample!r}]))",
				default_timeout=30,
			).run()
			app.number_input(key="prediction-area-sqft").set_value(float(sample["area_sqft"]))
			app.number_input(key="prediction-bedrooms").set_value(int(sample["bedrooms"]))
			app.number_input(key="prediction-bathrooms").set_value(int(sample["bathrooms"]))
			app.number_input(key="prediction-floor").set_value(int(sample["floor"]))
			app.number_input(key="prediction-distance").set_value(float(sample["distance_to_city_center_km"]))
			app.number_input(key="prediction-age").set_value(int(sample["age_of_property"]))
			next(button for button in app.button if button.label == "Predict Rent").click().run()

		self.assertFalse(app.exception)
		estimate.assert_called_once()
		self.assertEqual(estimate.call_args.args[0], sample)
		self.assertTrue(any(metric.label == "Model estimate" and metric.value == "₹24,567 INR" for metric in app.metric))

	def test_renter_prediction_page_rejects_missing_area_without_calling_model(self):
		sample = self._features_from_row(self.complete_data.iloc[0])
		with patch("frontend.pages.rent_prediction.predict_rent") as estimate:
			app = AppTest.from_string(
				"import pandas as pd\n"
				"from frontend.pages.renter_workspace import render_prediction\n"
				f"render_prediction(pd.DataFrame([{sample!r}]))",
				default_timeout=30,
			).run()
			next(button for button in app.button if button.label == "Predict Rent").click().run()
		self.assertFalse(app.exception)
		estimate.assert_not_called()
		self.assertIn("Area must be greater than zero.", [error.value for error in app.error])

	def test_existing_classification_and_clustering_endpoints_still_work(self):
		row = self.complete_data.iloc[0]
		client = TestClient(app)
		classification = client.post("/ml/rent-classification", json={
			"rent": float(row["rent"]), "area_sqft": float(row["area_sqft"]),
			"city": str(row["city"]), "locality": str(row["locality"]),
			"property_type": str(row["property_type"]),
		})
		self.assertEqual(classification.status_code, 200)
		self.assertTrue(classification.json()["rent_category"])

		similar = client.post("/ml/similar-properties", json={
			"property_id": str(row["property_id"]), "limit": 3,
		})
		self.assertEqual(similar.status_code, 200)
		self.assertLessEqual(similar.json()["count"], 3)

	def test_unsaved_property_profile_returns_real_csv_comparables(self):
		row = self.complete_data.iloc[0]
		response = TestClient(app).post("/ml/similar-properties/search", json={
			"city": str(row["city"]), "locality": str(row["locality"]),
			"property_type": str(row["property_type"]),
			"furnishing_status": str(row["furnishing_status"]),
			"rent": float(row["rent"]), "area_sqft": float(row["area_sqft"]),
			"bedrooms": int(row["bedrooms"]), "bathrooms": int(row["bathrooms"]),
			"distance_to_city_center_km": float(row["distance_to_city_center_km"]),
			"age_of_property": int(row["age_of_property"]), "limit": 3,
		})
		self.assertEqual(response.status_code, 200, response.text)
		self.assertEqual(response.json()["count"], 3)
		self.assertTrue(all("property_id" in item and "rent" in item for item in response.json()["properties"]))

		invalid = TestClient(app).post("/ml/similar-properties/search", json={
			"city": "Pune", "locality": "Aundh", "property_type": "Apartment",
			"furnishing_status": "Furnished", "rent": 25000, "area_sqft": 0,
			"bedrooms": 2, "bathrooms": 1, "distance_to_city_center_km": 3,
			"age_of_property": 4,
		})
		self.assertEqual(invalid.status_code, 422)


if __name__ == "__main__":
	unittest.main()