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
                       sync_runs, media_assets, media_variants
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
| `APP_ENV` | no | `production` (default: secure cookies, HSTS) or `development` (plain HTTP on localhost). |

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
python -m app.cli sync
uvicorn app.main:app --reload
```

Open http://localhost:8000 and sign in with the DM account.

## Useful commands

| Command | Purpose |
| --- | --- |
| `python -m app.cli sync` | Incremental sync: only articles whose World Anvil `updateDate` changed are fetched. |
| `python -m app.cli sync --full` | Refetch every article. |
| `python -m app.cli create-dm --login LOGIN --name NAME` | Create a DM account (prompts for the password; `--password-stdin` reads it from stdin). |
| `python -m app.cli wait-db` | Wait until the database accepts connections (used at start-up while Neon wakes up). |
| `python -m app.cli import-media FOLDER [--manifest PATH]` | Import the portraits, maps and PDFs listed in the manifest (see below). |
| `python -m app.cli import-characters [--manifest PATH]` | Create the player accounts and characters listed in the manifest. |
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
python -m app.cli import-characters          # players without password, no invitation link
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
   then Uvicorn with proxy headers enabled so secure cookies and invitation links use `https`.
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
