import json
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from phylogenomica.data.onezoom_download import sha256_file
from phylogenomica.generation.eligibility import (
    ELIGIBILITY_DATABASE_FILENAME,
    TargetEligibilityError,
    TargetEligibilityIndex,
    build_target_eligibility_index,
)
from phylogenomica.generation.feasibility import (
    FeasibilityConfig,
    _advance_lineage,
    _LineageState,
    _target_metrics,
    audit_target_feasibility,
    summarize_distribution,
)
from phylogenomica.tree.preprocess import _create_tree_database, analyze_parent_graph


def test_validates_feasibility_configuration() -> None:
    config = FeasibilityConfig()

    assert config.lineage_species == 50
    assert config.relative_species == 49
    assert config.decoys_per_transition_stage == 8
    assert config.decoys_in_ultimate_stage == 8
    assert config.total_unlock_species == 4
    assert config.total_mulligan_species == 5
    assert config.total_decoy_species == 40
    with pytest.raises(ValueError, match="positive"):
        FeasibilityConfig(members_per_stage=0)
    with pytest.raises(ValueError, match="exceed"):
        FeasibilityConfig(members_per_stage=1)


def test_assigns_decoys_mulligan_and_unlock_to_separate_tiers() -> None:
    config = FeasibilityConfig(
        members_per_stage=4,
        stages_per_game=2,
    )
    state = _LineageState(0, 0, 0, 0, 0)
    state = _advance_lineage(state, tier_capacity=10, config=config)
    assert state == _LineageState(1, 0, 2, 0, 0)

    state = _advance_lineage(state, tier_capacity=1, config=config)
    assert state == _LineageState(2, 0, 2, 1, 0)

    state = _advance_lineage(state, tier_capacity=5, config=config)
    assert state == _LineageState(3, 1, 0, 0, 0)

    state = _advance_lineage(state, tier_capacity=10, config=config)
    assert state == _LineageState(4, 1, 2, 0, 0)
    state = _advance_lineage(state, tier_capacity=1, config=config)
    assert state == _LineageState(5, 2, 0, 0, 0)


def test_final_stage_can_end_before_the_literal_closest_tier() -> None:
    config = FeasibilityConfig(
        members_per_stage=4,
        stages_per_game=1,
    )
    state = _advance_lineage(
        _LineageState(0, 0, 0, 0, 0), tier_capacity=3, config=config
    )
    state = _advance_lineage(state, tier_capacity=1, config=config)

    metrics = _target_metrics(
        state,
        final_tier_capacity=0,
        total_relative_capacity=20,
        config=config,
    )

    assert metrics.supports(config)
    assert metrics.completed_stages == 1


def test_total_capacity_alone_does_not_create_ordered_stages() -> None:
    config = FeasibilityConfig(members_per_stage=4, stages_per_game=2)
    state = _advance_lineage(
        _LineageState(0, 0, 0, 0, 0), tier_capacity=100, config=config
    )

    metrics = _target_metrics(
        state,
        final_tier_capacity=0,
        total_relative_capacity=100,
        config=config,
    )

    assert metrics.total_relative_capacity >= config.relative_species
    assert not metrics.supports(config)


def test_monotypic_root_edge_does_not_create_a_usable_tier() -> None:
    config = FeasibilityConfig()

    unchanged = _advance_lineage(
        _LineageState(0, 0, 0, 0, 0), tier_capacity=0, config=config
    )

    assert unchanged == _LineageState(0, 0, 0, 0, 0)


def test_summarizes_weighted_integer_distribution() -> None:
    summary = summarize_distribution(Counter({1: 1, 5: 2, 9: 1}))

    assert summary["count"] == 4
    assert summary["median"] == 5
    assert summary["p75"] == 5
    assert summary["max"] == 9


def _write_normalized_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA user_version = 1;
        CREATE TABLE dataset_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE leaves (
            leaf_id INTEGER PRIMARY KEY,
            scientific_name TEXT,
            ott_id INTEGER
        );
        CREATE TABLE vernacular_names (
            subject_type TEXT,
            ott_id INTEGER,
            scientific_name TEXT,
            preferred INTEGER,
            language_primary TEXT
        );
        CREATE TABLE images (
            subject_type TEXT,
            ott_id INTEGER,
            scientific_name TEXT,
            overall_best_any INTEGER,
            url TEXT,
            rights TEXT,
            license TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO dataset_metadata VALUES ('dataset_version', 'test-1')"
    )
    connection.executemany(
        "INSERT INTO leaves VALUES (?, ?, ?)",
        (
            (1, "Alpha one", 101),
            (2, "Alpha two", 102),
            (3, "Beta one", 103),
            (4, "Beta two", 104),
            (5, "Beta three", 105),
        ),
    )
    connection.execute(
        "INSERT INTO vernacular_names VALUES ('ott', 103, NULL, 1, 'en')"
    )
    connection.execute(
        "INSERT INTO images VALUES "
        "('ott', 103, NULL, 1, 'https://example.test/3', 'Author', 'CC BY')"
    )
    connection.commit()
    connection.close()


def _write_tree_database(
    path: Path, *, normalized_database_sha256: str = "abc"
) -> None:
    source = sqlite3.connect(":memory:")
    source.execute(
        "CREATE TABLE leaves (leaf_id INTEGER, biological_parent_id INTEGER)"
    )
    source.executemany(
        "INSERT INTO leaves VALUES (?, ?)",
        ((1, 3), (2, 3), (3, 4), (4, 4), (5, 4)),
    )
    analysis = analyze_parent_graph(
        {1: None, 2: 1, 3: 2, 4: 1},
        {3: 2, 4: 3},
    )
    _create_tree_database(
        path,
        source=source,
        analysis=analysis,
        dataset_version="test-1",
        normalized_database_sha256=normalized_database_sha256,
    )
    source.close()


def _write_processed_manifests(normalized_dir: Path) -> None:
    normalized_database = normalized_dir / "onezoom.sqlite3"
    normalized_sha256 = sha256_file(normalized_database)
    normalized_manifest = {
        "schema_version": 1,
        "database_schema_version": 1,
        "dataset_version": "test-1",
        "source_tree_version": "test-tree",
        "database": {
            "name": normalized_database.name,
            "bytes": normalized_database.stat().st_size,
            "sha256": normalized_sha256,
        },
    }
    (normalized_dir / "manifest.json").write_text(
        json.dumps(normalized_manifest), encoding="utf-8"
    )

    tree_dir = normalized_dir / "tree-v1"
    tree_dir.mkdir()
    tree_database = tree_dir / "biological_tree.sqlite3"
    _write_tree_database(
        tree_database, normalized_database_sha256=normalized_sha256
    )
    tree_manifest = {
        "schema_version": 1,
        "dataset_version": "test-1",
        "source_tree_version": "test-tree",
        "tree_builder_version": 1,
        "tree_schema_version": 1,
        "source": {"normalized_database_sha256": normalized_sha256},
        "database": {
            "name": tree_database.name,
            "bytes": tree_database.stat().st_size,
            "sha256": sha256_file(tree_database),
        },
    }
    (tree_dir / "manifest.json").write_text(
        json.dumps(tree_manifest), encoding="utf-8"
    )


def test_audits_every_target_in_batch(tmp_path: Path) -> None:
    normalized = tmp_path / "normalized.sqlite3"
    tree = tmp_path / "tree.sqlite3"
    _write_normalized_database(normalized)
    _write_tree_database(tree)

    result = audit_target_feasibility(
        tree_database=tree,
        normalized_database=normalized,
        config=FeasibilityConfig(
            members_per_stage=3,
            stages_per_game=1,
        ),
    )

    assert result["targets"] == {
        "source_leaves": 5,
        "total": 5,
        "supporting_configuration": {"count": 5, "percent": 100.0},
        "failure_reasons": {
            "insufficient_total_relatives": {"count": 0, "percent": 0.0},
            "insufficient_ordered_stage_structure": {
                "count": 0,
                "percent": 0.0,
            },
        },
    }
    topology = result["topology"]
    assert topology["usable_depth"]["median"] == 2
    assert topology["total_relative_capacity"]["median"] == 4
    assert topology["completed_stages"]["median"] == 1
    assert result["metadata_coverage"]["all_targets"]["rich_card_ready"] == {
        "count": 1,
        "percent": 20.0,
    }
    assert result["metadata_coverage"]["topology_supported_targets"][
        "rich_card_ready"
    ] == {"count": 1, "percent": 20.0}


def test_filters_both_targets_and_relative_capacity(tmp_path: Path) -> None:
    normalized = tmp_path / "normalized.sqlite3"
    tree = tmp_path / "tree.sqlite3"
    _write_normalized_database(normalized)
    _write_tree_database(tree)
    connection = sqlite3.connect(normalized)
    connection.executemany(
        "INSERT INTO vernacular_names VALUES ('ott', ?, NULL, 1, 'en')",
        ((101,), (104,), (105,)),
    )
    connection.executemany(
        "INSERT INTO images VALUES "
        "('ott', ?, NULL, 1, 'https://example.test/image', 'Author', 'CC BY')",
        ((101,), (104,), (105,)),
    )
    connection.commit()
    connection.close()

    result = audit_target_feasibility(
        tree_database=tree,
        normalized_database=normalized,
        config=FeasibilityConfig(
            members_per_stage=3,
            stages_per_game=1,
            require_rich_card_metadata=True,
        ),
    )

    assert result["targets"] == {
        "source_leaves": 5,
        "total": 4,
        "supporting_configuration": {"count": 3, "percent": 75.0},
        "failure_reasons": {
            "insufficient_total_relatives": {"count": 0, "percent": 0.0},
            "insufficient_ordered_stage_structure": {
                "count": 1,
                "percent": 25.0,
            },
        },
    }
    assert result["topology"]["relative_tier_capacity"]["count"] == 7
    assert result["metadata_coverage"]["all_targets"]["rich_card_ready"] == {
        "count": 4,
        "percent": 100.0,
    }


def _write_eligibility_sources(normalized_dir: Path) -> None:
    normalized_dir.mkdir()
    normalized = normalized_dir / "onezoom.sqlite3"
    _write_normalized_database(normalized)
    connection = sqlite3.connect(normalized)
    connection.execute(
        "UPDATE leaves SET scientific_name = NULL WHERE leaf_id = 2"
    )
    connection.executemany(
        "INSERT INTO vernacular_names VALUES ('ott', ?, NULL, 1, 'en')",
        ((101,), (104,), (105,)),
    )
    connection.executemany(
        "INSERT INTO images VALUES "
        "('ott', ?, NULL, 1, 'https://example.test/image', 'Author', 'CC BY')",
        ((101,), (104,), (105,)),
    )
    connection.commit()
    connection.close()
    _write_processed_manifests(normalized_dir)


def test_builds_deterministic_queryable_target_eligibility_index(
    tmp_path: Path,
) -> None:
    normalized_dir = tmp_path / "processed"
    _write_eligibility_sources(normalized_dir)
    config = FeasibilityConfig(
        members_per_stage=3,
        stages_per_game=1,
        require_rich_card_metadata=True,
    )

    output_a, manifest_a = build_target_eligibility_index(
        normalized_dir=normalized_dir,
        output_dir=tmp_path / "eligibility-a",
        config=config,
    )
    output_b, manifest_b = build_target_eligibility_index(
        normalized_dir=normalized_dir,
        output_dir=tmp_path / "eligibility-b",
        config=config,
    )

    assert manifest_a["validation"] == {
        "source_targets": 5,
        "indexed_targets": 4,
        "metadata_excluded_targets": 1,
        "eligible_targets": 3,
        "ineligible_indexed_targets": 1,
        "eligible_targets_with_reasons": 0,
        "ineligible_targets_without_reasons": 0,
        "sqlite_integrity_check": "ok",
    }
    assert manifest_a["database"]["sha256"] == manifest_b["database"]["sha256"]
    assert manifest_a["source_exclusion_reason_counts"] == {
        "missing_licensed_overall_best_image": 1,
        "missing_preferred_english_vernacular": 1,
        "missing_scientific_name": 1,
    }

    with TargetEligibilityIndex(
        output_a / ELIGIBILITY_DATABASE_FILENAME
    ) as index:
        eligible_ids = list(index.iter_eligible_target_ids())
        assert len(eligible_ids) == 3
        assert eligible_ids == sorted(eligible_ids)
        assert index.get(999) is None

        assert index.get(2) is None

        topology_failures = [
            evaluation
            for target_id in range(1, 6)
            if (evaluation := index.get(target_id)) is not None
            and evaluation.metadata_eligible
            and not evaluation.topology_supported
        ]
        assert len(topology_failures) == 1
        assert topology_failures[0].reason_codes == (
            "insufficient_ordered_stage_structure",
        )


def test_refuses_to_overwrite_target_eligibility_index(tmp_path: Path) -> None:
    normalized_dir = tmp_path / "processed"
    _write_eligibility_sources(normalized_dir)
    output = tmp_path / "eligibility"
    output.mkdir()

    with pytest.raises(TargetEligibilityError, match="already exists"):
        build_target_eligibility_index(
            normalized_dir=normalized_dir,
            output_dir=output,
            config=FeasibilityConfig(
                members_per_stage=3,
                stages_per_game=1,
                require_rich_card_metadata=True,
            ),
        )
