from dataclasses import dataclass
from typing import Any

from app.worldanvil.bbcode import restricted_tags_in

GM_ONLY_ENTITY_CLASSES = frozenset({"plot"})


@dataclass(frozen=True)
class Visibility:
    player_visible: bool
    reason: str | None


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "null", "none"}
    return bool(value)


def assess_player_visibility(article: dict[str, Any]) -> Visibility:
    if article.get("state") != "public":
        return Visibility(False, "Article privé sur World Anvil")
    if article.get("isDraft") is not False:
        return Visibility(False, "Brouillon ou statut de publication inconnu")
    if article.get("subscribergroups"):
        return Visibility(False, "Réservé à des groupes d'abonnés")
    if article.get("secrets"):
        return Visibility(False, "Des secrets sont rattachés à l'article")
    if _truthy(article.get("isLocked")):
        return Visibility(False, "Article verrouillé sur World Anvil")
    if _truthy(article.get("passcodecta")):
        return Visibility(False, "Article protégé par un code d'accès")
    entity_class = str(article.get("entityClass") or article.get("templateType") or "").lower()
    if entity_class in GM_ONLY_ENTITY_CLASSES:
        return Visibility(False, "Article d'intrigue (Plot), réservé au MJ")
    content = article.get("content")
    if content is not None and not isinstance(content, str):
        return Visibility(False, "Contenu dans un format inattendu")
    if restricted_tags_in(content):
        return Visibility(False, "Le texte contient des balises réservées (secrets, abonnés)")
    return Visibility(True, None)
