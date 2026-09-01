import json
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from phylogenomica.data.cards import CardImage, MetadataSource, SpeciesCard
from phylogenomica.data.wikimedia import (
    COMMONS_API_URL,
    WIKIDATA_API_URL,
    WikimediaResolutionError,
    _commons_media,
    _fetch_json_curl,
    _p18_candidates,
    main,
    resolve_game_wikimedia,
)
from phylogenomica.generation.feasibility import FeasibilityConfig
from phylogenomica.generation.game import (
    GAME_GENERATOR_VERSION,
    GAME_SCHEMA_VERSION,
    GameMember,
    GeneratedGame,
    GeneratedStage,
)


def _card(species_id: int) -> SpeciesCard:
    return SpeciesCard(
        species_id=species_id,
        scientific_name=f"Genus species{species_id}",
        english_name=f"Common {species_id}",
        ott_id=100 + species_id,
        popularity_rank=species_id,
        vernacular_source=MetadataSource("vernacular_by_ott", species_id),
        image=CardImage(
            url=f"https://example.test/{species_id}.jpg",
            rights="Historical author",
            license="CC BY 4.0",
            source_code=99,
            source_id=str(species_id),
            source=MetadataSource("images_by_ott", species_id),
        ),
    )


def _member(species_id: int, role: str, tier: int | None) -> GameMember:
    return GameMember(
        species_id=species_id,
        role=role,  # type: ignore[arg-type]
        tier_index=tier,
        ancestor_node_id=None if tier is None else 1000 + tier,
        card=_card(species_id),
    )


def _game() -> GeneratedGame:
    return GeneratedGame(
        schema_version=GAME_SCHEMA_VERSION,
        game_id="a" * 64,
        dataset_version="test-wikimedia-1",
        generator_version=GAME_GENERATOR_VERSION,
        selector_version=1,
        eligibility_index_version=1,
        target_id=6,
        seed=7,
        configuration=FeasibilityConfig(members_per_stage=3, stages_per_game=2),
        stages=(
            GeneratedStage(
                stage_index=0,
                start_node_id=1000,
                end_node_id=1002,
                members=(
                    _member(1, "decoy", 0),
                    _member(2, "mulligan", 1),
                    _member(3, "unlock", 2),
                ),
                tiers=(),
                mulligan_species_ids=(2,),
                unlock_species_ids=(3,),
                target_species_id=None,
            ),
            GeneratedStage(
                stage_index=1,
                start_node_id=1003,
                end_node_id=1004,
                members=(
                    _member(4, "decoy", 3),
                    _member(5, "mulligan", 4),
                    _member(6, "target", None),
                ),
                tiers=(),
                mulligan_species_ids=(5,),
                unlock_species_ids=(),
                target_species_id=6,
            ),
        ),
    )


def _write_database(path: Path, *, dataset_version: str = "test-wikimedia-1") -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA user_version = 1;
        CREATE TABLE dataset_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE leaves (
            leaf_id INTEGER PRIMARY KEY,
            scientific_name TEXT,
            wikidata_id INTEGER
        );
        """
    )
    connection.execute(
        "INSERT INTO dataset_metadata VALUES ('dataset_version', ?)",
        (dataset_version,),
    )
    connection.executemany(
        "INSERT INTO leaves VALUES (?, ?, ?)",
        (
            (1, "Genus species1", 1),
            (2, "Genus species2", 2),
            (3, "Genus species3", None),
            (4, "Genus species4", 4),
            (5, "Genus species5", 5),
            (6, "Genus species6", 6),
        ),
    )
    connection.commit()
    connection.close()


def _p18(filename: str, *, rank: str = "normal") -> dict[str, object]:
    return {
        "rank": rank,
        "mainsnak": {
            "snaktype": "value",
            "datavalue": {"type": "string", "value": filename},
        },
    }


def _image_page(
    filename: str, *, artist: str | None, license_name: str = "CC BY-SA 4.0"
) -> dict[str, object]:
    extmetadata: dict[str, object] = {
        "Credit": {"value": "Own work"},
        "LicenseShortName": {"value": license_name},
        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"},
        "UsageTerms": {"value": "Creative Commons Attribution-Share Alike"},
        "AttributionRequired": {"value": "true"},
    }
    if artist is not None:
        extmetadata["Artist"] = {"value": artist}
    else:
        extmetadata.pop("Credit")
    return {
        "pageid": 10,
        "title": f"File:{filename}",
        "imageinfo": [
            {
                "url": (
                    f"https://upload.wikimedia.org/{filename}"
                    "?utm_source=commons.wikimedia.org&utm_campaign=imageinfo"
                ),
                "descriptionurl": (
                    f"https://commons.wikimedia.org/wiki/File:{filename}"
                    "?uselang=en&utm_source=api"
                ),
                "thumburl": (
                    f"https://upload.wikimedia.org/thumb/{filename}"
                    "?width=512&utm_source=commons"
                ),
                "mime": "image/jpeg",
                "size": 1234,
                "width": 1200,
                "height": 800,
                "thumbwidth": 512,
                "thumbheight": 341,
                "sha1": "base36sha1",
                "extmetadata": extmetadata,
            }
        ],
    }


class _FakeWikimedia:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, endpoint, parameters, _context):
        self.calls.append((endpoint, dict(parameters)))
        if endpoint == WIKIDATA_API_URL:
            assert parameters["ids"] == "Q1|Q2|Q4|Q5|Q6"
            return {
                "entities": {
                    "Q1": {
                        "claims": {
                            "P18": [
                                _p18("Other.jpg"),
                                _p18("Resolved.jpg", rank="preferred"),
                            ]
                        }
                    },
                    "Q2": {"claims": {}},
                    "Q4": {"missing": ""},
                    "Q5": {"claims": {"P18": [_p18("Missing.jpg")]}},
                    "Q6": {"claims": {"P18": [_p18("Incomplete.jpg")]}},
                }
            }
        if endpoint == COMMONS_API_URL:
            assert parameters["titles"] == (
                "File:Incomplete.jpg|File:Missing.jpg|File:Resolved.jpg"
            )
            return {
                "query": {
                    "pages": [
                        _image_page("Incomplete.jpg", artist=None),
                        {"title": "File:Missing.jpg", "missing": True},
                        _image_page(
                            "Resolved.jpg",
                            artist='<a href="/wiki/User:Jane">Jane Doe</a>',
                        ),
                    ]
                }
            }
        raise AssertionError(endpoint)


def test_prefers_ranked_p18_and_ignores_deprecated_or_duplicate_claims() -> None:
    entity = {
        "claims": {
            "P18": [
                _p18("normal.jpg"),
                _p18("preferred.jpg", rank="preferred"),
                _p18("ignored.jpg", rank="deprecated"),
                _p18("NORMAL.JPG"),
            ]
        }
    }

    assert _p18_candidates(entity) == ("preferred.jpg", "normal.jpg")


def test_resolves_a_game_and_preserves_explicit_failure_outcomes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "onezoom.sqlite3"
    cache_root = tmp_path / "cache"
    _write_database(database)
    fetcher = _FakeWikimedia()
    fixed = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)

    manifest_path, manifest = resolve_game_wikimedia(
        _game(),
        normalized_database=database,
        cache_root=cache_root,
        fetch_json=fetcher,
        clock=lambda: fixed,
    )

    assert manifest_path.is_file()
    assert manifest["species_count"] == 6
    assert manifest["status_counts"] == {
        "commons_page_missing": 1,
        "incomplete_attribution": 1,
        "missing_p18": 1,
        "missing_wikidata_id": 1,
        "resolved": 1,
        "wikidata_entity_missing": 1,
    }
    by_id = {record["species_id"]: record for record in manifest["records"]}
    assert by_id[1]["p18_candidates"] == ["Resolved.jpg", "Other.jpg"]
    assert by_id[1]["selected_p18"] == "Resolved.jpg"
    assert by_id[1]["media"]["creator"] == "Jane Doe"
    assert by_id[1]["media"]["license_name"] == "CC BY-SA 4.0"
    assert by_id[1]["media"]["original_url"].endswith(
        "?utm_source=commons.wikimedia.org&utm_campaign=imageinfo"
    )
    assert by_id[1]["media"]["thumbnail_url"].endswith(
        "?width=512&utm_source=commons"
    )
    assert by_id[1]["media"]["commons_page_url"].endswith(
        "?uselang=en&utm_source=api"
    )
    assert by_id[3]["status"] == "missing_wikidata_id"
    assert by_id[5]["status"] == "commons_page_missing"
    assert by_id[6]["media"]["missing_attribution_fields"] == [
        "creator_or_credit"
    ]
    assert len(manifest["raw_requests"]) == 2
    assert all(not request["cache_hit"] for request in manifest["raw_requests"])
    for request in manifest["raw_requests"]:
        raw = cache_root / _game().dataset_version / _game().game_id / request["path"]
        assert raw.is_file()

    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert loaded["records"] == manifest["records"]
    assert loaded["review_status"] == "required-before-download-or-promotion"
    assert len(fetcher.calls) == 2


def test_reuses_raw_request_cache_without_network(tmp_path: Path) -> None:
    database = tmp_path / "onezoom.sqlite3"
    cache_root = tmp_path / "cache"
    _write_database(database)
    fixed = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    resolve_game_wikimedia(
        _game(),
        normalized_database=database,
        cache_root=cache_root,
        fetch_json=_FakeWikimedia(),
        clock=lambda: fixed,
    )

    def no_network(*_args):
        raise AssertionError("cached rerun attempted a network request")

    manifest_path, manifest = resolve_game_wikimedia(
        _game(),
        normalized_database=database,
        cache_root=cache_root,
        fetch_json=no_network,
        clock=lambda: fixed,
    )

    assert all(request["cache_hit"] for request in manifest["raw_requests"])
    assert manifest["status_counts"]["resolved"] == 1


def test_resolves_only_a_requested_subset_of_game_species(tmp_path: Path) -> None:
    database = tmp_path / "onezoom.sqlite3"
    _write_database(database)

    def fetch_subset(endpoint, parameters, _context):
        if endpoint == WIKIDATA_API_URL:
            assert parameters["ids"] == "Q1"
            return {
                "entities": {
                    "Q1": {
                        "claims": {"P18": [_p18("Resolved.jpg")]},
                    }
                }
            }
        assert endpoint == COMMONS_API_URL
        assert parameters["titles"] == "File:Resolved.jpg"
        return {
            "query": {
                "pages": [_image_page("Resolved.jpg", artist="Jane Doe")]
            }
        }

    manifest_path, manifest = resolve_game_wikimedia(
        _game(),
        normalized_database=database,
        cache_root=tmp_path / "cache",
        species_ids={1, 3},
        fetch_json=fetch_subset,
    )

    assert manifest["species_count"] == 2
    assert manifest_path.name.startswith("manifest-subset-")
    assert manifest["game_species_count"] == 6
    assert manifest["configuration"]["species_scope"] == "subset"
    assert [record["species_id"] for record in manifest["records"]] == [1, 3]
    assert manifest["status_counts"] == {
        "missing_wikidata_id": 1,
        "resolved": 1,
    }


def test_rejects_a_mismatched_normalized_dataset(tmp_path: Path) -> None:
    database = tmp_path / "onezoom.sqlite3"
    _write_database(database, dataset_version="other-version")

    with pytest.raises(WikimediaResolutionError, match="dataset versions differ"):
        resolve_game_wikimedia(
            _game(),
            normalized_database=database,
            cache_root=tmp_path / "cache",
            fetch_json=_FakeWikimedia(),
        )


def test_classifies_non_image_commons_media() -> None:
    status, media = _commons_media(
        {"title": "File:Recording.ogg", "imageinfo": [{"mime": "audio/ogg"}]}
    )

    assert status == "unsupported_media"
    assert media is None


def test_classifies_an_image_without_a_download_url() -> None:
    page = _image_page("No-url.jpg", artist="Example creator")
    info = page["imageinfo"][0]  # type: ignore[index]
    del info["url"]  # type: ignore[index]
    del info["thumburl"]  # type: ignore[index]

    status, media = _commons_media(page)

    assert status == "missing_image_url"
    assert media is not None
    assert media["original_url"] is None
    assert media["thumbnail_url"] is None


def test_curl_transport_uses_an_argument_list_and_parses_json(monkeypatch) -> None:
    def run(command, **options):
        assert command[0] == "curl"
        assert command[-1].startswith(WIKIDATA_API_URL)
        assert options["check"] is False
        return subprocess.CompletedProcess(
            command, 0, stdout='{"entities": {"Q1": {}}}', stderr=""
        )

    monkeypatch.setattr("phylogenomica.data.wikimedia.subprocess.run", run)

    payload = _fetch_json_curl(
        WIKIDATA_API_URL, {"action": "wbgetentities", "ids": "Q1"}, None
    )

    assert payload == {"entities": {"Q1": {}}}


def test_command_line_reports_the_written_manifest(monkeypatch, capsys) -> None:
    game = _game()
    monkeypatch.setattr("phylogenomica.data.wikimedia.load_game", lambda _path: game)
    monkeypatch.setattr(
        "phylogenomica.data.wikimedia.resolve_game_wikimedia",
        lambda *_args, **_kwargs: (
            Path("data/cache/wikimedia/manifest.json"),
            {"status_counts": {"resolved": 6}},
        ),
    )

    main(["game.json"])

    assert "resolved=6" in capsys.readouterr().out
