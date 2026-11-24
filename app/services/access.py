from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Character, CharacterArticle, User, WaArticle, WaImage
from app.worldanvil.bbcode import ImageRef, RenderContext, normalize_wa_url


def assigned_article_ids(db: Session, player: User) -> set[str]:
    main = select(Character.main_article_id).where(
        Character.player_id == player.id, Character.main_article_id.is_not(None)
    )
    extra = (
        select(CharacterArticle.article_id)
        .join(Character, Character.id == CharacterArticle.character_id)
        .where(Character.player_id == player.id)
    )
    return {wa_id for wa_id in db.scalars(main.union(extra)).all() if wa_id}


def accessible_article_ids(db: Session, player: User) -> set[str]:
    if not player.is_active:
        return set()
    assigned = assigned_article_ids(db, player)
    if not assigned:
        return set()
    return set(
        db.scalars(
            select(WaArticle.wa_id).where(
                WaArticle.wa_id.in_(assigned),
                WaArticle.player_visible.is_(True),
                WaArticle.removed_at.is_(None),
            )
        ).all()
    )


def get_player_article(db: Session, player: User, wa_id: str) -> WaArticle | None:
    if wa_id not in accessible_article_ids(db, player):
        return None
    return db.get(WaArticle, wa_id)


def player_shared_articles(db: Session, player: User) -> list[WaArticle]:
    ids = accessible_article_ids(db, player)
    main_ids = set(
        db.scalars(
            select(Character.main_article_id).where(
                Character.player_id == player.id, Character.main_article_id.is_not(None)
            )
        ).all()
    )
    shared = ids - main_ids
    if not shared:
        return []
    return list(
        db.scalars(
            select(WaArticle).where(WaArticle.wa_id.in_(shared)).order_by(WaArticle.title)
        ).all()
    )


def _image_resolver(db: Session, *, player_mode: bool) -> dict[int, ImageRef]:
    query = select(WaImage).where(WaImage.url.is_not(None))
    if player_mode:
        query = query.where(WaImage.player_visible.is_(True))
    return {
        image.wa_id: ImageRef(image.url or "", image.title or "") for image in db.scalars(query)
    }


def dm_render_context(db: Session) -> RenderContext:
    rows = db.execute(
        select(WaArticle.wa_id, WaArticle.url).where(WaArticle.removed_at.is_(None))
    ).all()
    known = {row.wa_id for row in rows}
    by_url = {normalize_wa_url(row.url): row.wa_id for row in rows if row.url}
    images = _image_resolver(db, player_mode=False)
    return RenderContext(
        resolve_article=lambda wa_id: f"/mj/articles/{wa_id}" if wa_id in known else None,
        resolve_article_url=lambda url: f"/mj/articles/{by_url[url]}" if url in by_url else None,
        resolve_image=images.get,
        player_mode=False,
    )


def player_render_context(db: Session, player: User) -> RenderContext:
    accessible = accessible_article_ids(db, player)
    rows = db.execute(
        select(WaArticle.wa_id, WaArticle.url).where(WaArticle.wa_id.in_(accessible))
    ).all()
    by_url = {normalize_wa_url(row.url): row.wa_id for row in rows if row.url}
    images = _image_resolver(db, player_mode=True)
    return RenderContext(
        resolve_article=lambda wa_id: f"/joueur/articles/{wa_id}" if wa_id in accessible else None,
        resolve_article_url=lambda url: (
            f"/joueur/articles/{by_url[url]}" if url in by_url else None
        ),
        resolve_image=images.get,
        player_mode=True,
    )
