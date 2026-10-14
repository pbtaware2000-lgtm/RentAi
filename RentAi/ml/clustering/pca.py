"""Principal component analysis using NumPy and shared feature preprocessing."""

import numpy as np
import pandas as pd

from ml.clustering.kmeans import scale_features


def explore_pca(
	data: pd.DataFrame,
	features: list[str],
) -> tuple[np.ndarray, np.ndarray]:
	"""Return two principal-component coordinates and their explained variance.

	The selected features are converted, median-imputed, and standardized by the
	same preprocessing used by the project's clustering methods. The returned
	variance values are percentages for PC1 and PC2, in that order.
	"""
	if len(features) < 2:
		raise ValueError("PCA needs at least two selected numeric features to produce PC1 and PC2.")
	if len(data) < 2:
		raise ValueError("PCA needs at least two property rows.")

	values = scale_features(data, features)
	covariance = values.T @ values / (len(values) - 1)
	eigenvalues, eigenvectors = np.linalg.eigh(covariance)
	order = np.argsort(eigenvalues)[::-1]
	eigenvalues = np.maximum(eigenvalues[order], 0.0)
	if not np.isfinite(eigenvalues).all() or float(eigenvalues.sum()) <= 0:
		raise ValueError("PCA cannot run because the selected features contain no variance.")

	components = eigenvectors[:, order[:2]]
	coordinates = values @ components
	variance_percentages = eigenvalues[:2] / eigenvalues.sum() * 100.0
	if coordinates.shape != (len(data), 2) or not np.isfinite(coordinates).all():
		raise ValueError("PCA could not produce finite two-component coordinates.")
	return coordinates, variance_percentages
