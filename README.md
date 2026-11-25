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
| `PLAYER_PASSWORD_CHANGE` | no | `false` (default): players sign in with the temporary password and the password page is hidden. `true`: a temporary password must be replaced at first login. |
| `PLAYER_INITIAL_PASSWORD` | no | Temporary password given to new or reset player accounts (default `0000`). Players must replace it at their first login when `PLAYER_PASSWORD_CHANGE` is on. |

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
their password. Accounts created by `import-characters`, or reset from the DM's *Joueurs* page with
*Mot de passe provisoire*, use `PLAYER_INITIAL_PASSWORD`. While `PLAYER_PASSWORD_CHANGE` is off (the
default) that password is all players need. When it is on, a player who signs in with it is sent to
*Choisissez votre mot de passe* and cannot open anything else until they choose their own (at least
10 characters). Invitation links still work as an alternative.

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
year = 987                            # world calendar, An 0 = creation of the Empire d'Ébène
age = 31                              # the character's age
approximate = false                   # true shows "Vers l'an 987", "vers 31 ans"
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
article = "Kratéros"                  # optional: links the World Anvil fiche (or article_id = "...")
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
  (An 987) are marked. Filters (era, importance, text; origin and status for the DM) live in the
  URL. On phones the frise becomes a list grouped by era.
- **Chronique de la campagne**: the events flagged as chronicle, by session and day (J1, J2).

The calendar (current year, founding, end of the Conquest, end of the Empire), the eras and their
default names, subtitles and colours are defined in `app/chronology.py`.

**Where the data lives.** Titles, dates, places, summaries, importance, origin and "Lié à" links are
written on World Anvil, in the private timeline "Chronologie de Valhar Molaris" (one history per
event; its content ends with a `Code :` line such as `B3`). The app only reads World Anvil:
every sync (*Synchroniser* or `python -m app.cli sync`) refreshes these fields, matching events by
history id then by code, adds new histories as hidden and *à valider*, and hides events removed
from the timeline. Everything else belongs to the app and is never overwritten by a sync: status
(*établi* / *à valider*), visibility (MJ only / revealed to players), chronicle flag, session, day,
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

The Lore section (`/mj/articles?section=lore`, `/joueur/articles?section=lore`) has two tabs, plus a
third one for the other Lore fiches when there are any:

- **Frise**: a vertical timeline of Lore entries, sorted and grouped by the eras of the chronology
  (same configuration as the *Chronologie*, names included), then a *Non daté* list. Chips filter by
  element type (artefact, lieu, personnage, organisation, divinité, événement); the DM also gets an
  *À vérifier* chip. A click opens the entry (`/mj/lore/<id>`, `/joueur/lore/<id>`).
- **Artefacts**: the World Anvil articles of the *Item* template. Their fiche now shows only the
  characteristics (one-line description, type, rarity, attunement, charges, recharge, constraint or
  curse, current holder, location, status, mechanical properties and bonuses) and a *Lore associé*
  block (title, date and link of each linked entry). The World Anvil text itself is no longer shown
  on these fiches; the DM keeps the *Ouvrir sur World Anvil* link and `wa_articles.content` is kept
  untouched.

**Where the data lives.** Characteristics (`artefacts`) and Lore entries (`lore_entries`) are app
tables, edited by the DM only; nothing is written to World Anvil. An entry has a title, an element
type, a narrative text (World Anvil BBCode), an optional link to a fiche and an optional link to a
chronology event, and a date: a year, a range, an era only, or nothing (*Non daté*), with an
*approximatif* flag. An entry linked to an event reads its date from the event. Years before An 0 are
negative; the global sort key orders entries by era first, so periods known only by their era sort
correctly. The current holder comes from the dossier inventories (`character_items`); the manual
field is used only when no inventory carries the item.

**Initial split and sync.** Migration 0010 splits the cached Item articles (`wa_articles`, no
network): the *Fiche rapide* lines *Type*, *Autre(s) nom(s)* and *État*, the italic subtitle and the
whole *Statistiques (D&D 5e)* block go to the fiche, verbatim; every other heading becomes a Lore
entry linked to the artefact, verbatim. Unclear cases carry an *À vérifier* note (DM only; *Marquer
vérifié* clears it) and are logged by the migration. Each sync applies the same pure split to new
Item articles that have no fiche yet, with every entry marked *À vérifier*; an existing fiche is never
overwritten. The rules for Le Gage d'Arès and Le Joug de Malvaar (MJ-only sections, dates, links to
chronology events A1–A4) are in `app/services/artefact_split.py`.

**Players.** A player sees an entry only if it is not *MJ seulement* and the element it is linked to
is already accessible to them (a shared fiche, or a revealed chronology event); an entry linked to
nothing stays with the DM. An entry dated by a chronology event the player cannot see appears in
*Non daté*, without the event link. *À vérifier* notes and editing forms are never rendered for a
player. On an artefact fiche a player sees as holders only their own characters.

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
**Hiérarchie** (anchors `#territoire` and `#hierarchie`). One shared model serves both sections:

- `places`: places with their geographical nesting (`parent`), a kind (territoire, région, ville,
  quartier, bâtiment, terre, niveau, pièce, lieu), an optional article of the place, an optional
  owning organisation, a free location and a passage of a source article.
- `positions`: people, posts and collectives, attached either to an organisation or to a place
  (the extent of their power), with a direct superior, an assignment site, a *sommet* flag, a
  *collectif* flag and a *présence* flag (`hierarchy = false`: a notable person on a place, outside
  the hierarchy).

An organisation's Territoire lists the places it owns as collapsible cards (nested places inside),
each with its *Personnel* (the organisation's positions assigned to that site). A Monde fiche's
Territoire starts from the place linked to the article and goes down level by level (`?lieu=<key>`,
loaded without reload), with a breadcrumb; each card lists the organisations and notable people
present. Hiérarchie is a keyboard-navigable tree (arrows, Home/End, Enter opens the fiche, Space
toggles, `*` opens the siblings) split into *Sommet* and *Sans supérieur défini*; a Monde hierarchy
includes the positions of every place it contains, with *Relève de …* when the superior is outside.

**Where the data lives.** Read-only in the app: the rows come from the git-ignored TOML files of
`source/structures/` and are imported with:

```
python -m app.cli import-structures source/structures            # created / updated / unchanged / failed
python -m app.cli import-structures source/structures --dry-run  # check only, nothing written
python -m app.cli import-structures source/structures --prune    # also delete rows whose key left the files
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
owner = "La Nuit"                   # organisation article (exact title, or owner_id)
location = "caché dans le dédale de la cité"
source = "La Nuit"                  # required: article the fact comes from (or source_id)
anchor = "Le lieu"                  # heading or bold label of that article, shown as a live excerpt
visibility = "mj"                   # mj (default) or joueurs
status = "a_valider"                # a_valider (default) or etabli

[[positions]]
key = "nuit-erdene"
organisation = "La Nuit"            # or place = "<place key>"
name = "Erdene"                     # or person = "<person article title>"
title = "l'Ezen du Grand Étal"
scope = "Le commerce illicite et le marché noir"
superior = "nuit-conseil"           # key of another position
site = "marche-noir"                # place key
head = false                        # sommet
group = false                       # collectif
hierarchy = true                    # false: présence only
source = "La Nuit"
gm_notes = "Incohérence : …"
```

**Players.** The tabs appear only on a fiche already shared with the player and only when something
is visible. Only rows marked `joueurs` are shown, and a hidden place or position hides its whole
subtree. A person, place or organisation links to its fiche only when that fiche is accessible to
the player; DM notes, *À valider* and *MJ seulement* badges are never rendered for a player. Excerpts
are read live from the source article and shown only when that article is readable.

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
`/mj/liens`. Character dossier aliases (Maelys) never create a link.

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
| `python -m app.cli import-structures PATH... [--dry-run] [--prune]` | Import the places and positions of the Territoire and Hiérarchie tabs (see above). |
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
