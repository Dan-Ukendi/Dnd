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
    world_category_row: Mapped["WorldCategory | None"] = relationship(
        lazy="selectin", viewonly=True
    )

    @property
    def is_available(self) -> bool:
        return self.removed_at is None

    @property
    def world_category(self) -> str | None:
        row = self.world_category_row
        return row.category if row is not None else None


PERSON_CATEGORIES = ("lore", "character", "encounter")


class ArticleCategory(Base):
    __tablename__ = "article_categories"
    __table_args__ = (
        CheckConstraint(
            "category IN ('lore', 'character', 'encounter')", name="ck_article_categories_category"
        ),
    )

    wa_id: Mapped[str] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="CASCADE"), primary_key=True
    )
    category: Mapped[str] = mapped_column(String(10))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )


WORLD_CATEGORIES = ("pays", "ville", "batiment")


class WorldCategory(Base):
    __tablename__ = "world_categories"
    __table_args__ = (
        CheckConstraint(
            "category IS NULL OR category IN ('pays', 'ville', 'batiment')",
            name="ck_world_categories_category",
        ),
    )

    wa_id: Mapped[str] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="CASCADE"), primary_key=True
    )
    category: Mapped[str | None] = mapped_column(String(10))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )


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
    actions: Mapped[list["CharacterAction"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CharacterAction.position",
    )
    journal: Mapped[list["CharacterJournalEntry"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CharacterJournalEntry.episode",
    )


class CharacterProfile(Base):
    __tablename__ = "character_profiles"

    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True
    )
    backstory: Mapped[str] = mapped_column(Text, default="", server_default="")
    combat: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    aliases: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    relations_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    events_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_file: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )


class CharacterEvent(Base):
    __tablename__ = "character_events"
    __table_args__ = (CheckConstraint("age IS NULL OR age >= 0", name="ck_character_events_age"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    session: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    world_year: Mapped[int | None] = mapped_column(Integer)
    age: Mapped[int | None] = mapped_column(Integer)
    date_approximate: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    place_name: Mapped[str | None] = mapped_column(String(200))
    place_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL")
    )
    consequences: Mapped[str] = mapped_column(Text, default="", server_default="")
    gm_notes: Mapped[str] = mapped_column(Text, default="", server_default="")

    place_article: Mapped[WaArticle | None] = relationship()
    people: Mapped[list["CharacterEventPerson"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CharacterEventPerson.position",
    )
    organizations: Mapped[list["CharacterEventOrganization"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CharacterEventOrganization.position",
    )


class CharacterEventPerson(Base):
    __tablename__ = "character_event_people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("character_events.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(200))
    wa_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL")
    )

    article: Mapped[WaArticle | None] = relationship()


class CharacterEventOrganization(Base):
    __tablename__ = "character_event_organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("character_events.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    wa_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL")
    )

    article: Mapped[WaArticle | None] = relationship()


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
    gm_notes: Mapped[str] = mapped_column(Text, default="", server_default="")

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
    gm_notes: Mapped[str] = mapped_column(Text, default="", server_default="")

    article: Mapped[WaArticle | None] = relationship()


class CharacterAction(Base):
    __tablename__ = "character_actions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('action', 'bonus', 'reaction', 'free')", name="ck_character_actions_kind"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(100))
    hit: Mapped[str | None] = mapped_column(String(60))
    damage: Mapped[str | None] = mapped_column(String(200))
    save: Mapped[str | None] = mapped_column(String(60))
    range: Mapped[str | None] = mapped_column(String(100))
    uses: Mapped[str | None] = mapped_column(String(100))
    concentration: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    text: Mapped[str] = mapped_column(Text, default="", server_default="")


class CharacterJournalEntry(Base):
    __tablename__ = "character_journal_entries"
    __table_args__ = (
        UniqueConstraint("character_id", "episode", name="uq_character_journal_entries_episode"),
        CheckConstraint("episode > 0", name="ck_character_journal_entries_episode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    episode: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    days: Mapped[str | None] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    lists: Mapped[dict[str, list[str]]] = mapped_column(JSONB, default=dict, server_default="{}")


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
    events_created: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    events_updated: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    events_removed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
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


class TimelineEvent(Base):
    __tablename__ = "timeline_events"
    __table_args__ = (
        CheckConstraint(
            "end_year IS NULL OR end_year >= start_year", name="ck_timeline_events_years"
        ),
        CheckConstraint(
            "importance IN ('majeur', 'notable', 'mineur')", name="ck_timeline_events_importance"
        ),
        CheckConstraint("origin IN ('canon', 'ajout')", name="ck_timeline_events_origin"),
        CheckConstraint("visibility IN ('mj', 'joueurs')", name="ck_timeline_events_visibility"),
        CheckConstraint("day IS NULL OR day IN ('J1', 'J2')", name="ck_timeline_events_day"),
        CheckConstraint("session IS NULL OR session >= 1", name="ck_timeline_events_session"),
        Index("ix_timeline_events_start_year_sort_order", "start_year", "sort_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    start_year: Mapped[int] = mapped_column(Integer)
    end_year: Mapped[int | None] = mapped_column(Integer)
    place: Mapped[str | None] = mapped_column(String(300))
    importance: Mapped[str] = mapped_column(String(10))
    origin: Mapped[str] = mapped_column(String(10))
    visibility: Mapped[str] = mapped_column(String(10), default="mj", server_default="mj")
    chronicle: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    session: Mapped[int | None] = mapped_column(Integer)
    day: Mapped[str | None] = mapped_column(String(2))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    wa_history_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    wa_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modified_by_dm: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_range(self) -> bool:
        return self.end_year is not None and self.end_year != self.start_year


class TimelineEventLink(Base):
    __tablename__ = "timeline_event_links"
    __table_args__ = (
        CheckConstraint("source_id <> target_id", name="ck_timeline_event_links_distinct"),
        CheckConstraint("origin IN ('wa', 'mj')", name="ck_timeline_event_links_origin"),
    )

    source_id: Mapped[int] = mapped_column(
        ForeignKey("timeline_events.id", ondelete="CASCADE"), primary_key=True
    )
    target_id: Mapped[int] = mapped_column(
        ForeignKey("timeline_events.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    origin: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class TimelineEventFiche(Base):
    __tablename__ = "timeline_event_fiches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("timeline_events.id", ondelete="CASCADE"), index=True
    )
    wa_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    article: Mapped[WaArticle | None] = relationship()


class TimelineEra(Base):
    __tablename__ = "timeline_eras"

    slug: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(100))
    subtitle: Mapped[str | None] = mapped_column(String(300))
    color: Mapped[str | None] = mapped_column(String(7))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )


class Artefact(Base):
    __tablename__ = "artefacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wa_article_id: Mapped[str] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="CASCADE"), unique=True
    )
    tagline: Mapped[str] = mapped_column(String(300), default="", server_default="")
    object_type: Mapped[str] = mapped_column(String(300), default="", server_default="")
    other_names: Mapped[str] = mapped_column(String(500), default="", server_default="")
    rarity: Mapped[str] = mapped_column(String(300), default="", server_default="")
    attunement: Mapped[str] = mapped_column(String(300), default="", server_default="")
    properties: Mapped[str] = mapped_column(Text, default="", server_default="")
    bonuses: Mapped[str] = mapped_column(Text, default="", server_default="")
    charges: Mapped[str] = mapped_column(String(100), default="", server_default="")
    recharge: Mapped[str] = mapped_column(String(200), default="", server_default="")
    curse: Mapped[str] = mapped_column(Text, default="", server_default="")
    holder: Mapped[str] = mapped_column(String(200), default="", server_default="")
    location: Mapped[str] = mapped_column(String(300), default="", server_default="")
    status: Mapped[str] = mapped_column(String(200), default="", server_default="")
    gm_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )

    article: Mapped[WaArticle] = relationship()


class LoreType(Base):
    __tablename__ = "lore_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(60), unique=True)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class LoreGroup(Base):
    __tablename__ = "lore_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300), unique=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )


LORE_STORY_KINDS = ("legende", "ouvrage")


class LoreStory(Base):
    __tablename__ = "lore_stories"
    __table_args__ = (
        CheckConstraint("kind IN ('legende', 'ouvrage')", name="ck_lore_stories_kind"),
        CheckConstraint(
            "end_year IS NULL OR (start_year IS NOT NULL AND end_year >= start_year)",
            name="ck_lore_stories_years",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    intro_sections: Mapped[str] = mapped_column(Text, default="", server_default="")
    type_id: Mapped[int | None] = mapped_column(
        ForeignKey("lore_types.id", ondelete="SET NULL"), index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("lore_groups.id", ondelete="SET NULL"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    start_year: Mapped[int | None] = mapped_column(Integer)
    end_year: Mapped[int | None] = mapped_column(Integer)
    era: Mapped[str | None] = mapped_column(String(30))
    approximate: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    wa_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL"), index=True
    )
    artefact_id: Mapped[int | None] = mapped_column(
        ForeignKey("artefacts.id", ondelete="SET NULL"), index=True
    )
    legend_id: Mapped[int | None] = mapped_column(
        ForeignKey("lore_stories.id", ondelete="SET NULL"), index=True
    )
    gm_only: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )

    type: Mapped[LoreType | None] = relationship()
    group: Mapped[LoreGroup | None] = relationship()
    article: Mapped[WaArticle | None] = relationship()
    artefact: Mapped[Artefact | None] = relationship()
    legend: Mapped["LoreStory | None"] = relationship(remote_side="LoreStory.id")


LORE_ELEMENT_TYPES = ("artefact", "lieu", "personnage", "organisation", "divinite", "evenement")
LORE_PLACEMENTS = ("part", "physical", "characteristic", "dm_note")


class LoreEntry(Base):
    __tablename__ = "lore_entries"
    __table_args__ = (
        CheckConstraint(
            "element_type IN ('artefact', 'lieu', 'personnage', 'organisation', 'divinite', "
            "'evenement')",
            name="ck_lore_entries_element_type",
        ),
        CheckConstraint(
            "end_year IS NULL OR (start_year IS NOT NULL AND end_year >= start_year)",
            name="ck_lore_entries_years",
        ),
        CheckConstraint(
            "placement IN ('part', 'physical', 'characteristic', 'dm_note')",
            name="ck_lore_entries_placement",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    element_type: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    start_year: Mapped[int | None] = mapped_column(Integer)
    end_year: Mapped[int | None] = mapped_column(Integer)
    era: Mapped[str | None] = mapped_column(String(30))
    approximate: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    wa_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL"), index=True
    )
    timeline_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("timeline_events.id", ondelete="SET NULL"), index=True
    )
    source_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL")
    )
    source_section: Mapped[str | None] = mapped_column(String(300))
    story_id: Mapped[int | None] = mapped_column(
        ForeignKey("lore_stories.id", ondelete="CASCADE"), index=True
    )
    placement: Mapped[str] = mapped_column(String(20), default="part", server_default="part")
    gm_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )

    article: Mapped[WaArticle | None] = relationship(foreign_keys=[wa_article_id])
    source: Mapped[WaArticle | None] = relationship(foreign_keys=[source_article_id])
    event: Mapped[TimelineEvent | None] = relationship()
    story: Mapped[LoreStory | None] = relationship()


PLACE_KINDS = (
    "territoire",
    "region",
    "ville",
    "quartier",
    "batiment",
    "terre",
    "niveau",
    "piece",
    "lieu",
)
STRUCTURE_VISIBILITIES = ("mj", "joueurs")


class Place(Base):
    __tablename__ = "places"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('territoire', 'region', 'ville', 'quartier', 'batiment', 'terre', "
            "'niveau', 'piece', 'lieu')",
            name="ck_places_kind",
        ),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_places_parent"),
        CheckConstraint("visibility IN ('mj', 'joueurs')", name="ck_places_visibility"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("places.id", ondelete="SET NULL"), index=True
    )
    wa_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL"), unique=True
    )
    owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL"), index=True
    )
    location: Mapped[str] = mapped_column(String(300), default="", server_default="")
    source_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL"), index=True
    )
    source_anchor: Mapped[str] = mapped_column(String(300), default="", server_default="")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    gm_notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    visibility: Mapped[str] = mapped_column(String(10), default="mj", server_default="mj")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(organisation_id, place_id) = 1", name="ck_positions_attachment"
        ),
        CheckConstraint("superior_id IS NULL OR superior_id <> id", name="ck_positions_superior"),
        CheckConstraint("visibility IN ('mj', 'joueurs')", name="ck_positions_visibility"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    organisation_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="CASCADE"), index=True
    )
    place_id: Mapped[int | None] = mapped_column(
        ForeignKey("places.id", ondelete="CASCADE"), index=True
    )
    person_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), default="", server_default="")
    title: Mapped[str] = mapped_column(String(300), default="", server_default="")
    scope: Mapped[str] = mapped_column(String(200), default="", server_default="")
    superior_id: Mapped[int | None] = mapped_column(
        ForeignKey("positions.id", ondelete="SET NULL"), index=True
    )
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("places.id", ondelete="SET NULL"), index=True
    )
    is_head: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    in_hierarchy: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    source_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL"), index=True
    )
    source_anchor: Mapped[str] = mapped_column(String(300), default="", server_default="")
    gm_notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    npc_id: Mapped[int | None] = mapped_column(
        ForeignKey("npcs.id", ondelete="SET NULL"), index=True
    )
    npc_role_id: Mapped[int | None] = mapped_column(
        ForeignKey("npc_roles.id", ondelete="SET NULL"), index=True
    )
    visibility: Mapped[str] = mapped_column(String(10), default="mj", server_default="mj")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )


class StatBlock(Base):
    __tablename__ = "stat_blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    size: Mapped[str] = mapped_column(String(40), default="", server_default="")
    creature_type: Mapped[str] = mapped_column(String(200), default="", server_default="")
    alignment: Mapped[str] = mapped_column(String(200), default="", server_default="")
    armor_class: Mapped[int | None] = mapped_column(Integer)
    armor_desc: Mapped[str] = mapped_column(String(200), default="", server_default="")
    hit_points: Mapped[int | None] = mapped_column(Integer)
    hit_dice: Mapped[str] = mapped_column(String(60), default="", server_default="")
    speed: Mapped[str] = mapped_column(String(200), default="", server_default="")
    abilities: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default="[]")
    saves: Mapped[str] = mapped_column(String(300), default="", server_default="")
    skills: Mapped[str] = mapped_column(String(500), default="", server_default="")
    vulnerabilities: Mapped[str] = mapped_column(String(300), default="", server_default="")
    resistances: Mapped[str] = mapped_column(String(300), default="", server_default="")
    immunities: Mapped[str] = mapped_column(String(300), default="", server_default="")
    condition_immunities: Mapped[str] = mapped_column(String(300), default="", server_default="")
    senses: Mapped[str] = mapped_column(String(300), default="", server_default="")
    languages: Mapped[str] = mapped_column(String(300), default="", server_default="")
    challenge: Mapped[str] = mapped_column(String(20), default="", server_default="")
    traits: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    bonus_actions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    reactions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    source: Mapped[str] = mapped_column(String(300), default="", server_default="")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )


class Npc(Base):
    __tablename__ = "npcs"
    __table_args__ = (
        CheckConstraint("category IN ('lore', 'character', 'encounter')", name="ck_npcs_category"),
        CheckConstraint("visibility IN ('mj', 'joueurs')", name="ck_npcs_visibility"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300), default="", server_default="")
    gender: Mapped[str] = mapped_column(String(60), default="", server_default="")
    race: Mapped[str] = mapped_column(String(100), default="", server_default="")
    age: Mapped[str] = mapped_column(String(60), default="", server_default="")
    standing: Mapped[str] = mapped_column(String(300), default="", server_default="")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    gm_notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    category: Mapped[str] = mapped_column(String(10), default="lore", server_default="lore")
    stat_block_id: Mapped[int | None] = mapped_column(
        ForeignKey("stat_blocks.id", ondelete="SET NULL"), index=True
    )
    organisation_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL"), index=True
    )
    place_id: Mapped[int | None] = mapped_column(
        ForeignKey("places.id", ondelete="SET NULL"), index=True
    )
    wa_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL"), unique=True
    )
    visibility: Mapped[str] = mapped_column(String(10), default="mj", server_default="mj")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )


class NpcRole(Base):
    __tablename__ = "npc_roles"
    __table_args__ = (
        CheckConstraint("visibility IN ('mj', 'joueurs')", name="ck_npc_roles_visibility"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    headcount: Mapped[str] = mapped_column(String(100), default="", server_default="")
    gm_notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    stat_block_id: Mapped[int | None] = mapped_column(
        ForeignKey("stat_blocks.id", ondelete="SET NULL"), index=True
    )
    organisation_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="SET NULL"), index=True
    )
    place_id: Mapped[int | None] = mapped_column(
        ForeignKey("places.id", ondelete="SET NULL"), index=True
    )
    visibility: Mapped[str] = mapped_column(String(10), default="mj", server_default="mj")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )


class FicheSection(Base):
    __tablename__ = "fiche_sections"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(place_id, npc_id, npc_role_id) = 1", name="ck_fiche_sections_owner"
        ),
        CheckConstraint("visibility IN ('mj', 'joueurs')", name="ck_fiche_sections_visibility"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    place_id: Mapped[int | None] = mapped_column(
        ForeignKey("places.id", ondelete="CASCADE"), index=True
    )
    npc_id: Mapped[int | None] = mapped_column(
        ForeignKey("npcs.id", ondelete="CASCADE"), index=True
    )
    npc_role_id: Mapped[int | None] = mapped_column(
        ForeignKey("npc_roles.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    visibility: Mapped[str] = mapped_column(String(10), default="joueurs", server_default="joueurs")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class NpcRelation(Base):
    __tablename__ = "npc_relations"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(target_npc_id, target_article_id) = 1", name="ck_npc_relations_target"
        ),
        CheckConstraint("visibility IN ('mj', 'joueurs')", name="ck_npc_relations_visibility"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    npc_id: Mapped[int] = mapped_column(ForeignKey("npcs.id", ondelete="CASCADE"), index=True)
    target_npc_id: Mapped[int | None] = mapped_column(
        ForeignKey("npcs.id", ondelete="CASCADE"), index=True
    )
    target_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(200), default="", server_default="")
    reverse_label: Mapped[str] = mapped_column(String(200), default="", server_default="")
    visibility: Mapped[str] = mapped_column(String(10), default="mj", server_default="mj")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class LinkAlias(Base):
    __tablename__ = "link_aliases"
    __table_args__ = (
        UniqueConstraint("wa_article_id", "alias", name="uq_link_aliases_article_alias"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alias: Mapped[str] = mapped_column(String(200))
    wa_article_id: Mapped[str] = mapped_column(
        ForeignKey("wa_articles.wa_id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    article: Mapped[WaArticle] = relationship()


class LinkExclusion(Base):
    __tablename__ = "link_exclusions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    term: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
