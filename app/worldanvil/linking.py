import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

MIN_NAME_LENGTH = 3
APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "′": "'"})
LEADING_ARTICLE_RE = re.compile(r"^(?:(le|la|les) |(l)')")


@dataclass(frozen=True)
class Target:
    wa_id: str
    title: str
    kind: str
    label: str


@dataclass(frozen=True)
class Name:
    text: str
    wa_id: str
    is_title: bool = True


@dataclass(frozen=True)
class Found:
    start: int
    end: int
    wa_id: str | None


def fold(text: str) -> tuple[str, list[int]]:
    """Case-fold, strip accents and collapse whitespace, keeping a map to the source."""
    chars: list[str] = []
    origins: list[int] = []
    previous_space = False
    for index, char in enumerate(text.translate(APOSTROPHES)):
        if char.isspace():
            if previous_space:
                if char == "\n":
                    chars[-1] = "\n"
                continue
            chars.append("\n" if char == "\n" else " ")
            origins.append(index)
            previous_space = True
            continue
        previous_space = False
        for part in unicodedata.normalize("NFKD", char):
            if unicodedata.combining(part):
                continue
            for folded in part.casefold():
                chars.append(folded)
                origins.append(index)
    return "".join(chars), origins


def name_key(text: str) -> tuple[str, str]:
    """Return the folded name without its leading article, and that article."""
    folded = fold(text)[0].replace("\n", " ").strip()
    match = LEADING_ARTICLE_RE.match(folded)
    if match is None:
        return folded, ""
    return folded[match.end() :].strip(), match.group(0)


@dataclass
class Matcher:
    targets: dict[str, Target]
    keys: dict[str, str | None]
    articles: dict[str, str]
    conflicts: dict[str, tuple[str, ...]] = field(default_factory=dict)
    pattern: re.Pattern[str] | None = None

    @classmethod
    def build(
        cls,
        targets: Iterable[Target],
        names: Iterable[Name],
        exclusions: Iterable[str] = (),
    ) -> "Matcher":
        by_id = {target.wa_id: target for target in targets}
        owners: dict[str, set[str]] = defaultdict(set)
        articles: dict[str, str] = {}
        for name in names:
            if name.wa_id not in by_id:
                continue
            key, article = name_key(name.text)
            if len(key) < MIN_NAME_LENGTH:
                continue
            owners[key].add(name.wa_id)
            if name.is_title and article:
                articles[key] = article
        keys: dict[str, str | None] = {}
        conflicts: dict[str, tuple[str, ...]] = {}
        for key, wa_ids in owners.items():
            if len(wa_ids) > 1:
                conflicts[key] = tuple(sorted(wa_ids))
                keys[key] = None
            else:
                keys[key] = next(iter(wa_ids))
        for exclusion in exclusions:
            key = name_key(exclusion)[0]
            if key:
                keys[key] = None
                articles.pop(key, None)
                conflicts.pop(key, None)
        alternatives = sorted(keys, key=lambda key: (-len(key), key))
        pattern = (
            re.compile(
                r"(?<![\w-])(?:" + "|".join(re.escape(key) for key in alternatives) + r")(?![\w-])"
            )
            if alternatives
            else None
        )
        return cls(by_id, keys, articles, conflicts, pattern)

    def find(self, text: str) -> list[Found]:
        if self.pattern is None or not text:
            return []
        folded, origins = fold(text)
        found: list[Found] = []
        for match in self.pattern.finditer(folded):
            key = match.group(0)
            start = match.start()
            article = self.articles.get(key, "")
            if article and folded[max(0, start - len(article)) : start] == article:
                before = start - len(article)
                if before == 0 or not _joins(folded[before - 1]):
                    start = before
            found.append(Found(origins[start], origins[match.end() - 1] + 1, self.keys[key]))
        return found


def _joins(char: str) -> bool:
    return char.isalnum() or char in "_-"
