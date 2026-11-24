from typing import Any

import pytest

from app.worldanvil.visibility import assess_player_visibility


def article(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "state": "public",
        "isDraft": False,
        "isWip": True,
        "subscribergroups": [],
        "secrets": [],
        "isLocked": False,
        "passcodecta": None,
        "entityClass": "Person",
        "templateType": "person",
        "content": "[p]Texte public[/p]",
    }
    base.update(overrides)
    return base


def test_public_article_is_visible_even_when_work_in_progress() -> None:
    result = assess_player_visibility(article())
    assert result.player_visible
    assert result.reason is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": "private"},
        {"state": None},
        {"isDraft": True},
        {"isDraft": None},
        {"subscribergroups": [{"id": "g1"}]},
        {"secrets": [{"id": "s1", "title": "Le traître"}]},
        {"isLocked": True},
        {"passcodecta": "Entrez le code"},
        {"entityClass": "Plot", "templateType": "plot"},
        {"content": "[p]x[/p][secret:abc]"},
        {"content": "[subcontainer:g]x[/subcontainer]"},
        {"content": {"unexpected": True}},
    ],
)
def test_doubtful_articles_are_hidden(overrides: dict[str, Any]) -> None:
    result = assess_player_visibility(article(**overrides))
    assert not result.player_visible
    assert result.reason
