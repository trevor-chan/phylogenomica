import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from phylogenomica.data.wikimedia_download import (
    BinaryResponse,
    WikimediaDownloadError,
    _image_details,
    download_resolved_record,
    download_wikimedia_assets,
)

PNG_1_BY_1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00"
)
JPEG_3_BY_2 = (
    b"\xff\xd8\xff\xc0\x00\x0b\x08\x00\x02\x00\x03\x01\x01\x11\x00\xff\xd9"
)


def _record(species_id: int, status: str = "resolved") -> dict[str, object]:
    return {
        "species_id": species_id,
        "scientific_name": f"Species {species_id}",
        "wikidata_qid": f"Q{species_id}",
        "status": status,
        "media": {
            "commons_title": f"File:{species_id}.png",
            "commons_page_url": f"https://commons.example/File:{species_id}.png",
            "original_url": f"https://upload.example/{species_id}.png",
            "thumbnail_url": (
                f"https://upload.example/{species_id}.png?width=512&utm_source=api"
            ),
            "mime_type": "image/png",
            "width": 1200,
            "height": 800,
            "thumbnail_width": 512,
            "thumbnail_height": 341,
            "creator": "Example creator",
            "credit": "Own work",
            "license_name": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "usage_terms": "Creative Commons Attribution",
            "attribution_required": True,
            "source_sha1": "base36sha1",
        },
    }


def _write_manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "resolver_version": 2,
                "dataset_version": "test-dataset",
                "game_id": "test-game",
                "records": [
                    _record(2),
                    _record(1),
                    _record(3, "incomplete_attribution"),
                ],
            }
        ),
        encoding="utf-8",
    )


def test_downloads_only_resolved_records_with_validation_and_provenance(
    tmp_path: Path,
) -> None:
    source = tmp_path / "resolver.json"
    _write_manifest(source)
    requested: list[str] = []

    def fetch(url, _context, _max_bytes):
        requested.append(url)
        return BinaryResponse(PNG_1_BY_1, "image/png; charset=binary", url)

    fixed = datetime(2026, 9, 1, 15, 0, tzinfo=UTC)
    manifest_path, manifest = download_wikimedia_assets(
        source,
        output_root=tmp_path / "assets",
        fetch_binary=fetch,
        clock=lambda: fixed,
    )

    assert manifest_path.is_file()
    assert requested == [
        "https://upload.example/1.png?width=512&utm_source=api",
        "https://upload.example/2.png?width=512&utm_source=api",
    ]
    assert manifest["eligible_resolved_count"] == 2
    assert manifest["downloaded_count"] == 2
    assert manifest["skipped_status_counts"] == {"incomplete_attribution": 1}
    assert manifest["review_status"] == (
        "working-copy-requires-visual-and-license-review"
    )
    first = manifest["records"][0]
    assert first["width"] == first["height"] == 1
    assert first["source_width"] == 1200
    assert first["requested_thumbnail_width"] == 512
    assert first["original_url"] == "https://upload.example/1.png"
    assert first["sha256"] == hashlib.sha256(PNG_1_BY_1).hexdigest()
    assert first["license_name"] == "CC BY 4.0"
    assert (manifest_path.parent / first["local_path"]).read_bytes() == PNG_1_BY_1


def test_reads_jpeg_dimensions_from_start_of_frame() -> None:
    details = _image_details(JPEG_3_BY_2)

    assert details.mime_type == "image/jpeg"
    assert details.width == 3
    assert details.height == 2


def test_limit_selects_a_deterministic_prefix(tmp_path: Path) -> None:
    source = tmp_path / "resolver.json"
    _write_manifest(source)

    def fetch(url, _context, _max_bytes):
        return BinaryResponse(PNG_1_BY_1, "image/png", url)

    _, manifest = download_wikimedia_assets(
        source,
        output_root=tmp_path / "assets",
        limit=1,
        fetch_binary=fetch,
    )

    assert [record["species_id"] for record in manifest["records"]] == [1]
    assert manifest["eligible_resolved_count"] == 2


def test_rejects_content_type_that_disagrees_with_image_signature(
    tmp_path: Path,
) -> None:
    source = tmp_path / "resolver.json"
    _write_manifest(source)

    def fetch(url, _context, _max_bytes):
        return BinaryResponse(PNG_1_BY_1, "text/html", url)

    with pytest.raises(WikimediaDownloadError, match="content type"):
        download_wikimedia_assets(
            source,
            output_root=tmp_path / "assets",
            limit=1,
            fetch_binary=fetch,
        )


def test_rejects_non_image_bytes_even_with_an_image_content_type(
    tmp_path: Path,
) -> None:
    source = tmp_path / "resolver.json"
    _write_manifest(source)

    def fetch(url, _context, _max_bytes):
        return BinaryResponse(b"<html>challenge</html>", "image/png", url)

    with pytest.raises(WikimediaDownloadError, match="not a supported"):
        download_wikimedia_assets(
            source,
            output_root=tmp_path / "assets",
            limit=1,
            fetch_binary=fetch,
        )


GIF_1_BY_1 = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"


def test_accepts_a_thumbnail_rendition_of_an_unsupported_source_format(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path)
    record = _record(1)
    # Commons renders a TIFF or SVG original as JPEG or PNG at thumbnail size.
    record["media"]["mime_type"] = "image/tiff"  # type: ignore[index]

    def fetch(url, _context, _max_bytes):
        return BinaryResponse(PNG_1_BY_1, "image/png", url)

    downloaded, body = download_resolved_record(
        record, context=None, max_bytes=1024, fetch_binary=fetch
    )

    # What was downloaded is what is recorded; the source format stays as
    # provenance on the original_url fields.
    assert downloaded["mime_type"] == "image/png"
    assert downloaded["download_url_variant"] == "thumbnail"
    assert downloaded["local_path"].endswith(".png")
    assert body == PNG_1_BY_1


def test_an_original_download_must_still_match_the_resolved_media_type() -> None:
    record = _record(1)
    record["media"]["mime_type"] = "image/tiff"  # type: ignore[index]
    del record["media"]["thumbnail_url"]  # type: ignore[index]

    def fetch(url, _context, _max_bytes):
        return BinaryResponse(PNG_1_BY_1, "image/png", url)

    with pytest.raises(WikimediaDownloadError, match="unsupported media type"):
        download_resolved_record(
            record, context=None, max_bytes=1024, fetch_binary=fetch
        )


def test_reads_gif_dimensions_from_the_logical_screen_descriptor() -> None:
    record = _record(1)
    record["media"]["mime_type"] = "image/gif"  # type: ignore[index]

    def fetch(url, _context, _max_bytes):
        return BinaryResponse(GIF_1_BY_1, "image/gif", url)

    downloaded, _ = download_resolved_record(
        record, context=None, max_bytes=1024, fetch_binary=fetch
    )

    # Commons serves GIF sources as GIF even at thumbnail sizes.
    assert downloaded["mime_type"] == "image/gif"
    assert (downloaded["width"], downloaded["height"]) == (1, 1)
    assert downloaded["local_path"].endswith(".gif")


def test_still_rejects_a_thumbnail_whose_bytes_belie_its_content_type() -> None:
    record = _record(1)
    record["media"]["mime_type"] = "image/tiff"  # type: ignore[index]

    def fetch(url, _context, _max_bytes):
        return BinaryResponse(PNG_1_BY_1, "image/jpeg", url)

    with pytest.raises(WikimediaDownloadError, match="signature and response"):
        download_resolved_record(
            record, context=None, max_bytes=1024, fetch_binary=fetch
        )
