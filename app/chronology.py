from dataclasses import dataclass
from enum import StrEnum

CURRENT_YEAR = 829
CREATION_YEAR = 0
CONQUEST_END_YEAR = 345
EMPIRE_END_YEAR = 749
FORGOTTEN_CENTURIES_START_YEAR = -500


class Importance(StrEnum):
    MAJOR = "majeur"
    NOTABLE = "notable"
    MINOR = "mineur"


class Origin(StrEnum):
    CANON = "canon"
    ADDITION = "ajout"


class EventVisibility(StrEnum):
    DM = "mj"
    PLAYERS = "joueurs"


class SessionDay(StrEnum):
    DAY_1 = "J1"
    DAY_2 = "J2"


IMPORTANCE_LABELS = {
    Importance.MAJOR: "Majeur",
    Importance.NOTABLE: "Notable",
    Importance.MINOR: "Mineur",
}
ORIGIN_LABELS = {Origin.CANON: "Canon", Origin.ADDITION: "Ajout"}
VISIBILITY_LABELS = {
    EventVisibility.DM: "Réservé au MJ",
    EventVisibility.PLAYERS: "Révélé aux joueurs",
}
IMPORTANCE_SIGNIFICANCE = {Importance.MAJOR: 5, Importance.NOTABLE: 3, Importance.MINOR: 1}


@dataclass(frozen=True)
class Era:
    slug: str
    name: str
    subtitle: str
    color: str
    start: int | None
    end: int | None

    def contains(self, year: int) -> bool:
        return (self.start is None or year >= self.start) and (self.end is None or year <= self.end)


ERAS: tuple[Era, ...] = (
    Era(
        "aubes-anciennes",
        "Les Aubes Anciennes",
        "Des royaumes dont seuls les érudits murmurent le nom.",
        "#4A5A8A",
        None,
        FORGOTTEN_CENTURIES_START_YEAR - 1,
    ),
    Era(
        "siecles-oublies",
        "Les Siècles Oubliés",
        "Ce que l'Empire a effacé, nul ne le réclame.",
        "#6F6A62",
        FORGOTTEN_CENTURIES_START_YEAR,
        CREATION_YEAR - 1,
    ),
    Era(
        "ascension",
        "L'Ascension (ère de la Conquête)",
        "D'une colline à l'horizon entier.",
        "#7A1F24",
        CREATION_YEAR,
        CONQUEST_END_YEAR,
    ),
    Era(
        "grand-midi",
        "Le Grand Midi",
        "Le jour est à son plus haut ; les ombres commencent à s'allonger.",
        "#C9A24B",
        CONQUEST_END_YEAR + 1,
        EMPIRE_END_YEAR - 1,
    ),
    Era(
        "tumultes",
        "Les Tumultes",
        "Trois nations pour un héritage brisé.",
        "#4F7A70",
        EMPIRE_END_YEAR,
        None,
    ),
)
ERAS_BY_SLUG = {era.slug: era for era in ERAS}


@dataclass(frozen=True)
class Milestone:
    year: int
    caption: str
    current: bool = False

    @property
    def label(self) -> str:
        return f"Aujourd'hui, {format_year(self.year)}" if self.current else format_year(self.year)


MILESTONES: tuple[Milestone, ...] = (
    Milestone(CREATION_YEAR, "Fondation de l'Empire"),
    Milestone(CONQUEST_END_YEAR, "Fin de la Conquête"),
    Milestone(EMPIRE_END_YEAR, "Révolte des Échos"),
    Milestone(CURRENT_YEAR, "Le temps présent", current=True),
)


def era_of(year: int) -> Era:
    return next(era for era in ERAS if era.contains(year))


def format_year(year: int) -> str:
    if year < 0:
        return f"{-year} av. É."
    return f"An {year}"


def duration_years(start: int, end: int | None) -> int:
    return 1 if end is None else end - start + 1


def format_span(start: int, end: int | None) -> str:
    if end is None or end == start:
        return format_year(start)
    return f"{format_year(start)} – {format_year(end)}"


def format_duration(start: int, end: int | None) -> str:
    years = duration_years(start, end)
    return "1 an" if years == 1 else f"{years} ans"
