from dataclasses import dataclass


@dataclass(frozen=True)
class Credit:
    key: str
    name: str
    publisher: str
    licence: str
    licence_url: str
    statement: str
    markers: tuple[str, ...]

    @property
    def anchor(self) -> str:
        return f"credit-{self.key}"


CATALOGUE_NAME = "Open5e"
CATALOGUE_URL = "https://open5e.com"

CREDITS: tuple[Credit, ...] = (
    Credit(
        key="srd-5-1",
        name="System Reference Document 5.1 (SRD 5.1)",
        publisher="Wizards of the Coast",
        licence="Creative Commons Attribution 4.0 International (CC BY 4.0)",
        licence_url="https://creativecommons.org/licenses/by/4.0/legalcode",
        statement=(
            "This work includes material taken from the System Reference Document 5.1 "
            "(“SRD 5.1”) by Wizards of the Coast LLC and available at "
            "https://dnd.wizards.com/resources/systems-reference-document. The SRD 5.1 is "
            "licensed under the Creative Commons Attribution 4.0 International License "
            "available at https://creativecommons.org/licenses/by/4.0/legalcode."
        ),
        markers=("srd 5.1", "srd5.1", "5e core rules"),
    ),
    Credit(
        key="a5e",
        name="Level Up Advanced 5th Edition, Monstrous Menagerie",
        publisher="EN Publishing",
        licence="",
        licence_url="",
        statement="",
        markers=("level up", "a5e", "monstrous menagerie"),
    ),
    Credit(
        key="monsters-of-the-multiverse",
        name="Mordenkainen Presents: Monsters of the Multiverse ; Volo's Guide to Monsters",
        publisher="Wizards of the Coast",
        licence="",
        licence_url="",
        statement="",
        markers=("monsters of the multiverse", "volo's guide"),
    ),
    Credit(
        key="creature-codex",
        name="Creature Codex",
        publisher="Kobold Press",
        licence="",
        licence_url="",
        statement="",
        markers=("creature codex",),
    ),
    Credit(
        key="black-flag",
        name="Black Flag SRD",
        publisher="Kobold Press",
        licence="",
        licence_url="",
        statement="",
        markers=("black flag",),
    ),
)


def credit_for(source: str | None) -> Credit | None:
    text = (source or "").casefold()
    if not text:
        return None
    return next(
        (credit for credit in CREDITS if any(marker in text for marker in credit.markers)), None
    )
