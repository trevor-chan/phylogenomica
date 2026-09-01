import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from phylogenomica.data.wikimedia_download import BinaryResponse
from phylogenomica.data.wikimedia_library import (
    load_wikimedia_library,
    update_wikimedia_library,
)

PNG_1_BY_1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00"
)


def _media_fields(species_id: int) -> dict[str, object]:
    return {
        "commons_title": f"File:{species_id}.png",
        "commons_page_url": f"https://commons.example/File:{species_id}.png",
        "original_url": f"https://upload.example/{species_id}.png",
        "thumbnail_url": f"https://upload.example/{species_id}.png?width=512",
        "mime_type": "image/png",
        "creator": "Example creator",
        "credit": "Own work",
        "license_name": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "usage_terms": "Creative Commons Attribution",
        "source_sha1": f"sha1-{species_id}",
    }


def _resolver_record(species_id: int) -> dict[str, object]:
    return {
        "species_id": species_id,
        "scientific_name": f"Species {species_id}",
        "wikidata_qid": f"Q{species_id}",
        "status": "resolved",
        "media": _media_fields(species_id),
    }


def _download_record(species_id: int, local_path: str) -> dict[str, object]:
    media = _media_fields(species_id)
    return {
        "species_id": species_id,
        "scientific_name": f"Species {species_id}",
        "wikidata_qid": f"Q{species_id}",
        "commons_title": media["commons_title"],
        "commons_page_url": media["commons_page_url"],
        "original_url": media["original_url"],
        "thumbnail_url": media["thumbnail_url"],
        "download_url": media["thumbnail_url"],
        "download_url_variant": "thumbnail",
        "final_url": media["thumbnail_url"],
        "response_content_type": "image/png",
        "mime_type": "image/png",
        "width": 1,
        "height": 1,
        "bytes": len(PNG_1_BY_1),
        "sha256": hashlib.sha256(PNG_1_BY_1).hexdigest(),
        "local_path": local_path,
        "creator": media["creator"],
        "credit": media["credit"],
        "license_name": media["license_name"],
        "license_url": media["license_url"],
        "usage_terms": media["usage_terms"],
        "original_source_sha1": media["source_sha1"],
        "transformation": "No local transformation.",
    }


def _write_download_manifest(path: Path, species_ids: list[int]) -> None:
    files = path.parent / "files"
    files.mkdir(parents=True)
    records = []
    for species_id in species_ids:
        local_path = f"files/{species_id}.png"
        (path.parent / local_path).write_bytes(PNG_1_BY_1)
        records.append(_download_record(species_id, local_path))
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "downloader_version": 1,
                "dataset_version": "test-dataset",
                "game_id": "download-game",
                "records": records,
            }
        ),
        encoding="utf-8",
    )


def _write_resolver_manifest(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "resolver_version": 2,
                "dataset_version": "test-dataset",
                "game_id": "resolver-game",
                "records": records,
            }
        ),
        encoding="utf-8",
    )


def test_imports_existing_downloads_and_reuses_them_on_repeat(
    tmp_path: Path,
) -> None:
    source = tmp_path / "download" / "manifest.json"
    _write_download_manifest(source, [2, 1])
    root = tmp_path / "library"
    fixed = datetime(2026, 9, 1, 15, 0, tzinfo=UTC)

    manifest_path, first = update_wikimedia_library(
        source, library_root=root, clock=lambda: fixed
    )
    _, second = update_wikimedia_library(
        source, library_root=root, clock=lambda: fixed
    )

    assert first["record_count"] == 2
    assert first["last_update"]["imported_count"] == 2
    assert second["last_update"]["imported_count"] == 0
    assert second["last_update"]["reused_count"] == 2
    assert len(second["source_manifests"]) == 1
    assert [record["species_id"] for record in second["records"]] == [1, 2]
    assert all("rights" in record for record in second["records"])
    library = load_wikimedia_library(
        manifest_path, expected_dataset_version="test-dataset"
    )
    assert library.asset(1).path.read_bytes() == PNG_1_BY_1  # type: ignore[union-attr]


def test_resolver_update_downloads_only_missing_or_source_changed_records(
    tmp_path: Path,
) -> None:
    download = tmp_path / "download" / "manifest.json"
    _write_download_manifest(download, [1])
    root = tmp_path / "library"
    update_wikimedia_library(download, library_root=root)
    resolver = tmp_path / "resolver.json"
    _write_resolver_manifest(resolver, [_resolver_record(1), _resolver_record(2)])
    requested: list[str] = []

    def fetch(url, _context, _max_bytes):
        requested.append(url)
        return BinaryResponse(PNG_1_BY_1, "image/png", url)

    _, first = update_wikimedia_library(
        resolver, library_root=root, fetch_binary=fetch
    )
    assert requested == ["https://upload.example/2.png?width=512"]
    assert first["last_update"]["downloaded_count"] == 1
    assert first["last_update"]["reused_count"] == 1
    assert first["record_count"] == 2

    records = [_resolver_record(1)]
    records[0]["media"]["thumbnail_url"] += "&revision=2"  # type: ignore[index,operator]
    _write_resolver_manifest(resolver, records)
    requested.clear()
    _, second = update_wikimedia_library(
        resolver, library_root=root, fetch_binary=fetch
    )

    assert requested == ["https://upload.example/1.png?width=512&revision=2"]
    assert second["last_update"]["downloaded_count"] == 1
    assert second["last_update"]["reused_count"] == 0
    # An incremental update never discards species absent from the new game.
    assert [record["species_id"] for record in second["records"]] == [1, 2]


def test_blocks_unrecognized_rights_before_downloading(tmp_path: Path) -> None:
    record = _resolver_record(1)
    record["media"]["license_name"] = "Mystery license"  # type: ignore[index]
    resolver = tmp_path / "resolver.json"
    _write_resolver_manifest(resolver, [record])

    def unexpected_fetch(*_args):
        raise AssertionError("blocked media must not be downloaded")

    _, manifest = update_wikimedia_library(
        resolver,
        library_root=tmp_path / "library",
        fetch_binary=unexpected_fetch,
    )

    assert manifest["record_count"] == 0
    assert manifest["last_update"]["blocked_count"] == 1
    assert manifest["last_update"]["blocked_species_ids"] == [1]
