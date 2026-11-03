"""Property / location schemas, including the structured property knowledge model.

Every detail field is optional: properties will not all have the same information.
Fields whose names appear in SENSITIVE_FIELDS are treated as sensitive in RAG.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_Str = str | None


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckInOut(_Section):
    check_in_time: _Str = None
    check_out_time: _Str = None
    early_check_in_policy: _Str = None
    late_check_out_policy: _Str = None
    self_check_in_instructions: _Str = None
    key_collection_instructions: _Str = None


class Facilities(_Section):
    wifi_name: _Str = None
    wifi_password: _Str = None
    air_conditioning: _Str = None
    heating: _Str = None
    kitchen: _Str = None
    refrigerator: _Str = None
    washing_machine: _Str = None
    television: _Str = None
    parking: _Str = None
    lift: _Str = None
    power_backup: _Str = None
    drinking_water: _Str = None
    housekeeping: _Str = None


class HouseRules(_Section):
    smoking_policy: _Str = None
    pet_policy: _Str = None
    visitors_policy: _Str = None
    noise_restrictions: _Str = None
    parties_events_policy: _Str = None
    security_instructions: _Str = None
    garbage_disposal: _Str = None
    other_restrictions: _Str = None


class Emergency(_Section):
    host_contact: _Str = None
    property_manager_contact: _Str = None
    emergency_exit_instructions: _Str = None
    nearby_hospital: _Str = None
    emergency_numbers: _Str = None


class Neighbourhood(_Section):
    nearest_metro_station: _Str = None
    nearest_bus_stop: _Str = None
    airport_connectivity: _Str = None
    nearby_shopping: _Str = None
    nearby_restaurants: _Str = None
    nearby_medical: _Str = None
    nearby_attractions: _Str = None


class PropertyDetails(_Section):
    check_in_out: CheckInOut = Field(default_factory=CheckInOut)
    facilities: Facilities = Field(default_factory=Facilities)
    house_rules: HouseRules = Field(default_factory=HouseRules)
    emergency: Emergency = Field(default_factory=Emergency)
    neighbourhood: Neighbourhood = Field(default_factory=Neighbourhood)


# Section/field names whose values grant physical or network access.
SENSITIVE_FIELDS = {
    "wifi_password",
    "self_check_in_instructions",
    "key_collection_instructions",
}

SECTION_TITLES = {
    "check_in_out": "Check-in and check-out",
    "facilities": "Facilities",
    "house_rules": "House rules",
    "emergency": "Emergency information",
    "neighbourhood": "Local neighbourhood information",
}


def field_label(name: str) -> str:
    special = {"wifi_name": "Wi-Fi network name", "wifi_password": "Wi-Fi password", "lift": "Lift / elevator"}
    return special.get(name, name.replace("_", " ").capitalize())


class LocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    slug: str
    name: str
    city: str
    tagline: str | None = None
    description: str | None = None
    property_count: int = 0


class PropertySummary(BaseModel):
    """Public, non-sensitive view of a property shown to guests."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    slug: str
    name: str
    location_slug: str
    location_name: str
    neighbourhood: str | None = None
    address: str | None = None
    bedrooms: int | None = None
    bathrooms: float | None = None
    max_guests: int | None = None
    description: str | None = None
    amenities: list[str] = []
    has_coordinates: bool = False
    requires_pin: bool = False
    is_placeholder: bool = False


class PropertyBase(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9\-]{1,78}$")
    location_slug: str = Field(min_length=2, max_length=64)
    address: str | None = Field(default=None, max_length=1000)
    neighbourhood: str | None = Field(default=None, max_length=160)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    discovery_radius_m: int = Field(default=3000, ge=100, le=50000)
    bedrooms: int | None = Field(default=None, ge=0, le=50)
    bathrooms: float | None = Field(default=None, ge=0, le=50)
    max_guests: int | None = Field(default=None, ge=1, le=100)
    description: str | None = Field(default=None, max_length=5000)
    amenities: list[str] = Field(default_factory=list, max_length=100)
    details: PropertyDetails = Field(default_factory=PropertyDetails)
    is_placeholder: bool = False
    is_active: bool = True

    @field_validator("amenities")
    @classmethod
    def _clean_amenities(cls, v: list[str]) -> list[str]:
        return [a.strip()[:80] for a in v if a and a.strip()]


class PropertyCreate(PropertyBase):
    guest_pin: str | None = Field(default=None, min_length=4, max_length=12, pattern=r"^[0-9A-Za-z]+$")


class PropertyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    location_slug: str | None = None
    address: str | None = Field(default=None, max_length=1000)
    neighbourhood: str | None = Field(default=None, max_length=160)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    discovery_radius_m: int | None = Field(default=None, ge=100, le=50000)
    bedrooms: int | None = Field(default=None, ge=0, le=50)
    bathrooms: float | None = Field(default=None, ge=0, le=50)
    max_guests: int | None = Field(default=None, ge=1, le=100)
    description: str | None = Field(default=None, max_length=5000)
    amenities: list[str] | None = None
    details: PropertyDetails | None = None
    is_placeholder: bool | None = None
    is_active: bool | None = None
    # Empty string clears the PIN; None leaves it unchanged.
    guest_pin: str | None = Field(default=None, max_length=12, pattern=r"^([0-9A-Za-z]{4,12})?$")


class PropertyAdminOut(PropertySummary):
    latitude: float | None = None
    longitude: float | None = None
    discovery_radius_m: int
    details: PropertyDetails
    is_active: bool
    created_at: datetime
    updated_at: datetime
    document_count: int = 0
    chunk_count: int = 0


class LocationCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]{1,62}$")
    name: str = Field(min_length=2, max_length=120)
    city: str = "Delhi"
    tagline: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
