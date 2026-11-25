import re
import uuid
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from app.models import Character, CharacterArticle, Role, User, WaArticle, utcnow
from app.security import hash_password
from app.worldanvil.bbcode import to_plain_text

PASSWORD = "un-mot-de-passe-solide"
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def new_id() -> str:
    return str(uuid.uuid4())


def make_user(
    db: Session,
    login: str,
    role: Role = Role.PLAYER,
    *,
    password: str | None = PASSWORD,
    active: bool = True,
) -> User:
    user = User(
        display_name=login.capitalize(),
        login=login,
        role=role,
        password_hash=hash_password(password) if password else None,
        is_active=active,
    )
    db.add(user)
    db.commit()
    return user


def make_article(
    db: Session,
    title: str,
    *,
    content: str = "",
    template_type: str = "person",
    player_visible: bool = True,
    url: str | None = None,
    removed: bool = False,
    raw: dict[str, Any] | None = None,
    linked_ids: list[str] | None = None,
) -> WaArticle:
    article = WaArticle(
        wa_id=new_id(),
        title=title,
        template_type=template_type,
        entity_class=template_type.capitalize(),
        state="public",
        content=content,
        content_text=to_plain_text(content),
        raw=raw or {"title": title, "content": content},
        url=url,
        player_visible=player_visible,
        visibility_reason=None if player_visible else "Article privé sur World Anvil",
        linked_ids=linked_ids or [],
        synced_at=utcnow(),
        removed_at=utcnow() if removed else None,
    )
    db.add(article)
    db.commit()
    return article


def make_character(
    db: Session,
    name: str,
    player: User,
    main: WaArticle | None = None,
    extras: list[WaArticle] | None = None,
) -> Character:
    character = Character(
        name=name, player_id=player.id, main_article_id=main.wa_id if main else None
    )
    db.add(character)
    db.flush()
    for article in extras or []:
        db.add(CharacterArticle(character_id=character.id, article_id=article.wa_id))
    db.commit()
    return character


def csrf_token(client: TestClient, path: str = "/connexion/mj") -> str:
    response = client.get(path)
    match = CSRF_RE.search(response.text)
    assert match, f"No CSRF token found on {path}"
    return match.group(1)


def login(client: TestClient, user_login: str, password: str = PASSWORD) -> Response:
    token = csrf_token(client, "/connexion/mj")
    return client.post(
        "/connexion",
        data={"login": user_login, "password": password, "csrf_token": token},
        follow_redirects=False,
    )


def post_form(client: TestClient, path: str, data: dict[str, str], csrf_page: str) -> Response:
    token = csrf_token(client, csrf_page)
    return client.post(path, data={**data, "csrf_token": token}, follow_redirects=False)


BARE_NUMBER_RE = re.compile(
    r"<span(?![^>]*(?:visually-hidden|resource__uses))[^>]*>\s*\d+\s*</span>"
)
COUNT_BADGE_CLASSES = ("tabs__count", "tree__count", "frise__count", "place-focus__level")


def bare_numbers(page: str) -> list[str]:
    found = BARE_NUMBER_RE.findall(page)
    return found + [name for name in COUNT_BADGE_CLASSES if name in page]
