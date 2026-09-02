import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from phylogenomica.data.wikimedia_rights import (
    classify_rights,
    evaluate_wikimedia_rights,
)


def _record(
    species_id: int,
    license_name: str | None,
    *,
    creator: str | None = "Example creator",
) -> dict[str, object]:
    return {
        "species_id": species_id,
        "scientific_name": f"Species {species_id}",
        "commons_title": f"File:Species-{species_id}.jpg",
        "commons_page_url": f"https://commons.example/Species-{species_id}.jpg",
        "license_name": license_name,
        "license_url": "http://license.example/old-link",
        "creator": creator,
        "credit": None,
        "sha256": hashlib.sha256(str(species_id).encode()).hexdigest(),
        "transformation": "No local transformation; Wikimedia thumbnail.",
    }


def _write_manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_version": "test-dataset",
                "game_id": "test-game",
                "records": [
                    _record(1, "CC BY 4.0"),
                    _record(2, "CC BY-SA 2.0 de"),
                    _record(3, "CC0"),
                    _record(4, "Public domain"),
                    _record(5, "GFDL 1.2"),
                    _record(6, "Copyrighted free use"),
                    _record(7, "No restrictions"),
                    _record(8, "Mystery license"),
                ],
            }
        ),
        encoding="utf-8",
    )


def test_classifies_standard_cc_with_canonical_url_and_requirements() -> None:
    rights = classify_rights(_record(1, "CC BY-SA 2.0 de"))

    assert rights["identifier"] == "CC-BY-SA-2.0-DE"
    assert rights["rights_url"] == (
        "https://creativecommons.org/licenses/by-sa/2.0/de/"
    )
    assert rights["promotion_status"] == "ready"
    assert rights["requires_attribution"] is True
    assert rights["requires_share_alike"] is True
    assert rights["suggested_review_decision"] == "accept"


def test_keeps_recognized_nonstandard_claims_available_but_conditional() -> None:
    for label in ("Public domain", "GFDL 1.2", "Copyrighted free use"):
        rights = classify_rights(_record(1, label))
        assert rights["working_use_allowed"] is True
        assert rights["promotion_ready"] is False
        assert rights["promotion_status"] == "conditional"
        assert rights["suggested_review_decision"] == "conditional"


def test_blocks_unknown_labels_or_missing_required_attribution() -> None:
    unknown = classify_rights(_record(1, "Mystery license"))
    unattributed = classify_rights(_record(2, "CC BY 4.0", creator=None))

    assert unknown["promotion_status"] == "blocked"
    assert unknown["working_use_allowed"] is False
    assert unattributed["promotion_status"] == "blocked"
    assert "Restore the required creator" in unattributed["requirements"][-1]


def test_writes_pinned_rights_manifest_with_policy_counts(tmp_path: Path) -> None:
    source = tmp_path / "manifest.json"
    _write_manifest(source)
    fixed = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)

    output, manifest = evaluate_wikimedia_rights(
        source,
        clock=lambda: fixed,
        reproduction_command="test command",
    )

    assert output.name == "rights.json"
    assert manifest["status_counts"] == {
        "blocked": 1,
        "conditional": 4,
        "ready": 3,
    }
    assert manifest["source"]["download_manifest_sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    assert manifest["policy"]["commercial_intent"] is False
    assert manifest["records"][0]["attribution_text"].startswith(
        '"File:Species-1.jpg" by Example creator'
    )


@pytest.mark.parametrize(
    ("label", "identifier", "url"),
    [
        ("CC BY 3.0 us", "CC-BY-3.0-US",
         "https://creativecommons.org/licenses/by/3.0/us/"),
        ("CC BY-SA 2.0 fr", "CC-BY-SA-2.0-FR",
         "https://creativecommons.org/licenses/by-sa/2.0/fr/"),
        ("CC BY 2.5 au", "CC-BY-2.5-AU",
         "https://creativecommons.org/licenses/by/2.5/au/"),
        ("CC BY-SA 1.0", "CC-BY-SA-1.0",
         "https://creativecommons.org/licenses/by-sa/1.0/"),
    ],
)
def test_accepts_jurisdiction_ported_creative_commons_labels(
    label: str, identifier: str, url: str
) -> None:
    rights = classify_rights(
        {
            "license_name": label,
            "creator": "Jane Doe",
            "commons_page_url": "https://commons.example/File:X.jpg",
            "commons_title": "File:X.jpg",
            "transformation": "none",
        }
    )

    # A port is the same licence under a national adaptation, carrying the
    # same obligations, so it is classified rather than left unrecognized.
    assert rights["identifier"] == identifier
    assert rights["rights_url"] == url
    assert rights["promotion_status"] == "ready"
    assert rights["working_use_allowed"] is True


def test_still_blocks_a_label_that_is_not_a_creative_commons_licence() -> None:
    for label in ("GPL", "CC BY 9.9", "Nonsense", "CC XX 3.0 us"):
        rights = classify_rights(
            {
                "license_name": label,
                "creator": "Jane Doe",
                "commons_page_url": "https://commons.example/File:X.jpg",
                "commons_title": "File:X.jpg",
                "transformation": "none",
            }
        )
        assert rights["working_use_allowed"] is False, label
