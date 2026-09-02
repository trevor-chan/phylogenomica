import json
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from phylogenomica.data.cards import CardImage, MetadataSource, SpeciesCard
from phylogenomica.data.wikimedia import (
    WIKIDATA_API_URL,
    WikimediaResolutionError,
)
from phylogenomica.data.wikipedia import (
    MAX_EXTRACT_CHARACTERS,
    WIKIPEDIA_API_URL,
    WikipediaResolutionError,
    _trim_extract,
    main,
    resolve_game_wikipedia,
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
        game_id="b" * 64,
        dataset_version="test-wikipedia-1",
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


def _write_database(path: Path, *, dataset_version: str = "test-wikipedia-1") -> None:
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


def _article(title: str, extract: str, *, page_id: int = 10) -> dict[str, object]:
    return {
        "pageid": page_id,
        "lastrevid": 5000 + page_id,
        "title": title,
        "extract": extract,
        "fullurl": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
        "canonicalurl": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
    }


class _FakeWikipedia:
    """Cover every resolution outcome the module defines a status for."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, endpoint, parameters, _context):
        self.calls.append((endpoint, dict(parameters)))
        if endpoint == WIKIDATA_API_URL:
            assert parameters["props"] == "sitelinks"
            assert parameters["sitefilter"] == "enwiki"
            assert parameters["ids"] == "Q1|Q2|Q4|Q5|Q6"
            return {
                "entities": {
                    "Q1": {"sitelinks": {"enwiki": {"title": "Resolved species"}}},
                    # No English article at all.
                    "Q2": {"sitelinks": {}},
                    "Q4": {"missing": ""},
                    "Q5": {"sitelinks": {"enwiki": {"title": "Deleted species"}}},
                    "Q6": {"sitelinks": {"enwiki": {"title": "Blank species"}}},
                }
            }
        if endpoint == WIKIPEDIA_API_URL:
            assert parameters["explaintext"] == "1"
            assert parameters["exintro"] == "1"
            assert parameters["titles"] == (
                "Blank species|Deleted species|Resolved species"
            )
            return {
                "query": {
                    "redirects": [
                        {"from": "Resolved species", "to": "Resolved species (fish)"}
                    ],
                    "pages": [
                        _article(
                            "Resolved species (fish)",
                            "A  freshwater\nfish of the family Examplidae.",
                            page_id=11,
                        ),
                        {"title": "Deleted species", "missing": True},
                        _article("Blank species", "   ", page_id=13),
                    ],
                }
            }
        raise AssertionError(endpoint)


def test_resolves_a_game_and_preserves_explicit_failure_outcomes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "onezoom.sqlite3"
    cache_root = tmp_path / "cache"
    _write_database(database)
    fetcher = _FakeWikipedia()
    fixed = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)

    manifest_path, manifest = resolve_game_wikipedia(
        _game(),
        normalized_database=database,
        cache_root=cache_root,
        fetch_json=fetcher,
        clock=lambda: fixed,
    )

    assert manifest_path.is_file()
    assert manifest["species_count"] == 6
    assert manifest["status_counts"] == {
        "missing_extract": 1,
        "missing_sitelink": 1,
        "missing_wikidata_id": 1,
        "resolved": 1,
        "wikidata_entity_missing": 1,
        "wikipedia_page_missing": 1,
    }
    by_id = {record["species_id"]: record for record in manifest["records"]}
    text = by_id[1]["text"]
    # A redirect is followed, and the extract arrives whitespace-normalized.
    assert text["title"] == "Resolved species (fish)"
    assert text["extract"] == "A freshwater fish of the family Examplidae."
    assert text["extract_truncated"] is False
    assert text["revision_id"] == 5011
    assert text["license_name"] == "CC BY-SA 4.0"
    assert "Resolved species (fish)" in text["attribution_text"]
    assert text["url"] == "https://en.wikipedia.org/wiki/Resolved_species_(fish)"
    assert by_id[2]["status"] == "missing_sitelink"
    assert by_id[3]["status"] == "missing_wikidata_id"
    assert by_id[4]["status"] == "wikidata_entity_missing"
    assert by_id[5]["status"] == "wikipedia_page_missing"
    assert by_id[6]["status"] == "missing_extract"
    assert len(manifest["raw_requests"]) == 2
    assert all(not request["cache_hit"] for request in manifest["raw_requests"])
    for request in manifest["raw_requests"]:
        raw = cache_root / _game().dataset_version / _game().game_id / request["path"]
        assert raw.is_file()

    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert loaded["records"] == manifest["records"]
    assert loaded["text_license"]["name"] == "CC BY-SA 4.0"
    assert len(fetcher.calls) == 2


def test_reuses_raw_request_cache_without_network(tmp_path: Path) -> None:
    database = tmp_path / "onezoom.sqlite3"
    cache_root = tmp_path / "cache"
    _write_database(database)
    resolve_game_wikipedia(
        _game(),
        normalized_database=database,
        cache_root=cache_root,
        fetch_json=_FakeWikipedia(),
    )

    def no_network(*_args):
        raise AssertionError("cached rerun attempted a network request")

    _, manifest = resolve_game_wikipedia(
        _game(),
        normalized_database=database,
        cache_root=cache_root,
        fetch_json=no_network,
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
                    "Q1": {"sitelinks": {"enwiki": {"title": "Resolved species"}}}
                }
            }
        assert endpoint == WIKIPEDIA_API_URL
        assert parameters["titles"] == "Resolved species"
        return {"query": {"pages": [_article("Resolved species", "A species.")]}}

    manifest_path, manifest = resolve_game_wikipedia(
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
    assert manifest["status_counts"] == {
        "missing_wikidata_id": 1,
        "resolved": 1,
    }


def test_rejects_a_mismatched_normalized_dataset(tmp_path: Path) -> None:
    database = tmp_path / "onezoom.sqlite3"
    _write_database(database, dataset_version="other-version")

    with pytest.raises(WikipediaResolutionError, match="dataset versions differ"):
        resolve_game_wikipedia(
            _game(),
            normalized_database=database,
            cache_root=tmp_path / "cache",
            fetch_json=_FakeWikipedia(),
        )


def test_truncates_a_long_lead_on_a_sentence_boundary() -> None:
    sentence = "This species lives in the example biome. "
    extract, truncated = _trim_extract(sentence * 60)

    assert truncated is True
    assert len(extract) <= MAX_EXTRACT_CHARACTERS
    assert extract.endswith("biome.")


def test_truncates_without_a_boundary_using_an_ellipsis() -> None:
    extract, truncated = _trim_extract("word " * 400)

    assert truncated is True
    assert extract.endswith("…")
    assert len(extract) <= MAX_EXTRACT_CHARACTERS + 1


def test_keeps_a_short_lead_intact() -> None:
    assert _trim_extract("  A   short   lead. ") == ("A short lead.", False)
    assert _trim_extract("   ") == (None, False)
    assert _trim_extract(None) == (None, False)


def test_command_line_resolves_and_reports_status_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "onezoom.sqlite3"
    _write_database(database)
    game = _game()
    monkeypatch.setattr(
        "phylogenomica.data.wikipedia.load_game", lambda _path: game
    )
    monkeypatch.setattr(
        "phylogenomica.data.wikipedia._fetch_json", _FakeWikipedia()
    )

    main(
        [
            "game.json",
            "--normalized-dir",
            str(tmp_path),
            "--cache-root",
            str(tmp_path / "cache"),
        ]
    )

    out = capsys.readouterr().out
    assert "resolved=1" in out
    assert "missing_sitelink=1" in out
    manifest = json.loads(
        (
            tmp_path / "cache" / game.dataset_version / game.game_id / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["reproduction_command"].startswith(
        "phylogenomica-resolve-wikipedia game.json"
    )


def test_curl_transport_is_available_on_this_machine() -> None:
    assert subprocess.run(["curl", "--version"], capture_output=True).returncode == 0


def test_waits_out_replication_lag_and_then_succeeds(tmp_path: Path) -> None:
    database = tmp_path / "onezoom.sqlite3"
    _write_database(database)
    real = _FakeWikipedia()
    attempts = {"count": 0}
    waits: list[float] = []

    def lagging(endpoint, parameters, context):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise WikimediaResolutionError(
                "API returned an error: {'code': 'maxlag', 'lag': 15.05}"
            )
        return real(endpoint, parameters, context)

    _, manifest = resolve_game_wikipedia(
        _game(),
        normalized_database=database,
        cache_root=tmp_path / "cache",
        fetch_json=lagging,
        sleep=waits.append,
    )

    assert waits == [5]
    assert manifest["status_counts"]["resolved"] == 1


def test_gives_up_after_persistent_replication_lag(tmp_path: Path) -> None:
    database = tmp_path / "onezoom.sqlite3"
    _write_database(database)
    waits: list[float] = []

    def always_lagged(*_args):
        raise WikimediaResolutionError("API returned an error: {'code': 'maxlag'}")

    with pytest.raises(WikipediaResolutionError, match="stayed lagged"):
        resolve_game_wikipedia(
            _game(),
            normalized_database=database,
            cache_root=tmp_path / "cache",
            fetch_json=always_lagged,
            sleep=waits.append,
        )

    assert waits == [5, 10, 15]


def test_does_not_retry_an_error_that_describes_the_request(tmp_path: Path) -> None:
    database = tmp_path / "onezoom.sqlite3"
    _write_database(database)
    calls = {"count": 0}

    def rejected(*_args):
        calls["count"] += 1
        raise WikimediaResolutionError("API returned an error: {'code': 'badvalue'}")

    with pytest.raises(WikipediaResolutionError, match="badvalue"):
        resolve_game_wikipedia(
            _game(),
            normalized_database=database,
            cache_root=tmp_path / "cache",
            fetch_json=rejected,
            sleep=lambda _seconds: pytest.fail("a permanent error must not wait"),
        )

    assert calls["count"] == 1
