from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
        }
    )


class Role(StrEnum):
    DM = "dm"
    PLAYER = "player"


class SyncStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class MediaRole(StrEnum):
    PORTRAIT = "portrait"
    MAP = "map"
    SHEET = "sheet"


SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('french'::regconfig, coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('french'::regconfig, coalesce(content_text, '')), 'B')"
)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('dm', 'player')", name="ck_users_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100))
    login: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(10))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    session_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    characters: Mapped[list["Character"]] = relationship(back_populates="player")

    @property
    def is_dm(self) -> bool:
        return self.role == Role.DM

    @property
    def has_password(self) -> bool:
        return self.password_hash is not None


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    user: Mapped[User] = relationship()


class WaArticle(Base):
    __tablename__ = "wa_articles"
    __table_args__ = (
        Index("ix_wa_articles_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_wa_articles_template_type", "template_type"),
    )

    wa_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    template_type: Mapped[str | None] = mapped_column(String(50))
    entity_class: Mapped[str | None] = mapped_column(String(50))
    state: Mapped[str | None] = mapped_column(String(20))
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_wip: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    url: Mapped[str | None] = mapped_column(String(1000), index=True)
    content: Mapped[str] = mapped_column(Text, default="", server_default="")
    content_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    linked_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String(36)), default=list, server_default="{}"
    )
    player_visible: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    visibility_reason: Mapped[str | None] = mapped_column(String(200))
    wa_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed(SEARCH_VECTOR_SQL, persisted=True)
    )

    media: Mapped[list["MediaAsset"]] = relationship(
        lazy="selectin", order_by="MediaAsset.id", viewonly=True
    )

    @property
    def is_available(self) -> bool:
        return self.removed_at is None


class WaImage(Base):
    __tablename__ = "wa_images"

    wa_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    title: Mapped[str | None] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(String(1000))
    player_visible: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    player_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    main_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    player: Mapped[User] = relationship(back_populates="characters")
    main_article: Mapped[WaArticle | None] = relationship()
    extra_links: Mapped[list["CharacterArticle"]] = relationship(
        back_populates="character", cascade="all, delete-orphan"
    )
    profile: Mapped["CharacterProfile | None"] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )
    events: Mapped[list["CharacterEvent"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CharacterEvent.position",
    )
    relations: Mapped[list["CharacterRelation"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CharacterRelation.position",
    )
    items: Mapped[list["CharacterItem"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CharacterItem.position",
    )


class CharacterProfile(Base):
    __tablename__ = "character_profiles"

    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True
    )
    backstory: Mapped[str] = mapped_column(Text, default="", server_default="")
    source_file: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )


class CharacterEvent(Base):
    __tablename__ = "character_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    session: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text, default="", server_default="")


class CharacterRelation(Base):
    __tablename__ = "character_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str | None] = mapped_column(String(100))
    wa_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL")
    )
    text: Mapped[str] = mapped_column(Text, default="", server_default="")

    article: Mapped[WaArticle | None] = relationship()


class CharacterItem(Base):
    __tablename__ = "character_items"
    __table_args__ = (CheckConstraint("quantity >= 0", name="ck_character_items_quantity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    category: Mapped[str | None] = mapped_column(String(60))
    equipped: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    wa_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL")
    )
    text: Mapped[str] = mapped_column(Text, default="", server_default="")

    article: Mapped[WaArticle | None] = relationship()


class CharacterArticle(Base):
    __tablename__ = "character_articles"

    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True
    )
    article_id: Mapped[str] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    character: Mapped[Character] = relationship(back_populates="extra_links")
    article: Mapped[WaArticle] = relationship()


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'success', 'failed')", name="ck_sync_runs_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(10))
    full: Mapped[bool] = mapped_column(Boolean, default=False)
    trigger: Mapped[str] = mapped_column(String(10))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    removed_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        CheckConstraint("role IN ('portrait', 'map', 'sheet')", name="ck_media_assets_role"),
        UniqueConstraint("wa_article_id", "role", name="uq_media_assets_article_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wa_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(10))
    banner_section: Mapped[str | None] = mapped_column(String(30), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    @property
    def version(self) -> str:
        return self.sha256[:12]


class MediaVariant(Base):
    __tablename__ = "media_variants"
    __table_args__ = (CheckConstraint("name IN ('full', 'thumb')", name="ck_media_variants_name"),)

    asset_id: Mapped[int] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(10), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(100))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    gzip_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    etag: Mapped[str] = mapped_column(String(64))
