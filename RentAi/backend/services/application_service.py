"""Business rules for role-protected application records."""

from fastapi import HTTPException
import math
import re
from datetime import date, datetime, timezone
import uuid

from backend.auth.dependencies import CurrentUser
from backend.core.auth import (
    get_trusted_user_role,
    grant_role_to_user,
    register_user_role as sqlite_register_user_role,
)
from backend.repositories.sqlite_repository import DuplicateBusinessRecordError, SQLiteRepository


ROLE_OPERATIONS = {
    "owner": {"listing": {"create", "read", "update", "delete"}, "profile": {"create", "read", "update", "delete"}, "inquiry": {"read", "update"}, "maintenance": {"create", "read", "update"}, "tenancy": {"create", "read", "update"}, "rent_payment": {"create", "read", "update"}},
    "renter": {"listing": {"read"}, "profile": {"create", "read", "update", "delete"}, "favorite": {"create", "read", "update", "delete"}, "inquiry": {"create", "read", "delete"}, "maintenance": {"create", "read", "delete"}, "rent_payment": {"read"}},
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
        return self._images_for_records(visible_listing_ids)

    def list_profile_images_for_user(self, user: CurrentUser) -> list[dict]:
        visible_profile_ids = {
            record.get("id")
            for record in self.list_records_for_user(user, "profile")
            if (record.get("data") or {}).get("record_type") == "owner_profile"
        }
        return self._images_for_records(visible_profile_ids)

    def _images_for_records(self, record_ids: set[str]) -> list[dict]:
        if not record_ids:
            return []
        return [
            {key: image.get(key) for key in ("id", "record_id", "url", "format", "width", "height")}
            for image in self.repository.list_images()
            if image.get("record_id") in record_ids and isinstance(image.get("url"), str)
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
            if record_data.get("record_type") == "owner_settings" and user.role != "owner":
                raise HTTPException(status_code=403, detail="Only an Owner can save Owner notification preferences")
            record_data = self._validate_profile_data(record_data)
        if kind == "tenancy":
            record_data = self._validate_tenancy_data(user, record_data)
        if kind == "rent_payment":
            record_data = self._validate_rent_payment_data(user, record_data)
        if kind == "inquiry":
            listing_id = record_data.get("listing_id")
            listing = self.repository.get("application_records", listing_id) if listing_id else None
            if not listing or listing.get("kind") != "listing":
                raise HTTPException(status_code=400, detail="Inquiry must reference an existing listing")
            if user.role == "renter" and (listing.get("data") or {}).get("status") != "published":
                raise HTTPException(status_code=403, detail="Only published properties can receive enquiries")
            message = record_data.get("message", "")
            if not isinstance(message, str) or len(message.strip()) > 2000:
                raise HTTPException(status_code=400, detail="Inquiry message must be 2,000 characters or fewer")
            client_name = record_data.get("client_name", "")
            phone = record_data.get("client_phone", "")
            if not isinstance(client_name, str) or not client_name.strip() or len(client_name.strip()) > 160:
                raise HTTPException(status_code=400, detail="A client name is required")
            if not isinstance(phone, str) or len(phone.strip()) > 40:
                raise HTTPException(status_code=400, detail="Client phone number is invalid")
            if user.role == "renter":
                list_records = getattr(self.repository, "list_records", None)
                existing_records = list_records() if list_records else []
                active_statuses = {"new", "pending", "contacted", "viewing_scheduled", "accepted"}
                duplicate = any(
                    item.get("kind") == "inquiry"
                    and (item.get("data") or {}).get("client_uid") == user.uid
                    and (item.get("data") or {}).get("listing_id") == listing_id
                    and (item.get("data") or {}).get("status", "new") in active_statuses
                    for item in existing_records
                )
                if duplicate:
                    raise HTTPException(status_code=409, detail="You already have an active enquiry for this property.")
            listing_data = listing.get("data") or {}
            record_data = {
                "listing_id": listing_id,
                "message": message.strip(),
                "client_uid": user.uid,
                "client_name": client_name.strip(),
                "client_email": getattr(user, "claims", {}).get("email", ""),
                "client_phone": phone.strip(),
                "status": "pending",
                "property_owner_uid": listing.get("owner_uid"),
                "property": {
                    key: listing_data.get(key)
                    for key in ("title", "locality", "city", "property_type", "rent")
                    if listing_data.get(key) is not None
                },
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
            # The source is required so legacy or malformed clients cannot silently
            # turn an ambiguous property reference into a dataset bookmark.
            source = record_data.get("source")
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
                "image", "photo_url", "image_path", "description", "status",
            }
            snapshot = {key: value for key, value in prop.items() if key in allowed_fields}
            record_data = {"property_id": property_id, "source": source, "property": snapshot}
        record = {"owner_uid": user.uid, "kind": kind, "data": data}
        record["data"] = record_data
        notification = None
        if kind == "inquiry":
            owner_uid = record_data.get("property_owner_uid")
            if owner_uid:
                notification = {
                    "event_type": "new_enquiry", "owner_uid": owner_uid,
                    "payload": dict(record_data),
                }
        try:
            atomic_create = getattr(self.repository, "create_with_notification", None)
            if kind == "inquiry" and atomic_create:
                record_id, _ = atomic_create(
                    record, notification, prevent_duplicate_enquiry=user.role == "renter",
                )
            else:
                record_id, _ = self.repository.create("application_records", record)
        except DuplicateBusinessRecordError:
            if kind == "inquiry":
                message = "You already have an active enquiry for this property."
            else:
                message = "This accepted enquiry already has an active tenancy" if kind == "tenancy" else "A rent record already exists for this tenancy and period"
            raise HTTPException(status_code=409, detail=message) from None
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
        if record.get("kind") == "profile" and (
            (record.get("data") or {}).get("record_type") == "owner_settings"
            or safe_data.get("record_type") == "owner_settings"
        ) and user.role != "owner":
            raise HTTPException(status_code=403, detail="Only an Owner can update Owner notification preferences")
        maintenance_notification = None
        if record.get("kind") == "listing":
            previous_data = record.get("data") or {}
            if (
                safe_data.get("occupancy_status") == "vacant"
                and previous_data.get("occupancy_status") != "vacant"
                and any(
                    item.get("kind") == "tenancy"
                    and item.get("owner_uid") == user.uid
                    and (item.get("data") or {}).get("listing_id") == record_id
                    and (item.get("data") or {}).get("status") == "active"
                    for item in self.repository.list_records()
                )
            ):
                raise HTTPException(status_code=409, detail="A property with an active tenancy cannot be marked vacant")
            safe_data = self._validate_listing_data(safe_data)
        elif record.get("kind") == "profile":
            safe_data = self._validate_profile_data(safe_data)
        elif record.get("kind") == "inquiry" and user.role == "owner":
            status = safe_data.get("status")
            if status not in {"pending", "accepted", "rejected"}:
                raise HTTPException(status_code=400, detail="Enquiry status must be pending, accepted, or rejected")
            safe_data = {**(record.get("data") or {}), "status": status}
        elif record.get("kind") == "inquiry" and user.role == "broker":
            status = safe_data.get("status")
            note = safe_data.get("broker_note", "")
            if status not in {"new", "contacted", "viewing_scheduled", "completed", "closed"}:
                raise HTTPException(status_code=400, detail="Unsupported enquiry status")
            if not isinstance(note, str) or len(note) > 2000:
                raise HTTPException(status_code=400, detail="Broker note must be 2,000 characters or fewer")
            safe_data = {**(record.get("data") or {}), "status": status, "broker_note": note.strip()}
        elif record.get("kind") == "maintenance" and user.role == "owner":
            old_data = record.get("data") or {}
            status = safe_data.get("status")
            note = safe_data.get("owner_note", "")
            if status not in {"open", "in_progress", "completed", "cancelled"}:
                raise HTTPException(status_code=400, detail="Unsupported maintenance status")
            if not isinstance(note, str) or len(note) > 2000:
                raise HTTPException(status_code=400, detail="Maintenance note must be 2,000 characters or fewer")
            safe_data = {**old_data, "status": status, "owner_note": note.strip()}
            previous_status = old_data.get("status", "open")
            listing = self.repository.get("application_records", old_data.get("listing_id"))
            actual_owner_uid = (listing or {}).get("owner_uid")
            if previous_status != status and actual_owner_uid:
                listing_data = (listing or {}).get("data") or {}
                maintenance_notification = {
                    "event_key": f"maintenance_status:{record_id}:{uuid.uuid4().hex}",
                    "event_type": "maintenance_status_changed",
                    "owner_uid": actual_owner_uid,
                    "payload": {
                        "title": old_data.get("title", ""),
                        "description": old_data.get("description", ""),
                        "property_title": listing_data.get("title", "Property"),
                        "previous_status": previous_status,
                        "new_status": status,
                        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    },
                }
        elif record.get("kind") == "rent_payment" and user.role == "owner":
            old_data = record.get("data") or {}
            if isinstance(old_data.get("razorpay_order"), dict):
                raise HTTPException(status_code=409, detail="This rent record has a Razorpay order and can only be marked Paid after server verification")
            if old_data.get("status") != "pending" or safe_data.get("status") != "paid":
                raise HTTPException(status_code=400, detail="Only a pending rent record can be marked paid")
            amount = safe_data.get("paid_amount")
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(float(amount)) or float(amount) != float(old_data.get("amount", 0)):
                raise HTTPException(status_code=400, detail="Paid amount must match the full rent due")
            try:
                paid_date = date.fromisoformat(safe_data.get("paid_date", ""))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Payment date must use YYYY-MM-DD") from None
            if paid_date > date.today():
                raise HTTPException(status_code=400, detail="Payment date cannot be in the future")
            method = safe_data.get("payment_method")
            if method not in {"UPI", "Bank transfer", "Cash", "Cheque", "Other"}:
                raise HTTPException(status_code=400, detail="Choose a valid payment method")
            reference = safe_data.get("payment_reference", "")
            if not isinstance(reference, str) or len(reference.strip()) > 120:
                raise HTTPException(status_code=400, detail="Payment reference must be 120 characters or fewer")
            safe_data = {
                **old_data, "status": "paid", "paid_amount": float(amount),
                "paid_date": paid_date.isoformat(), "payment_method": method,
                "payment_reference": reference.strip(),
            }
        elif record.get("kind") == "tenancy" and user.role == "owner":
            old_data = record.get("data") or {}
            if old_data.get("status") != "active" or safe_data.get("status") != "ended":
                raise HTTPException(status_code=400, detail="Only an active tenancy can be ended")
            try:
                end_date = date.fromisoformat(safe_data.get("end_date", ""))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Tenancy end date must use YYYY-MM-DD") from None
            if end_date < date.fromisoformat(old_data["start_date"]) or end_date > date.today():
                raise HTTPException(status_code=400, detail="Tenancy end date must be between its start date and today")
            safe_data = {**old_data, "status": "ended", "end_date": end_date.isoformat()}
        if record.get("kind") == "maintenance" and user.role == "owner":
            atomic_update = getattr(self.repository, "update_maintenance_with_notification", None)
            if atomic_update:
                outcome, updated_record = atomic_update(
                    record_id,
                    (record.get("data") or {}).get("status", "open"),
                    safe_data,
                    maintenance_notification,
                )
                if outcome == "missing":
                    raise HTTPException(status_code=404, detail="Record not found")
                if outcome == "conflict":
                    raise HTTPException(status_code=409, detail="Maintenance request changed concurrently. Refresh and try again.")
                return updated_record
        if not self.repository.update("application_records", record_id, {"data": safe_data}):
            raise HTTPException(status_code=404, detail="Record not found")
        return {**record, "data": safe_data}

    def delete_record(self, user: CurrentUser, record_id: str) -> None:
        record = self.repository.get("application_records", record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Record not found")
        self._authorize_record_access(user, record, "delete")
        if record.get("kind") == "listing":
            from backend.repositories.sqlite_repository import connect
            with connect() as db:
                has_booking_history = db.execute("SELECT 1 FROM bookings WHERE property_id=? LIMIT 1", (record_id,)).fetchone()
            if has_booking_history:
                raise HTTPException(status_code=409, detail="This listing has booking history and cannot be deleted. Unpublish it instead.")
            delete_listing = getattr(self.repository, "delete_listing_if_unreferenced", None)
            if delete_listing:
                result = delete_listing(record_id)
                if result == "linked":
                    raise HTTPException(
                        status_code=409,
                        detail="This property has linked records or images. Unlist it instead of deleting it.",
                    )
                if result == "missing":
                    raise HTTPException(status_code=404, detail="Record not found")
                return
            if self._listing_has_references(record_id):
                raise HTTPException(
                    status_code=409,
                    detail="This property has linked records or images. Unlist it instead of deleting it.",
                )
        if not self.repository.delete("application_records", record_id):
            raise HTTPException(status_code=404, detail="Record not found")

    def _listing_has_references(self, listing_id: str) -> bool:
        for related in self.repository.list_records():
            data = related.get("data") or {}
            if related.get("kind") in {"inquiry", "maintenance", "tenancy", "rent_payment"} and data.get("listing_id") == listing_id:
                return True
            if (
                related.get("kind") == "favorite"
                and data.get("source") == "owner_listing"
                and data.get("property_id") == listing_id
            ):
                return True
        list_images = getattr(self.repository, "list_images", None)
        return bool(list_images and any(
            image.get("record_id") == listing_id for image in list_images()
        ))

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
        if status not in {"draft", "published", "unlisted"}:
            raise HTTPException(status_code=400, detail="Listing status must be draft, published, or unlisted")
        description = data.get("description", "")
        if not isinstance(description, str) or len(description) > 5000:
            raise HTTPException(status_code=400, detail="Listing description must be 5,000 characters or fewer")
        property_type = data.get("property_type", "Other")
        if not isinstance(property_type, str) or not property_type.strip() or len(property_type) > 80:
            raise HTTPException(status_code=400, detail="Property type is invalid")
        occupancy_status = data.get("occupancy_status", "not_tracked")
        if occupancy_status not in {"vacant", "occupied", "not_tracked"}:
            raise HTTPException(status_code=400, detail="Occupancy status is invalid")
        result = {
            "title": title.strip(), "city": city.strip(), "locality": locality.strip(),
            "rent": float(rent), "bedrooms": bedrooms, "property_type": property_type.strip(),
            "description": description.strip(), "status": status,
            "occupancy_status": occupancy_status,
          }
        validated = {
            "title": title.strip(), "city": city.strip(), "locality": locality.strip(),
            "rent": float(rent), "bedrooms": bedrooms, "property_type": property_type.strip(),
            "description": description.strip(), "status": status,
            "occupancy_status": occupancy_status,
        }
        image_path = data.get("image_path")
        if image_path is not None:
            if not isinstance(image_path, str) or not re.fullmatch(
                r"assets/property_images/owner_uploads/[0-9a-f]{32}\.jpg", image_path
            ):
                raise HTTPException(status_code=400, detail="Listing image path is invalid")
            result["image_path"] = image_path
        if "security_deposit" in data:
            deposit = data.get("security_deposit")
            if isinstance(deposit, bool) or not isinstance(deposit, (int, float)) or not math.isfinite(float(deposit)) or deposit < 0:
                raise HTTPException(status_code=400, detail="Security deposit must be a non-negative amount")
            result["security_deposit"] = float(deposit)
        return result
        integer_fields = {
            "bathrooms": 30, "floor": 500, "age_of_property": 1000, "parking": 100,
        }
        for field, maximum in integer_fields.items():
            if field not in data:
                continue
            value = data[field]
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
                raise HTTPException(status_code=400, detail=f"{field.replace('_', ' ').title()} must be between 0 and {maximum}")
            validated[field] = value
        for field in ("area_sqft", "distance_to_city_center_km"):
            if field not in data:
                continue
            value = data[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
                raise HTTPException(status_code=400, detail=f"{field.replace('_', ' ').title()} must be a finite non-negative number")
            validated[field] = float(value)
        furnishing = data.get("furnishing_status")
        if furnishing is not None:
            if not isinstance(furnishing, str) or len(furnishing.strip()) > 80:
                raise HTTPException(status_code=400, detail="Furnishing status is invalid")
            validated["furnishing_status"] = furnishing.strip()
        return validated

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
            property_type = data.get("preferred_property_type", "Any")
            furnishing = data.get("furnishing_preference", "Any")
            preferred_bhk = data.get("preferred_bhk", "Any")
            budget_min = data.get("monthly_budget_min")
            budget_max = data.get("monthly_budget_max")
            if property_type not in {"Any", "Apartment", "House", "Villa", "Studio", "Other"}:
                raise HTTPException(status_code=400, detail="Unsupported preferred property type")
            if furnishing not in {"Any", "Furnished", "Semi-Furnished", "Unfurnished"}:
                raise HTTPException(status_code=400, detail="Unsupported furnishing preference")
            if preferred_bhk not in {"Any", "1 BHK", "2 BHK", "3 BHK", "4+ BHK"}:
                raise HTTPException(status_code=400, detail="Unsupported bedroom preference")
            for label, value in (("Minimum", budget_min), ("Maximum", budget_max)):
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0):
                    raise HTTPException(status_code=400, detail=f"Monthly budget {label.lower()} must be a non-negative amount")
            if budget_min is not None and budget_max is not None and budget_min > budget_max:
                raise HTTPException(status_code=400, detail="Monthly budget minimum cannot exceed maximum")
            return {"record_type": record_type, "name": name.strip(), "phone": phone.strip(), "preferred_city": preferred_city.strip(),
                    "preferred_property_type": property_type, "monthly_budget_min": budget_min, "monthly_budget_max": budget_max,
                    "preferred_bhk": preferred_bhk, "furnishing_preference": furnishing}
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
        if kind not in {"listing", "profile", "favorite", "inquiry", "maintenance", "tenancy", "rent_payment"}:
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
        if user.role == "renter" and kind == "rent_payment":
            if operation == "read" and data.get("renter_uid") == user.uid:
                return
            raise HTTPException(status_code=403, detail="Rent record belongs to another Renter")
        owns_record = record.get("owner_uid") == user.uid
        if kind in {"inquiry", "maintenance"} and user.role == "owner":
            owns_record = data.get("property_owner_uid") == user.uid
        if not owns_record:
            raise HTTPException(status_code=403, detail="Record belongs to another user")

    def _validate_tenancy_data(self, user: CurrentUser, data: dict) -> dict:
        if user.role != "owner":
            raise HTTPException(status_code=403, detail="Only an Owner can confirm a tenancy")
        enquiry_id = data.get("inquiry_id")
        enquiry = self.repository.get("application_records", enquiry_id) if isinstance(enquiry_id, str) else None
        enquiry_data = (enquiry or {}).get("data") or {}
        if not enquiry or enquiry.get("kind") != "inquiry" or enquiry_data.get("property_owner_uid") != user.uid:
            raise HTTPException(status_code=404, detail="Accepted enquiry was not found in your portfolio")
        if enquiry_data.get("status") != "accepted":
            raise HTTPException(status_code=409, detail="Only an accepted enquiry can be used to confirm a tenancy")
        listing_id = enquiry_data.get("listing_id")
        listing = self.repository.get("application_records", listing_id) if listing_id else None
        if not listing or listing.get("kind") != "listing" or listing.get("owner_uid") != user.uid:
            raise HTTPException(status_code=404, detail="The property for this enquiry is no longer in your portfolio")
        renter_uid = enquiry_data.get("client_uid")
        renter = self.repository.account_by_uid(renter_uid) if renter_uid else None
        if not renter or renter.get("role") != "renter":
            raise HTTPException(status_code=409, detail="The enquiry is not linked to an active Renter account")
        for existing in self.repository.list_records():
            current = existing.get("data") or {}
            if existing.get("kind") == "tenancy" and current.get("inquiry_id") == enquiry_id and current.get("status") == "active":
                raise HTTPException(status_code=409, detail="This accepted enquiry already has an active tenancy")
        try:
            start_date = date.fromisoformat(data.get("start_date", ""))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Tenancy start date must use YYYY-MM-DD") from None
        if start_date > date.today():
            raise HTTPException(status_code=400, detail="Tenancy start date cannot be in the future")
        amount = data.get("monthly_rent")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(float(amount)) or amount <= 0:
            raise HTTPException(status_code=400, detail="Monthly rent must be a positive amount")
        due_day = data.get("due_day", 1)
        if isinstance(due_day, bool) or not isinstance(due_day, int) or not 1 <= due_day <= 28:
            raise HTTPException(status_code=400, detail="Rent due day must be between 1 and 28")
        listing_data = listing.get("data") or {}
        return {
            "inquiry_id": enquiry_id, "listing_id": listing_id,
            "renter_uid": renter_uid, "renter_name": enquiry_data.get("client_name", renter.get("name", "")),
            "renter_email": enquiry_data.get("client_email", renter.get("email", "")),
            "property_title": listing_data.get("title", "Property"),
            "start_date": start_date.isoformat(), "monthly_rent": float(amount),
            "due_day": due_day, "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def _validate_rent_payment_data(self, user: CurrentUser, data: dict) -> dict:
        if user.role != "owner":
            raise HTTPException(status_code=403, detail="Only an Owner can record rent due")
        tenancy_id = data.get("tenancy_id")
        tenancy = self.repository.get("application_records", tenancy_id) if isinstance(tenancy_id, str) else None
        tenancy_data = (tenancy or {}).get("data") or {}
        if not tenancy or tenancy.get("kind") != "tenancy" or tenancy.get("owner_uid") != user.uid or tenancy_data.get("status") != "active":
            raise HTTPException(status_code=404, detail="Active tenancy was not found in your portfolio")
        period = data.get("period")
        if not isinstance(period, str) or len(period) != 7 or period[4] != "-" or not period[:4].isdigit() or not period[5:].isdigit():
            raise HTTPException(status_code=400, detail="Rent period must use YYYY-MM")
        try:
            period_date = datetime.strptime(period, "%Y-%m").date().replace(day=1)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Rent period must use YYYY-MM") from None
        if period_date < date.fromisoformat(tenancy_data["start_date"]).replace(day=1):
            raise HTTPException(status_code=400, detail="Rent period cannot begin before the tenancy")
        if any(
            item.get("kind") == "rent_payment"
            and (item.get("data") or {}).get("tenancy_id") == tenancy_id
            and (item.get("data") or {}).get("period") == period
            for item in self.repository.list_records()
        ):
            raise HTTPException(status_code=409, detail="A rent record already exists for this tenancy and period")
        due_date = period_date.replace(day=tenancy_data["due_day"])
        return {
            "tenancy_id": tenancy_id, "listing_id": tenancy_data["listing_id"],
            "renter_uid": tenancy_data["renter_uid"], "renter_name": tenancy_data["renter_name"],
            "property_title": tenancy_data["property_title"], "period": period,
            "amount": tenancy_data["monthly_rent"], "due_date": due_date.isoformat(),
            "status": "pending", "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
