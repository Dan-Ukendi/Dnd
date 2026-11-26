import html
import re
from collections.abc import Iterable
from dataclasses import dataclass

from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.credits import Credit, credit_for
from app.models import FicheSection, Npc, NpcRelation, Place, Position, StatBlock
from app.services.structures import KIND_LABELS, Link, Member, Viewer, _World
from app.worldanvil.bbcode import RenderContext, render_plain_text, render_text_blocks

ABILITY_LABELS = ("FOR", "DEX", "CON", "INT", "SAG", "CHA")
CATEGORY_LABELS = {"lore": "Lore", "character": "Character", "encounter": "Rencontre campagne"}
ATTACK_RE = re.compile(
    r"^((?:Melee or Ranged|Melee|Ranged) (?:Weapon|Spell) Attack)(\s*[:,])", re.IGNORECASE
)
HIT_RE = re.compile(r"\bHit:")


@dataclass(frozen=True)
class SectionView:
    title: str
    html: Markup
    visibility: str


@dataclass(frozen=True)
class StatEntry:
    name: str
    html: Markup
    spells: list[tuple[str, str]]


@dataclass(frozen=True)
class StatBlockView:
    key: str
    name: str
    subtitle: str
    armor: str
    hit_points: str
    speed: str
    abilities: list[tuple[str, int, str]]
    properties: list[tuple[str, str]]
    traits: list[StatEntry]
    groups: list[tuple[str, list[StatEntry]]]
    source: str
    credit: Credit | None


@dataclass(frozen=True)
class PositionView:
    member: Member
    scope: str
    site: Link | None
    organisation: Link | None
    reports_to: Link | None


@dataclass(frozen=True)
class ChildPlace:
    name: str
    href: str | None
    kind_label: str
    description: Markup
    visibility: str


@dataclass(frozen=True)
class PlacePage:
    key: str
    name: str
    kind_label: str
    location: str
    parent: Link | None
    owner: Link | None
    article: Link | None
    description: Markup
    sections: list[SectionView]
    children: list[ChildPlace]
    positions: list[PositionView]
    crumbs: list[Link]
    gm_notes: Markup
    visibility: str


@dataclass(frozen=True)
class RelationView:
    target: Link
    label: str
    visibility: str


@dataclass(frozen=True)
class NpcPage:
    key: str
    name: str
    title: str
    category: str
    category_label: str
    facts: list[tuple[str, Link]]
    description: Markup
    sections: list[SectionView]
    positions: list[PositionView]
    relations: list[RelationView]
    gm_notes: Markup
    stat_block: StatBlockView | None
    visibility: str


@dataclass(frozen=True)
class RolePage:
    key: str
    name: str
    description: Markup
    headcount: str
    facts: list[tuple[str, Link]]
    sections: list[SectionView]
    positions: list[PositionView]
    gm_notes: Markup
    stat_block: StatBlockView | None
    visibility: str


def _modifier(score: int) -> str:
    value = (score - 10) // 2
    return f"+{value}" if value >= 0 else f"−{abs(value)}"


def _entry_html(text: str) -> Markup:
    if not text:
        return Markup("")
    body = text[:1].upper() + text[1:]
    escaped = html.escape(body, quote=False)
    escaped = ATTACK_RE.sub(lambda m: f"<em>{m.group(1)}</em>{m.group(2)}", escaped, count=1)
    escaped = HIT_RE.sub("<em>Hit:</em>", escaped)
    return Markup(escaped)  # noqa: S704


def _entries(items: Iterable[dict]) -> list[StatEntry]:
    return [
        StatEntry(
            item.get("name", ""),
            _entry_html(item.get("text", "")),
            [(label, names) for label, names in item.get("spells", [])],
        )
        for item in items
    ]


def stat_block_view(block: StatBlock | None) -> StatBlockView | None:
    if block is None:
        return None
    subtitle = " ".join(part for part in (block.size, block.creature_type) if part)
    if block.alignment:
        subtitle = f"{subtitle}, {block.alignment}" if subtitle else block.alignment
    armor = "" if block.armor_class is None else str(block.armor_class)
    if armor and block.armor_desc:
        armor = f"{armor} ({block.armor_desc})"
    hit_points = "" if block.hit_points is None else str(block.hit_points)
    if hit_points and block.hit_dice:
        hit_points = f"{hit_points} ({block.hit_dice})"
    scores = block.abilities if len(block.abilities or []) == 6 else []
    properties = [
        (label, value)
        for label, value in (
            ("Jets de sauvegarde", block.saves),
            ("Compétences", block.skills),
            ("Vulnérabilités", block.vulnerabilities),
            ("Résistances", block.resistances),
            ("Immunités", block.immunities),
            ("Immunités aux états", block.condition_immunities),
            ("Sens", block.senses),
            ("Langues", block.languages),
            ("Puissance", f"FP {block.challenge}" if block.challenge else ""),
        )
        if value
    ]
    groups = [
        (label, _entries(items))
        for label, items in (
            ("Actions", block.actions),
            ("Actions bonus", block.bonus_actions),
            ("Réactions", block.reactions),
        )
        if items
    ]
    return StatBlockView(
        key=block.key,
        name=block.name,
        subtitle=subtitle,
        armor=armor,
        hit_points=hit_points,
        speed=block.speed,
        abilities=[
            (label, score, _modifier(score))
            for label, score in zip(ABILITY_LABELS, scores, strict=True)
        ],
        properties=properties,
        traits=_entries(block.traits or []),
        groups=groups,
        source=block.source,
        credit=credit_for(block.source),
    )


class FicheBuilder:
    def __init__(self, db: Session, viewer: Viewer, context: RenderContext) -> None:
        self.db = db
        self.viewer = viewer
        self.context = context
        self.world = _World(db, viewer, context)

    def _sections(self, column: str, owner_id: int) -> list[SectionView]:
        rows = self.db.scalars(
            select(FicheSection)
            .where(getattr(FicheSection, column) == owner_id)
            .order_by(FicheSection.position, FicheSection.id)
        )
        return [
            SectionView(
                row.title,
                render_text_blocks(row.text, self.context),
                row.visibility if self.viewer.dm else "",
            )
            for row in rows
            if self.viewer.dm or row.visibility == "joueurs"
        ]

    def _gm(self, text: str) -> Markup:
        if not self.viewer.dm:
            return Markup("")
        return render_plain_text(text, self.context, paragraphs=True)

    def _visibility(self, value: str) -> str:
        return value if self.viewer.dm else ""

    def _place_link(self, place: Place | None) -> Link | None:
        if place is None or not self.world.place_visible(place):
            return None
        return Link(place.name, self.world.place_href(place), place.key)

    def _position_views(self, positions: Iterable[Position]) -> list[PositionView]:
        views = []
        world = self.world
        for position in sorted(positions, key=lambda row: (row.position, row.id)):
            if not world.position_visible(position):
                continue
            superior = world.positions.get(position.superior_id) if position.superior_id else None
            reports_to = None
            if superior is not None and world.position_visible(superior):
                boss = world.member(superior)
                reports_to = Link(boss.name, boss.href, boss.key)
            views.append(
                PositionView(
                    member=world.member(position),
                    scope=position.scope,
                    site=self._place_link(
                        world.places.get(position.site_id) if position.site_id else None
                    ),
                    organisation=world.link(position.organisation_id),
                    reports_to=reports_to,
                )
            )
        return views

    def place(self, key: str) -> PlacePage | None:
        world = self.world
        place = next((row for row in world.places.values() if row.key == key), None)
        if place is None or not world.place_reachable(place):
            return None
        chain: list[Place] = []
        current = world.places.get(place.parent_id) if place.parent_id else None
        seen = {place.id}
        while current is not None and current.id not in seen:
            seen.add(current.id)
            chain.insert(0, current)
            current = world.places.get(current.parent_id) if current.parent_id else None
        crumbs = [Link(row.name, world.place_href(row), row.key) for row in chain]
        children = [
            ChildPlace(
                child.name,
                world.place_href(child),
                KIND_LABELS.get(child.kind, child.kind),
                render_text_blocks(child.description, self.context),
                self._visibility(child.visibility),
            )
            for child in world.children.get(place.id, [])
            if world.place_visible(child)
        ]
        positions = [
            row
            for row in world.positions.values()
            if row.site_id == place.id or (row.site_id is None and row.place_id == place.id)
        ]
        parent = world.places.get(place.parent_id) if place.parent_id else None
        return PlacePage(
            key=place.key,
            name=place.name,
            kind_label=KIND_LABELS.get(place.kind, place.kind),
            location=place.location,
            parent=self._place_link(parent),
            owner=world.link(place.owner_id),
            article=world.link(place.wa_article_id),
            description=render_text_blocks(place.description, self.context),
            sections=self._sections("place_id", place.id),
            children=children,
            positions=self._position_views(positions),
            crumbs=crumbs,
            gm_notes=self._gm(place.gm_notes),
            visibility=self._visibility(place.visibility),
        )

    def npc(self, key: str) -> NpcPage | None:
        world = self.world
        npc = next((row for row in world.npcs.values() if row.key == key), None)
        if npc is None or not world.npc_visible(npc):
            return None
        facts: list[tuple[str, Link]] = [
            (label, Link(value))
            for label, value in (
                ("Genre", npc.gender),
                ("Race", npc.race),
                ("Âge", npc.age),
                ("Statut", npc.standing),
            )
            if value
        ]
        organisation = world.link(npc.organisation_id)
        if organisation is not None:
            facts.append(("Organisation", organisation))
        place = self._place_link(world.places.get(npc.place_id) if npc.place_id else None)
        if place is not None:
            facts.append(("Lieu", place))
        article = world.link(npc.wa_article_id)
        if article is not None:
            facts.append(("Fiche", article))
        block = self.db.get(StatBlock, npc.stat_block_id) if npc.stat_block_id else None
        return NpcPage(
            key=npc.key,
            name=npc.name,
            title=npc.title,
            category=npc.category,
            category_label=CATEGORY_LABELS.get(npc.category, npc.category),
            facts=facts,
            description=render_text_blocks(npc.description, self.context),
            sections=self._sections("npc_id", npc.id),
            positions=self._position_views(
                row for row in world.positions.values() if row.npc_id == npc.id
            ),
            relations=self._relations(npc),
            gm_notes=self._gm(npc.gm_notes),
            stat_block=stat_block_view(block) if self.viewer.dm else None,
            visibility=self._visibility(npc.visibility),
        )

    def _relations(self, npc: Npc) -> list[RelationView]:
        world = self.world
        rows = self.db.scalars(
            select(NpcRelation)
            .where((NpcRelation.npc_id == npc.id) | (NpcRelation.target_npc_id == npc.id))
            .order_by(NpcRelation.position, NpcRelation.id)
        )
        views = []
        for row in rows:
            if not self.viewer.dm and row.visibility != "joueurs":
                continue
            if row.npc_id == npc.id and row.target_npc_id is not None:
                other = world.npcs.get(row.target_npc_id)
                if other is None or not world.npc_visible(other):
                    continue
                target = Link(other.name, world.npc_href(other), other.key)
                label = row.label
            elif row.npc_id == npc.id:
                link = world.link(row.target_article_id)
                if link is None:
                    continue
                target, label = link, row.label
            else:
                other = world.npcs.get(row.npc_id)
                if other is None or not world.npc_visible(other):
                    continue
                target = Link(other.name, world.npc_href(other), other.key)
                label = row.reverse_label
            views.append(RelationView(target, label, self._visibility(row.visibility)))
        return views

    def role(self, key: str) -> RolePage | None:
        world = self.world
        role = next((row for row in world.roles.values() if row.key == key), None)
        if role is None or not world.role_visible(role):
            return None
        facts: list[tuple[str, Link]] = []
        organisation = world.link(role.organisation_id)
        if organisation is not None:
            facts.append(("Organisation", organisation))
        place = self._place_link(world.places.get(role.place_id) if role.place_id else None)
        if place is not None:
            facts.append(("Lieu", place))
        if role.headcount:
            facts.append(("Effectif", Link(role.headcount)))
        block = self.db.get(StatBlock, role.stat_block_id) if role.stat_block_id else None
        return RolePage(
            key=role.key,
            name=role.name,
            description=render_text_blocks(role.description, self.context),
            headcount=role.headcount,
            facts=facts,
            sections=self._sections("npc_role_id", role.id),
            positions=self._position_views(
                row for row in world.positions.values() if row.npc_role_id == role.id
            ),
            gm_notes=self._gm(role.gm_notes),
            stat_block=stat_block_view(block) if self.viewer.dm else None,
            visibility=self._visibility(role.visibility),
        )


def article_npc_sheet(db: Session, wa_id: str, context: RenderContext) -> NpcPage | None:
    npc = db.scalar(select(Npc).where(Npc.wa_article_id == wa_id))
    if npc is None:
        return None
    return FicheBuilder(db, Viewer(dm=True), context).npc(npc.key)
