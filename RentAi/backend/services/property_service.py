from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = PROJECT_ROOT / "data" / "rentai_dataset.csv"


def load_properties() -> pd.DataFrame:
    """Load rental property records from the CSV dataset."""
    if not DATASET_PATH.exists():
        raise FileNotFoundError(
            f"Rental dataset not found: {DATASET_PATH}"
        )

    return pd.read_csv(DATASET_PATH)


def get_property_count() -> int:
    """Return the number of property records."""
    return len(load_properties())


def get_property_by_id(property_id: str) -> dict | None:
    """Return a single property record by its ID, or None if not found."""
    df = load_properties()

    property_row = df[df["property_id"] == property_id]

    if property_row.empty:
        return None

    return property_row.iloc[0].to_dict()


def get_properties_by_ids(property_ids: list[str]) -> tuple[list[dict], list[str]]:
    """Return properties in requested order and IDs that were not found."""
    df = load_properties()
    by_id = df.set_index("property_id", drop=False)
    properties = []
    missing_ids = []

    for property_id in property_ids:
        if property_id in by_id.index:
            properties.append(by_id.loc[property_id].to_dict())
        else:
            missing_ids.append(property_id)

    return properties, missing_ids


def get_all_properties(
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Return a paginated list of property records."""
    df = load_properties()

    page = df.iloc[offset:offset + limit]

    return page.to_dict(orient="records")


def _filter_properties(
    df: pd.DataFrame,
    city: str | None,
    locality: str | None,
    min_rent: float | None,
    max_rent: float | None,
    bedrooms: int | None,
    property_type: str | None,
) -> pd.DataFrame:
    for column, value in (("city", city), ("locality", locality), ("property_type", property_type)):
        if value:
            df = df[df[column].str.contains(value, case=False, na=False, regex=False)]
    if min_rent is not None:
        df = df[df["rent"] >= min_rent]
    if max_rent is not None:
        df = df[df["rent"] <= max_rent]
    if bedrooms is not None:
        df = df[df["bedrooms"] == bedrooms]
    return df


def count_matching_properties(
    city: str | None = None,
    locality: str | None = None,
    min_rent: float | None = None,
    max_rent: float | None = None,
    bedrooms: int | None = None,
    property_type: str | None = None,
) -> int:
    """Count records matching the supplied search filters."""
    return len(_filter_properties(load_properties(), city, locality, min_rent, max_rent, bedrooms, property_type))


def search_properties(
    city: str | None = None,
    locality: str | None = None,
    min_rent: float | None = None,
    max_rent: float | None = None,
    bedrooms: int | None = None,
    property_type: str | None = None,
    sort_by: str = "rent",
    order: str = "asc",
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Search and sort properties with pagination."""
    df = _filter_properties(load_properties(), city, locality, min_rent, max_rent, bedrooms, property_type)

    # Sort properties
    allowed_sort_columns = ["rent", "area_sqft", "bedrooms"]

    if sort_by not in allowed_sort_columns:
        raise ValueError(
            f"Invalid sort_by. Choose from: {allowed_sort_columns}"
        )

    if order.lower() not in ["asc", "desc"]:
        raise ValueError("order must be 'asc' or 'desc'")

    ascending = order.lower() == "asc"

    df = df.sort_values(
        by=sort_by,
        ascending=ascending,
        kind="stable",
    )

    # Apply pagination after filtering and sorting
    page = df.iloc[offset:offset + limit]

    return page.to_dict(orient="records")
