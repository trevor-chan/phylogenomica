import hashlib
import json
from pathlib import Path

import pytest

from phylogenomica.data.wikimedia_review import (
    WikimediaReviewError,
    generate_wikimedia_review,
)
from phylogenomica.data.wikimedia_rights import evaluate_wikimedia_rights

IMAGE = b"verified image bytes"


def _write_download(path: Path, *, checksum: str | None = None) -> None:
    files = path.parent / "files"
    files.mkdir()
    (files / "1.jpg").write_bytes(IMAGE)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_version": "test-dataset",
                "game_id": "test-game",
                "records": [
                    {
                        "species_id": 1,
                        "scientific_name": "Species <script>alert(1)</script>",
                        "commons_title": "File:Species.jpg",
                        "commons_page_url": "https://commons.example/Species.jpg",
                        "local_path": "files/1.jpg",
                        "mime_type": "image/jpeg",
                        "width": 640,
                        "height": 480,
                        "bytes": len(IMAGE),
                        "sha256": checksum or hashlib.sha256(IMAGE).hexdigest(),
                        "creator": "Example creator",
                        "credit": None,
                        "license_name": "CC BY 4.0",
                        "license_url": "https://creativecommons.org/licenses/by/4.0/",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_rights(path: Path) -> None:
    evaluate_wikimedia_rights(path)


def test_generates_review_page_after_verifying_assets(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_download(manifest)
    _write_rights(manifest)

    output, count = generate_wikimedia_review(manifest)

    rendered = output.read_text(encoding="utf-8")
    assert count == 1
    assert output.name == "review.html"
    assert "Wikimedia candidate review" in rendered
    assert "Species \\u003cscript\\u003ealert(1)" in rendered
    assert "Species <script>" not in rendered
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() in rendered
    assert "Export review JSON" in rendered
    assert '"rights_policy_version":1' in rendered
    assert '"suggested_review_decision":"accept"' in rendered
    assert 'JSON.stringify(review, null, 2) + "\\n"' in rendered


def test_rejects_a_working_asset_with_a_changed_checksum(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_download(manifest, checksum="0" * 64)
    _write_rights(manifest)

    with pytest.raises(WikimediaReviewError, match="checksum differs"):
        generate_wikimedia_review(manifest)


def test_rejects_an_unsafe_local_path(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_download(manifest)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["records"][0]["local_path"] = "../outside.jpg"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    _write_rights(manifest)

    with pytest.raises(WikimediaReviewError, match="unsafe asset path"):
        generate_wikimedia_review(manifest)


def test_rejects_rights_manifest_for_an_older_download_manifest(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    _write_download(manifest)
    _write_rights(manifest)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["records"][0]["scientific_name"] = "Changed species"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(WikimediaReviewError, match="does not match"):
        generate_wikimedia_review(manifest)
