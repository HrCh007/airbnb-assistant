"""ORM models (SQLite).

Property isolation is enforced structurally:
* every knowledge chunk carries a NOT NULL `property_id`;
* a composite FK (document_id, property_id) -> property_documents(id, property_id)
  guarantees a chunk can never be attached to a document of another property;
* vectors live in ChromaDB in one collection *per property* (see rag/vectorstore.py);
* every conversation pins exactly one property at a time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=_now, nullable=False)


def _updated() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[uuid.UUID] = _uuid()
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False, default="Delhi")
    tagline: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    properties: Mapped[list[Property]] = relationship(back_populates="location")


class Property(Base):
    __tablename__ = "properties"
    __table_args__ = (
        CheckConstraint("latitude IS NULL OR (latitude BETWEEN -90 AND 90)", name="ck_property_lat"),
        CheckConstraint("longitude IS NULL OR (longitude BETWEEN -180 AND 180)", name="ck_property_lng"),
        CheckConstraint("discovery_radius_m BETWEEN 100 AND 50000", name="ck_property_radius"),
    )

    id: Mapped[uuid.UUID] = _uuid()
    location_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    neighbourhood: Mapped[str | None] = mapped_column(String(160))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    discovery_radius_m: Mapped[int] = mapped_column(Integer, default=3000, nullable=False)
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    bathrooms: Mapped[float | None] = mapped_column(Float)
    max_guests: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    amenities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Structured sections (check-in, facilities incl. Wi-Fi/parking/kitchen, house
    # rules, emergency contacts, neighbourhood). Validated by schemas.PropertyDetails.
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # If set, sensitive facts (Wi-Fi password, door codes) require the guest PIN.
    guest_pin_hash: Mapped[str | None] = mapped_column(String(200))
    is_placeholder: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    location: Mapped[Location] = relationship(back_populates="properties", lazy="joined")
    documents: Mapped[list[PropertyDocument]] = relationship(
        back_populates="property", cascade="all, delete-orphan", passive_deletes=True
    )


class PropertyDocument(Base):
    __tablename__ = "property_documents"
    __table_args__ = (
        UniqueConstraint("id", "property_id", name="uq_document_property"),
        CheckConstraint("status IN ('pending','processing','ready','failed')", name="ck_document_status"),
    )

    id: Mapped[uuid.UUID] = _uuid()
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[str] = mapped_column(String(20), nullable=False)  # pdf/docx/txt/md/csv/json/profile
    mime_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    # Original file, stored in the uploads volume: <data_dir>/uploads/<property>/<document>.<ext>
    storage_path: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uploaded_at: Mapped[datetime] = _created()
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    property: Mapped[Property] = relationship(back_populates="documents")


class KnowledgeChunk(Base):
    """Chunk text and metadata (the vector for the same id lives in ChromaDB)."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "property_id"],
            ["property_documents.id", "property_documents.property_id"],
            ondelete="CASCADE",
            name="fk_chunk_document_same_property",
        ),
        Index("ix_chunks_property", "property_id"),
        Index("ix_chunks_document", "document_id"),
    )

    id: Mapped[uuid.UUID] = _uuid()
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # property_id, location, document_id, document_name, document_type, section, chunk_id, source, last_updated
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = _created()


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = _uuid()
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"), index=True)
    property_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("properties.id", ondelete="SET NULL"), index=True)
    # SHA-256 of a random token held only by the guest's browser.
    client_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    pin_verified_property_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )
    preferences: Mapped[GuestPreferences | None] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", uselist=False
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conv_created", "conversation_id", "created_at"),
        CheckConstraint("role IN ('user','assistant','system')", name="ck_message_role"),
    )

    id: Mapped[uuid.UUID] = _uuid()
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    property_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(40))
    # Internal trace: sources, places shown, search plan, latency, verification.
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = _created()

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class GuestPreferences(Base):
    __tablename__ = "guest_preferences"

    id: Mapped[uuid.UUID] = _uuid()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    budget: Mapped[str | None] = mapped_column(String(20))
    dietary: Mapped[str | None] = mapped_column(String(60))
    companions: Mapped[str | None] = mapped_column(String(60))
    interests: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    travel_mode: Mapped[str | None] = mapped_column(String(30))
    max_distance_km: Mapped[float | None] = mapped_column(Float)
    other: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = _updated()

    conversation: Mapped[Conversation] = relationship(back_populates="preferences")


class CuratedRecommendation(Base):
    __tablename__ = "curated_recommendations"

    id: Mapped[uuid.UUID] = _uuid()
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)  # host's editorial note
    address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    osm_id: Mapped[str | None] = mapped_column(String(64))
    price_level: Mapped[int | None] = mapped_column(Integer)  # host's own estimate 1-4
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[uuid.UUID] = _uuid()
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(160))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
