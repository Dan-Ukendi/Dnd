import logging
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session_factory
from app.models import SyncRun, SyncStatus, WaArticle, WaImage, utcnow
from app.services.chronology import ChronologyError
from app.services.chronology_sync import sync_timeline
from app.services.lore import ensure_artefacts
from app.worldanvil.bbcode import (
    extract_image_ids,
    extract_links,
    normalize_wa_url,
    to_plain_text,
)
from app.worldanvil.client import (
    WorldAnvilClient,
    WorldAnvilError,
    WorldAnvilNotFoundError,
    WorldAnvilSource,
)
from app.worldanvil.dates import parse_wa_date
from app.worldanvil.visibility import assess_player_visibility

logger = logging.getLogger(__name__)

STALE_RUN_AFTER = timedelta(minutes=15)
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
REMOVED_REASON = "Supprimé de World Anvil"


class SyncAlreadyRunningError(Exception):
    pass


def latest_run(db: Session) -> SyncRun | None:
    return db.scalar(select(SyncRun).order_by(SyncRun.started_at.desc(), SyncRun.id.desc()))


def start_run(db: Session, *, full: bool, trigger: str) -> SyncRun:
    now = utcnow()
    running = db.scalars(
        select(SyncRun).where(SyncRun.status == SyncStatus.RUNNING).with_for_update()
    ).all()
    for previous in running:
        if now - previous.started_at < STALE_RUN_AFTER:
            db.rollback()
            raise SyncAlreadyRunningError("A synchronisation is already running")
        previous.status = SyncStatus.FAILED
        previous.finished_at = now
        previous.error = "Interrupted before completion"
    run = SyncRun(
        status=SyncStatus.RUNNING,
        full=full,
        trigger=trigger,
        started_at=now,
        created_count=0,
        updated_count=0,
        unchanged_count=0,
        removed_count=0,
    )
    db.add(run)
    db.commit()
    return run


def _apply_article(
    article: WaArticle, detail: dict[str, Any], ref: dict[str, Any], now: datetime
) -> None:
    content = detail.get("content")
    entity_class = detail.get("entityClass") or ref.get("entityClass")
    template_type = detail.get("templateType") or entity_class
    visibility = assess_player_visibility(detail)
    article.title = str(detail.get("title") or ref.get("title") or "Sans titre")[:500]
    article.entity_class = str(entity_class)[:50] if entity_class else None
    article.template_type = str(template_type).lower()[:50] if template_type else None
    article.state = str(detail.get("state"))[:20] if detail.get("state") else None
    article.is_draft = bool(detail.get("isDraft"))
    article.is_wip = bool(detail.get("isWip"))
    article.url = str(detail.get("url") or ref.get("url") or "")[:1000] or None
    article.content = content if isinstance(content, str) else ""
    article.content_text = to_plain_text(article.content)
    article.raw = detail
    article.wa_updated_at = parse_wa_date(ref.get("updateDate")) or parse_wa_date(
        detail.get("updateDate")
    )
    article.player_visible = visibility.player_visible
    article.visibility_reason = visibility.reason
    article.synced_at = now
    article.removed_at = None


def _refresh_links(db: Session) -> None:
    articles = db.scalars(select(WaArticle).where(WaArticle.removed_at.is_(None))).all()
    known = {article.wa_id for article in articles}
    by_url = {normalize_wa_url(article.url): article.wa_id for article in articles if article.url}
    for article in articles:
        links = extract_links(article.content)
        targets = {wa_id for wa_id in links.article_ids if wa_id in known}
        targets |= {by_url[url] for url in links.world_anvil_urls if url in by_url}
        targets.discard(article.wa_id)
        ordered = sorted(targets)
        if list(article.linked_ids or []) != ordered:
            article.linked_ids = ordered


def _sync_images(db: Session, source: WorldAnvilSource, *, full: bool) -> None:
    contents = db.scalars(select(WaArticle.content).where(WaArticle.removed_at.is_(None))).all()
    wanted: set[int] = set()
    for content in contents:
        wanted |= extract_image_ids(content)
    if not wanted:
        return
    existing = {image.wa_id: image for image in db.scalars(select(WaImage)).all()}
    now = utcnow()
    for image_id in sorted(wanted):
        image = existing.get(image_id)
        if image is not None and not full:
            continue
        try:
            detail = source.get_image(image_id, granularity=0)
        except WorldAnvilNotFoundError:
            detail = {}
        if image is None:
            image = WaImage(wa_id=image_id)
            db.add(image)
        url = detail.get("url")
        image.title = str(detail.get("title") or "")[:500] or None
        image.url = url if isinstance(url, str) else None
        image.player_visible = detail.get("state") == "public" and not detail.get(
            "subscribergroups"
        )
        image.raw = detail
        image.synced_at = now


def execute_sync(db: Session, source: WorldAnvilSource, world_id: str, run: SyncRun) -> SyncRun:
    run_id = run.id
    try:
        listing = list(source.iter_world_articles(world_id))
        existing = {article.wa_id: article for article in db.scalars(select(WaArticle)).all()}
        seen: set[str] = set()
        now = utcnow()
        for ref in listing:
            wa_id = str(ref.get("id") or "").lower()
            if not UUID_RE.match(wa_id):
                logger.warning("Skipping article with unexpected id format")
                continue
            seen.add(wa_id)
            updated_at = parse_wa_date(ref.get("updateDate"))
            article = existing.get(wa_id)
            if (
                not run.full
                and article is not None
                and article.removed_at is None
                and updated_at is not None
                and article.wa_updated_at == updated_at
            ):
                run.unchanged_count += 1
                continue
            detail = source.get_article(wa_id, granularity=2)
            if article is None:
                article = WaArticle(wa_id=wa_id)
                db.add(article)
                existing[wa_id] = article
                run.created_count += 1
            else:
                run.updated_count += 1
            _apply_article(article, detail, ref, now)
        for wa_id, article in existing.items():
            if wa_id not in seen and article.removed_at is None:
                article.removed_at = now
                article.player_visible = False
                article.visibility_reason = REMOVED_REASON
                run.removed_count += 1
        db.flush()
        _refresh_links(db)
        created_fiches = ensure_artefacts(db)
        _sync_images(db, source, full=run.full)
        sync_timeline(db, source, world_id, run)
        run.status = SyncStatus.SUCCESS
        run.finished_at = utcnow()
        db.commit()
        logger.info(
            "Sync %d finished: %d created, %d updated, %d unchanged, %d removed; "
            "chronology: %d created, %d updated, %d removed",
            run_id,
            run.created_count,
            run.updated_count,
            run.unchanged_count,
            run.removed_count,
            run.events_created,
            run.events_updated,
            run.events_removed,
        )
        if created_fiches:
            logger.info("Sync %d created %d artefact fiches", run_id, len(created_fiches))
    except Exception as exc:
        db.rollback()
        failed = db.get(SyncRun, run_id)
        if failed is None:
            raise
        failed.status = SyncStatus.FAILED
        failed.finished_at = utcnow()
        if isinstance(exc, WorldAnvilError):
            failed.error = str(exc)
        elif isinstance(exc, ChronologyError):
            failed.error = f"Invalid chronology data: {exc.code}"
        else:
            failed.error = f"Unexpected {type(exc).__name__}"
        db.commit()
        logger.exception("Sync %d failed", run_id)
        return failed
    return run


def sync_world(
    db: Session, source: WorldAnvilSource, world_id: str, *, full: bool, trigger: str
) -> SyncRun:
    run = start_run(db, full=full, trigger=trigger)
    return execute_sync(db, source, world_id, run)


def run_sync_job(run_id: int) -> None:
    settings = get_settings()
    with get_session_factory()() as db:
        run = db.get(SyncRun, run_id)
        if run is None:
            return
        try:
            client = WorldAnvilClient.from_settings(settings)
        except WorldAnvilError as exc:
            run.status = SyncStatus.FAILED
            run.finished_at = utcnow()
            run.error = str(exc)
            db.commit()
            return
        with client:
            execute_sync(db, client, settings.wa_world_id, run)
