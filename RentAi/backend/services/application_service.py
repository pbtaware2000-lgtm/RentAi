"""Business rules for role-protected application records."""

from fastapi import HTTPException
import math
from datetime import date, datetime, timezone

from backend.auth.dependencies import CurrentUser
from backend.core.auth import (
    get_trusted_user_role,
    grant_role_to_user,
    register_user_role as sqlite_register_user_role,
)
from backend.repositories.sqlite_repository import SQLiteRepository


ROLE_OPERATIONS = {
    "owner": {"listing": {"create", "read", "update", "delete"}, "profile": {"create", "read", "update", "delete"}, "inquiry": {"read"}, "maintenance": {"create", "read", "update"}},
    "renter": {"listing": {"read"}, "profile": {"create", "read", "update", "delete"}, "favorite": {"create", "read", "update", "delete"}, "inquiry": {"create", "read", "delete"}, "maintenance": {"create", "read", "delete"}},
    "broker": {"profile": {"create", "read", "update", "delete"}, "listing": {"read"}, "inquiry": {"read", "update"}},
    "admin": {"*": {"create", "read", "update", "delete"}},
}


class ApplicationService:
    def __init__(self, repository: SQLiteRepository | None = None):
        self.repository = repository or SQLiteRepository()

    def register_user_role(self, id_token: str, role: str) -> dict:
        uid = sqlite_register_user_role(id_token=id_token, role=role)
        return {"uid": uid, "role": role}

    def list_records_for_user(self, user: CurrentUser, kind: str | None = None) -> list[dict]:
        records = self.repository.list_records()
        visible = []
        for record in records:
            if kind and record.get("kind") != kind:
                continue
            if user.role == "admin":
                visible.append(record)
                continue
            try:
                self._authorize_record_access(user, record, "read")
            except HTTPException:
                continue
            visible.append(record)
        return visible

    def list_listing_images_for_user(self, user: CurrentUser) -> list[dict]:
        visible_listing_ids = {
            record.get("id") for record in self.list_records_for_user(user, "listing")
        }
        if not visible_listing_ids:
            return []
        return [
            {key: image.get(key) for key in ("id", "record_id", "url", "format", "width", "height")}
            for image in self.repository.list_images()
            if image.get("record_id") in visible_listing_ids and isinstance(image.get("url"), str)
        ]

    def list_users(self, actor: CurrentUser) -> list[dict]:
        if actor.role != "admin":
            raise HTTPException(status_code=403, detail="Trusted Admin authorization is required")
        return self.repository.list_user_roles()

    def provision_user_role(
        self, actor: CurrentUser, actor_id_token: str, uid: str, role: str
    ) -> None:
        if actor.role != "admin":
            raise HTTPException(status_code=403, detail="Trusted Admin authorization is required")
        grant_role_to_user(actor_id_token=actor_id_token, uid=uid, role=role)

    def create_record(self, user: CurrentUser, kind: str, data: dict) -> dict:
        self._authorize_kind_operation(user, kind, "create")
        record_data = dict(data)
        if kind == "listing":
            record_data = self._validate_listing_data(record_data)
        if kind == "profile":
            record_data = self._validate_profile_data(record_data)
        if kind == "inquiry":
            listing_id = record_data.get("listing_id")
            listing = self.repository.get("application_records", listing_id) if listing_id else None
            if not listing or listing.get("kind") != "listing":
                raise HTTPException(status_code=400, detail="Inquiry must reference an existing listing")
            if user.role == "renter" and (listing.get("data") or {}).get("status") != "published":
                raise HTTPException(status_code=403, detail="Only published properties can receive enquiries")
            message = record_data.get("message", "")
            if not isinstance(message, str) or not message.strip() or len(message.strip()) > 2000:
                raise HTTPException(status_code=400, detail="Inquiry message is required (2,000 characters maximum)")
            client_name = record_data.get("client_name", "")
            phone = record_data.get("client_phone", "")
            if not isinstance(client_name, str) or not client_name.strip() or len(client_name.strip()) > 160:
                raise HTTPException(status_code=400, detail="A client name is required")
            if not isinstance(phone, str) or len(phone.strip()) > 40:
                raise HTTPException(status_code=400, detail="Client phone number is invalid")
            record_data = {
                "listing_id": listing_id,
                "message": message.strip(),
                "client_uid": user.uid,
                "client_name": client_name.strip(),
                "client_email": getattr(user, "claims", {}).get("email", ""),
                "client_phone": phone.strip(),
                "status": "new",
                "property_owner_uid": listing.get("owner_uid"),
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        if kind == "maintenance":
            listing_id = record_data.get("listing_id")
            listing = self.repository.get("application_records", listing_id) if listing_id else None
            if not listing or listing.get("kind") != "listing":
                raise HTTPException(status_code=400, detail="Maintenance request must reference a property")
            if user.role == "owner" and listing.get("owner_uid") != user.uid:
                raise HTTPException(status_code=403, detail="You can only log maintenance for your properties")
            if user.role == "renter" and (listing.get("data") or {}).get("status") != "published":
                raise HTTPException(status_code=403, detail="Only published properties can receive requests")
            title, description = record_data.get("title", ""), record_data.get("description", "")
            if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
                raise HTTPException(status_code=400, detail="A maintenance title is required")
            if not isinstance(description, str) or not description.strip() or len(description.strip()) > 2000:
                raise HTTPException(status_code=400, detail="A maintenance description is required (2,000 characters maximum)")
            due_date = None
            if user.role == "owner" and record_data.get("due_date"):
                try:
                    due_date = date.fromisoformat(record_data["due_date"]).isoformat()
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="Maintenance due date must use YYYY-MM-DD") from None
            record_data = {
                "listing_id": listing_id, "title": title.strip(),
                "description": description.strip(), "status": "open",
                "property_owner_uid": listing.get("owner_uid"),
                "reported_by_uid": user.uid,
                "reported_by_role": user.role,
            }
            if due_date:
                record_data["due_date"] = due_date
        if kind == "favorite":
            property_id = record_data.get("property_id")
            if isinstance(property_id, bool) or not isinstance(property_id, (str, int)) or not str(property_id).strip() or len(str(property_id)) > 160:
                raise HTTPException(status_code=400, detail="Favorite must reference a property")
            property_id = str(property_id).strip()
            source = record_data.get("source", "catalog")
            if source == "catalog":
                from backend.services.property_service import get_property_by_id

                prop = get_property_by_id(property_id)
                if prop is None:
                    raise HTTPException(status_code=400, detail="Catalog property does not exist")
            elif source == "owner_listing":
                listing = self.repository.get("application_records", property_id)
                if not listing or listing.get("kind") != "listing" or (listing.get("data") or {}).get("status") != "published":
                    raise HTTPException(status_code=400, detail="Only a published Owner listing can be saved")
                prop = {"id": property_id, **(listing.get("data") or {})}
                image_records = getattr(self.repository, "list_images", lambda: [])()
                image = next((item for item in image_records if item.get("record_id") == property_id and item.get("url")), None)
                if image:
                    prop["image_url"] = image["url"]
            else:
                raise HTTPException(status_code=400, detail="Unsupported saved-property source")
            allowed_fields = {
                "id", "property_id", "title", "city", "locality", "property_type", "rent",
                "bedrooms", "bathrooms", "area_sqft", "furnishing_status", "parking",
                "security_deposit", "maintenance", "available_from", "posted_date", "image_url",
                "image", "photo_url", "description", "status",
            }
            snapshot = {key: value for key, value in prop.items() if key in allowed_fields}
            record_data = {"property_id": property_id, "source": source, "property": snapshot}
        record = {"owner_uid": user.uid, "kind": kind, "data": data}
        record["data"] = record_data
        record_id, _ = self.repository.create("application_records", record)
        return {"id": record_id, **record}

    def get_record(self, user: CurrentUser, record_id: str) -> dict:
        record = self.repository.get("application_records", record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Record not found")
        self._authorize_record_access(user, record, "read")
        return record

    def update_record(self, user: CurrentUser, record_id: str, data: dict) -> dict:
        record = self.repository.get("application_records", record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Record not found")
        self._authorize_record_access(user, record, "update")
        safe_data = dict(data)
        if record.get("kind") == "listing":
            safe_data = self._validate_listing_data(safe_data)
        elif record.get("kind") == "profile":
            safe_data = self._validate_profile_data(safe_data)
        elif record.get("kind") == "inquiry" and user.role == "broker":
            status = safe_data.get("status")
            note = safe_data.get("broker_note", "")
            if status not in {"new", "contacted", "viewing_scheduled", "completed", "closed"}:
                raise HTTPException(status_code=400, detail="Unsupported enquiry status")
            if not isinstance(note, str) or len(note) > 2000:
                raise HTTPException(status_code=400, detail="Broker note must be 2,000 characters or fewer")
            safe_data = {**(record.get("data") or {}), "status": status, "broker_note": note.strip()}
        elif record.get("kind") == "maintenance" and user.role == "owner":
            status = safe_data.get("status")
            note = safe_data.get("owner_note", "")
            if status not in {"open", "in_progress", "completed", "cancelled"}:
                raise HTTPException(status_code=400, detail="Unsupported maintenance status")
            if not isinstance(note, str) or len(note) > 2000:
                raise HTTPException(status_code=400, detail="Maintenance note must be 2,000 characters or fewer")
            safe_data = {**(record.get("data") or {}), "status": status, "owner_note": note.strip()}
        if not self.repository.update("application_records", record_id, {"data": safe_data}):
            raise HTTPException(status_code=404, detail="Record not found")
        return {**record, "data": safe_data}

    def delete_record(self, user: CurrentUser, record_id: str) -> None:
        record = self.repository.get("application_records", record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Record not found")
        self._authorize_record_access(user, record, "delete")
        if not self.repository.delete("application_records", record_id):
            raise HTTPException(status_code=404, detail="Record not found")

    def assign_broker(self, actor: CurrentUser, record_id: str, broker_uid: str) -> dict:
        if actor.role != "admin":
            raise HTTPException(status_code=403, detail="Trusted Admin authorization is required")
        if get_trusted_user_role(broker_uid) != "broker":
            raise HTTPException(status_code=400, detail="Target account is not a verified Broker")
        updated = self.repository.assign_broker(record_id, broker_uid)
        if updated is None:
            raise HTTPException(status_code=404, detail="Record not found")
        record = self.repository.get("application_records", record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Record not found")
        return {**record, "assigned_broker_uids": updated}

    @staticmethod
    def _validate_listing_data(data: dict) -> dict:
        title, city, locality = (data.get(key) for key in ("title", "city", "locality"))
        if any(not isinstance(value, str) or not value.strip() or len(value.strip()) > 160 for value in (title, city, locality)):
            raise HTTPException(status_code=400, detail="Listing title, city, and locality are required (160 characters maximum)")
        rent = data.get("rent")
        if isinstance(rent, bool) or not isinstance(rent, (int, float)) or not math.isfinite(float(rent)) or rent <= 0:
            raise HTTPException(status_code=400, detail="Rent must be a positive number")
        bedrooms = data.get("bedrooms", 1)
        if isinstance(bedrooms, bool) or not isinstance(bedrooms, int) or not 1 <= bedrooms <= 30:
            raise HTTPException(status_code=400, detail="Bedrooms must be between 1 and 30")
        status = data.get("status", "draft")
        if status not in {"draft", "published"}:
            raise HTTPException(status_code=400, detail="Listing status must be draft or published")
        description = data.get("description", "")
        if not isinstance(description, str) or len(description) > 5000:
            raise HTTPException(status_code=400, detail="Listing description must be 5,000 characters or fewer")
        property_type = data.get("property_type", "Other")
        if not isinstance(property_type, str) or not property_type.strip() or len(property_type) > 80:
            raise HTTPException(status_code=400, detail="Property type is invalid")
        occupancy_status = data.get("occupancy_status", "not_tracked")
        if occupancy_status not in {"vacant", "occupied", "not_tracked"}:
            raise HTTPException(status_code=400, detail="Occupancy status is invalid")
        return {
            "title": title.strip(), "city": city.strip(), "locality": locality.strip(),
            "rent": float(rent), "bedrooms": bedrooms, "property_type": property_type.strip(),
            "description": description.strip(), "status": status,
            "occupancy_status": occupancy_status,
        }

    @staticmethod
    def _validate_profile_data(data: dict) -> dict:
        record_type = data.get("record_type")
        if record_type == "owner_profile":
            name = data.get("name")
            phone = data.get("phone", "")
            business_name = data.get("business_name", "")
            bio = data.get("bio", "")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
                raise HTTPException(status_code=400, detail="Profile name is required")
            if not isinstance(phone, str) or len(phone) > 40:
                raise HTTPException(status_code=400, detail="Profile phone number is invalid")
            if not isinstance(business_name, str) or len(business_name) > 120:
                raise HTTPException(status_code=400, detail="Business name is too long")
            if not isinstance(bio, str) or len(bio) > 1000:
                raise HTTPException(status_code=400, detail="Profile description is too long")
            return {"record_type": record_type, "name": name.strip(), "phone": phone.strip(), "business_name": business_name.strip(), "bio": bio.strip()}
        if record_type == "renter_profile":
            name = data.get("name")
            phone = data.get("phone", "")
            preferred_city = data.get("preferred_city", "")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
                raise HTTPException(status_code=400, detail="Profile name is required")
            if not isinstance(phone, str) or len(phone) > 40:
                raise HTTPException(status_code=400, detail="Profile phone number is invalid")
            if not isinstance(preferred_city, str) or len(preferred_city) > 120:
                raise HTTPException(status_code=400, detail="Preferred city is too long")
            return {"record_type": record_type, "name": name.strip(), "phone": phone.strip(), "preferred_city": preferred_city.strip()}
        if record_type == "renter_settings":
            sort_by = data.get("sort_by", "rent")
            sort_order = data.get("sort_order", "asc")
            if sort_by not in {"rent", "area_sqft", "bedrooms"}:
                raise HTTPException(status_code=400, detail="Unsupported default property sort")
            if sort_order not in {"asc", "desc"}:
                raise HTTPException(status_code=400, detail="Unsupported sort order")
            return {"record_type": record_type, "sort_by": sort_by, "sort_order": sort_order}
        if record_type == "owner_settings":
            email_alerts = data.get("email_alerts", True)
            maintenance_alerts = data.get("maintenance_alerts", True)
            frequency = data.get("summary_frequency", "weekly")
            if not isinstance(email_alerts, bool) or not isinstance(maintenance_alerts, bool):
                raise HTTPException(status_code=400, detail="Notification preferences must be boolean")
            if frequency not in {"weekly", "monthly", "off"}:
                raise HTTPException(status_code=400, detail="Summary frequency is invalid")
            return {"record_type": record_type, "email_alerts": email_alerts, "maintenance_alerts": maintenance_alerts, "summary_frequency": frequency}
        return data

    @staticmethod
    def _authorize_kind_operation(user: CurrentUser, kind: str, operation: str) -> None:
        if kind not in {"listing", "profile", "favorite", "inquiry", "maintenance"}:
            raise HTTPException(status_code=400, detail="Unsupported application record kind")
        if user.role == "admin":
            return
        operations = ROLE_OPERATIONS.get(user.role, {}).get(kind, set())
        if operation not in operations:
            raise HTTPException(status_code=403, detail="Role cannot perform this operation on this record kind")

    @classmethod
    def _authorize_record_access(cls, user: CurrentUser, record: dict, operation: str) -> None:
        if user.role == "admin":
            return
        kind = record.get("kind")
        cls._authorize_kind_operation(user, kind, operation)
        data = record.get("data") or {}
        assigned = user.uid in record.get("assigned_broker_uids", [])
        if user.role == "broker" and kind in {"listing", "inquiry"}:
            if operation not in ({"read", "update"} if kind == "inquiry" else {"read"}) or not assigned:
                raise HTTPException(status_code=403, detail="Broker is not assigned to this record")
            return
        if user.role == "renter" and kind == "listing":
            if operation == "read" and data.get("status") == "published":
                return
            raise HTTPException(status_code=403, detail="Only published listings are visible to renters")
        owns_record = record.get("owner_uid") == user.uid
        if kind in {"inquiry", "maintenance"} and user.role == "owner":
            owns_record = data.get("property_owner_uid") == user.uid
        if not owns_record:
            raise HTTPException(status_code=403, detail="Record belongs to another user")
