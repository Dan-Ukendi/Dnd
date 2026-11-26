from collections import defaultdict
from dataclasses import dataclass, field

from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Npc, NpcRole, Place, Position, WaArticle
from app.sections import section_of
from app.services.structure_excerpts import AnchorStatus, find_excerpt
from app.worldanvil.bbcode import RenderContext, render_bbcode, render_plain_text

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

    @property
    def initial(self) -> str:
        return (self.name.strip()[:1] or "?").upper()


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


@dataclass
class Territory:
    mode: str
    cards: list[PlaceCard]
    focus: PlaceCard | None
    crumbs: list[Link]
    total: int
    has_root: bool


@dataclass
class Hierarchy:
    heads: list[TreeNode]
    orphans: list[TreeNode]
    total: int


@dataclass
class Structure:
    mode: str
    article: WaArticle
    territory: Territory
    hierarchy: Hierarchy
    fragment_url: str
    page_url: str
    show_tabs: bool


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
        self.subordinates: dict[int | None, list[Position]] = defaultdict(list)
        for position in sorted(self.positions.values(), key=lambda row: (row.position, row.id)):
            self.subordinates[position.superior_id].append(position)
        self._place_visible: dict[int, bool] = {}
        self._position_visible: dict[int, bool] = {}

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
        members = self._members_at(place, mode, organisation_id)
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

    def _members_at(self, place: Place, mode: str, organisation_id: str | None) -> list[Member]:
        members: list[Member] = []
        seen: set[str] = set()
        for position in sorted(self.positions.values(), key=lambda row: (row.position, row.id)):
            if not self.position_visible(position):
                continue
            if mode == ORGANISATION:
                matches = (
                    position.organisation_id == organisation_id and position.site_id == place.id
                )
            else:
                matches = place.id in (position.site_id, position.place_id)
            if not matches:
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

    def tree(self, positions: list[Position], root_place: Place | None) -> Hierarchy:
        in_scope = {position.id for position in positions}
        heads: list[TreeNode] = []
        orphans: list[TreeNode] = []
        for position in positions:
            superior = self.positions.get(position.superior_id) if position.superior_id else None
            if superior is not None and superior.id in in_scope:
                continue
            node = self._node(position, in_scope, root_place)
            if superior is not None and self.position_visible(superior):
                node.reports_to = self.member(superior).name
                if superior.title:
                    node.reports_to = f"{node.reports_to} ({superior.title})"
            (heads if position.is_head else orphans).append(node)
        return Hierarchy(heads, orphans, len(positions))

    def _node(self, position: Position, in_scope: set[int], root_place: Place | None) -> TreeNode:
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
        node = TreeNode(
            key=position.key,
            member=self.member(position),
            scope=scope,
            site=site.name if site is not None and self.place_visible(site) else "",
            reports_to="",
            source=self.source(position),
        )
        node.children = [
            self._node(child, in_scope, root_place)
            for child in self.subordinates.get(position.id, [])
            if child.id in in_scope
        ]
        return node


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
            for position in sorted(world.positions.values(), key=lambda row: (row.position, row.id))
            if position.organisation_id == article.wa_id
            and position.in_hierarchy
            and world.position_visible(position)
        ]
        hierarchy = world.tree(positions, None)
    else:
        root = next(
            (place for place in world.places.values() if place.wa_article_id == article.wa_id),
            None,
        )
        territory = _monde_territory(world, root, page_url, focus_key)
        scope_ids = set()
        if root is not None and world.place_visible(root):
            scope_ids = {root.id} | {place.id for place in world.descendants(root.id)}
        positions = [
            position
            for position in sorted(world.positions.values(), key=lambda row: (row.position, row.id))
            if position.place_id in scope_ids
            and position.in_hierarchy
            and world.position_visible(position)
        ]
        hierarchy = world.tree(positions, root)
    show = viewer.dm or bool(territory.total or hierarchy.total)
    return Structure(
        mode=mode,
        article=article,
        territory=territory,
        hierarchy=hierarchy,
        fragment_url=f"{page_url}/territoire",
        page_url=page_url,
        show_tabs=show,
    )


def _organisation_territory(world: _World, article: WaArticle) -> Territory:
    owned = [
        place
        for place in sorted(world.places.values(), key=lambda row: (row.position, row.id))
        if place.owner_id == article.wa_id and world.place_visible(place)
    ]
    owned_ids = {place.id for place in owned}
    tops = [place for place in owned if not _has_ancestor_in(world, place, owned_ids)]
    cards = [
        world.card(place, mode=ORGANISATION, organisation_id=article.wa_id, depth=-1)
        for place in tops
    ]
    total = sum(1 + card.descendant_count for card in cards)
    return Territory(ORGANISATION, cards, None, [], total, True)


def _has_ancestor_in(world: _World, place: Place, ids: set[int]) -> bool:
    seen: set[int] = set()
    current = world.places.get(place.parent_id) if place.parent_id else None
    while current is not None and current.id not in seen:
        if current.id in ids:
            return True
        seen.add(current.id)
        current = world.places.get(current.parent_id) if current.parent_id else None
    return False


def _monde_territory(world: _World, root: Place | None, page_url: str, focus_key: str) -> Territory:
    if root is None or not world.place_visible(root):
        return Territory(MONDE, [], None, [], 0, False)
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
    focus_card = world.card(focus, mode=MONDE, explore=explore, depth=1)
    return Territory(
        MONDE,
        focus_card.children,
        focus_card,
        crumbs,
        len(world.descendants(root.id)),
        True,
    )
