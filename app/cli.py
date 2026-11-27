import argparse
import getpass
import logging
import sys
from pathlib import Path

from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.db import get_engine, get_session_factory, wait_for_database
from app.models import Role, SyncStatus
from app.services.accounts import AccountError, create_user
from app.services.battles import import_library, library_size
from app.services.chronology import ChronologyError, load_seed
from app.services.dossiers import (
    DossierError,
    events_locked,
    import_dossier,
    load_dossier_file,
    relations_locked,
)
from app.services.imports import (
    DEFAULT_MANIFEST,
    ImportLine,
    ManifestError,
    import_characters,
    import_media,
    load_manifest,
)
from app.services.structure_import import (
    StructureError,
    import_structures,
    load_structure_file,
    structure_files,
)
from app.services.sync import SyncAlreadyRunningError, sync_world
from app.worldanvil.client import WorldAnvilClient, WorldAnvilError

logger = logging.getLogger("app.cli")


def cmd_wait_db(args: argparse.Namespace) -> int:
    try:
        wait_for_database(get_engine(), attempts=args.attempts)
    except OperationalError:
        logger.error("Database is still unreachable")
        return 1
    logger.info("Database is reachable")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.world_anvil_configured:
        logger.error("WA_APPLICATION_KEY, WA_AUTH_TOKEN and WA_WORLD_ID must be set")
        return 1
    try:
        client = WorldAnvilClient.from_settings(settings)
    except WorldAnvilError as exc:
        logger.error("%s", exc)
        return 1
    with client, get_session_factory()() as db:
        try:
            run = sync_world(db, client, settings.wa_world_id, full=args.full, trigger="cli")
        except SyncAlreadyRunningError:
            logger.error("A synchronisation is already running")
            return 1
    if run.status != SyncStatus.SUCCESS:
        logger.error("Synchronisation failed: %s", run.error)
        return 1
    print(
        f"Synchronisation succeeded: {run.created_count} created, {run.updated_count} updated, "
        f"{run.unchanged_count} unchanged, {run.removed_count} removed; chronology: "
        f"{run.events_created} created, {run.events_updated} updated, "
        f"{run.events_removed} removed"
    )
    return 0


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Confirm password: "):
        raise AccountError("password_mismatch")
    return password


def cmd_create_dm(args: argparse.Namespace) -> int:
    try:
        password = _read_password(args.password_stdin)
        with get_session_factory()() as db:
            user = create_user(
                db,
                display_name=args.name,
                login=args.login,
                role=Role.DM,
                password=password,
            )
    except AccountError as exc:
        logger.error("Could not create the DM account: %s", exc.code)
        return 1
    print(f"DM account '{user.login}' created")
    return 0


def _print_report(lines: list[ImportLine]) -> int:
    counts: dict[str, int] = {}
    for line in lines:
        counts[line.status] = counts.get(line.status, 0) + 1
        print(f"{line.status:<9} {line.label} {line.detail}")
    summary = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    print(f"Summary: {summary or 'nothing to import'}")
    return 1 if any(line.failed for line in lines) else 0


def cmd_import_media(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    if not folder.is_dir():
        logger.error("Folder not found: %s", folder)
        return 1
    try:
        manifest = load_manifest(Path(args.manifest))
    except ManifestError as exc:
        logger.error("%s", exc)
        return 1
    with get_session_factory()() as db:
        return _print_report(import_media(db, folder, manifest))


def cmd_import_characters(args: argparse.Namespace) -> int:
    try:
        manifest = load_manifest(Path(args.manifest))
    except ManifestError as exc:
        logger.error("%s", exc)
        return 1
    with get_session_factory()() as db:
        return _print_report(import_characters(db, manifest))


def cmd_import_dossiers(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    files = sorted(folder.glob("*.toml")) if folder.is_dir() else []
    if not files:
        logger.error("No .toml dossier found in %s", folder)
        return 1
    lines: list[ImportLine] = []
    with get_session_factory()() as db:
        for path in files:
            try:
                character = import_dossier(
                    db,
                    load_dossier_file(path),
                    path.name,
                    replace_relations=args.replace_relations,
                    replace_events=args.replace_events,
                )
            except DossierError as exc:
                db.rollback()
                lines.append(ImportLine("failed", path.name, str(exc)))
                continue
            lines.append(
                ImportLine(
                    "imported",
                    path.name,
                    f"-> {character.name}: {len(character.events)} events, "
                    f"{len(character.relations)} relations, {len(character.items)} items, "
                    f"{len(character.actions)} actions, {len(character.journal)} journal entries",
                )
            )
            if relations_locked(character):
                logger.warning(
                    "%s: relations were edited in the app and were kept; "
                    "use --replace-relations to overwrite them with the file",
                    path.name,
                )
            if events_locked(character):
                logger.warning(
                    "%s: events were edited in the app and were kept; "
                    "use --replace-events to overwrite them with the file",
                    path.name,
                )
    return _print_report(lines)


def cmd_import_structures(args: argparse.Namespace) -> int:
    files = structure_files(Path(path) for path in args.paths)
    if not files:
        logger.error("No .toml structure file found in %s", ", ".join(args.paths))
        return 1
    try:
        documents = [(path.name, load_structure_file(path)) for path in files]
    except StructureError as exc:
        logger.error("%s", exc)
        return 1
    with get_session_factory()() as db:
        status = _print_report(
            import_structures(db, documents, dry_run=args.dry_run, prune=args.prune)
        )
    if args.dry_run:
        print("Dry run: nothing was written")
    return status


def cmd_load_timeline(args: argparse.Namespace) -> int:
    with get_session_factory()() as db:
        try:
            report = load_seed(db)
        except ChronologyError as exc:
            logger.error("Invalid timeline dataset: %s", exc.code)
            return 1
    print(
        f"Timeline loaded: {report.created} created, {report.updated} updated, "
        f"{report.unchanged} unchanged, {report.skipped} kept as edited in the app"
    )
    return 0


def cmd_import_battle_maps(args: argparse.Namespace) -> int:
    root = Path(args.folder)
    if not root.is_dir():
        logger.error("Folder not found: %s", root)
        return 1
    status = 0
    with get_session_factory()() as db:
        for entry, battle_map, result in import_library(db, root):
            if battle_map is None:
                print(f"{entry.title}: {result} ({entry.path})")
                status = status or int(result != "missing")
                continue
            size = len(battle_map.image) + len(battle_map.thumbnail or b"")
            print(
                f"{battle_map.name}: {result}, {battle_map.cols}x{battle_map.rows} cells, "
                f"{size / 1024:.0f} KB"
            )
        print(f"Library size: {library_size(db) / 1024 / 1024:.1f} MB")
    return status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    wait = commands.add_parser("wait-db", help="Wait until the database accepts connections")
    wait.add_argument("--attempts", type=int, default=8)
    wait.set_defaults(handler=cmd_wait_db)

    sync = commands.add_parser("sync", help="Synchronise World Anvil articles")
    sync.add_argument("--full", action="store_true", help="Refetch every article")
    sync.set_defaults(handler=cmd_sync)

    create_dm = commands.add_parser("create-dm", help="Create a DM account")
    create_dm.add_argument("--login", required=True)
    create_dm.add_argument("--name", required=True)
    create_dm.add_argument(
        "--password-stdin", action="store_true", help="Read the password from standard input"
    )
    create_dm.set_defaults(handler=cmd_create_dm)

    media = commands.add_parser("import-media", help="Import portraits, maps and sheets")
    media.add_argument("folder", help="Folder containing the files listed in the manifest")
    media.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    media.set_defaults(handler=cmd_import_media)

    players = commands.add_parser(
        "import-characters", help="Create the player accounts and characters of the manifest"
    )
    players.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    players.set_defaults(handler=cmd_import_characters)

    dossiers = commands.add_parser(
        "import-dossiers", help="Import character backstories, relations and inventories"
    )
    dossiers.add_argument("folder", help="Folder containing one .toml file per character")
    dossiers.add_argument(
        "--replace-relations",
        action="store_true",
        help="Also replace relations that the DM edited in the app",
    )
    dossiers.add_argument(
        "--replace-events",
        action="store_true",
        help="Also replace events that the DM edited in the app",
    )
    dossiers.set_defaults(handler=cmd_import_dossiers)

    structures = commands.add_parser(
        "import-structures",
        help="Import places, positions, NPCs, roles and stat blocks (TOML and JSON files)",
    )
    structures.add_argument("paths", nargs="+", help="Folders or .toml files to import")
    structures.add_argument(
        "--dry-run", action="store_true", help="Check and report without writing anything"
    )
    structures.add_argument(
        "--prune",
        action="store_true",
        help="Also delete the rows whose key is no longer in the files",
    )
    structures.set_defaults(handler=cmd_import_structures)

    timeline = commands.add_parser(
        "load-timeline", help="Insert the chronology events of the versioned dataset"
    )
    timeline.set_defaults(handler=cmd_load_timeline)

    battle_maps = commands.add_parser(
        "import-battle-maps", help="Import the battle map library from a folder of downloaded maps"
    )
    battle_maps.add_argument("folder", help="Folder that holds the downloaded map packs")
    battle_maps.set_defaults(handler=cmd_import_battle_maps)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
