from collections import defaultdict
from dataclasses import dataclass, field

from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    BUILDING_TYPES,
    WORLD_CATEGORIES,
    FicheSection,
    Npc,
    NpcRole,
    Place,
    Position,
    WaArticle,
)
from app.sections import section_of
from app.services.structure_excerpts import AnchorStatus, find_excerpt
from app.worldanvil.bbcode import (
    RenderContext,
    render_bbcode,
    render_plain_text,
    render_text_blocks,
)

ORGANISATION = "organisation"
MONDE = "monde"
STRUCTURE_SECTIONS = frozenset({ORGANISATION, MONDE})
KIND_LABELS = {
    "territoire": "Territoire",
    "region": "Région",
    "ville": "Ville",
    "quartier": "Quartier",
    "batiment": "Bâtiment",
    "terre": "Terre",
    "niveau": "Niveau",
    "piece": "Pièce",
    "lieu": "Lieu",
}
UNNAMED = "Titulaire non nommé"
ORGANISATION_DEPTH = 2
ARCHITECTURE = "architecture"
CITY = "ville"
CITY_CATEGORY = WORLD_CATEGORIES[1]
BUILDING_CATEGORY = WORLD_CATEGORIES[2]
OTHER_INHABITANTS = "Autres habitants"
BRANCH, DWELLING = BUILDING_TYPES
BUILDING_TYPE_LABELS = {BRANCH: "Branche d'une organisation", DWELLING: "Habitation"}
PERSON_TEMPLATE = "person"
HOUSEHOLD_GROUPS = (
    ("owner", "Propriétaire", "Propriétaires"),
    ("resident", "Habitants", "Habitants"),
    ("employee", "Employés", "Employés"),
    ("", "Personnes présentes", "Personnes présentes"),
)


@dataclass(frozen=True)
class Viewer:
    dm: bool
    accessible: frozenset[str] = frozenset()

    @property
    def base(self) -> str:
        return "/mj/articles" if self.dm else "/joueur/articles"

    def can_read(self, article: WaArticle | None) -> bool:
        if article is None:
            return False
        if self.dm:
            return article.removed_at is None
        return article.wa_id in self.accessible

    def href(self, article: WaArticle | None) -> str | None:
        return f"{self.base}/{article.wa_id}" if article and self.can_read(article) else None

    @property
    def root(self) -> str:
        return "/mj" if self.dm else "/joueur"


@dataclass
class Link:
    label: str
    href: str | None = None
    key: str = ""


@dataclass
class Member:
    key: str
    name: str
    title: str
    href: str | None
    article: WaArticle | None
    is_group: bool
    organisation: Link | None = None
    gm_notes: str = ""
    visibility: str = ""
    place: Link | None = None

    @property
    def initial(self) -> str:
        return (self.name.strip()[:1] or "?").upper()


@dataclass(frozen=True)
class PlaceSection:
    title: str
    html: Markup
    visibility: str


@dataclass
class PlaceCard:
    key: str
    name: str
    kind: str
    kind_label: str
    href: str | None
    location: str
    parent: Link | None
    excerpt: Markup
    description: Markup
    source: Link | None
    owner: Link | None
    organisations: list[Link]
    members: list[Member]
    sub_places: list[Link] = field(default_factory=list)
    children: list["PlaceCard"] = field(default_factory=list)
    explore_href: str | None = None
    page_href: str | None = None
    gm_notes: str = ""
    visibility: str = ""
    sections: list[PlaceSection] = field(default_factory=list)

    @property
    def descendant_count(self) -> int:
        return sum(1 + child.descendant_count for child in self.children)


@dataclass
class TreeNode:
    key: str
    member: Member
    scope: str
    site: str
    reports_to: str
    source: Link | None
    children: list["TreeNode"] = field(default_factory=list)
    site_href: str | None = None
    hidden: int = 0


@dataclass
class Territory:
    mode: str
    cards: list[PlaceCard]
    focus: PlaceCard | None
    crumbs: list[Link]
    total: int
    has_root: bool
    entries: list[PlaceCard] = field(default_factory=list)
    card_mode: str = MONDE


@dataclass
class Hierarchy:
    heads: list[TreeNode]
    orphans: list[TreeNode]
    total: int
    branches: list[TreeNode] = field(default_factory=list)
    staff: list[TreeNode] = field(default_factory=list)
    sites: list[Link] = field(default_factory=list)

    @property
    def tree_ids(self) -> list[str]:
        groups = (
            (self.heads, "structure-sommet"),
            (self.branches, "structure-rattaches"),
            (self.orphans, "structure-sans-superieur"),
        )
        return [name for nodes, name in groups if nodes]

    @property
    def empty(self) -> bool:
        return not (self.heads or self.branches or self.orphans or self.staff)


@dataclass
class HouseholdGroup:
    key: str
    label: str
    members: list[Member]
    link: Link | None = None


@dataclass
class Inhabitants:
    leaders: list[Member]
    groups: list[HouseholdGroup]

    @property
    def count(self) -> int:
        return len(self.leaders) + sum(len(group.members) for group in self.groups)


@dataclass
class Building:
    building_type: str | None
    default_type: str | None
    rooms: list[PlaceCard]
    household: list[HouseholdGroup]

    @property
    def is_dwelling(self) -> bool:
        return self.building_type == DWELLING

    @property
    def type_label(self) -> str:
        return BUILDING_TYPE_LABELS.get(self.building_type or "", "")

    @property
    def room_count(self) -> int:
        return sum(1 + card.descendant_count for card in self.rooms)

    @property
    def household_count(self) -> int:
        return sum(len(group.members) for group in self.household)


@dataclass
class Structure:
    mode: str
    article: WaArticle
    territory: Territory
    hierarchy: Hierarchy
    fragment_url: str
    page_url: str
    show_tabs: bool
    building: Building | None = None
    inhabitants: Inhabitants | None = None


class _World:
    def __init__(self, db: Session, viewer: Viewer, context: RenderContext) -> None:
        self.db = db
        self.viewer = viewer
        self.context = context
        self.places = {row.id: row for row in db.scalars(select(Place))}
        self.positions = {row.id: row for row in db.scalars(select(Position))}
        self.npcs = {row.id: row for row in db.scalars(select(Npc))}
        self.roles = {row.id: row for row in db.scalars(select(NpcRole))}
        ids = {
            wa_id
            for row in [*self.places.values(), *self.positions.values()]
            for wa_id in _article_ids(row)
        }
        self.articles = (
            {
                row.wa_id: row
                for row in db.scalars(select(WaArticle).where(WaArticle.wa_id.in_(ids)))
            }
            if ids
            else {}
        )
        self.children: dict[int | None, list[Place]] = defaultdict(list)
        for place in sorted(self.places.values(), key=lambda row: (row.position, row.id)):
            self.children[place.parent_id].append(place)
        self.ordered = sorted(self.positions.values(), key=lambda row: (row.position, row.id))
        self.subordinates: dict[int | None, list[Position]] = defaultdict(list)
        for position in self.ordered:
            self.subordinates[position.superior_id].append(position)
        self._place_visible: dict[int, bool] = {}
        self._position_visible: dict[int, bool] = {}
        self._sections: dict[int, list[FicheSection]] | None = None

    def article(self, wa_id: str | None) -> WaArticle | None:
        return self.articles.get(wa_id) if wa_id else None

    def place_visible(self, place: Place | None) -> bool:
        if place is None:
            return False
        if self.viewer.dm:
            return True
        if place.id not in self._place_visible:
            self._place_visible[place.id] = False
            parent = self.places.get(place.parent_id) if place.parent_id else None
            self._place_visible[place.id] = place.visibility == "joueurs" and (
                place.parent_id is None or self.place_visible(parent)
            )
        return self._place_visible[place.id]

    def position_visible(self, position: Position | None) -> bool:
        if position is None:
            return False
        if self.viewer.dm:
            return True
        if position.id not in self._position_visible:
            self._position_visible[position.id] = False
            visible = position.visibility == "joueurs"
            if visible and position.superior_id is not None:
                visible = self.position_visible(self.positions.get(position.superior_id))
            if visible and position.place_id is not None:
                visible = self.place_visible(self.places.get(position.place_id))
            if visible and position.organisation_id is not None:
                visible = self.viewer.can_read(self.article(position.organisation_id))
            self._position_visible[position.id] = visible
        return self._position_visible[position.id]

    def place_reachable(self, place: Place | None) -> bool:
        if place is None or not self.place_visible(place):
            return False
        if self.viewer.dm:
            return True
        seen: set[int] = set()
        current: Place | None = place
        while current is not None and current.id not in seen:
            seen.add(current.id)
            for wa_id in (current.wa_article_id, current.owner_id):
                if self.viewer.can_read(self.article(wa_id)):
                    return True
            current = self.places.get(current.parent_id) if current.parent_id else None
        return False

    def place_href(self, place: Place | None) -> str | None:
        if place is None or not self.place_reachable(place):
            return None
        return f"{self.viewer.root}/lieux/{place.key}"

    def npc_visible(self, npc: Npc | None) -> bool:
        if npc is None:
            return False
        if self.viewer.dm:
            return True
        if npc.visibility != "joueurs":
            return False
        if any(
            position.npc_id == npc.id and self.position_visible(position)
            for position in self.positions.values()
        ):
            return True
        return self.place_reachable(self.places.get(npc.place_id) if npc.place_id else None)

    def role_visible(self, role: NpcRole | None) -> bool:
        if role is None:
            return False
        if self.viewer.dm:
            return True
        return role.visibility == "joueurs" and any(
            position.npc_role_id == role.id and self.position_visible(position)
            for position in self.positions.values()
        )

    def npc_href(self, npc: Npc | None) -> str | None:
        return f"{self.viewer.root}/pnj/{npc.key}" if npc and self.npc_visible(npc) else None

    def role_href(self, role: NpcRole | None) -> str | None:
        return f"{self.viewer.root}/roles/{role.key}" if role and self.role_visible(role) else None

    def link(self, wa_id: str | None) -> Link | None:
        article = self.article(wa_id)
        if article is None or not (self.viewer.dm or self.viewer.can_read(article)):
            return None
        return Link(article.title, self.viewer.href(article))

    def excerpt(self, row: Place | Position) -> Markup:
        article = self.article(row.source_article_id)
        if not row.source_anchor or not self.viewer.can_read(article):
            return Markup("")
        found = find_excerpt(article.content, row.source_anchor)
        if found.status != AnchorStatus.FOUND:
            return Markup("")
        return render_bbcode(found.source, self.context)

    def source(self, row: Place | Position) -> Link | None:
        link = self.link(row.source_article_id)
        if link is None:
            return None
        if row.source_anchor:
            link.label = f"{link.label} › {row.source_anchor}"
        return link

    def descendants(self, place_id: int) -> list[Place]:
        found: list[Place] = []
        stack = list(self.children.get(place_id, []))
        while stack:
            place = stack.pop(0)
            if not self.place_visible(place):
                continue
            found.append(place)
            stack[:0] = self.children.get(place.id, [])
        return found

    def member(self, position: Position, with_organisation: bool = False) -> Member:
        article = self.article(position.person_article_id)
        npc = self.npcs.get(position.npc_id) if position.npc_id else None
        role = self.roles.get(position.npc_role_id) if position.npc_role_id else None
        name = (
            position.name
            or (article.title if article else "")
            or (npc.name if npc else "")
            or (role.name if role else "")
            or UNNAMED
        )
        organisation = self.link(position.organisation_id) if with_organisation else None
        href = self.viewer.href(article) or self.npc_href(npc) or self.role_href(role)
        return Member(
            key=position.key,
            name=name,
            title=position.title,
            href=href,
            article=article,
            is_group=position.is_group,
            organisation=organisation,
            **self._dm_fields(position),
        )

    def _dm_fields(self, row: Place | Position) -> dict[str, str]:
        if not self.viewer.dm:
            return {}
        return {
            "gm_notes": render_plain_text(row.gm_notes, self.context),
            "visibility": row.visibility,
        }

    def place_sections(self, place: Place) -> list[PlaceSection]:
        if self._sections is None:
            self._sections = defaultdict(list)
            rows = self.db.scalars(
                select(FicheSection)
                .where(FicheSection.place_id.is_not(None))
                .order_by(FicheSection.position, FicheSection.id)
            )
            for row in rows:
                self._sections[row.place_id].append(row)
        return [
            PlaceSection(
                row.title,
                render_text_blocks(row.text, self.context),
                row.visibility if self.viewer.dm else "",
            )
            for row in self._sections.get(place.id, [])
            if self.viewer.dm or row.visibility == "joueurs"
        ]

    def default_building_type(self, place: Place) -> str | None:
        owner = self.article(place.owner_id)
        if owner is not None and section_of(owner).key == ORGANISATION:
            return BRANCH
        if owner is not None and (owner.template_type or "").lower() == PERSON_TEMPLATE:
            return DWELLING
        if any(
            position.household == "owner"
            for position in self.ordered
            if place.id in (position.place_id, position.site_id)
        ):
            return DWELLING
        return None

    def household(self, place: Place) -> list[HouseholdGroup]:
        scope = {place.id} | {row.id for row in self.descendants(place.id)}
        members: dict[str, list[Member]] = defaultdict(list)
        seen: set[str] = set()
        for position in self.ordered:
            if position.place_id not in scope and position.site_id not in scope:
                continue
            if not self.position_visible(position):
                continue
            identity = (
                position.person_article_id
                or (f"npc:{position.npc_id}" if position.npc_id else "")
                or position.name
                or position.key
            )
            if identity in seen:
                continue
            seen.add(identity)
            role = position.household or ("employee" if position.is_staff else "")
            members[role].append(self.member(position))
        return [
            HouseholdGroup(key, singular if len(members[key]) == 1 else plural, members[key])
            for key, singular, plural in HOUSEHOLD_GROUPS
            if members.get(key)
        ]

    def building(self, place: Place | None, article: WaArticle | None = None) -> Building:
        if article is None and place is not None:
            article = self.article(place.wa_article_id)
        chosen = article.building_type if article is not None else None
        if place is None or not self.place_visible(place):
            return Building(chosen, None, [], [])
        default = self.default_building_type(place)
        building_type = chosen or default
        rooms = [
            self.card(child, mode=ARCHITECTURE, depth=-1)
            for child in self.children.get(place.id, [])
            if self.place_visible(child)
        ]
        household = self.household(place) if building_type == DWELLING else []
        return Building(building_type, default, rooms, household)

    def identity(self, position: Position | None = None, npc: Npc | None = None) -> str:
        if position is not None:
            if position.person_article_id:
                return position.person_article_id
            npc = self.npcs.get(position.npc_id) if position.npc_id else None
            if npc is None:
                if position.npc_role_id:
                    return f"role:{position.npc_role_id}"
                return f"name:{position.name or position.key}"
        if npc is None:
            return ""
        return npc.wa_article_id or f"npc:{npc.id}"

    def _located_member(self, position: Position, place: Place | None) -> Member:
        member = self.member(position)
        if place is not None:
            member.place = Link(place.name, self.place_href(place), place.key)
        return member

    def _npc_member(self, npc: Npc, place: Place | None) -> Member:
        article = self.article(npc.wa_article_id)
        return Member(
            key=f"npc-{npc.key}",
            name=article.title if article else npc.name,
            title=npc.title,
            href=self.viewer.href(article) or self.npc_href(npc),
            article=article,
            is_group=False,
            place=Link(place.name, self.place_href(place), place.key) if place else None,
            **({"visibility": npc.visibility} if self.viewer.dm else {}),
        )

    def inhabitants(self, city: Place | None) -> Inhabitants:
        if city is None or not self.place_visible(city):
            return Inhabitants([], [])
        scope = {city.id} | {row.id for row in self.descendants(city.id)}
        leaders: list[Member] = []
        seen: set[str] = set()
        for position in self.ordered:
            if (
                position.place_id == city.id
                and position.organisation_id is None
                and position.is_head
                and not position.is_group
                and self.position_visible(position)
                and not (position.npc_id and not self.npc_visible(self.npcs.get(position.npc_id)))
            ):
                identity = self.identity(position)
                if identity not in seen:
                    seen.add(identity)
                    in_city = position.site_id in scope and position.site_id != city.id
                    site = self.places.get(position.site_id) if in_city else None
                    leaders.append(self._located_member(position, site))
        organisations: dict[str, str] = {}
        ranks: dict[str, int] = {}
        for rank, position in enumerate(self.ordered):
            if position.organisation_id and self.position_visible(position):
                organisations.setdefault(self.identity(position), position.organisation_id)
                ranks.setdefault(self.identity(position), rank)
        for npc in sorted(self.npcs.values(), key=lambda row: (row.position, row.id)):
            if npc.organisation_id and self.npc_visible(npc):
                organisations.setdefault(self.identity(npc=npc), npc.organisation_id)
        found: list[tuple[str, Member]] = []
        for position in self.ordered:
            if position.is_group or not self.position_visible(position):
                continue
            if position.npc_id and not self.npc_visible(self.npcs.get(position.npc_id)):
                continue
            located = position.site_id if position.site_id is not None else position.place_id
            if located not in scope:
                continue
            identity = self.identity(position)
            if identity in seen:
                continue
            seen.add(identity)
            place = self.places.get(located)
            found.append(
                (identity, self._located_member(position, None if located == city.id else place))
            )
        for npc in sorted(self.npcs.values(), key=lambda row: (row.position, row.id)):
            if npc.place_id not in scope or not self.npc_visible(npc):
                continue
            identity = self.identity(npc=npc)
            if identity in seen:
                continue
            seen.add(identity)
            place = self.places.get(npc.place_id)
            found.append(
                (identity, self._npc_member(npc, None if npc.place_id == city.id else place))
            )
        grouped: dict[str, list[Member]] = defaultdict(list)
        links: dict[str, Link] = {}
        order = {identity: index for index, (identity, _) in enumerate(found)}
        found.sort(key=lambda item: (ranks.get(item[0], len(self.ordered)), order[item[0]]))
        for identity, member in found:
            organisation = organisations.get(identity)
            link = self.link(organisation) if organisation else None
            key = organisation if link is not None else ""
            if link is not None:
                links[key] = link
            grouped[key].append(member)
        keys = sorted((key for key in grouped if key), key=lambda key: links[key].label.casefold())
        groups = [HouseholdGroup(key, links[key].label, grouped[key], links[key]) for key in keys]
        if grouped.get(""):
            groups.append(HouseholdGroup("", OTHER_INHABITANTS, grouped[""]))
        return Inhabitants(leaders, groups)

    def card(
        self,
        place: Place,
        *,
        mode: str,
        organisation_id: str | None = None,
        explore: str | None = None,
        depth: int = 0,
    ) -> PlaceCard:
        parent = self.places.get(place.parent_id) if place.parent_id else None
        owner = self.link(place.owner_id)
        members = self._members_at(place, mode)
        organisations: list[Link] = []
        if mode == MONDE:
            seen: set[str] = set()
            for link in [owner, *(member.organisation for member in members)]:
                if link is not None and link.label not in seen:
                    seen.add(link.label)
                    organisations.append(link)
        visible_children = [
            child for child in self.children.get(place.id, []) if self.place_visible(child)
        ]
        card = PlaceCard(
            key=place.key,
            name=place.name,
            kind=place.kind,
            kind_label=KIND_LABELS.get(place.kind, place.kind),
            href=self.viewer.href(self.article(place.wa_article_id)),
            location=place.location,
            parent=Link(parent.name) if parent is not None and self.place_visible(parent) else None,
            excerpt=self.excerpt(place),
            description=render_bbcode(place.description, self.context),
            source=self.source(place),
            owner=owner,
            organisations=organisations,
            members=members,
            sub_places=[
                Link(child.name, f"{explore}{child.key}" if explore else None, child.key)
                for child in visible_children
            ],
            explore_href=f"{explore}{place.key}" if explore and visible_children else None,
            page_href=self.place_href(place),
            sections=self.place_sections(place) if mode == ARCHITECTURE else [],
            **self._dm_fields(place),
        )
        if depth != 0:
            card.children = [
                self.card(
                    child,
                    mode=mode,
                    organisation_id=organisation_id,
                    explore=explore,
                    depth=depth - 1,
                )
                for child in visible_children
            ]
        return card

    def _members_at(self, place: Place, mode: str) -> list[Member]:
        members: list[Member] = []
        if mode in (ORGANISATION, CITY):
            return members
        seen: set[str] = set()
        for position in self.ordered:
            if not self.position_visible(position):
                continue
            if place.id not in (position.site_id, position.place_id):
                continue
            identity = (
                position.person_article_id
                or (f"npc:{position.npc_id}" if position.npc_id else "")
                or position.name
                or position.key
            )
            if identity in seen:
                continue
            seen.add(identity)
            members.append(self.member(position, with_organisation=mode == MONDE))
        return members

    def tree(
        self,
        positions: list[Position],
        root_place: Place | None,
        depth: int | None = None,
    ) -> Hierarchy:
        members = [position for position in positions if in_tree(position)]
        in_scope = {position.id for position in members}
        heads: list[TreeNode] = []
        branches: list[TreeNode] = []
        orphans: list[TreeNode] = []
        for position in members:
            superior = self.positions.get(position.superior_id) if position.superior_id else None
            if superior is not None and superior.id in in_scope:
                continue
            node = self._node(position, in_scope, root_place, depth)
            if position.is_head:
                heads.append(node)
            elif superior is not None and self.position_visible(superior):
                node.reports_to = self.member(superior).name
                if superior.title:
                    node.reports_to = f"{node.reports_to} ({superior.title})"
                branches.append(node)
            else:
                orphans.append(node)
        staff = [
            self._node(position, set(), root_place)
            for position in positions
            if position.is_staff and position.in_hierarchy
        ]
        hierarchy = Hierarchy(heads, orphans, len(members) + len(staff), branches, staff)
        hierarchy.sites = _hidden_sites([*heads, *branches, *orphans])
        return hierarchy

    def _node(
        self,
        position: Position,
        in_scope: set[int],
        root_place: Place | None,
        depth: int | None = None,
    ) -> TreeNode:
        place = self.places.get(position.place_id) if position.place_id else None
        site = self.places.get(position.site_id) if position.site_id else None
        scope = position.scope
        if (
            not scope
            and place is not None
            and place is not root_place
            and self.place_visible(place)
        ):
            scope = place.name
        visible_site = site is not None and site is not root_place and self.place_visible(site)
        node = TreeNode(
            key=position.key,
            member=self.member(position),
            scope=scope,
            site=site.name if visible_site else "",
            reports_to="",
            source=self.source(position),
            site_href=self.place_href(site) if visible_site else None,
        )
        children = [
            child for child in self.subordinates.get(position.id, []) if child.id in in_scope
        ]
        if depth is not None and depth <= 1:
            node.hidden = len(children)
            return node
        node.children = [
            self._node(child, in_scope, root_place, None if depth is None else depth - 1)
            for child in children
        ]
        return node

    def local_positions(self, place: Place) -> list[Position]:
        if not self.place_visible(place):
            return []
        scope = {place.id} | {row.id for row in self.descendants(place.id)}
        found = []
        for position in self.ordered:
            if not self.position_visible(position):
                continue
            if position.organisation_id is None:
                matches = position.place_id in scope or position.site_id == place.id
            else:
                matches = position.site_id == place.id or (
                    position.site_id in scope and position.organisation_id == place.owner_id
                )
            if matches:
                found.append(position)
        return found

    def local_hierarchy(self, place: Place) -> Hierarchy:
        return self.tree(self.local_positions(place), place)


def in_tree(position: Position) -> bool:
    return position.in_hierarchy and not position.is_staff


def _hidden_sites(nodes: list[TreeNode]) -> list[Link]:
    sites: list[Link] = []
    seen: set[str] = set()
    stack = list(nodes)
    while stack:
        node = stack.pop(0)
        stack[:0] = node.children
        if node.hidden and node.site_href and node.site_href not in seen:
            seen.add(node.site_href)
            sites.append(Link(node.site, node.site_href))
    return sites


def _article_ids(row: Place | Position) -> list[str]:
    if isinstance(row, Place):
        values = [row.wa_article_id, row.owner_id, row.source_article_id]
    else:
        values = [row.organisation_id, row.person_article_id, row.source_article_id]
    return [value for value in values if value]


def structure_mode(article: WaArticle) -> str | None:
    key = section_of(article).key
    return key if key in STRUCTURE_SECTIONS else None


def build_structure(
    db: Session,
    article: WaArticle,
    viewer: Viewer,
    context: RenderContext,
    focus_key: str = "",
) -> Structure | None:
    mode = structure_mode(article)
    if mode is None:
        return None
    world = _World(db, viewer, context)
    page_url = f"{viewer.base}/{article.wa_id}"
    if mode == ORGANISATION:
        territory = _organisation_territory(world, article)
        positions = [
            position
            for position in world.ordered
            if position.organisation_id == article.wa_id
            and in_tree(position)
            and world.position_visible(position)
        ]
        hierarchy = world.tree(positions, None, ORGANISATION_DEPTH)
    else:
        root = next(
            (place for place in world.places.values() if place.wa_article_id == article.wa_id),
            None,
        )
        city = article.world_category == CITY_CATEGORY
        territory = _monde_territory(world, root, page_url, focus_key, CITY if city else MONDE)
        if city:
            hierarchy = Hierarchy([], [], 0)
        else:
            hierarchy = world.local_hierarchy(root) if root is not None else Hierarchy([], [], 0)
    building = None
    inhabitants = None
    if mode == MONDE and article.world_category == BUILDING_CATEGORY:
        building = world.building(root, article)
    if mode == MONDE and article.world_category == CITY_CATEGORY:
        inhabitants = world.inhabitants(root)
    if inhabitants is not None:
        show = viewer.dm or bool(territory.total or inhabitants.count)
    elif building is None:
        show = viewer.dm or bool(territory.total or hierarchy.total)
    else:
        people = building.household_count if building.is_dwelling else hierarchy.total
        show = viewer.dm or bool(building.room_count or people)
    return Structure(
        mode=mode,
        article=article,
        territory=territory,
        hierarchy=hierarchy,
        fragment_url=f"{page_url}/territoire",
        page_url=page_url,
        show_tabs=show,
        building=building,
        inhabitants=inhabitants,
    )


def _organisation_territory(world: _World, article: WaArticle) -> Territory:
    owned = [
        place
        for place in sorted(world.places.values(), key=lambda row: (row.position, row.id))
        if place.owner_id == article.wa_id and world.place_visible(place)
    ]
    owned_ids = {place.id for place in owned}
    tops = [place for place in owned if not _has_ancestor_in(world, place, owned_ids)]
    entries = [
        world.card(place, mode=ORGANISATION, organisation_id=article.wa_id)
        for place in tops
        if place.wa_article_id
    ]
    cards = [
        world.card(place, mode=ORGANISATION, organisation_id=article.wa_id, depth=-1)
        for place in tops
        if not place.wa_article_id
    ]
    total = len(entries) + sum(1 + card.descendant_count for card in cards)
    return Territory(ORGANISATION, cards, None, [], total, True, entries)


def _has_ancestor_in(world: _World, place: Place, ids: set[int]) -> bool:
    seen: set[int] = set()
    current = world.places.get(place.parent_id) if place.parent_id else None
    while current is not None and current.id not in seen:
        if current.id in ids:
            return True
        seen.add(current.id)
        current = world.places.get(current.parent_id) if current.parent_id else None
    return False


def _monde_territory(
    world: _World, root: Place | None, page_url: str, focus_key: str, card_mode: str = MONDE
) -> Territory:
    if root is None or not world.place_visible(root):
        return Territory(MONDE, [], None, [], 0, False, card_mode=card_mode)
    explore = f"{page_url}?lieu="
    focus = root
    if focus_key:
        candidate = next(
            (place for place in world.descendants(root.id) if place.key == focus_key), None
        )
        if candidate is not None:
            focus = candidate
    chain: list[Place] = []
    current: Place | None = focus
    while current is not None:
        chain.insert(0, current)
        if current.id == root.id:
            break
        current = world.places.get(current.parent_id) if current.parent_id else None
    crumbs = [
        Link(place.name, None if place.id == focus.id else f"{explore}{place.key}", place.key)
        for place in chain
    ]
    crumbs[0].href = None if focus.id == root.id else page_url
    focus_card = world.card(focus, mode=card_mode, explore=explore, depth=1)
    return Territory(
        MONDE,
        focus_card.children,
        focus_card,
        crumbs,
        len(world.descendants(root.id)),
        True,
        card_mode=card_mode,
    )
