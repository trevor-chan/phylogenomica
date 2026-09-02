import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from phylogenomica.data.wikipedia import (
    WIKIPEDIA_MANIFEST_SCHEMA_VERSION,
    WIKIPEDIA_RESOLVER_VERSION,
)
from phylogenomica.data.wikipedia_library import (
    WikipediaLibraryError,
    load_wikipedia_library,
    main,
    update_wikipedia_library,
)

DATASET = "test-wikipedia-1"
FIXED = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def _text(title: str, extract: str, *, revision_id: int = 900) -> dict[str, object]:
    return {
        "title": title,
        "page_id": 11,
        "revision_id": revision_id,
        "url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
        "extract": extract,
        "extract_truncated": False,
        "license_name": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attribution_text": f"“{title}”, English Wikipedia contributors, CC BY-SA 4.0",
    }


def _resolver_manifest(
    tmp_path: Path,
    records: list[dict[str, Any]],
    *,
    name: str = "manifest.json",
    game_id: str = "b" * 64,
) -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "schema_version": WIKIPEDIA_MANIFEST_SCHEMA_VERSION,
                "resolver_version": WIKIPEDIA_RESOLVER_VERSION,
                "dataset_version": DATASET,
                "game_id": game_id,
                "records": records,
            }
        ),
        encoding="utf-8",
    )
    return path


def _resolved(species_id: int, title: str, extract: str, **kwargs: Any) -> dict:
    return {
        "species_id": species_id,
        "scientific_name": f"Genus species{species_id}",
        "wikidata_qid": f"Q{species_id}",
        "status": "resolved",
        "text": _text(title, extract, **kwargs),
    }


def test_imports_resolved_descriptions_and_skips_failures(tmp_path: Path) -> None:
    source = _resolver_manifest(
        tmp_path,
        [
            _resolved(1, "Alpha species", "A small burrowing mammal."),
            _resolved(2, "Beta species", "A wading bird."),
            {"species_id": 3, "status": "missing_sitelink", "text": None},
        ],
    )

    manifest_path, manifest = update_wikipedia_library(
        source, library_root=tmp_path / "library", clock=lambda: FIXED
    )

    assert manifest["record_count"] == 2
    assert manifest["last_update"]["imported_count"] == 2
    assert manifest["last_update"]["reused_count"] == 0
    assert manifest["last_update"]["unresolved_status_counts"] == {
        "missing_sitelink": 1
    }
    assert manifest["text_license"]["name"] == "CC BY-SA 4.0"

    library = load_wikipedia_library(
        manifest_path, expected_dataset_version=DATASET
    )
    entry = library.description(1)
    assert entry is not None
    assert entry.title == "Alpha species"
    assert entry.extract == "A small burrowing mammal."
    assert entry.revision_id == 900
    assert entry.license_name == "CC BY-SA 4.0"
    assert library.description(3) is None


def test_reuses_unchanged_records_and_replaces_a_new_revision(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    first = _resolver_manifest(
        tmp_path, [_resolved(1, "Alpha species", "First text.")], name="first.json"
    )
    update_wikipedia_library(first, library_root=library_root, clock=lambda: FIXED)

    unchanged = _resolver_manifest(
        tmp_path,
        [_resolved(1, "Alpha species", "First text.")],
        name="unchanged.json",
    )
    _, manifest = update_wikipedia_library(
        unchanged, library_root=library_root, clock=lambda: FIXED
    )
    assert manifest["last_update"]["reused_count"] == 1
    assert manifest["last_update"]["imported_count"] == 0

    # A new revision of the same article is new prose, not a cache hit.
    revised = _resolver_manifest(
        tmp_path,
        [_resolved(1, "Alpha species", "Rewritten text.", revision_id=901)],
        name="revised.json",
    )
    manifest_path, manifest = update_wikipedia_library(
        revised, library_root=library_root, clock=lambda: FIXED
    )
    assert manifest["last_update"]["imported_count"] == 1
    library = load_wikipedia_library(manifest_path)
    assert library.description(1).extract == "Rewritten text."
    assert manifest["record_count"] == 1


def test_merges_successive_games_into_one_dataset_library(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    update_wikipedia_library(
        _resolver_manifest(
            tmp_path, [_resolved(1, "Alpha species", "One.")], name="a.json"
        ),
        library_root=library_root,
        clock=lambda: FIXED,
    )
    manifest_path, manifest = update_wikipedia_library(
        _resolver_manifest(
            tmp_path,
            [_resolved(5, "Epsilon species", "Five.")],
            name="b.json",
            game_id="c" * 64,
        ),
        library_root=library_root,
        clock=lambda: FIXED,
    )

    assert manifest["record_count"] == 2
    assert len(manifest["source_manifests"]) == 2
    library = load_wikipedia_library(manifest_path)
    assert sorted(library.descriptions) == [1, 5]


def test_limits_and_selects_species(tmp_path: Path) -> None:
    source = _resolver_manifest(
        tmp_path,
        [
            _resolved(1, "Alpha species", "One."),
            _resolved(2, "Beta species", "Two."),
            _resolved(3, "Gamma species", "Three."),
        ],
    )

    _, manifest = update_wikipedia_library(
        source,
        library_root=tmp_path / "library",
        species_ids={2, 3},
        limit=1,
        clock=lambda: FIXED,
    )

    assert [record["species_id"] for record in manifest["records"]] == [2]


def test_rejects_a_manifest_without_resolved_descriptions(tmp_path: Path) -> None:
    source = _resolver_manifest(
        tmp_path, [{"species_id": 1, "status": "missing_extract", "text": None}]
    )

    with pytest.raises(WikipediaLibraryError, match="no resolved descriptions"):
        update_wikipedia_library(source, library_root=tmp_path / "library")


def test_rejects_a_non_wikipedia_source_manifest(tmp_path: Path) -> None:
    path = tmp_path / "media.json"
    path.write_text(json.dumps({"downloader_version": 1}), encoding="utf-8")

    with pytest.raises(WikipediaLibraryError, match="Wikipedia resolver manifest"):
        update_wikipedia_library(path, library_root=tmp_path / "library")


def test_rejects_a_library_from_another_dataset(tmp_path: Path) -> None:
    manifest_path, _ = update_wikipedia_library(
        _resolver_manifest(tmp_path, [_resolved(1, "Alpha species", "One.")]),
        library_root=tmp_path / "library",
        clock=lambda: FIXED,
    )

    with pytest.raises(WikipediaLibraryError, match="does not match game dataset"):
        load_wikipedia_library(manifest_path, expected_dataset_version="other")


def test_rejects_a_record_missing_its_attribution(tmp_path: Path) -> None:
    record = _resolved(1, "Alpha species", "One.")
    del record["text"]["attribution_text"]  # type: ignore[index]
    source = _resolver_manifest(tmp_path, [record])

    with pytest.raises(WikipediaLibraryError, match="attribution metadata"):
        update_wikipedia_library(source, library_root=tmp_path / "library")


def test_command_line_reports_the_written_library(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _resolver_manifest(tmp_path, [_resolved(1, "Alpha species", "One.")])

    main([str(source), "--library-root", str(tmp_path / "library")])

    out = capsys.readouterr().out
    assert "imported=1" in out
    assert "reused=0" in out
