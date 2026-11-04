"""Schemas for documents, curated recommendations, places, chat and admin."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- documents
class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    property_id: uuid.UUID
    property_name: str | None = None
    filename: str
    document_type: str
    size_bytes: int
    status: str
    error_message: str | None = None
    chunk_count: int
    is_system: bool
    uploaded_at: datetime
    last_indexed_at: datetime | None = None


# --------------------------------------------------------------------------- curated
class CuratedIn(BaseModel):
    property_id: uuid.UUID
    name: str = Field(min_length=2, max_length=200)
    category: str = Field(min_length=2, max_length=60)
    note: str | None = Field(default=None, max_length=1000)
    address: str | None = Field(default=None, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    osm_id: str | None = Field(default=None, max_length=64, pattern=r"^(node|way|relation)/\d+$")
    price_level: int | None = Field(default=None, ge=1, le=4)
    tags: list[str] = Field(default_factory=list, max_length=20)
    is_active: bool = True


class CuratedOut(CuratedIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime


# --------------------------------------------------------------------------- places
PlaceSource = Literal["osm", "osm_snapshot", "host_curated", "sample"]


class PlaceResult(BaseModel):
    """A local recommendation. Every field comes from OpenStreetMap, the host's
    curated list or the clearly-labelled sample dataset - never from the LLM."""

    osm_id: str | None = None  # e.g. "node/123456"
    name: str
    category: str | None = None
    cuisine: str | None = None
    types: list[str] = []
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    distance_km: float | None = None
    opening_hours: str | None = None  # raw OSM value, e.g. "Mo-Su 11:00-23:00"
    open_now: bool | None = None
    open_late: bool | None = None
    price_level: int | None = None  # 1-4 only from the host or a clear tag (fast food = 1)
    price_note: str | None = None
    diet: str | None = None
    phone: str | None = None
    website: str | None = None
    rating: float | None = None  # only for host-entered data; OSM has no ratings
    summary: str | None = None
    maps_url: str | None = None
    directions_url: str | None = None
    osm_url: str | None = None
    source: PlaceSource = "osm"
    host_note: str | None = None
    score: float | None = None


class PlacesSearchRequest(BaseModel):
    property_id: uuid.UUID
    query: str = Field(min_length=2, max_length=200)
    category_key: str | None = Field(default=None, max_length=40)
    radius_m: int | None = Field(default=None, ge=100, le=50000)
    open_now: bool | None = None
    limit: int = Field(default=6, ge=1, le=20)


class PlacesSearchResponse(BaseModel):
    provider: str
    origin: dict
    radius_m: int
    results: list[PlaceResult]
    notice: str | None = None
    attribution: str | None = None


# --------------------------------------------------------------------------- chat
class Intent(StrEnum):
    PROPERTY_QUERY = "PROPERTY_QUERY"
    LOCAL_DISCOVERY_QUERY = "LOCAL_DISCOVERY_QUERY"
    GENERAL_NEIGHBOURHOOD_QUERY = "GENERAL_NEIGHBOURHOOD_QUERY"
    MIXED_QUERY = "MIXED_QUERY"
    GENERAL_CONVERSATION = "GENERAL_CONVERSATION"
    EMERGENCY_QUERY = "EMERGENCY_QUERY"


class SessionCreate(BaseModel):
    location_slug: str | None = None
    property_id: uuid.UUID | None = None


class SessionSelect(BaseModel):
    location_slug: str | None = None
    property_id: uuid.UUID | None = None


class SessionOut(BaseModel):
    session_id: uuid.UUID
    session_token: str | None = None  # only returned once, on creation
    location_slug: str | None = None
    location_name: str | None = None
    property: dict | None = None
    available_properties: list[dict] = []
    preferences: dict = {}
    pin_verified: bool = False
    created_at: datetime


class ChatMessageIn(BaseModel):
    session_id: uuid.UUID
    message: str = Field(min_length=1, max_length=1500)


class PreferencesIn(BaseModel):
    max_distance_km: float | None = Field(default=None, ge=0.5, le=25)
    budget: Literal["budget", "moderate", "premium"] | None = None
    dietary: str | None = Field(default=None, max_length=60)


class PinVerifyIn(BaseModel):
    pin: str = Field(min_length=4, max_length=12)


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    intent: str | None = None
    places: list[PlaceResult] = []
    suggestions: list[str] = []
    created_at: datetime


# --------------------------------------------------------------------------- admin
class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
