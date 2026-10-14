"""Small domain types shared by application services and repositories."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ApplicationRecord:
    id: str
    owner_uid: str
    kind: str
    data: dict[str, Any]


@dataclass(frozen=True)
class ImageMetadata:
    url: str
    public_id: str
    format: str | None = None
    width: int | None = None
    height: int | None = None
    bytes: int | None = None
