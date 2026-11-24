from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.models import Character, CharacterArticle, Role, User, WaArticle


class CharacterError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def list_players(db: Session) -> list[User]:
    return list(
        db.scalars(select(User).where(User.role == Role.PLAYER).order_by(User.display_name)).all()
    )


def list_characters(db: Session) -> list[Character]:
    return list(
        db.scalars(
            select(Character)
            .options(selectinload(Character.player), selectinload(Character.main_article))
            .order_by(Character.name)
        ).all()
    )


def characters_of(db: Session, player: User) -> list[Character]:
    return list(
        db.scalars(
            select(Character)
            .options(selectinload(Character.main_article))
            .where(Character.player_id == player.id)
            .order_by(Character.name)
        ).all()
    )


def _validate(db: Session, name: str, player_id: int, main_article_id: str | None) -> str:
    name = name.strip()
    if not name or len(name) > 200:
        raise CharacterError("invalid_name")
    player = db.get(User, player_id)
    if player is None or player.role != Role.PLAYER:
        raise CharacterError("invalid_player")
    if main_article_id:
        article = db.get(WaArticle, main_article_id)
        if article is None or article.removed_at is not None:
            raise CharacterError("invalid_article")
    return name


def create_character(
    db: Session, *, name: str, player_id: int, main_article_id: str | None
) -> Character:
    name = _validate(db, name, player_id, main_article_id)
    character = Character(name=name, player_id=player_id, main_article_id=main_article_id or None)
    db.add(character)
    db.commit()
    return character


def update_character(
    db: Session,
    character: Character,
    *,
    name: str,
    player_id: int,
    main_article_id: str | None,
) -> None:
    character.name = _validate(db, name, player_id, main_article_id)
    character.player_id = player_id
    character.main_article_id = main_article_id or None
    db.commit()


def delete_character(db: Session, character: Character) -> None:
    db.delete(character)
    db.commit()


def extra_article_ids(db: Session, character: Character) -> set[str]:
    return set(
        db.scalars(
            select(CharacterArticle.article_id).where(CharacterArticle.character_id == character.id)
        ).all()
    )


def set_extra_article(db: Session, character: Character, wa_id: str, shared: bool) -> None:
    article = db.get(WaArticle, wa_id)
    if article is None:
        raise CharacterError("invalid_article")
    if shared:
        if wa_id not in extra_article_ids(db, character):
            db.add(CharacterArticle(character_id=character.id, article_id=wa_id))
    else:
        db.execute(
            delete(CharacterArticle).where(
                CharacterArticle.character_id == character.id,
                CharacterArticle.article_id == wa_id,
            )
        )
    db.commit()
