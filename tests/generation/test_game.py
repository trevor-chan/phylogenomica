import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from phylogenomica.data.onezoom_download import sha256_file
from phylogenomica.generation.eligibility import (
    ELIGIBILITY_DATABASE_FILENAME,
    build_target_eligibility_index,
)
from phylogenomica.generation.feasibility import (
    FeasibilityConfig,
    feasibility_configuration,
)
from phylogenomica.generation.game import (
    GAME_GENERATOR_VERSION,
    GAME_SCHEMA_VERSION,
    GameGenerationError,
    _validate_stage_continuity,
    assemble_game,
    game_from_dict,
    generate_game,
    load_game,
    main,
    validate_game_structure,
    validate_generated_game,
)
from phylogenomica.generation.selection import select_relatives
from phylogenomica.tree.preprocess import _create_tree_database, analyze_parent_graph
from phylogenomica.tree.query import BiologicalTree, TaxonRef

TARGET_ID = 1
TIER_COUNT = 10
CANDIDATES_PER_TIER = 2
DATASET_VERSION = "test-game-1"


def _write_game_sources(normalized_dir: Path) -> None:
    """Write a chain backbone with the target and several relatives per tier.

    Internal nodes ``1..TIER_COUNT`` form one monotonic chain. The target leaf
    hangs off the deepest node, and every node carries ``CANDIDATES_PER_TIER``
    relative leaves, so tier index ``j - 1`` corresponds to node ``j``.
    """
    normalized_dir.mkdir()
    parent_by_node = {
        node_id: (None if node_id == 1 else node_id - 1)
        for node_id in range(1, TIER_COUNT + 1)
    }
    leaf_parent = {TARGET_ID: TIER_COUNT}
    next_leaf_id = TARGET_ID + 1
    for node_id in range(1, TIER_COUNT + 1):
        for _ in range(CANDIDATES_PER_TIER):
            leaf_parent[next_leaf_id] = node_id
            next_leaf_id += 1
    direct_leaf_count_by_node = {
        node_id: sum(1 for parent in leaf_parent.values() if parent == node_id)
        for node_id in range(1, TIER_COUNT + 1)
    }
    species_ids = sorted(leaf_parent)

    normalized_database = normalized_dir / "onezoom.sqlite3"
    connection = sqlite3.connect(normalized_database)
    connection.executescript(
        """
        PRAGMA user_version = 1;
        CREATE TABLE dataset_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE leaves (
            leaf_id INTEGER PRIMARY KEY,
            scientific_name TEXT,
            ott_id INTEGER,
            popularity_rank INTEGER
        );
        CREATE TABLE nodes (
            node_id INTEGER PRIMARY KEY,
            scientific_name TEXT,
            age_ma REAL
        );
        CREATE TABLE vernacular_names (
            subject_type TEXT,
            ott_id INTEGER,
            scientific_name TEXT,
            preferred INTEGER,
            language_primary TEXT,
            vernacular_name TEXT,
            source_table TEXT,
            source_row_id INTEGER
        );
        CREATE TABLE images (
            subject_type TEXT,
            ott_id INTEGER,
            scientific_name TEXT,
            overall_best_any INTEGER,
            url TEXT,
            rights TEXT,
            license TEXT,
            source_code INTEGER,
            source_id TEXT,
            source_table TEXT,
            source_row_id INTEGER
        );
        """
    )
    connection.execute(
        "INSERT INTO dataset_metadata VALUES ('dataset_version', ?)",
        (DATASET_VERSION,),
    )
    connection.executemany(
        "INSERT INTO leaves VALUES (?, ?, ?, ?)",
        (
            (species_id, f"Species {species_id}", 100 + species_id, species_id)
            for species_id in species_ids
        ),
    )
    # Ages fall toward the target, and one node has none: most real nodes
    # carry no age at all.
    connection.executemany(
        "INSERT INTO nodes VALUES (?, ?, ?)",
        (
            (
                node_id,
                None if node_id == 3 else f"Clade {node_id}",
                None
                if node_id == 3
                else float((TIER_COUNT + 1 - node_id) * 100),
            )
            for node_id in range(1, TIER_COUNT + 1)
        ),
    )
    connection.executemany(
        "INSERT INTO vernacular_names VALUES "
        "('ott', ?, NULL, 1, 'en', ?, 'vernacular_by_ott', ?)",
        (
            (100 + species_id, f"Common {species_id}", species_id)
            for species_id in species_ids
        ),
    )
    connection.executemany(
        "INSERT INTO images VALUES "
        "('ott', ?, NULL, 1, ?, 'Test author', 'CC BY 4.0', "
        "99, ?, 'images_by_ott', ?)",
        (
            (
                100 + species_id,
                f"https://example.test/{species_id}.jpg",
                f"image-{species_id}",
                species_id,
            )
            for species_id in species_ids
        ),
    )
    connection.commit()
    connection.close()

    normalized_sha256 = sha256_file(normalized_database)
    (normalized_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "database_schema_version": 1,
                "dataset_version": DATASET_VERSION,
                "source_tree_version": "test-game-tree",
                "database": {
                    "name": normalized_database.name,
                    "bytes": normalized_database.stat().st_size,
                    "sha256": normalized_sha256,
                },
            }
        ),
        encoding="utf-8",
    )

    tree_dir = normalized_dir / "tree-v1"
    tree_dir.mkdir()
    tree_database = tree_dir / "biological_tree.sqlite3"
    source = sqlite3.connect(":memory:")
    source.execute(
        "CREATE TABLE leaves (leaf_id INTEGER, biological_parent_id INTEGER)"
    )
    source.executemany(
        "INSERT INTO leaves VALUES (?, ?)", sorted(leaf_parent.items())
    )
    _create_tree_database(
        tree_database,
        source=source,
        analysis=analyze_parent_graph(parent_by_node, direct_leaf_count_by_node),
        dataset_version=DATASET_VERSION,
        normalized_database_sha256=normalized_sha256,
    )
    source.close()
    (tree_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_version": DATASET_VERSION,
                "source_tree_version": "test-game-tree",
                "tree_builder_version": 1,
                "tree_schema_version": 1,
                "source": {"normalized_database_sha256": normalized_sha256},
                "database": {
                    "name": tree_database.name,
                    "bytes": tree_database.stat().st_size,
                    "sha256": sha256_file(tree_database),
                },
            }
        ),
        encoding="utf-8",
    )


def _config() -> FeasibilityConfig:
    """Three stages of three members: two transition stages and an ultimate."""
    return FeasibilityConfig(
        members_per_stage=3,
        stages_per_game=3,
        require_rich_card_metadata=True,
    )


@pytest.fixture
def sources(tmp_path: Path) -> dict[str, Path]:
    normalized_dir = tmp_path / "processed"
    _write_game_sources(normalized_dir)
    eligibility_dir, _ = build_target_eligibility_index(
        normalized_dir=normalized_dir, config=_config()
    )
    return {
        "normalized_database": normalized_dir / "onezoom.sqlite3",
        "tree_database": normalized_dir / "tree-v1" / "biological_tree.sqlite3",
        "eligibility_database": eligibility_dir / ELIGIBILITY_DATABASE_FILENAME,
    }


def _selection_and_game(sources: dict[str, Path], *, seed: int = 11):
    config = _config()
    selection = select_relatives(
        target_id=TARGET_ID, seed=seed, config=config, **sources
    )
    game = assemble_game(
        selection,
        normalized_database=sources["normalized_database"],
        tree_database=sources["tree_database"],
    )
    return selection, game


def _backbone(sources: dict[str, Path]) -> tuple[int, ...]:
    with BiologicalTree.open(sources["tree_database"]) as tree:
        return tree.lineage_node_ids(TaxonRef("leaf", TARGET_ID))


def test_generates_a_complete_deterministic_game(sources: dict[str, Path]) -> None:
    game = generate_game(target_id=TARGET_ID, seed=11, config=_config(), **sources)
    repeated = generate_game(
        target_id=TARGET_ID, seed=11, config=_config(), **sources
    )

    assert game == repeated
    assert len(game.game_id) == 64
    assert game.schema_version == GAME_SCHEMA_VERSION
    assert game.generator_version == GAME_GENERATOR_VERSION
    assert game.dataset_version == DATASET_VERSION
    assert game.target_id == TARGET_ID
    assert game.seed == 11
    assert [len(stage.members) for stage in game.stages] == [3, 3, 3]

    species_ids = [
        member.species_id for stage in game.stages for member in stage.members
    ]
    assert len(species_ids) == len(set(species_ids)) == 9
    assert species_ids.count(TARGET_ID) == 1

    other_seed = generate_game(
        target_id=TARGET_ID, seed=12, config=_config(), **sources
    )
    assert other_seed.game_id != game.game_id


def test_shows_the_target_only_in_the_ultimate_stage(
    sources: dict[str, Path],
) -> None:
    game = generate_game(target_id=TARGET_ID, seed=11, config=_config(), **sources)

    for stage in game.stages[:-1]:
        assert all(member.role != "target" for member in stage.members)
        assert TARGET_ID not in {member.species_id for member in stage.members}
        assert stage.target_species_id is None
        assert stage.unlock_species_ids
        assert stage.mulligan_species_ids

    ultimate = game.stages[-1]
    assert ultimate.target_species_id == TARGET_ID
    assert ultimate.unlock_species_ids == ()
    assert ultimate.mulligan_species_ids
    target_members = [member for member in ultimate.members if member.role == "target"]
    assert len(target_members) == 1
    target_member = target_members[0]
    assert target_member.species_id == TARGET_ID
    assert target_member.tier_index is None
    assert target_member.ancestor_node_id is None
    assert target_member.card.english_name == "Common 1"
    assert target_member.card.scientific_name == "Species 1"
    assert target_member.card.image.url == "https://example.test/1.jpg"
    assert target_member.card.image.rights == "Test author"
    assert target_member.card.image.license == "CC BY 4.0"


def test_orders_stage_roles_on_strictly_deeper_tiers(
    sources: dict[str, Path],
) -> None:
    game = generate_game(target_id=TARGET_ID, seed=11, config=_config(), **sources)

    deepest_previous_tier = -1
    for stage in game.stages:
        is_ultimate = stage.stage_index == len(game.stages) - 1
        tier_by_role: dict[str, list[int]] = {"decoy": [], "mulligan": [], "unlock": []}
        for member in stage.members:
            if member.role != "target":
                tier_by_role[member.role].append(member.tier_index)
        assert len(tier_by_role["mulligan"]) == 1
        assert len(tier_by_role["unlock"]) == (0 if is_ultimate else 1)
        assert len(tier_by_role["decoy"]) == 1
        assert max(tier_by_role["decoy"]) < min(tier_by_role["mulligan"])
        if not is_ultimate:
            assert max(tier_by_role["mulligan"]) < min(tier_by_role["unlock"])

        stage_tiers = [tier.tier_index for tier in stage.tiers]
        assert stage_tiers == sorted(set(stage_tiers))
        assert min(stage_tiers) > deepest_previous_tier
        deepest_previous_tier = max(stage_tiers)
        # No selected tier may carry two different stage roles.
        assert len({tier.tier_index for tier in stage.tiers}) == len(stage.tiers)


def test_anchors_stage_boundaries_to_the_target_backbone(
    sources: dict[str, Path],
) -> None:
    backbone = _backbone(sources)
    position = {node_id: index for index, node_id in enumerate(backbone)}
    skipped_deepest_tiers = 0

    for seed in range(12):
        game = generate_game(
            target_id=TARGET_ID, seed=seed, config=_config(), **sources
        )
        previous_end = -1
        for stage in game.stages:
            assert stage.start_node_id == stage.tiers[0].ancestor_node_id
            for tier in stage.tiers:
                assert backbone[tier.tier_index] == tier.ancestor_node_id
            assert position[stage.start_node_id] > previous_end
            assert position[stage.start_node_id] <= position[stage.end_node_id]
            previous_end = position[stage.end_node_id]

        for stage in game.stages[:-1]:
            assert stage.end_node_id == stage.tiers[-1].ancestor_node_id
        # The ultimate stage ends at the target's own parent, which may lie
        # deeper than its deepest selected tier when trailing tiers go unused.
        ultimate = game.stages[-1]
        assert ultimate.end_node_id == backbone[-1]
        if ultimate.end_node_id != ultimate.tiers[-1].ancestor_node_id:
            skipped_deepest_tiers += 1

    assert skipped_deepest_tiers, "no game exercised an unused deepest tier"


def test_shuffles_members_without_exposing_tier_order(
    sources: dict[str, Path],
) -> None:
    def tier_permutation(stage) -> tuple[int, ...]:
        """Positions of a stage's members in strict shallow-to-deep tier order."""
        ordered = sorted(member.tier_index for member in stage.members)
        return tuple(ordered.index(member.tier_index) for member in stage.members)

    first_stage_orders = set()
    target_positions = set()
    stages_share_one_permutation = True
    for seed in range(12):
        game = generate_game(
            target_id=TARGET_ID, seed=seed, config=_config(), **sources
        )
        first_stage_orders.add(
            tuple(member.tier_index for member in game.stages[0].members)
        )
        target_positions.add(
            next(
                index
                for index, member in enumerate(game.stages[-1].members)
                if member.role == "target"
            )
        )
        transition_permutations = {
            tier_permutation(stage) for stage in game.stages[:-1]
        }
        if len(transition_permutations) > 1:
            stages_share_one_permutation = False

    assert len(first_stage_orders) > 1
    # The visible target must not sit in a fixed, learnable slot.
    assert len(target_positions) > 1
    # Stages must not reuse one permutation, which would pin the unlock to the
    # same visible slot in every stage of a game.
    assert not stages_share_one_permutation


def test_varies_selected_species_across_seeds(sources: dict[str, Path]) -> None:
    lineages = {
        tuple(
            sorted(
                member.species_id
                for stage in generate_game(
                    target_id=TARGET_ID, seed=seed, config=_config(), **sources
                ).stages
                for member in stage.members
            )
        )
        for seed in range(12)
    }
    assert len(lineages) > 1


def test_serializes_a_complete_game_to_json(sources: dict[str, Path]) -> None:
    game = generate_game(target_id=TARGET_ID, seed=11, config=_config(), **sources)

    payload = json.loads(json.dumps(game.to_dict(), sort_keys=True))

    assert payload["game_id"] == game.game_id
    assert payload["configuration"] == feasibility_configuration(game.configuration)
    assert len(payload["stages"]) == 3
    ultimate = payload["stages"][-1]
    assert ultimate["target_species_id"] == TARGET_ID
    serialized_target = next(
        member for member in ultimate["members"] if member["role"] == "target"
    )
    assert serialized_target["card"]["english_name"] == "Common 1"
    assert serialized_target["card"]["image"]["license"] == "CC BY 4.0"
    assert serialized_target["card"]["image"]["source"] == {
        "source_table": "images_by_ott",
        "source_row_id": TARGET_ID,
    }


def _serialized(game) -> dict:
    return json.loads(json.dumps(game.to_dict(), sort_keys=True))


def test_round_trips_a_serialized_game(sources: dict[str, Path]) -> None:
    game = generate_game(target_id=TARGET_ID, seed=11, config=_config(), **sources)

    restored = game_from_dict(_serialized(game))

    assert restored == game
    assert restored.configuration == game.configuration
    assert _serialized(restored) == _serialized(game)
    original_card = game.stages[-1].members[0].card
    assert restored.stages[-1].members[0].card == original_card


def test_round_trips_every_seeded_game(sources: dict[str, Path]) -> None:
    for seed in range(6):
        game = generate_game(
            target_id=TARGET_ID, seed=seed, config=_config(), **sources
        )
        assert game_from_dict(_serialized(game)) == game


def test_loads_and_validates_a_game_from_disk(
    sources: dict[str, Path], tmp_path: Path
) -> None:
    game = generate_game(target_id=TARGET_ID, seed=11, config=_config(), **sources)
    path = tmp_path / "game.json"
    path.write_text(
        json.dumps(game.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )

    assert load_game(path) == game

    with pytest.raises(GameGenerationError, match="cannot read game"):
        load_game(tmp_path / "absent.json")

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not json", encoding="utf-8")
    with pytest.raises(GameGenerationError, match="not valid JSON"):
        load_game(invalid)

    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    with pytest.raises(GameGenerationError, match="must be a JSON object"):
        load_game(array)


def test_validates_a_generated_game_without_its_selection(
    sources: dict[str, Path],
) -> None:
    game = generate_game(target_id=TARGET_ID, seed=11, config=_config(), **sources)

    validate_game_structure(game)
    validate_game_structure(game, backbone_node_ids=_backbone(sources))


def _blank_a_serialized_name(payload: dict) -> None:
    payload["stages"][0]["members"][0]["card"]["english_name"] = "  "


def _hide_a_serialized_tier(payload: dict) -> None:
    payload["stages"][0]["tiers"][0]["ancestor_node_id"] += 100


def _show_a_target_in_a_transition_stage(payload: dict) -> None:
    payload["stages"][0]["target_species_id"] = payload["target_id"]


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        pytest.param(
            lambda payload: payload["stages"][0]["members"].reverse(),
            "shuffle is not deterministic",
            id="reordered-members",
        ),
        pytest.param(
            lambda payload: payload.update(seed=99),
            "game ID does not match",
            id="edited-seed",
        ),
        pytest.param(
            lambda payload: payload.update(target_id=2),
            "game ID does not match",
            id="edited-target",
        ),
        pytest.param(
            lambda payload: payload["stages"].pop(),
            "wrong number of stages",
            id="dropped-stage",
        ),
        pytest.param(
            lambda payload: payload["stages"][0].update(stage_index=5),
            "stage indexes are not contiguous",
            id="renumbered-stage",
        ),
        pytest.param(
            lambda payload: payload["stages"][0]["members"].pop(),
            "wrong member count",
            id="dropped-member",
        ),
        pytest.param(_blank_a_serialized_name, "no English name", id="blank-card"),
        pytest.param(
            _hide_a_serialized_tier,
            "tier projection does not match",
            id="edited-tier",
        ),
        pytest.param(
            _show_a_target_in_a_transition_stage,
            "stage target ID is incorrect",
            id="leaked-target",
        ),
        pytest.param(
            lambda payload: payload["stages"][0].update(mulligan_species_ids=[]),
            "mulligan IDs are incorrect",
            id="cleared-mulligan",
        ),
        pytest.param(
            lambda payload: payload["stages"][0]["members"][0].update(role="bonus"),
            "unknown role",
            id="unknown-role",
        ),
        pytest.param(
            lambda payload: payload.pop("target_id"),
            "invalid serialized game",
            id="missing-field",
        ),
        pytest.param(
            lambda payload: payload["stages"][0]["members"][0].update(
                species_id="not-a-number"
            ),
            "invalid serialized game",
            id="non-numeric-id",
        ),
        pytest.param(
            lambda payload: payload["configuration"].update(total_decoy_species=99),
            "inconsistent fields",
            id="inconsistent-configuration",
        ),
        pytest.param(
            lambda payload: payload["configuration"].pop("members_per_stage"),
            "missing fields",
            id="incomplete-configuration",
        ),
        pytest.param(
            lambda payload: payload.update(generator_version=99),
            "unsupported generator version",
            id="foreign-generator",
        ),
        pytest.param(
            lambda payload: payload.update(schema_version=99),
            "unsupported schema version",
            id="foreign-schema",
        ),
        pytest.param(
            lambda payload: payload["stages"][0].update(start_node_id=999),
            "start node is not its shallowest tier",
            id="moved-start-node",
        ),
    ],
)
def test_rejects_tampered_serialized_games(
    sources: dict[str, Path], tamper, message: str
) -> None:
    game = generate_game(target_id=TARGET_ID, seed=11, config=_config(), **sources)
    payload = _serialized(game)

    game_from_dict(payload)
    tamper(payload)
    with pytest.raises(GameGenerationError, match=message):
        game_from_dict(payload)


def _swap_target_into_first_stage(game, selection):
    target_member = next(
        member for member in game.stages[-1].members if member.role == "target"
    )
    first = replace(
        game.stages[0], members=(target_member, *game.stages[0].members[1:])
    )
    return replace(game, stages=(first, *game.stages[1:]))


def _give_the_target_a_tier(game, selection):
    ultimate = game.stages[-1]
    members = tuple(
        replace(member, tier_index=0, ancestor_node_id=1)
        if member.role == "target"
        else member
        for member in ultimate.members
    )
    return replace(game, stages=(*game.stages[:-1], replace(ultimate, members=members)))


def _with_first_stage(game, **changes):
    first = replace(game.stages[0], **changes)
    return replace(game, stages=(first, *game.stages[1:]))


def _reorder_first_stage(game, selection):
    return _with_first_stage(game, members=tuple(reversed(game.stages[0].members)))


def _blank_an_english_name(game, selection):
    members = game.stages[0].members
    blanked = replace(members[0], card=replace(members[0].card, english_name="   "))
    return _with_first_stage(game, members=(blanked, *members[1:]))


def _move_a_start_node(game, selection):
    return _with_first_stage(
        game, start_node_id=game.stages[0].start_node_id + 100
    )


def _move_a_transition_end_node(game, selection):
    return _with_first_stage(game, end_node_id=game.stages[0].start_node_id)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        pytest.param(
            lambda game, selection: replace(game, schema_version=99),
            "unsupported schema version",
            id="schema-version",
        ),
        pytest.param(
            lambda game, selection: replace(game, generator_version=99),
            "unsupported generator version",
            id="generator-version",
        ),
        pytest.param(
            lambda game, selection: replace(game, game_id="0" * 64),
            "game ID does not match",
            id="game-id",
        ),
        pytest.param(
            lambda game, selection: replace(game, dataset_version="other"),
            "dataset versions differ",
            id="dataset-version",
        ),
        pytest.param(
            lambda game, selection: replace(game, selector_version=99),
            "component versions differ",
            id="selector-version",
        ),
        pytest.param(
            lambda game, selection: replace(game, seed=game.seed + 1),
            "identity differ",
            id="seed",
        ),
        pytest.param(
            lambda game, selection: replace(
                game,
                configuration=replace(
                    game.configuration, mulligan_species_per_stage=2
                ),
            ),
            "configurations differ",
            id="configuration",
        ),
        pytest.param(
            lambda game, selection: replace(game, stages=game.stages[:-1]),
            "stage counts differ",
            id="stage-count",
        ),
        pytest.param(
            _swap_target_into_first_stage, "target visibility", id="target-visibility"
        ),
        pytest.param(
            _give_the_target_a_tier, "terminal endpoint", id="target-tier"
        ),
        pytest.param(_reorder_first_stage, "shuffle is not", id="shuffle"),
        pytest.param(_blank_an_english_name, "no English name", id="blank-card-field"),
        pytest.param(_move_a_start_node, "start node", id="start-node"),
        pytest.param(_move_a_transition_end_node, "end node", id="end-node"),
    ],
)
def test_rejects_invalid_generated_games(
    sources: dict[str, Path], mutate, message: str
) -> None:
    selection, game = _selection_and_game(sources)

    validate_generated_game(game, selection=selection)
    with pytest.raises(GameGenerationError, match=message):
        validate_generated_game(mutate(game, selection), selection=selection)


def test_rejects_games_that_leave_the_target_backbone(
    sources: dict[str, Path],
) -> None:
    selection, game = _selection_and_game(sources)
    backbone = _backbone(sources)

    validate_generated_game(game, selection=selection, backbone_node_ids=backbone)

    with pytest.raises(GameGenerationError, match="backbone position"):
        validate_generated_game(
            game, selection=selection, backbone_node_ids=tuple(reversed(backbone))
        )
    with pytest.raises(GameGenerationError, match="repeats an ancestor"):
        validate_generated_game(
            game, selection=selection, backbone_node_ids=(*backbone, backbone[0])
        )
    # An extra backbone node leaves every tier anchored but moves the endpoint.
    with pytest.raises(GameGenerationError, match="end at the target parent"):
        validate_generated_game(
            game, selection=selection, backbone_node_ids=(*backbone, 999)
        )

    ultimate = game.stages[-1]
    off_backbone = replace(
        game, stages=(*game.stages[:-1], replace(ultimate, end_node_id=999))
    )
    with pytest.raises(GameGenerationError, match="not on the backbone"):
        validate_generated_game(
            off_backbone, selection=selection, backbone_node_ids=backbone
        )
    inverted = replace(
        game, stages=(*game.stages[:-1], replace(ultimate, end_node_id=backbone[0]))
    )
    with pytest.raises(GameGenerationError, match="boundaries are inverted"):
        validate_generated_game(
            inverted, selection=selection, backbone_node_ids=backbone
        )


def test_rejects_stages_that_do_not_descend_one_lineage(
    sources: dict[str, Path],
) -> None:
    _, game = _selection_and_game(sources)
    backbone = _backbone(sources)

    repeated_stage = replace(game, stages=(game.stages[0], game.stages[0]))
    with pytest.raises(GameGenerationError, match="descend one lineage"):
        _validate_stage_continuity(repeated_stage, backbone)

    first = game.stages[0]
    unordered = replace(first, tiers=tuple(reversed(first.tiers)))
    with pytest.raises(GameGenerationError, match="not strictly ordered"):
        _validate_stage_continuity(
            replace(game, stages=(unordered, *game.stages[1:])), backbone
        )

    moved_ancestor = replace(
        first,
        tiers=(replace(first.tiers[0], ancestor_node_id=999), *first.tiers[1:]),
        start_node_id=999,
    )
    with pytest.raises(GameGenerationError, match="backbone position"):
        _validate_stage_continuity(
            replace(game, stages=(moved_ancestor, *game.stages[1:])), backbone
        )

    emptied = replace(first, tiers=())
    with pytest.raises(GameGenerationError, match="no relative tiers"):
        _validate_stage_continuity(
            replace(game, stages=(emptied, *game.stages[1:])), backbone
        )


def test_rejects_a_selected_species_without_card_metadata(
    sources: dict[str, Path],
) -> None:
    selection, _ = _selection_and_game(sources)
    selected_id = selection.relative_species_ids[0]

    connection = sqlite3.connect(sources["normalized_database"])
    connection.execute(
        "DELETE FROM images WHERE ott_id = ?", (100 + selected_id,)
    )
    connection.commit()
    connection.close()

    with pytest.raises(GameGenerationError, match="licensed overall-best image"):
        assemble_game(
            selection,
            normalized_database=sources["normalized_database"],
            tree_database=sources["tree_database"],
        )


def test_rejects_mismatched_or_missing_sources(
    sources: dict[str, Path], tmp_path: Path
) -> None:
    selection, _ = _selection_and_game(sources)

    with pytest.raises(GameGenerationError, match="does not exist"):
        assemble_game(
            selection,
            normalized_database=tmp_path / "absent.sqlite3",
            tree_database=sources["tree_database"],
        )

    connection = sqlite3.connect(sources["normalized_database"])
    connection.execute(
        "UPDATE dataset_metadata SET value = 'other' WHERE key = 'dataset_version'"
    )
    connection.commit()
    connection.close()
    with pytest.raises(GameGenerationError, match="dataset versions differ"):
        assemble_game(
            selection,
            normalized_database=sources["normalized_database"],
            tree_database=sources["tree_database"],
        )


def test_command_line_writes_and_reports_games(
    sources: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    normalized_dir = sources["normalized_database"].parent
    output = tmp_path / "games" / "game.json"

    def argv(target_id: int, *extra: str) -> list[str]:
        return [
            str(target_id),
            "--seed",
            "11",
            "--normalized-dir",
            str(normalized_dir),
            "--members-per-stage",
            "3",
            "--stages-per-game",
            "3",
            *extra,
        ]

    main(argv(TARGET_ID, "--output", str(output)))

    payload = json.loads(output.read_text(encoding="utf-8"))
    expected = generate_game(target_id=TARGET_ID, seed=11, config=_config(), **sources)
    assert payload["game_id"] == expected.game_id
    assert len(payload["stages"]) == 3
    assert "wrote" in capsys.readouterr().out

    main(argv(TARGET_ID))
    assert json.loads(capsys.readouterr().out)["game_id"] == expected.game_id

    # Leaf 2 sits on the shallowest tier and cannot support the stage shape.
    with pytest.raises(SystemExit, match="ineligible"):
        main(argv(2))


def test_carries_divergence_ages_on_every_tier(sources: dict[str, Path]) -> None:
    game = generate_game(target_id=TARGET_ID, seed=11, config=_config(), **sources)

    tiers = [tier for stage in game.stages for tier in stage.tiers]
    # Node 3 has no age, and tier index j - 1 corresponds to node j.
    for tier in tiers:
        expected = (
            None
            if tier.ancestor_node_id == 3
            else float((TIER_COUNT + 1 - tier.ancestor_node_id) * 100)
        )
        assert tier.age_ma == expected, tier

    ages = [tier.age_ma for tier in tiers if tier.age_ma is not None]
    assert ages == sorted(ages, reverse=True)
    assert any(tier.age_ma is None for tier in tiers)


def test_carries_optional_clade_names_on_every_tier(
    sources: dict[str, Path],
) -> None:
    game = generate_game(target_id=TARGET_ID, seed=11, config=_config(), **sources)

    tiers = [tier for stage in game.stages for tier in stage.tiers]
    for tier in tiers:
        expected = (
            None
            if tier.ancestor_node_id == 3
            else f"Clade {tier.ancestor_node_id}"
        )
        assert tier.clade_name == expected


def test_round_trips_tier_display_metadata(sources: dict[str, Path]) -> None:
    game = generate_game(target_id=TARGET_ID, seed=11, config=_config(), **sources)

    payload = _serialized(game)
    assert "age_ma" in payload["stages"][0]["tiers"][0]
    assert "clade_name" in payload["stages"][0]["tiers"][0]
    restored = game_from_dict(payload)

    assert restored == game
    assert [t.age_ma for s in restored.stages for t in s.tiers] == [
        t.age_ma for s in game.stages for t in s.tiers
    ]
    assert [t.clade_name for s in restored.stages for t in s.tiers] == [
        t.clade_name for s in game.stages for t in s.tiers
    ]


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        pytest.param(
            # Make the shallowest tier younger than a deeper one.
            lambda payload: payload["stages"][0]["tiers"][0].update(age_ma=0.5),
            "divergence ages increase toward the target",
            id="ages-increase",
        ),
        pytest.param(
            lambda payload: payload["stages"][0]["tiers"][0].update(age_ma=-5.0),
            "divergence age is negative",
            id="negative-age",
        ),
        pytest.param(
            lambda payload: payload["stages"][0]["tiers"][0].update(
                age_ma=float("nan")
            ),
            "divergence age is not finite",
            id="nan-age",
        ),
        pytest.param(
            lambda payload: payload["stages"][0]["tiers"][0].update(
                age_ma=float("inf")
            ),
            "divergence age is not finite",
            id="infinite-age",
        ),
        pytest.param(
            lambda payload: payload["stages"][0]["tiers"][0].update(age_ma="old"),
            "invalid serialized game",
            id="non-numeric-age",
        ),
        pytest.param(
            lambda payload: payload["stages"][0]["tiers"][0].pop("age_ma"),
            "invalid serialized game",
            id="missing-age-field",
        ),
        pytest.param(
            lambda payload: payload["stages"][0]["tiers"][0].update(
                clade_name="  "
            ),
            "clade name is blank or unnormalized",
            id="blank-clade-name",
        ),
        pytest.param(
            lambda payload: payload["stages"][0]["tiers"][0].pop(
                "clade_name"
            ),
            "invalid serialized game",
            id="missing-clade-name-field",
        ),
    ],
)
def test_rejects_invalid_tier_display_metadata(
    sources: dict[str, Path], tamper, message: str
) -> None:
    game = generate_game(target_id=TARGET_ID, seed=11, config=_config(), **sources)
    payload = _serialized(game)

    game_from_dict(payload)
    tamper(payload)
    with pytest.raises(GameGenerationError, match=message):
        game_from_dict(payload)


def test_reports_the_new_schema_and_generator_versions(
    sources: dict[str, Path],
) -> None:
    game = generate_game(target_id=TARGET_ID, seed=11, config=_config(), **sources)

    assert game.schema_version == GAME_SCHEMA_VERSION == 3
    assert game.generator_version == GAME_GENERATOR_VERSION == 3
    # A game serialized by the previous generator must be refused, not guessed at.
    stale = _serialized(game)
    stale["schema_version"] = 2
    with pytest.raises(GameGenerationError, match="unsupported schema version"):
        game_from_dict(stale)
