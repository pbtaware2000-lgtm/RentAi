"""Feature scaling and K-Means clustering implemented with NumPy and Pandas."""

import numpy as np
import pandas as pd


def scale_features(data: pd.DataFrame, features: list[str]) -> np.ndarray:
    """Convert selected numeric columns to finite, standardized values."""
    if not features:
        raise ValueError("Select at least one numeric feature.")
    if len(set(features)) != len(features):
        raise ValueError("Feature selections must be unique.")

    missing = [name for name in features if name not in data.columns]
    if missing:
        raise ValueError(f"Selected features are not in the dataset: {missing}")

    numeric = data.loc[:, features].apply(pd.to_numeric, errors="coerce")
    numeric = numeric.replace([np.inf, -np.inf], np.nan)
    all_missing = numeric.columns[numeric.isna().all()].tolist()
    if all_missing:
        raise ValueError(f"Selected features contain no usable numeric values: {all_missing}")

    numeric = numeric.fillna(numeric.median())
    values = numeric.to_numpy(dtype=float)
    means = values.mean(axis=0)
    scales = values.std(axis=0)
    scales[scales == 0] = 1.0
    scaled = (values - means) / scales
    if not np.isfinite(scaled).all():
        raise ValueError("Selected features could not be scaled to finite values.")
    return scaled


def _squared_distances(values: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    return np.square(values[:, None, :] - centroids[None, :, :]).sum(axis=2)


def _initialize_centroids(values: np.ndarray, cluster_count: int, random_state: int) -> np.ndarray:
    """Choose starting centers with the K-Means++ initialization method."""
    random = np.random.default_rng(random_state)
    chosen = [int(random.integers(len(values)))]
    nearest_distances = np.square(values - values[chosen[0]]).sum(axis=1)

    while len(chosen) < cluster_count:
        weights = nearest_distances.copy()
        weights[chosen] = 0.0
        total = weights.sum()
        if total <= 0:
            remaining = np.setdiff1d(np.arange(len(values)), chosen, assume_unique=False)
            next_index = int(random.choice(remaining))
        else:
            next_index = int(random.choice(len(values), p=weights / total))
        chosen.append(next_index)
        distances = np.square(values - values[next_index]).sum(axis=1)
        nearest_distances = np.minimum(nearest_distances, distances)

    return values[chosen].copy()


def _fit_kmeans_once(
    values: np.ndarray,
    cluster_count: int,
    random_state: int = 42,
    max_iterations: int = 100,
) -> tuple[np.ndarray, np.ndarray, float]:
    centroids = _initialize_centroids(values, cluster_count, random_state)
    labels = np.full(len(values), -1, dtype=int)

    for _ in range(max_iterations):
        distances = _squared_distances(values, centroids)
        updated_labels = distances.argmin(axis=1)
        updated_centroids = centroids.copy()
        nearest_distances = distances[np.arange(len(values)), updated_labels]
        empty_clusters = []

        for cluster in range(cluster_count):
            members = values[updated_labels == cluster]
            if len(members):
                updated_centroids[cluster] = members.mean(axis=0)
            else:
                empty_clusters.append(cluster)

        if empty_clusters:
            farthest_rows = np.argsort(nearest_distances)[::-1]
            used_rows: set[int] = set()
            for cluster in empty_clusters:
                row = next(int(index) for index in farthest_rows if int(index) not in used_rows)
                updated_centroids[cluster] = values[row]
                used_rows.add(row)

        converged = np.array_equal(updated_labels, labels) and not empty_clusters
        labels = updated_labels
        centroids = updated_centroids
        if converged:
            break

    labels = _squared_distances(values, centroids).argmin(axis=1)
    distances = _squared_distances(values, centroids)
    inertia = float(distances[np.arange(len(values)), labels].sum())
    return labels, centroids, inertia


def fit_kmeans(
    values: np.ndarray,
    cluster_count: int,
    random_state: int = 42,
    max_iterations: int = 100,
    n_init: int = 10,
) -> tuple[np.ndarray, np.ndarray]:
    """Fit K-Means with multiple starts and keep the lowest-inertia result."""
    values = np.asarray(values, dtype=float)
    if values.ndim != 2 or values.shape[1] == 0:
        raise ValueError("Clustering requires a two-dimensional feature matrix.")
    if len(values) < 2 or not 2 <= cluster_count <= len(values):
        raise ValueError("The number of clusters must be between 2 and the number of rows.")
    if not np.isfinite(values).all():
        raise ValueError("Clustering features must contain only finite values.")
    if len(np.unique(values, axis=0)) < cluster_count:
        raise ValueError("There are fewer distinct feature rows than the selected cluster count.")
    if n_init < 1:
        raise ValueError("K-Means must use at least one initialization.")

    seeds = np.random.SeedSequence(random_state).generate_state(n_init)
    best_result = None
    for seed in seeds:
        result = _fit_kmeans_once(values, cluster_count, int(seed), max_iterations)
        if best_result is None or result[2] < best_result[2]:
            best_result = result

    labels, centroids, _ = best_result
    if len(np.unique(labels)) != cluster_count:
        raise ValueError("K-Means could not populate every selected cluster. Try different features or fewer clusters.")
    return labels, centroids


def cluster_properties(
    data: pd.DataFrame,
    features: list[str],
    cluster_count: int,
    random_state: int = 42,
) -> tuple[pd.DataFrame, np.ndarray]:
    """Scale selected features, run K-Means, and return assignments."""
    scaled = scale_features(data, features)
    labels, _ = fit_kmeans(scaled, cluster_count, random_state=random_state)
    assignments = data.copy()
    assignments["cluster"] = labels
    return assignments, scaled
