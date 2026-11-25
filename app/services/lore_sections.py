from dataclasses import dataclass
from enum import StrEnum

from markupsafe import Markup

from app.models import LoreEntry, WaArticle
from app.presentation import is_title_heading
from app.services.artefact_split import QUICK_SHEET, STATISTICS_PREFIX, _sections, fold
from app.services.structure_excerpts import FORBIDDEN_SECTIONS, AnchorStatus, find_excerpt
from app.worldanvil.bbcode import RenderContext, render_bbcode

DM_SECTION_TITLES = frozenset(fold(name) for name in FORBIDDEN_SECTIONS)


class TextStatus(StrEnum):
    LIVE = "live"
    STORED = "stored"
    MISSING = "missing"
    FORBIDDEN = "forbidden"
    EMPTY = "empty"


@dataclass(frozen=True)
class SectionText:
    status: TextStatus
    html: Markup

    @property
    def is_live(self) -> bool:
        return self.status == TextStatus.LIVE


def _article(entry: LoreEntry) -> WaArticle | None:
    article = entry.source
    if article is None or article.removed_at is not None:
        return None
    return article


def section_source(
    article: WaArticle, section: str | None, *, dm_note: bool = False
) -> tuple[TextStatus, str]:
    if not (section or "").strip():
        return (
            (TextStatus.LIVE, article.content)
            if article.content.strip()
            else (
                TextStatus.MISSING,
                "",
            )
        )
    forbidden = () if dm_note else FORBIDDEN_SECTIONS
    excerpt = find_excerpt(article.content, section or "", forbidden=forbidden, blocks=True)
    if excerpt.status == AnchorStatus.FOUND and excerpt.source.strip():
        return TextStatus.LIVE, excerpt.source
    if excerpt.status == AnchorStatus.FORBIDDEN:
        return TextStatus.FORBIDDEN, ""
    return TextStatus.MISSING, ""


def entry_text(entry: LoreEntry, context: RenderContext) -> SectionText:
    article = _article(entry)
    status = TextStatus.MISSING
    if article is not None and entry.source_article_id:
        status, source = section_source(
            article, entry.source_section, dm_note=entry.placement == "dm_note"
        )
        if status == TextStatus.LIVE:
            return SectionText(status, render_bbcode(source, context))
    if entry.text.strip():
        return SectionText(TextStatus.STORED, render_bbcode(entry.text, context))
    if not entry.source_article_id:
        status = TextStatus.EMPTY
    return SectionText(status, Markup(""))


def intro_html(article: WaArticle | None, sections: str, context: RenderContext) -> Markup:
    if article is None or article.removed_at is not None:
        return Markup("")
    parts = []
    for name in (line.strip() for line in sections.splitlines()):
        if not name:
            continue
        status, source = section_source(article, name)
        if status == TextStatus.LIVE:
            parts.append(render_bbcode(source, context))
    return Markup("").join(parts)


@dataclass(frozen=True)
class SourceSection:
    title: str
    level: int


def article_sections(article: WaArticle) -> list[SourceSection]:
    sections = _sections(article.content or "")
    return [
        SourceSection(section.title, section.level)
        for section in sections
        if section.title and not is_title_heading(section.title, article.title)
    ]


def _skipped(title: str) -> bool:
    folded = fold(title)
    return (
        folded == QUICK_SHEET
        or folded.startswith(STATISTICS_PREFIX)
        or any(folded.startswith(name) for name in DM_SECTION_TITLES)
    )


def narrative_sections(article: WaArticle) -> list[str]:
    content = article.content or ""
    sections = [
        section
        for section in _sections(content)
        if section.title and not is_title_heading(section.title, article.title)
    ]
    kept = []
    skip_level: int | None = None
    for section in sections:
        if skip_level is not None and section.level > skip_level:
            continue
        skip_level = None
        if _skipped(section.title):
            skip_level = section.level
            continue
        kept.append(section)
    if not kept:
        return []
    top = min(section.level for section in kept)
    titles = []
    for index, section in enumerate(kept):
        if section.level != top:
            continue
        children = []
        for child in kept[index + 1 :]:
            if child.level <= top:
                break
            children.append(child)
        body = content[section.body_start : section.end].strip()
        if children and not body:
            child_level = min(child.level for child in children)
            titles.extend(child.title for child in children if child.level == child_level)
        else:
            titles.append(section.title)
    return titles


def fiche_placement(title: str) -> str | None:
    folded = fold(title)
    if folded == "description":
        return "physical"
    if any(folded.startswith(name) for name in DM_SECTION_TITLES):
        return "dm_note"
    return None
