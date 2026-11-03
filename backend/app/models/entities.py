"""ORM models.

Property isolation is enforced structurally:
* every knowledge chunk carries a NOT NULL `property_id` FK;
* a composite FK (document_id, property_id) -> property_documents(id, property_id)
  guarantees a chunk can never be attached to a document from another property;
* every conversation pins exactly one property at a time.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.core.database import Base

EMBED_DIM = get_settings().embedding_dim


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


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
    amenities: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    # Optional structured sections (check-in, facilities, house rules, emergency,
    # neighbourhood). Validated by app.schemas.property.PropertyDetails.
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
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
    __table_args__ = (UniqueConstraint("id", "property_id", name="uq_document_property"),)

    id: Mapped[uuid.UUID] = _uuid()
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[str] = mapped_column(String(20), nullable=False)  # pdf/docx/txt/md/csv/json/profile
    mime_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    # Original bytes are kept so documents can be re-indexed on stateless hosts.
    content: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # System documents are generated from the structured property profile.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uploaded_at: Mapped[datetime] = _created()
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    property: Mapped[Property] = relationship(back_populates="documents")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "property_id"],
            ["property_documents.id", "property_documents.property_id"],
            ondelete="CASCADE",
            name="fk_chunk_document_same_property",
        ),
        Index("ix_chunks_property", "property_id"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = _uuid()
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM), nullable=False)
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(section,'') || ' ' || content)", persisted=True),
    )
    # property_id, location, document_name, document_type, section, chunk_id, last_updated
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = _created()


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = _uuid()
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), index=True
    )
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("properties.id", ondelete="SET NULL"), index=True
    )
    # Hash of a random token held only by the guest's browser; required to read
    # or post to the conversation, so sessions cannot be read by guessing IDs.
    client_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    pin_verified_property_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
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
    __table_args__ = (Index("ix_messages_conv_created", "conversation_id", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    # Property the message was exchanged under (lets us scope memory on switch).
    property_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user / assistant / system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(40))
    # Internal trace: sources, places shown, latency, planner output.
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class GuestPreferences(Base):
    __tablename__ = "guest_preferences"

    id: Mapped[uuid.UUID] = _uuid()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    budget: Mapped[str | None] = mapped_column(String(20))  # budget / moderate / premium
    dietary: Mapped[str | None] = mapped_column(String(60))  # vegetarian / vegan / jain / non-veg ...
    companions: Mapped[str | None] = mapped_column(String(60))  # family / couple / friends / solo / business
    interests: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    travel_mode: Mapped[str | None] = mapped_column(String(30))  # walking / metro / cab / car
    max_distance_km: Mapped[float | None] = mapped_column(Float)
    other: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
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
    google_place_id: Mapped[str | None] = mapped_column(String(255))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
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
