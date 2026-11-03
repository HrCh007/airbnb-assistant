"""Initial schema: locations, properties, documents, knowledge chunks (pgvector),
conversations, messages, guest preferences, curated recommendations, admin users.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-03
"""

import os

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

EMBED_DIM = int(os.environ.get("EMBEDDING_DIM", "1536"))


def _uuid_pk() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=nullable)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "locations",
        _uuid_pk(),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("city", sa.String(80), nullable=False, server_default="Delhi"),
        sa.Column("tagline", sa.String(200)),
        sa.Column("description", sa.Text),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        _ts("created_at"),
        _ts("updated_at"),
    )

    op.create_table(
        "properties",
        _uuid_pk(),
        sa.Column("location_id", pg.UUID(as_uuid=True), sa.ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("address", sa.Text),
        sa.Column("neighbourhood", sa.String(160)),
        sa.Column("latitude", sa.Float),
        sa.Column("longitude", sa.Float),
        sa.Column("discovery_radius_m", sa.Integer, nullable=False, server_default="3000"),
        sa.Column("bedrooms", sa.Integer),
        sa.Column("bathrooms", sa.Float),
        sa.Column("max_guests", sa.Integer),
        sa.Column("description", sa.Text),
        sa.Column("amenities", pg.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("details", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("guest_pin_hash", sa.String(200)),
        sa.Column("is_placeholder", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint("latitude IS NULL OR (latitude BETWEEN -90 AND 90)", name="ck_property_lat"),
        sa.CheckConstraint("longitude IS NULL OR (longitude BETWEEN -180 AND 180)", name="ck_property_lng"),
        sa.CheckConstraint("discovery_radius_m BETWEEN 100 AND 50000", name="ck_property_radius"),
    )
    op.create_index("ix_properties_location_id", "properties", ["location_id"])

    op.create_table(
        "property_documents",
        _uuid_pk(),
        sa.Column("property_id", pg.UUID(as_uuid=True), sa.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("document_type", sa.String(20), nullable=False),
        sa.Column("mime_type", sa.String(120)),
        sa.Column("size_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("checksum_sha256", sa.String(64)),
        sa.Column("content", sa.LargeBinary),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text),
        sa.Column("chunk_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.false()),
        _ts("uploaded_at"),
        sa.Column("last_indexed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("id", "property_id", name="uq_document_property"),
        sa.CheckConstraint(
            "status IN ('pending','processing','ready','failed')", name="ck_document_status"
        ),
    )
    op.create_index("ix_property_documents_property_id", "property_documents", ["property_id"])

    op.create_table(
        "knowledge_chunks",
        _uuid_pk(),
        sa.Column("property_id", pg.UUID(as_uuid=True), sa.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("section", sa.String(255)),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("is_sensitive", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=False),
        sa.Column(
            "tsv",
            pg.TSVECTOR,
            sa.Computed("to_tsvector('english', coalesce(section,'') || ' ' || content)", persisted=True),
        ),
        sa.Column("metadata", pg.JSONB, nullable=False, server_default="{}"),
        _ts("created_at"),
        sa.ForeignKeyConstraint(
            ["document_id", "property_id"],
            ["property_documents.id", "property_documents.property_id"],
            ondelete="CASCADE",
            name="fk_chunk_document_same_property",
        ),
    )
    op.create_index("ix_chunks_property", "knowledge_chunks", ["property_id"])
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])
    op.create_index("ix_chunks_tsv", "knowledge_chunks", ["tsv"], postgresql_using="gin")
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON knowledge_chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    op.create_table(
        "conversations",
        _uuid_pk(),
        sa.Column("location_id", pg.UUID(as_uuid=True), sa.ForeignKey("locations.id", ondelete="SET NULL")),
        sa.Column("property_id", pg.UUID(as_uuid=True), sa.ForeignKey("properties.id", ondelete="SET NULL")),
        sa.Column("client_token_hash", sa.String(64), nullable=False),
        sa.Column("pin_verified_property_id", pg.UUID(as_uuid=True)),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index("ix_conversations_location_id", "conversations", ["location_id"])
    op.create_index("ix_conversations_property_id", "conversations", ["property_id"])

    op.create_table(
        "messages",
        _uuid_pk(),
        sa.Column("conversation_id", pg.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("property_id", pg.UUID(as_uuid=True)),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("intent", sa.String(40)),
        sa.Column("metadata", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.clock_timestamp(), nullable=False),
        sa.CheckConstraint("role IN ('user','assistant','system')", name="ck_message_role"),
    )
    op.create_index("ix_messages_conv_created", "messages", ["conversation_id", "created_at"])

    op.create_table(
        "guest_preferences",
        _uuid_pk(),
        sa.Column("conversation_id", pg.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("budget", sa.String(20)),
        sa.Column("dietary", sa.String(60)),
        sa.Column("companions", sa.String(60)),
        sa.Column("interests", pg.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("travel_mode", sa.String(30)),
        sa.Column("max_distance_km", sa.Float),
        sa.Column("other", pg.JSONB, nullable=False, server_default="{}"),
        _ts("updated_at"),
    )

    op.create_table(
        "curated_recommendations",
        _uuid_pk(),
        sa.Column("property_id", pg.UUID(as_uuid=True), sa.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("note", sa.Text),
        sa.Column("address", sa.Text),
        sa.Column("latitude", sa.Float),
        sa.Column("longitude", sa.Float),
        sa.Column("google_place_id", sa.String(255)),
        sa.Column("tags", pg.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index("ix_curated_recommendations_property_id", "curated_recommendations", ["property_id"])

    op.create_table(
        "admin_users",
        _uuid_pk(),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("full_name", sa.String(160)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        _ts("created_at"),
    )


def downgrade() -> None:
    for table in (
        "admin_users",
        "curated_recommendations",
        "guest_preferences",
        "messages",
        "conversations",
        "knowledge_chunks",
        "property_documents",
        "properties",
        "locations",
    ):
        op.drop_table(table)
