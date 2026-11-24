import gzip
import io
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import MediaAsset, MediaRole, MediaVariant, Role, User, WaArticle
from app.presentation import article_image
from app.services.imports import import_characters, import_media
from app.services.media import attach_media, set_banner
from app.services.media_processing import (
    MAX_UPLOAD_BYTES,
    MediaError,
    process_raster,
    process_upload,
    sanitize_svg,
)
from tests.factories import csrf_token, login, make_article, make_character, make_user

SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200" viewBox="0 0 300 200">'
    b'<rect width="300" height="200" fill="#c6dadb"/><text x="10" y="20">Carte</text></svg>'
)
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def jpeg_bytes(width: int = 1600, height: int = 2000, *, orientation: int | None = None) -> bytes:
    image = Image.new("RGB", (width, height), (120, 40, 30))
    exif = Image.Exif()
    exif[0x010F] = "Camera maker"
    exif[0x8825] = {2: (48, 51, 24)}
    if orientation is not None:
        exif[0x0112] = orientation
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif.tobytes())
    return buffer.getvalue()


@dataclass
class MediaWorld:
    dm: User
    alice: User
    bob: User
    alice_main: WaArticle
    alice_extra: WaArticle
    bob_main: WaArticle
    hidden: WaArticle
    alice_portrait: MediaAsset
    alice_sheet: MediaAsset
    extra_map: MediaAsset
    bob_portrait: MediaAsset
    hidden_map: MediaAsset
    orphan: MediaAsset


@pytest.fixture
def media_world(db: Session) -> MediaWorld:
    dm = make_user(db, "maitre", Role.DM)
    alice = make_user(db, "alice")
    bob = make_user(db, "bob")
    alice_main = make_article(db, "Alice la voleuse")
    alice_extra = make_article(db, "Le Royaume", template_type="organization")
    bob_main = make_article(db, "Bob le barde")
    hidden = make_article(db, "Plan secret", template_type="location", player_visible=False)
    make_character(db, "Alice", alice, alice_main, [alice_extra, hidden])
    make_character(db, "Bob", bob, bob_main)
    portrait = jpeg_bytes(800, 1000)
    alice_portrait = attach_media(
        db, article=alice_main, role=MediaRole.PORTRAIT, data=portrait, filename="a.jpg"
    ).asset
    alice_sheet = attach_media(
        db, article=alice_main, role=MediaRole.SHEET, data=PDF, filename="Alice.pdf"
    ).asset
    extra_map = attach_media(
        db, article=alice_extra, role=MediaRole.MAP, data=SVG, filename="carte.svg"
    ).asset
    bob_portrait = attach_media(
        db, article=bob_main, role=MediaRole.PORTRAIT, data=portrait, filename="b.jpg"
    ).asset
    hidden_map = attach_media(
        db, article=hidden, role=MediaRole.MAP, data=SVG, filename="plan.svg"
    ).asset
    orphan = MediaAsset(
        role=MediaRole.MAP,
        title="Orpheline",
        original_filename="o.svg",
        content_type="image/svg+xml",
        size=len(SVG),
        sha256="0" * 64,
    )
    db.add(orphan)
    db.flush()
    db.add(
        MediaVariant(
            asset_id=orphan.id,
            name="full",
            content_type="image/svg+xml",
            data=SVG,
            size=len(SVG),
            etag="1" * 64,
        )
    )
    db.commit()
    return MediaWorld(
        dm,
        alice,
        bob,
        alice_main,
        alice_extra,
        bob_main,
        hidden,
        alice_portrait,
        alice_sheet,
        extra_map,
        bob_portrait,
        hidden_map,
        orphan,
    )


def media_paths(world: MediaWorld) -> list[str]:
    assets = (
        world.alice_portrait,
        world.alice_sheet,
        world.extra_map,
        world.bob_portrait,
        world.hidden_map,
        world.orphan,
    )
    return [f"/media/{asset.id}/{variant}" for asset in assets for variant in ("full", "thumb")]


def test_anonymous_is_redirected(client: TestClient, media_world: MediaWorld) -> None:
    for path in media_paths(media_world):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/connexion"


def test_dm_can_fetch_every_media(client: TestClient, media_world: MediaWorld) -> None:
    login(client, "maitre")
    for path in media_paths(media_world):
        assert client.get(path).status_code == 200, path


def test_player_only_fetches_media_of_accessible_articles(
    client: TestClient, media_world: MediaWorld
) -> None:
    login(client, "alice")
    world = media_world
    allowed = {world.alice_portrait.id, world.alice_sheet.id, world.extra_map.id}
    reference = client.get("/media/999999/full")
    assert reference.status_code == 404
    for path in media_paths(world):
        asset_id = int(path.split("/")[2])
        response = client.get(path)
        if asset_id in allowed:
            assert response.status_code == 200, path
        else:
            assert response.status_code == 404, path
            assert response.text == reference.text


def test_other_player_cannot_fetch_alice_media(client: TestClient, media_world: MediaWorld) -> None:
    login(client, "bob")
    assert client.get(f"/media/{media_world.bob_portrait.id}/full").status_code == 200
    for asset in (media_world.alice_portrait, media_world.alice_sheet, media_world.extra_map):
        assert client.get(f"/media/{asset.id}/full").status_code == 404


def test_deactivated_player_loses_access(
    client: TestClient, media_world: MediaWorld, db: Session
) -> None:
    login(client, "alice")
    path = f"/media/{media_world.alice_portrait.id}/full"
    assert client.get(path).status_code == 200
    media_world.alice.is_active = False
    db.commit()
    assert client.get(path, follow_redirects=False).status_code == 303


def test_unknown_variant_is_not_found(client: TestClient, media_world: MediaWorld) -> None:
    login(client, "maitre")
    assert client.get(f"/media/{media_world.alice_portrait.id}/original").status_code == 404


def test_image_response_headers(client: TestClient, media_world: MediaWorld) -> None:
    login(client, "alice")
    response = client.get(f"/media/{media_world.alice_portrait.id}/thumb")
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "private, max-age=86400"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-disposition" not in response.headers
    etag = response.headers["etag"]
    cached = client.get(
        f"/media/{media_world.alice_portrait.id}/thumb", headers={"If-None-Match": etag}
    )
    assert cached.status_code == 304
    assert cached.headers["etag"] == etag
    assert cached.content == b""


def test_svg_response_is_sandboxed_and_compressed(
    client: TestClient, media_world: MediaWorld
) -> None:
    login(client, "alice")
    path = f"/media/{media_world.extra_map.id}/full"
    response = client.get(path, headers={"Accept-Encoding": "gzip"})
    assert response.headers["content-type"] == "image/svg+xml"
    assert response.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in response.headers["vary"]
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    )
    assert b"<rect" in response.content
    plain = client.get(path, headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers
    assert plain.headers["etag"] != response.headers["etag"]


def test_pdf_is_served_as_attachment(client: TestClient, media_world: MediaWorld) -> None:
    login(client, "alice")
    response = client.get(f"/media/{media_world.alice_sheet.id}/full")
    assert response.headers["content-type"] == "application/pdf"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert 'filename="Fiche de personnage de Alice la voleuse.pdf"' in disposition
    assert response.content == PDF


def test_player_fiche_shows_portrait_and_sheet(client: TestClient, media_world: MediaWorld) -> None:
    login(client, "alice")
    page = client.get("/joueur").text
    assert f"/media/{media_world.alice_portrait.id}/thumb" in page
    assert f"/media/{media_world.alice_sheet.id}/full" in page
    assert "Fiche de personnage (PDF)" in page
    assert "Illustrations et documents" not in page
    assert 'enctype="multipart/form-data"' not in page
    extra = client.get(f"/joueur/articles/{media_world.alice_extra.wa_id}").text
    assert f"/media/{media_world.extra_map.id}/full" in extra
    assert "data-viewer-open" in extra


def test_dm_fiche_has_upload_controls(client: TestClient, media_world: MediaWorld) -> None:
    login(client, "maitre")
    page = client.get(f"/mj/articles/{media_world.alice_main.wa_id}").text
    assert "Illustrations et documents" in page
    assert page.count('enctype="multipart/form-data"') == 3
    assert f"/mj/articles/{media_world.alice_main.wa_id}/medias/" in page


def test_section_banner(client: TestClient, media_world: MediaWorld, db: Session) -> None:
    set_banner(db, media_world.extra_map, "organisation")
    login(client, "maitre")
    page = client.get("/mj/articles?section=organisation").text
    assert "map-banner" in page
    assert "map-banner" not in client.get("/mj/articles?section=monde").text
    client.post("/deconnexion", data={"csrf_token": csrf_token(client, "/mj")})
    login(client, "bob")
    assert "map-banner" not in client.get("/joueur/articles?section=organisation").text


def upload(
    client: TestClient,
    article: WaArticle,
    role: str,
    filename: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    *,
    with_csrf: bool = True,
) -> int:
    fields = {"role": role}
    if with_csrf:
        fields["csrf_token"] = csrf_token(client, f"/mj/articles/{article.wa_id}")
    response = client.post(
        f"/mj/articles/{article.wa_id}/medias",
        data=fields,
        files={"file": (filename, data, content_type)},
        follow_redirects=False,
    )
    return response.status_code


def test_dm_uploads_and_replaces_media(
    client: TestClient, media_world: MediaWorld, db: Session
) -> None:
    login(client, "maitre")
    article = media_world.bob_main
    assert upload(client, article, "sheet", "Bob.pdf", PDF, "application/pdf") == 303
    assert (
        "Fiche de personnage (PDF) enregistrée." in client.get(f"/mj/articles/{article.wa_id}").text
    )
    sheet = db.scalar(
        select(MediaAsset).where(
            MediaAsset.wa_article_id == article.wa_id, MediaAsset.role == "sheet"
        )
    )
    assert sheet is not None
    assert upload(client, article, "portrait", "new.png", png_bytes(), "image/png") == 303
    db.expire_all()
    portraits = db.scalars(
        select(MediaAsset).where(
            MediaAsset.wa_article_id == article.wa_id, MediaAsset.role == "portrait"
        )
    ).all()
    assert len(portraits) == 1
    assert portraits[0].id == media_world.bob_portrait.id
    assert portraits[0].original_filename == "new.png"
    assert portraits[0].content_type == "image/webp"


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (300, 400), (10, 20, 30, 128)).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("role", "filename", "data", "message"),
    [
        ("portrait", "notes.txt", b"hello", "Format non pris en charge"),
        ("portrait", "fake.jpg", b"\xff\xd8\xff\xe0garbage", "L'image est illisible"),
        ("portrait", "carte.svg", SVG, "Format non pris en charge"),
        ("sheet", "fiche.pdf", b"<html>not a pdf</html>", "PDF valide"),
        ("map", "carte.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><g></svg>", "SVG"),
        ("map", "vide.svg", b"", "vide"),
        ("banner", "carte.svg", SVG, "Type de fichier inconnu"),
    ],
)
def test_upload_validation(
    client: TestClient,
    media_world: MediaWorld,
    db: Session,
    role: str,
    filename: str,
    data: bytes,
    message: str,
) -> None:
    login(client, "maitre")
    article = media_world.bob_main
    before = db.scalar(select(func.count()).select_from(MediaAsset))
    assert upload(client, article, role, filename, data) == 303
    page = client.get(f"/mj/articles/{article.wa_id}").text
    assert message in page.replace("&#39;", "'")
    assert db.scalar(select(func.count()).select_from(MediaAsset)) == before


def test_upload_rejects_oversized_files(client: TestClient, media_world: MediaWorld) -> None:
    login(client, "maitre")
    article = media_world.bob_main
    data = b"%PDF-1.7\n" + b"0" * MAX_UPLOAD_BYTES
    assert upload(client, article, "sheet", "big.pdf", data) == 303
    assert "15 Mo" in client.get(f"/mj/articles/{article.wa_id}").text
    huge = b"%PDF-1.7\n" + b"0" * (MAX_UPLOAD_BYTES + 2 * 1024 * 1024)
    assert upload(client, article, "sheet", "huge.pdf", huge) == 413


def test_upload_requires_csrf(client: TestClient, media_world: MediaWorld, db: Session) -> None:
    login(client, "maitre")
    status = upload(client, media_world.bob_main, "sheet", "Bob.pdf", PDF, with_csrf=False)
    assert status == 403
    assert (
        db.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.role == "sheet"))
        == 1
    )


def test_players_and_anonymous_cannot_upload_or_remove(
    client: TestClient, media_world: MediaWorld, db: Session
) -> None:
    article = media_world.alice_main
    upload_path = f"/mj/articles/{article.wa_id}/medias"
    remove_path = f"{upload_path}/{media_world.alice_portrait.id}/supprimer"
    files = {"file": ("x.pdf", PDF, "application/pdf")}
    token = csrf_token(client, "/connexion")
    data = {"csrf_token": token, "role": "sheet"}
    response = client.post(upload_path, data=data, files=files, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/connexion"
    login(client, "alice")
    token = csrf_token(client, "/joueur")
    data = {"csrf_token": token, "role": "sheet"}
    assert client.post(upload_path, data=data, files=files).status_code == 404
    assert client.post(remove_path, data={"csrf_token": token}).status_code == 404
    assert db.get(MediaAsset, media_world.alice_portrait.id) is not None
    assert db.scalar(select(func.count()).select_from(MediaAsset)) == 6


def test_dm_removes_media(client: TestClient, media_world: MediaWorld, db: Session) -> None:
    login(client, "maitre")
    article_id = media_world.alice_main.wa_id
    asset_id = media_world.alice_portrait.id
    token = csrf_token(client, f"/mj/articles/{article_id}")
    wrong = f"/mj/articles/{media_world.bob_main.wa_id}/medias/{asset_id}/supprimer"
    assert client.post(wrong, data={"csrf_token": token}).status_code == 404
    path = f"/mj/articles/{article_id}/medias/{asset_id}/supprimer"
    response = client.post(path, data={"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    assert "Portrait retiré." in client.get(f"/mj/articles/{article_id}").text
    db.expire_all()
    assert db.get(MediaAsset, asset_id) is None
    variants = select(func.count()).select_from(MediaVariant)
    assert db.scalar(variants.where(MediaVariant.asset_id == asset_id)) == 0


def test_raster_is_reencoded_without_exif() -> None:
    processed = process_raster(jpeg_bytes(3000, 2000, orientation=6), 1400)
    full, thumb = processed.variants
    assert (full.content_type, thumb.content_type) == ("image/webp", "image/webp")
    assert (full.width, full.height) == (933, 1400)
    assert max(thumb.width or 0, thumb.height or 0) <= 640
    assert (thumb.width or 0) <= 480
    with Image.open(io.BytesIO(full.data)) as image:
        assert image.format == "WEBP"
        assert image.size == (933, 1400)
        assert not image.info.get("exif")
        assert not image.getexif()


def test_transparent_png_keeps_alpha() -> None:
    full = process_raster(png_bytes(), 1400).full
    with Image.open(io.BytesIO(full.data)) as image:
        assert image.mode == "RGBA"


def test_image_type_is_sniffed_not_trusted() -> None:
    with pytest.raises(MediaError) as error:
        process_upload(MediaRole.PORTRAIT, b"GIF89a" + b"\x00" * 100)
    assert error.value.code == "unsupported_type"


def test_svg_sanitisation() -> None:
    evil = b"""<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     width="10" height="10" onload="alert(1)">
  <script>alert(1)</script>
  <a xlink:href="javascript:alert(2)"><rect width="5" height="5" onclick="alert(3)"
     fill="url(https://evil.example/x)" style="fill:red"/></a>
  <foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>
  <image href="https://evil.example/x.png"/>
  <use xlink:href="#local"/>
  <use href="https://evil.example/sprite.svg#a"/>
  <style>@import url(https://evil.example/c.css);</style>
  <rect id="ok" fill="url(#gradient)" width="1" height="1"/>
  <set attributeName="href" to="javascript:alert(4)"/>
  <text x="1" y="2">Bonjour</text>
</svg>"""
    cleaned, width, height = sanitize_svg(evil)
    markup = cleaned.decode()
    assert (width, height) == (10, 10)
    for forbidden in (
        "script",
        "onload",
        "onclick",
        "javascript",
        "foreignObject",
        "evil.example",
        "<image",
        "@import",
        "<set",
        "xlink",
    ):
        assert forbidden not in markup, forbidden
    assert '<use href="#local"' in markup
    assert 'fill="url(#gradient)"' in markup
    assert "Bonjour" in markup
    assert markup.startswith("<svg") and 'xmlns="http://www.w3.org/2000/svg"' in markup


@pytest.mark.parametrize(
    "data",
    [
        b"<svg xmlns='http://www.w3.org/2000/svg'><g></svg>",
        b"<html><body>no</body></html>",
        b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg"><text>&a;</text></svg>',
        b"<svg><rect/></svg>",
    ],
)
def test_invalid_svg_is_rejected(data: bytes) -> None:
    with pytest.raises(MediaError) as error:
        sanitize_svg(data)
    assert error.value.code == "invalid_svg"


def test_svg_variant_stores_gzip() -> None:
    full = process_upload(MediaRole.MAP, SVG).full
    assert full.gzip_data is not None
    assert gzip.decompress(full.gzip_data) == full.data


def test_pdf_magic_header_is_checked() -> None:
    assert process_upload(MediaRole.SHEET, PDF).full.data == PDF
    with pytest.raises(MediaError):
        process_upload(MediaRole.SHEET, b"PK\x03\x04 zip file")
    with pytest.raises(MediaError) as error:
        process_upload(MediaRole.SHEET, b"%PDF-" + b"0" * MAX_UPLOAD_BYTES)
    assert error.value.code == "file_too_large"


def test_article_image_prefers_hosted_media(media_world: MediaWorld, db: Session) -> None:
    db.expire_all()
    article = db.get(WaArticle, media_world.alice_main.wa_id)
    assert article is not None
    assert article_image(article) == (
        f"/media/{media_world.alice_portrait.id}/thumb?v={media_world.alice_portrait.version}"
    )
    extra = db.get(WaArticle, media_world.alice_extra.wa_id)
    assert extra is not None
    assert article_image(extra, player_mode=True) == (
        f"/media/{media_world.extra_map.id}/thumb?v={media_world.extra_map.version}"
    )


def test_import_media_is_idempotent(tmp_path: Path, db: Session) -> None:
    article = make_article(db, "Kratéros")
    nation = make_article(db, "Le Khaanat", template_type="organization")
    (tmp_path / "krateros.jpg").write_bytes(jpeg_bytes(900, 1200))
    (tmp_path / "carte.svg").write_bytes(SVG)
    manifest = {
        "media": [
            {"file": "krateros.jpg", "article": "kratéros", "role": "portrait"},
            {"file": "carte.svg", "article_id": nation.wa_id, "role": "map", "banner": "monde"},
            {"file": "absent.pdf", "article": "Kratéros", "role": "sheet"},
            {"file": "krateros.jpg", "article": "Inconnu", "role": "portrait"},
        ]
    }
    first = import_media(db, tmp_path, manifest)
    assert [line.status for line in first] == ["created", "created", "failed", "failed"]
    second = import_media(db, tmp_path, manifest)
    assert [line.status for line in second] == ["unchanged", "unchanged", "failed", "failed"]
    (tmp_path / "krateros.jpg").write_bytes(jpeg_bytes(700, 900))
    third = import_media(db, tmp_path, manifest)
    assert third[0].status == "replaced"
    assert db.scalar(select(func.count()).select_from(MediaAsset)) == 2
    banner = db.scalar(select(MediaAsset).where(MediaAsset.banner_section == "monde"))
    assert banner is not None and banner.wa_article_id == nation.wa_id
    assert (
        db.scalar(select(MediaAsset.id).where(MediaAsset.wa_article_id == article.wa_id))
        is not None
    )


def test_import_characters_is_idempotent(db: Session) -> None:
    article = make_article(db, "Khaetar Voss")
    make_user(db, "existing")
    manifest = {
        "players": [
            {"login": "Dazen_Guile", "display_name": "Dazen_Guile"},
            {"login": "existing", "display_name": "Existing"},
        ],
        "characters": [
            {"name": "Khaetar Voss", "player": "dazen_guile", "article": "Khaetar Voss"},
            {"name": "Fantôme", "player": "nobody", "article": "Khaetar Voss"},
        ],
    }
    first = import_characters(db, manifest)
    assert [line.status for line in first] == ["created", "unchanged", "created", "failed"]
    second = import_characters(db, manifest)
    assert [line.status for line in second] == ["unchanged", "unchanged", "unchanged", "failed"]
    player = db.scalar(select(User).where(User.login == "dazen_guile"))
    assert player is not None
    assert player.role == Role.PLAYER
    assert player.password_hash is None
    assert player.display_name == "Dazen_Guile"
    assert [c.main_article_id for c in player.characters] == [article.wa_id]
