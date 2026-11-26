# Campaign Companion

A small web application for running a D&D campaign on top of a [World Anvil](https://www.worldanvil.com) world.
It pulls the world's articles through the Boromir API, caches them in Postgres and serves two views:

- **DM area** (`/mj`): sync status and a *Synchronise* button, full-text search across every article,
  article pages with their GM-only fields and cross-links, player accounts with single-use invitation
  links, and characters with the extra articles each player may read.
- **Player area** (`/joueur`): the player's own character sheet and the articles the DM shared with
  them. Nothing else is reachable; anything not shared answers with a plain 404.
- **Hosted media**: portraits, maps and character-sheet PDFs stored in Postgres and attached to
  articles, served with the same access rules as the articles themselves.
- **Chronologie** (`/mj/chronologie`, `/joueur/chronologie`): the world's history as a frise of eras
  and the campaign chronicle, read from a World Anvil timeline; players only see the events the DM
  revealed.

The interface is in French, rendered server-side with Jinja2 and HTMX, mobile-first, and installable as
a PWA.

## Stack

Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Postgres (psycopg 3), httpx, nh3, argon2-cffi, Pillow,
defusedxml, Jinja2, HTMX.

```
app/
  config.py            settings from environment variables
  db.py                engine (Neon-friendly pooling), sessions, wait-for-database
  models.py            users, invitations, characters, character_articles, wa_articles, wa_images,
                       sync_runs, media_assets, media_variants, timeline_events, timeline_event_links,
                       timeline_event_fiches, timeline_eras
  chronology.py        imperial calendar, eras and year display
  data/                timeline_events.json, the chronology seed dataset
  security.py          password hashing, CSRF, login rate limiting
  deps.py              current user and role checks
  worldanvil/          Boromir client, BBCode to HTML converter, player visibility rules
  services/            sync, accounts, characters, articles, access control, media, manifest imports
  routes/              auth, DM area, player area, media files
  templates/, static/  Jinja templates, CSS, HTMX, PWA files
  cli.py               command line entry point
migrations/            Alembic migrations
media-manifest.toml    files to import with import-media, players and characters for import-characters
tests/                 pytest suite
```

## Environment variables

| Variable | Required | Description |
| --- | --- | --- |
| `DATABASE_URL` | yes | Postgres URL used by the application. On Neon, use the pooled URL (`-pooler` host). |
| `DATABASE_URL_UNPOOLED` | no | Direct URL used by Alembic migrations. Falls back to `DATABASE_URL`. |
| `SECRET_KEY` | yes | At least 32 random characters; signs the session cookie. |
| `WA_APPLICATION_KEY` | for sync | World Anvil application key (`x-application-key`). |
| `WA_AUTH_TOKEN` | for sync | World Anvil user token (`x-auth-token`). |
| `WA_WORLD_ID` | for sync | Id of the world to synchronise. |
| `WA_TIMELINE_ID` | no | Id of the World Anvil timeline holding the chronology. By default the timeline titled "Chronologie de Valhar Molaris" is used. |
| `APP_ENV` | no | `production` (default: secure cookies, HSTS) or `development` (plain HTTP on localhost). |
| `PLAYER_INITIAL_PASSWORD` | no | Temporary code given to new or reset player accounts (default `0000`). Players replace it with their own 4-digit PIN at their first login. |

`postgres://` and `postgresql://` URLs are accepted and converted to the psycopg driver automatically.
Copy `.env.example` to `.env` for local work; `.env` is ignored by git.

## Local setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv -r requirements-dev.txt
# or: python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

cp .env.example .env            # then fill in the values, with APP_ENV=development
alembic upgrade head
python -m app.cli create-dm --login maitre --name "Maître du jeu"
python -m app.cli load-timeline
python -m app.cli sync
uvicorn app.main:app --reload
```

Open http://localhost:8000 and sign in with the DM account.

## Signing in

The landing page asks for a role. **Maître du jeu** opens the DM form (login and password).
**Joueur** lists the active player accounts that have a password; a player picks their name and types
their 4-digit PIN. Accounts created by `import-characters`, or reset from the DM's *Joueurs* page with
*Réinitialiser le code*, use `PLAYER_INITIAL_PASSWORD` (the *Code initial* state). A player who signs in with it is sent to
*Choisissez votre code PIN* and cannot open anything else until they choose their own 4-digit PIN;
they can change it later from *Code PIN*, given the current one. Invitation links also set a PIN.

The password of any active DM account is a master key: typed on a player's sign-in form, it opens that
player's account. A master-key session never triggers the PIN setup and cannot change the player's
PIN; the DM resets a forgotten PIN with *Réinitialiser le code*.

## Character dossiers

Each played character's fiche has five tabs: **Fiche** (the World Anvil article), **Actions** (what the
character can do on their combat turn), **Relations**, **Histoire** (sub-tabs **Récit**, the backstory; **Événements marquants**,
the character's memorable events, each with its own card; **Journal**, only when the character has
adventure journal episodes) and **Inventaire**.
Only the DM and the character's own player see the last four tabs; another player who was shared the
article only sees the Fiche.

Dossiers are written as one TOML file per character (keep them out of git, e.g. in
`source/dossiers/`) and imported with:

```bash
python -m app.cli import-dossiers source/dossiers
```

Re-importing a file replaces that character's whole dossier, except relations and events edited
in the app (see below). Text fields accept plain paragraphs (separated by a blank line) and the same BBCode as
World Anvil (`[b]`, `[i]`, `[ul]`…).

```toml
character = "Khaetar Voss"            # character name, or the title of its main article
aliases = ["Le Cornu", { name = "Maelys", note = "faux nom" }]   # optional, shown as "Alias"

[history]
backstory = """
Né orphelin dans le Khaanat...

Un vieux prêtre lui révèle que son sang vient de Zariel.
"""

[[history.events]]                    # memorable events; every field but title is optional
title = "Dispute pour l'amulette"
session = "Séance 1, jour 1"          # shown as a badge
year = 829                            # world calendar, An 0 = creation of the Empire d'Ébène
age = 31                              # the character's age
approximate = false                   # true shows "Vers l'an 829", "vers 31 ans"
place = "Marché noir de Khar Zakh"
place_article = "..."                 # exact title of a Monde fiche (or place_article_id)
text = "Au marché noir de Khar Zakh..."
consequences = "L'impact sur le personnage..."
gm_notes = "Ce que le joueur ignore"  # shown to the DM only

[[history.events.people]]             # repeat per person
name = "Zilvra"
role = "Rivale, puis alliée"          # optional
article = "Zilvra"                    # optional: exact fiche title, otherwise a free name

[[history.events.organizations]]      # repeat per organisation
name = "La Guilde des Aventuriers"
article = "La Guilde des Aventuriers" # optional

# category: famille, fideles, amis, allies, connaissances, rivaux, ennemis or autres; any other
# category ("Couvent de Lolth") is accepted as written and listed after these
[[relations]]
category = "famille"
name = "Mère inconnue"
role = "Mère"                         # optional
status = "Disparue"                   # optional
article = "Kratos"                    # optional: links the World Anvil fiche (or article_id = "...")
text = "Ce qu'il sait d'elle..."
gm_notes = "Ce que le joueur ignore"  # optional, shown to the DM only

[combat]                              # summary strip of the Actions tab (all optional)
armor_class = 11
hit_points = 24
speed = "9 m"
initiative = "+1"
spell_save_dc = 13
spell_attack = "+5"

[[combat.resources]]                  # spell slots, uses per rest...
name = "Emplacements de magie de pacte (niveau 2)"
uses = 2
recharge = "Repos court ou long"

[[actions]]
type = "action"                       # action, bonus, reaction or free
name = "Trait de feu (Fire Bolt)"
category = "Sort mineur"              # optional
hit = "+5"                            # optional
damage = "1d10 feu"                   # optional, shown as "Effet"
save = "DEX 13"                       # optional
range = "36 m"                        # optional
uses = "À volonté"                    # optional
concentration = false                 # optional
text = "Un objet inflammable touché prend feu."

[[inventory]]
name = "Pot en fer"
quantity = 1                          # optional, default 1
category = "Équipement"               # optional
equipped = false                      # optional
article = "Le Gage d'Arès"            # optional
text = "Description..."
gm_notes = "Ce que le joueur ignore"  # optional, shown to the DM only

[[journal]]                           # the adventure journal, one entry per episode
episode = 1                           # positive whole number, unique per character
title = "L'amulette du marché noir"
days = "Jours 1 et 2"                 # optional
text = """
Ce que le personnage a vécu, de son seul point de vue...
"""
encounters = ["[b]Jean-Charles[/b] : patron du Troll qui Rit."]   # optional lists of lines:
items = ["Une licence de rang B."]                                # Rencontres, Objets et argent,
quests = ["Déloger le troll de Sophie."]                          # Quêtes et engagements,
leads = ["Où l'amulette a-t-elle été forgée ?"]                   # Pistes et questions
```

`gm_notes` and the aliases follow the dossier rules: the aliases are shown to the DM and the
character's player, `gm_notes` only to the DM (they are never part of a player's page).

### Adventure journal

Each `[[journal]]` entry is written from the character's own point of view. Players find their
journal under **Mon journal** on the *Aventure* page (latest episode first); each episode opens on
its own page (`/joueur/journal/<character id>/<episode>`) with the narrative and the four lists.
The DM sees every character's journal on the DM *Aventure* page (`/mj/journal/...`) and in the
Histoire tab of each character's fiche. A journal is never shown to another player, even one who
was shared the character's article. Re-importing a file replaces that character's journal.

### Memorable events

The **Événements marquants** sub-tab lists the events with a world year on a frieze sorted by year
(then by file order), and groups the undated ones apart. Each card shows the title, the date and
age, the session, the place and the people; opening it shows the full event (what happened, place,
people with their role, organisations, consequences) with links to the fiches the reader can open,
and has the anchor `#evenement-<id>`. These events are personal: they never enter the world
chronology. Players only read their own character's events; the DM sees every event with its
GM notes and can add, edit and delete events from the fiche. As for relations, the first change made
in the app keeps the events through later imports (with a warning) until:

```bash
python -m app.cli import-dossiers source/dossiers --replace-events
```

### Editing relations in the app

On the DM view of a character's fiche, the Relations tab lets the DM edit, add and delete
relations (name, category, link, status, linked World Anvil fiche, text and GM notes). The first
change made there hands the relations over to the app: a later `import-dossiers` still updates
everything else from the file (history, inventory, actions, aliases) but keeps the relations as
edited in the app, and the command prints a warning for that file. To go back to the file's
relations, overwriting the app edits:

```bash
python -m app.cli import-dossiers source/dossiers --replace-relations
```

A relation or an item linked to an article shows a "Voir la fiche" link (and the portrait, for
relations) only when the reader can open that article.

## Chronology

The *Chronologie* page (Lore section, and the *Chronologie* link of both navigations) has two tabs:

- **Histoire du monde**: a frise of the five eras (Aubes Anciennes, Siècles Oubliés, Ascension,
  Grand Midi, Tumultes). Each era keeps a readable width whatever its length; inside an era, events
  are placed at scale. Periods are bars, single years are diamonds sized by importance, several
  events of the same year are grouped in a numbered seal, and An 0, An 345, An 749 and today
  (An 829) are marked. Filters (era, importance, text; origin for the DM) live in the
  URL. On phones the frise becomes a list grouped by era.
- **Chronique de la campagne**: the events flagged as chronicle, by session and day (J1, J2).

The calendar (current year, founding, end of the Conquest, end of the Empire), the eras and their
default names, subtitles and colours are defined in `app/chronology.py`.

**Where the data lives.** Titles, dates, places, summaries, importance, origin and "Lié à" links are
written on World Anvil, in the private timeline "Chronologie de Valhar Molaris" (one history per
event; its content ends with a `Code :` line such as `B3`). The app only reads World Anvil:
every sync (*Synchroniser* or `python -m app.cli sync`) refreshes these fields, matching events by
history id then by code, adds new histories as hidden, and hides events removed from the
timeline. Everything else belongs to the app and is never overwritten by a sync: visibility
(MJ only / revealed to players), chronicle flag, session, day,
order, links added in the app and linked fiches. The DM manages them from each event's panel, and
edits the era names, subtitles and colours from *Chronologie › Ères*.

**Seed.** `python -m app.cli load-timeline` inserts the events of `app/data/timeline_events.json`
that do not exist yet (by code). It updates an existing event only while it has never been synced
nor edited in the app, so it can run at every start (Render runs it after the migrations). On
Neon, run it once like `create-dm` if the service has not restarted since the upgrade:

```bash
DATABASE_URL=... DATABASE_URL_UNPOOLED=... SECRET_KEY=... python -m app.cli load-timeline
```

Players see an event only after the DM chose *Révéler aux joueurs*; any other event answers with the
usual 404, and never appears in lists, searches, filters, year groups or linked events.

## Lore and artefacts

The Lore home (`/mj/articles?section=lore`, `/joueur/articles?section=lore`) shows two halves side by
side, stacked on narrow screens: **Légendes** on the left and **Artefacts** on the right (title, one
line, count, preview), with a smaller full-width **Ouvrages** block below. Its header links to the
**Frise chronologique** (`&vue=frise`) and to the **Autres fiches** of the Lore section (`&vue=fiches`);
the DM also gets **Réglages** (`/mj/lore/reglages`).

- **Légendes** (`&vue=legendes`): three levels, the first optional: a *regroupement* (for example
  « Malvaar ») > a *récit* > its ordered *parts*. Chips filter by legend type, a search box matches the
  title, and the list sorts chronologically or alphabetically (`&type=<id>`, `&q=`, `&tri=alpha`).
  Legend types are rows of `lore_types` (Mythe, Conte, Légende, Histoire by default), edited on the
  settings page, never hard-coded. A récit opens on `/…/lore/recits/<id>` with the list of its parts
  and previous/next links (`/parties/<n>`). Its date is its own (year, range, era only, or nothing,
  with *approximatif*) or, when empty, read from its parts and their chronology events.
- **Ouvrages** (`&vue=ouvrages`, `/…/lore/ouvrages/<id>`): books found in the world, in-world
  sources that may be wrong. Same reading model, with optional header sections of the article and
  optional links to a legend and an artefact.
- **Artefacts** (`&vue=artefacts`): a card grid (image, name, type, rarity) filtered by rarity or type
  and searched by name. The fiche shows the characteristics, the characteristic sections and the
  physical description, never a narrated history; the DM also sees the *Notes du MJ* sections.
- **Frise chronologique**: an overview of the dated legends only, grouped by era.

**One source.** Parts and fiche sections are `lore_entries` rows that render their World Anvil
section live (`source_article_id` + `source_section`: a heading, or a bold label paragraph, see
`app/services/lore_sections.py`); the stored `text` is shown only when the section is missing.
Secrets, *Pistes pour la suite* and *Événements de la session 1* never render outside DM notes.
Grouping, order, types and links (`lore_groups`, `lore_stories`, `lore_types`, `artefacts`) belong to
the app: the sync updates the World Anvil text and never touches them. A legend or a book points to
its artefact (`artefact_id`) and a book to its legend (`legend_id`); the cards *Légende associée*,
*Artefact associé* and *Ouvrage associé* are derived both ways from these single links.

**Curation.** The sync never creates a Lore entry, a récit or an artefact. The DM adds a synced
article with *Ajouter au Lore* on its fiche, as a Légende (one part per narrative section), an
Artefact (characteristics, physical description and DM notes) or an Ouvrage; everything it creates is
*MJ seulement* until the DM shares it. Migration 0015 builds the validated Lore from the entries of
migration 0010 without deleting anything: the Joug history becomes the récit « Le Joug de Malvaar »
(regroupement « Malvaar », 7 parts, parts keep their flags), the other entries of both artefacts move
to their fiche (physical description, characteristics, DM notes), and it adds « L'Empire d'Ébène et
les Échos », « La fondation de l'Héliopolis » and the book « Les Feuillets du Dompteur », all *MJ
seulement*.

**Players.** A player sees a récit or a book only if it is not *MJ seulement* and its source article
is accessible to them; a part also needs its own flag off; an artefact needs its flag off and its
article accessible; a fiche section needs its flag off, and DM notes never reach players. A part dated
by a chronology event the player cannot see shows no date and no event link. Editing forms are
never rendered for a player, and the DM routes answer 404 to players.

## Monde: Pays, Villes, Bâtiments

The Monde section (`/mj/articles?section=monde`, `/joueur/articles?section=monde`) opens on three
tiles of equal size, side by side on wide screens and stacked on phones: **Pays**, **Villes** and
**Bâtiments** (title, number of fiches, a few fiche images). A tile opens its category
(`&categorie=pays|villes|batiments`): a card grid, a name search (`&q=`) and a bar to switch category.

**Category.** The category is app data: `world_categories` (one row per World Anvil article, `pays`,
`ville`, `batiment` or empty). A fiche with a category belongs to the Monde section whatever its World
Anvil template, and shows its category as its type. The sync never changes an existing row; it only
gives a new article a first category when its World Anvil data says so: `settlement` → Ville,
`landmark` → Bâtiment, an `organization` with the *country* variant → Pays. The DM changes it from
*Catégorie Monde* in the fiche's *Informations clés* (Organisation and Monde fiches only); *Aucune*
keeps the fiche out of the three categories (an Organisation template then goes back to
Organisation).

**Links between fiches.** A Ville shows its Pays and lists its Bâtiments; a Bâtiment shows its Ville
and its Pays; a Pays lists its Villes. They come only from written data: the World Anvil `parent`
field, or the Territoire places of `import-structures` (the nearest enclosing place that is a fiche).
A player sees a link only when both fiches are shared with them and every place on the way is marked
`joueurs`.

**Building types.** A Bâtiment fiche is either a **Branche d'une organisation** (temples of the
Clergé, the Guild building) or an **Habitation** (the home of a person, such as La demeure de
Sophie). The type is app data (`building_types`, one row per article, `branch` or `dwelling`) that the
sync never touches; without a row the type is automatic: a building owned by an organisation is a
branch, a building whose owner is a person (an owner article of the *person* template, or a position
with `household = "owner"`) is a dwelling, otherwise it has no type and behaves like a branch. The DM
changes it from *Type de bâtiment* in the fiche's *Informations clés*; *Automatique* removes the row.
A branch shows **Fiche | Architecture | Hiérarchie**, a dwelling **Fiche | Architecture | Habitants**
(anchors `#architecture`, `#hierarchie`, `#habitants`); the place page `/lieux/<key>` of a building
shows the same sections. *Architecture* lists the rooms and spots of the building as nested
collapsible cards (levels open), each with its live excerpt, description, sections and the people
assigned there. *Habitants* groups the people attached to the dwelling (or to its rooms) by their
`household` role: *Propriétaire*, *Habitants* (family, residents), *Employés* (household staff; a
`staff` position counts as an employee); people without a role are listed under *Personnes
présentes*. Empty groups are not shown.

## Personnage tags

Each fiche of the Personnage section carries one tag: **Lore** (people tied to the history of the
world or very well known), **Character** (the fiches of the played characters) or **Rencontre
campagne** (people met during the campaign). The tag lives in the app table `article_categories`
(keyed by the World Anvil article id); sync never changes it. A new person article gets *Rencontre
campagne* on its first sync, and the row is kept if the article is later removed. Migration 0011 tagged
the main articles of the active players' characters as *Character* and every other person fiche as
*Rencontre campagne*.

**Liens** are the existing dossier relations that point to a Personnage fiche (one source of truth):
a person appears under *Liens* for each played character whose dossier links to them, with a *Lien
<character>* badge, on top of their own tag. A relation to a *Character* fiche is left out of *Liens*
but kept in the dossier.

**DM.** `/mj/articles?section=personnage` opens on four tiles (Lore, Character, Liens, Rencontre
campagne) with their counts and up to four portraits. A tile opens the card list of that tag
(`&tag=lore|character|encounter`, with search) or, for *Liens*, the list of played characters and then
the people linked to the chosen one (`&tag=liens&pj=<character id>`); a tag bar switches tags. On a
person fiche the *Tags* form sets the tag and the linked played characters: ticking a character adds a
minimal relation (*Autres*, named after the fiche) to their dossier, unticking removes it only while
it is still empty; a relation with a role, status, text or DM notes stays and its box is locked (edit
it in the dossier). A *Character* fiche has no links; its relations are kept.

**Players.** `/joueur/articles?section=personnage` shows two tiles only: *Character* (their own
character) and *Liens* (their own character's links). A linked person whose fiche is not shared with
the player shows as a name (and the relation's role) without link, portrait or content, as in the
dossier. Players never see the other tags, nor the links of other characters.

## Territoire and Hiérarchie

The fiches of the Organisation and Monde sections get two tabs next to *Fiche*: **Territoire** and
**Hiérarchie** (anchors `#territoire` and `#hierarchie`); Bâtiment fiches get *Architecture* instead
of *Territoire* (see *Building types*). One shared model serves both sections:

- `places`: places with their geographical nesting (`parent`), a kind (territoire, région, ville,
  quartier, bâtiment, terre, niveau, pièce, lieu), an optional article of the place, an optional
  owning organisation, a free location and a passage of a source article.
- `positions`: people, posts and collectives, attached either to an organisation or to a place
  (the extent of their power), with a direct superior, an assignment site, a *sommet* flag, a
  *collectif* flag, a *présence* flag (`hierarchy = false`: a notable person on a place, outside
  the hierarchy) and a *personnel* flag (`staff = true`: an employee with a precise task, such as
  the infirmary, the guard of a crypt or the reception desk, kept outside the member hierarchy).

An organisation's Territoire lists the places it owns as collapsible cards (nested places inside).
An owned place that
has its own World Anvil fiche (`article`) appears instead under *Lieux contenus* as a single entry
whose name links to that fiche (the five temples of the Clergé d'Arès): its rooms and personnel are
read on the fiche itself. A Monde fiche's
Territoire starts from the place linked to the article and goes down level by level (`?lieu=<key>`,
loaded without reload), with a breadcrumb; each card lists the organisations and notable people
present. Hiérarchie is a keyboard-navigable tree (arrows, Home/End, Enter opens the fiche, Space
toggles, `*` opens the siblings) where every person with subordinates is a collapsible branch: the
first level is open, deeper branches start closed, and *Tout déplier* / *Tout replier* act on all.
Only people and groups of people are nodes (no institution): a council is read through its
president and its members, a guard through its captain, sergeants and guards, each branch ordered
as a promotion ladder that ends at the head.

- On an organisation's page the tree stops after the top rank (several heads allowed) and their
  direct subordinates; a node whose branch continues says how many subordinates are on the place
  fiche, and *Hiérarchie complète par lieu* links to those fiches. Staff never appear there.
- On a place fiche (`/lieux/<key>`, and the Hiérarchie tab of a building's article) the tree is
  complete: the place-attached positions of the place and of every place it contains, plus the
  positions of an organisation assigned there (on its sub-places too when the organisation owns the
  place). A branch whose superior works elsewhere is listed under *Sous un supérieur extérieur au
  lieu* with *Relève de …*; staff are listed apart under *Personnel*, and presences under
  *Personnes présentes*.

**Where the data lives.** Read-only in the app: the rows come from the git-ignored TOML files of
`source/structures/` and are imported with:

```
python -m app.cli import-structures source/structures source/npcs            # created / updated / unchanged / failed
python -m app.cli import-structures source/structures source/npcs --dry-run  # check only, nothing written
python -m app.cli import-structures source/structures source/npcs --prune    # also delete rows whose key left the files
```

The import is idempotent by `key` and all-or-nothing: an unknown article title, a cycle, a superior
from another organisation or an anchor inside *Secrets*, *Pistes pour la suite* or *Événements de la
session 1* fails the whole run. Rows missing from the files are kept unless `--prune` is given. Sync
never reads or writes these tables.

```toml
[[places]]
key = "marche-noir"                 # stable identifier
name = "Le marché noir (Khar Zakh)"
kind = "lieu"
parent = "khuchniin-khondii"        # key of another place
owner = "Shönö"                     # organisation article (exact title, or owner_id)
location = "caché dans le dédale de la cité"
source = "Shönö"                    # required: article the fact comes from (or source_id)
anchor = "Le lieu"                  # heading or bold label of that article, shown as a live excerpt
visibility = "mj"                   # mj (default) or joueurs

[[positions]]
key = "nuit-maren"
organisation = "Shönö"              # or place = "<place key>"
name = "Maren"                      # or person = "<person article title>"
title = "Intendante du Grand Étal"
scope = "Comptes, contrats et dîme du marché"
superior = "nuit-erdene"            # key of another position
site = "marche-noir"                # place key
head = false                        # sommet
group = false                       # collectif
hierarchy = true                    # false: présence only
staff = false                       # true: personnel (employee), shown apart from the tree
household = "owner"                 # place positions only: owner, resident or employee (Habitants)
source = "Shönö"
gm_notes = "…"                       # DM only
```

A row can be renamed without losing its id: give the new `key` and list the old one in
`previous_keys = ["old-key"]` (the run reports it as updated, *renamed from*). `--prune` deletes NPCs,
roles and stat blocks only when NPC files are part of the run.

**Players.** The tabs appear only on a fiche already shared with the player and only when something
is visible. Only rows marked `joueurs` are shown, and a hidden place or position hides its whole
subtree. A person, place or organisation links to its fiche only when that fiche is accessible to
the player; DM notes and *MJ seulement* badges are never rendered for a player. Excerpts
are read live from the source article and shown only when that article is readable.

## Place pages, NPCs and stat blocks

**Place pages.** Every place has a page, `/mj/lieux/<key>` or `/joueur/lieux/<key>` (`/lieux/<key>`
redirects to the right one): breadcrumb of the enclosing places, key facts, the short description,
its titled **sections**, the places it contains and the positions attached to it or exercised there
(a Khan on his Cour, with the site where he resides), each linking to its NPC or role. When a place is
linked to a World Anvil article (`article`), its sections also appear on that fiche for the DM only
(e.g. the *Secret* of Le Khaanat des Cinq Lances). On a Territoire card, clicking the card opens the dropdown as before (description,
Personnel); clicking the place name, or *Page du lieu*, opens the page. A place without sections
shows no empty block. Sections come from the place in the TOML file:

```toml
[[places.sections]]
title = "Le lieu"
text = """Paragraph lines.
• bullet lines start with •"""
visibility = "joueurs"            # joueurs (default) or mj; titles Secret(s) and Accroches are always mj
```

**NPCs and roles** are app data (no World Anvil article), imported by the same command from
`source/npcs/npcs.toml`, with the stat blocks of `source/npcs/statblocks.json`:

```toml
[[npcs]]
key = "erdene"
name = "Erdene"
title = "l'Ezen du Grand Étal"
gender = "homme"                   # also race, age, standing (Statut)
organisation = "Shönö"             # organisation article title (or organisation_id)
place = "comptoir-des-creances"    # optional place key
statblock = "crime-boss-a5e"       # stat block key
tag = "lore"                       # lore (default), character or encounter
visibility = "joueurs"             # mj (default) or joueurs
description = "…"
gm_notes = "…"                     # DM only
article = "Kratos"                 # optional: attach the sheet to an existing World Anvil person
[[npcs.sections]]                  # optional titled sections, e.g. Histoire, Apparence, Secret
[[npcs.relations]]                 # npc = "<key>" or article = "<title>", label, reverse_label

[[roles]]                          # generic role behind a group position (fiche de groupe)
key = "veilleur"
name = "Veilleur"
statblock = "guard"
headcount = "une dizaine"          # optional Effectif
description = "…"
```

A position points to an NPC with `npc = "<key>"` or to a role with `npc_role = "<key>"`. The DM finds
app NPCs under their tag in the Personnage section (`/mj/pnj/<key>`); roles open at `/mj/roles/<key>`.
An NPC attached to a World Anvil person (`article`) is not listed separately: its sections, DM notes
and stat block appear on that fiche for the DM.

**Stat blocks** use short JSON keys: `n` name, `sz` size, `tp` type, `al` alignment, `ac` / `acd`
armour class and its description, `hp` / `hd` hit points and dice, `spd` speed, `ab` the six scores
(STR DEX CON INT WIS CHA), `sv` saves, `sk` skills, `dv` / `dr` / `di` / `ci` vulnerabilities,
resistances, immunities, condition immunities, `se` senses, `la` languages, `cr` challenge, `tr`
traits, `ac_` actions, `ba` bonus actions, `re` reactions (lists of `{"n": name, "t": text}`, with an
optional `"sp": [["1st level (4 slots)", "spell, spell"]]` for spell lists) and `src` source. They are
rendered like a rulebook stat block, for the DM only, with the source and its licence in the footer.
`/credits` (linked in the footer of every page) lists the sources and licences: SRD 5.1 (CC BY 4.0),
Level Up Advanced 5th Edition Monstrous Menagerie (EN Publishing), Monsters of the Multiverse and
Volo's Guide to Monsters (Wizards of the Coast), Creature Codex and Black Flag SRD (Kobold Press).
A stat block adapted from a published one names its base in `src` (for example « D'après War
Priest (MotM p. 254), adapté au niveau 12 ») and keeps that book's credit.

**Players** open a place page only when the place and every enclosing place are marked `joueurs` and
the place, an enclosing place or its owner is a fiche shared with them; an NPC or role page only when
it is marked `joueurs` and a position they can see points to it. They never get *mj* sections, DM
notes, *mj* relations or stat blocks, and app NPCs never appear in their Personnage tiles.

## Automatic links between fiches

When a text names a fiche, the name becomes a link at render time; nothing stored or synced is
changed. The index holds the titles of the World Anvil articles that are not removed, plus the DM
aliases of `/mj/liens` (Lore entries and chronology events are not indexed, but their texts are
linked). Detection runs server-side on the text nodes of the BBCode tree (`app/worldanvil/linking.py`,
one compiled regex, memoised and rebuilt after every sync and every alias or exclusion change):
whole words, case- and accent-insensitive, longest match first, optional leading article (Le, La,
Les, L'), elisions and contractions (`l'`, `d'`, `du`, `au`), names under 3 letters ignored,
first occurrence per text section, never in headings, existing links or code, never to the fiche
being read. A name borne by several fiches is not linked, logged by `app.linking` and listed on
`/mj/liens`. Character dossier aliases (Maelys) never create a link. An exclusion blocks a
phrase that contains a fiche name: « Süüdriin shönö », the Khaanat's monthly night, never links
to the fiche Shönö.

A player gets a link only to a fiche he can open; any other name stays plain text, with no
tooltip. The hover and focus preview reads `/joueur/liens/apercus` (or `/mj/liens/apercus`), a JSON
index filtered per user and cached with an ETag. Generated links and fiche-to-fiche links navigate
with `hx-boost` (never forms); Back restores the previous fiche at the same scroll position.

## Useful commands

| Command | Purpose |
| --- | --- |
| `python -m app.cli sync` | Incremental sync: only articles whose World Anvil `updateDate` changed are fetched. |
| `python -m app.cli sync --full` | Refetch every article. |
| `python -m app.cli create-dm --login LOGIN --name NAME` | Create a DM account (prompts for the password; `--password-stdin` reads it from stdin). |
| `python -m app.cli load-timeline` | Insert the chronology events of `app/data/timeline_events.json` that are not in the database yet (idempotent). |
| `python -m app.cli wait-db` | Wait until the database accepts connections (used at start-up while Neon wakes up). |
| `python -m app.cli import-media FOLDER [--manifest PATH]` | Import the portraits, maps and PDFs listed in the manifest (see below). |
| `python -m app.cli import-characters [--manifest PATH]` | Create the player accounts and characters listed in the manifest. |
| `python -m app.cli import-structures PATH... [--dry-run] [--prune]` | Import the places, positions, NPCs, roles and stat blocks (TOML and JSON, see above). |
| `alembic upgrade head` | Apply migrations. |
| `ruff format . && ruff check .` | Format and lint. |
| `pytest` | Run the test suite (see below). |

## Media

Portraits, maps and character sheets are stored in Postgres (the Render disk is ephemeral) in
`media_assets` (one row per article and role: `portrait`, `map` or `sheet`) and `media_variants`
(the bytes). They are processed once, at import or upload time:

- **portraits** (JPEG, PNG or WebP, checked by content, not by extension) are re-encoded to WebP
  without EXIF metadata: a full view of at most 1400 px and a card thumbnail of at most 480 × 640 px;
- **maps** may be SVG or raster images. SVG files must be well-formed XML; they are rebuilt from an
  allow-list of SVG elements and attributes, which drops scripts, event handlers, `foreignObject`,
  `image`, external references and links, then stored with a pre-compressed gzip copy;
- **character sheets** must be PDF files (checked by their `%PDF-` header) and are stored as-is.

Every file is limited to 15 MB. Files are served by `GET /media/{id}/full` or `/media/{id}/thumb`:
the DM can fetch any file; a player can fetch a file only when it is attached to an article that
player can open, and gets the usual 404 otherwise. Responses carry an ETag, `Cache-Control: private`,
`X-Content-Type-Options: nosniff` and a sandboxing Content-Security-Policy; PDFs are sent as
downloads.

On a DM article page, the *Illustrations et documents* panel adds, replaces or removes the portrait,
the map and the PDF sheet of that article. Cards use the portrait thumbnail (or the map), the article
page frames the portrait next to the title, shows the map with a full-screen viewer, and offers the
PDF in *Informations clés*. A map can also be the banner of a section page (`banner = "monde"` in the
manifest).

`media-manifest.toml` lists, for each file of a local folder, its target article (`article` = title
or `article_id` = World Anvil id), its `role`, an optional `title` and an optional `banner`. It also
lists the player accounts (`[[players]]`) and the characters (`[[characters]]`) read from the D&D
Beyond sheets. The source files themselves stay out of git (`source/` is ignored).

```bash
python -m app.cli sync                       # the target articles must be synchronised first
python -m app.cli import-media source        # created / replaced / unchanged / failed, then a summary
python -m app.cli import-characters          # players get the temporary password, no invitation link
```

Both commands are idempotent: a file whose SHA-256 has not changed is left alone, a changed file
replaces the previous one, existing logins and characters are skipped. Invitation links are then
generated from the *Joueurs* page. Run them once against Neon, like `create-dm`, with the Neon URLs in
the environment:

```bash
DATABASE_URL=... DATABASE_URL_UNPOOLED=... SECRET_KEY=... python -m app.cli import-media source
DATABASE_URL=... DATABASE_URL_UNPOOLED=... SECRET_KEY=... python -m app.cli import-characters
```

## Tests

The suite needs a disposable Postgres database; it drops and recreates the `public` schema. It never calls
the real World Anvil API and refuses to run against a Neon host.

```bash
docker run -d --name campaign-test-pg -e POSTGRES_PASSWORD=testpw -e POSTGRES_DB=campaign_test \
  -p 55432:5432 postgres:17-alpine
TEST_DATABASE_URL=postgresql://postgres:testpw@localhost:55432/campaign_test pytest
```

## Deployment (Render + Neon, free plans)

1. **Neon**: create a project (or use the one linked through `neon.ts` / `.neon` with the Neon CLI) and
   copy both connection strings from the dashboard or with `neon connection-string`: the pooled one for
   `DATABASE_URL`, the direct one for `DATABASE_URL_UNPOOLED`. Keep `sslmode=require`.
2. **Render**: create a Blueprint from this repository. `render.yaml` declares a free Python web service
   with `/health` as health check. Fill in the variables marked `sync: false` when prompted;
   `SECRET_KEY` is generated by Render.
3. Every start runs `python -m app.cli wait-db`, then `alembic upgrade head` over the direct connection,
   then `python -m app.cli load-timeline`, then Uvicorn with proxy headers enabled so secure cookies and invitation links use `https`.
4. Create the DM account once. The free plan has no shell, so run the command locally with the Neon
   URLs in the environment:
   ```bash
   DATABASE_URL=... DATABASE_URL_UNPOOLED=... SECRET_KEY=... python -m app.cli create-dm --login maitre --name "Maître du jeu"
   ```
5. Sign in, press *Synchroniser*, create players, send them their invitation links, then create
   characters and share articles. To load the portraits, maps, sheets and the players' characters of
   `media-manifest.toml`, run `import-media` and `import-characters` against Neon (see *Media*).

Free-plan behaviour to expect: the Render service sleeps after 15 minutes without traffic (the first
request then takes a while), and the Neon compute suspends when idle. The engine uses `pool_pre_ping`,
a short `pool_recycle`, a connect timeout and no server-side prepared statements (compatible with
Neon's PgBouncer pooler); a request that still hits a waking database gets a short "retry" page
instead of an error. Login rate-limit counters live in memory and reset when the service restarts.

## Player visibility rules

An article can be shown to a player only if the DM shared it with one of the player's characters
**and** the last sync judged it safe. An article is considered unsafe, and stays invisible, when any of
these hold:

- its World Anvil state is not `public`, or it is a draft (or the draft flag is missing);
- it is restricted to subscriber groups, has secrets attached, is locked or passcode protected;
- it uses the Plot template;
- its text contains restricted BBCode (`[secret…]`, `[subcontainer…]`, `[gm…]`, `[private…]`,
  `[subscriber…]`, `[redacted…]`, `[passcode…]`).

Players only ever see the main article text. GM-oriented fields (storyteller seeds, author's notes,
sidebars, template fields) are shown in the DM area only. Links inside a player page point only to
articles that player can open; other references are rendered as plain text. The visibility reason is
displayed to the DM next to each article.
