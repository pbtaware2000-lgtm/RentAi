"""DBSCAN clustering using the project's NumPy and Pandas preprocessing."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ml.clustering.kmeans import scale_features


def _radius_neighbors(values: np.ndarray, row: int, radius_squared: float) -> np.ndarray:
	"""Return exact Euclidean radius neighbors for one row."""
	differences = values - values[row]
	distances_squared = np.einsum("ij,ij->i", differences, differences)
	return np.flatnonzero(distances_squared <= radius_squared)


def fit_dbscan(
	values: np.ndarray,
	eps: float = 0.8,
	min_samples: int = 10,
) -> np.ndarray:
	"""Fit DBSCAN with exact Euclidean neighborhoods and return labels (-1 is noise).

	Neighborhoods are queried on demand, so memory use is linear in the input size.
	This keeps the implementation suitable for the project's 10,000-row dataset
	without requiring SciPy or scikit-learn.
	"""
	values = np.asarray(values, dtype=float)
	if values.ndim != 2 or values.shape[0] == 0 or values.shape[1] == 0:
		raise ValueError("DBSCAN requires a non-empty two-dimensional feature matrix.")
	if not np.isfinite(values).all():
		raise ValueError("DBSCAN features must contain only finite values.")
	if not isinstance(eps, (int, float, np.integer, np.floating)) or not math.isfinite(float(eps)) or eps <= 0:
		raise ValueError("eps must be a finite number greater than zero.")
	if not isinstance(min_samples, (int, np.integer)) or isinstance(min_samples, bool) or min_samples < 1:
		raise ValueError("min_samples must be a positive integer.")

	radius_squared = float(eps) ** 2
	row_count = len(values)
	neighbor_counts = np.zeros(row_count, dtype=np.int32)
	# Count neighborhoods in bounded blocks; this avoids an N x N allocation.
	block_size = 128
	norms = np.einsum("ij,ij->i", values, values)
	for start in range(0, row_count, block_size):
		stop = min(start + block_size, row_count)
		distances_squared = norms[start:stop, None] + norms[None, :] - 2.0 * values[start:stop] @ values.T
		np.maximum(distances_squared, 0.0, out=distances_squared)
		neighbor_counts[start:stop] = np.count_nonzero(distances_squared <= radius_squared, axis=1)

	core_points = neighbor_counts >= int(min_samples)
	labels = np.full(row_count, -1, dtype=np.int32)
	visited = np.zeros(row_count, dtype=bool)
	cluster_id = 0

	for point in range(row_count):
		if visited[point]:
			continue
		visited[point] = True
		if not core_points[point]:
			continue

		labels[point] = cluster_id
		neighbors = _radius_neighbors(values, point, radius_squared)
		queue = list(neighbors[neighbors != point])
		queued = set(queue)
		cursor = 0
		while cursor < len(queue):
			neighbor = queue[cursor]
			cursor += 1
			if not visited[neighbor]:
				visited[neighbor] = True
				if core_points[neighbor]:
					neighbor_rows = _radius_neighbors(values, neighbor, radius_squared)
					for candidate in neighbor_rows:
						candidate = int(candidate)
						if candidate not in queued:
							queued.add(candidate)
							queue.append(candidate)
			if labels[neighbor] == -1:
				labels[neighbor] = cluster_id
		cluster_id += 1

	return labels


def cluster_properties(
	data: pd.DataFrame,
	features: list[str],
	eps: float = 0.8,
	min_samples: int = 10,
) -> tuple[pd.DataFrame, np.ndarray]:
	"""Scale selected features, run DBSCAN, and return labels plus scaled values."""
	scaled = scale_features(data, features)
	labels = fit_dbscan(scaled, eps=eps, min_samples=min_samples)
	assignments = data.copy()
	assignments["cluster"] = labels
	return assignments, scaled
