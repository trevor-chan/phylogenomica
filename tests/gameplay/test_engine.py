import json
from dataclasses import replace
from itertools import permutations

import pytest

from phylogenomica.data.cards import CardImage, MetadataSource, SpeciesCard
from phylogenomica.gameplay.engine import (
    GAMEPLAY_ENGINE_VERSION,
    GameplayError,
    GameState,
    apply_guess,
    best_achievable_score,
    forfeited_score,
    initial_state,
    main,
    maximum_score,
    perfect_guesses,
    replay,
    restore_state,
    score,
    stage_at_stake,
    stage_maximum,
    validate_state,
)
from phylogenomica.generation.feasibility import FeasibilityConfig
from phylogenomica.generation.game import (
    GAME_GENERATOR_VERSION,
    GAME_SCHEMA_VERSION,
    GameMember,
    GameTier,
    GeneratedGame,
    GeneratedStage,
    _canonical_members,
    _game_id,
    _shuffle_members,
    validate_game_structure,
)

# The engine consumes an already validated game, so these fixtures are built
# directly rather than generated from a database.

GAME_ID = "0" * 64


def _card(species_id: int) -> SpeciesCard:
    return SpeciesCard(
        species_id=species_id,
        scientific_name=f"Species {species_id}",
        english_name=f"Common {species_id}",
        ott_id=100 + species_id,
        popularity_rank=species_id,
        vernacular_source=MetadataSource("vernacular_by_ott", species_id),
        image=CardImage(
            url=f"https://example.test/{species_id}.jpg",
            rights="Test author",
            license="CC BY 4.0",
            source_code=99,
            source_id=f"image-{species_id}",
            source=MetadataSource("images_by_ott", species_id),
        ),
    )


def _member(species_id: int, role: str, tier_index: int | None) -> GameMember:
    return GameMember(
        species_id=species_id,
        role=role,  # type: ignore[arg-type]
        tier_index=tier_index,
        ancestor_node_id=None if tier_index is None else 1000 + tier_index,
        card=_card(species_id),
    )


def _stage(stage_index: int, specs, *, target_id: int | None = None):
    members = tuple(_member(*spec) for spec in specs)
    relatives = [member for member in members if member.role != "target"]
    grouped: dict[tuple[int, int, str], list[int]] = {}
    for relative in relatives:
        key = (relative.tier_index, relative.ancestor_node_id, relative.role)
        grouped.setdefault(key, []).append(relative.species_id)
    tiers = tuple(
        GameTier(
            tier_index=tier_index,
            ancestor_node_id=ancestor_node_id,
            role=role,  # type: ignore[arg-type]
            species_ids=tuple(sorted(species_ids)),
            age_ma=float(500 - tier_index * 10),
        )
        for (tier_index, ancestor_node_id, role), species_ids in sorted(
            grouped.items()
        )
    )
    return GeneratedStage(
        stage_index=stage_index,
        start_node_id=tiers[0].ancestor_node_id,
        end_node_id=tiers[-1].ancestor_node_id,
        members=members,
        tiers=tiers,
        mulligan_species_ids=tuple(
            sorted(r.species_id for r in relatives if r.role == "mulligan")
        ),
        unlock_species_ids=tuple(
            sorted(r.species_id for r in relatives if r.role == "unlock")
        ),
        target_species_id=target_id,
    )


def _game(stages, config: FeasibilityConfig, target_id: int) -> GeneratedGame:
    return GeneratedGame(
        schema_version=GAME_SCHEMA_VERSION,
        game_id=GAME_ID,
        dataset_version="test-play-1",
        generator_version=GAME_GENERATOR_VERSION,
        selector_version=1,
        eligibility_index_version=1,
        target_id=target_id,
        seed=1,
        configuration=config,
        stages=tuple(stages),
    )


# The worked example from docs/game_design.md, as a transition stage:
#   A(1) B(2) [C,D,E](3) F(4) G(5)=mulligan H(6)=unlock
A, B, C, D, E, F, G, H = 1, 2, 3, 4, 5, 6, 7, 8
TARGET = 100
DOC_CONFIG = FeasibilityConfig(members_per_stage=8, stages_per_game=2)


def _documented_game() -> GeneratedGame:
    # Member order is deliberately scrambled: the engine must not depend on it.
    transition = _stage(
        0,
        [
            (F, "decoy", 4),
            (A, "decoy", 1),
            (H, "unlock", 6),
            (D, "decoy", 3),
            (G, "mulligan", 5),
            (B, "decoy", 2),
            (E, "decoy", 3),
            (C, "decoy", 3),
        ],
    )
    ultimate = _stage(
        1,
        [
            (9, "decoy", 7),
            (10, "decoy", 8),
            (11, "decoy", 9),
            (12, "decoy", 10),
            (13, "decoy", 11),
            (14, "decoy", 12),
            (15, "mulligan", 13),
            (TARGET, "target", None),
        ],
        target_id=TARGET,
    )
    return _game([transition, ultimate], DOC_CONFIG, TARGET)


def _playable_game() -> GeneratedGame:
    """Return the documented game with a real identifier and stage shuffle.

    The engine never validates, but the CLI loads through ``load_game``, so
    this rebuilds the two fields generation derives rather than stores.
    """
    game = _documented_game()
    game = replace(game, game_id=_game_id(game))
    stages = tuple(
        replace(
            stage,
            members=_shuffle_members(
                _canonical_members(
                    stage, is_ultimate=index == len(game.stages) - 1
                ),
                source=game,
                stage_index=index,
            ),
        )
        for index, stage in enumerate(game.stages)
    )
    return replace(game, stages=stages)


SMALL_CONFIG = FeasibilityConfig(members_per_stage=5, stages_per_game=2)


def _small_game() -> GeneratedGame:
    transition = _stage(
        0,
        [
            (1, "decoy", 1),
            (2, "decoy", 2),
            (3, "decoy", 3),
            (4, "mulligan", 4),
            (5, "unlock", 5),
        ],
    )
    ultimate = _stage(
        1,
        [
            (6, "decoy", 6),
            (7, "decoy", 7),
            (8, "decoy", 8),
            (9, "mulligan", 9),
            (TARGET, "target", None),
        ],
        target_id=TARGET,
    )
    return _game([transition, ultimate], SMALL_CONFIG, TARGET)


def test_perfect_play_scores_the_maximum() -> None:
    game = _documented_game()

    state, outcomes = replay(game, perfect_guesses(game))

    assert score(game, state) == maximum_score(game) == 16
    assert state.completed
    assert state.stage_scores == (8, 8)
    assert len(state.placements) == 16
    assert all(outcome.penalty == 0 for outcome in outcomes)
    assert outcomes[-1].game_completed
    # Ending a stage resolves every remaining card at no cost.
    assert len(outcomes[0].placed) == 8


def test_mulligan_then_stage_ending_card_ties_perfect_play() -> None:
    game = _documented_game()
    immediate, _ = replay(game, perfect_guesses(game))

    routed, outcomes = replay(game, [G, H, 15, TARGET])

    assert score(game, routed) == score(game, immediate) == maximum_score(game)
    assert routed.stage_scores == immediate.stage_scores
    mulligan = outcomes[0]
    assert (mulligan.penalty, mulligan.bonus) == (1, 1)
    assert mulligan.score_change == 0
    assert not mulligan.stage_completed
    # The mulligan places the shallower decoys but is never charged for them.
    assert {placed.species_id for placed in mulligan.placed} == {A, B, C, D, E, F, G}
    assert mulligan.remaining_species_ids == (H,)


def test_scores_the_documented_reveal_weighted_example() -> None:
    game = _documented_game()

    _, outcomes = replay(game, [B, C, F, G, H])

    assert [(o.penalty, o.bonus) for o in outcomes] == [
        (2, 0),  # B exposes A
        (1, 0),  # C exposes nothing; D and E share its tier
        (3, 0),  # F exposes D and E
        (1, 1),  # mulligan: flat cost, cancelled by its bonus
        (0, 0),  # the unlock ends the stage for free
    ]
    # The open stage's standing value ticks down from the stage maximum.
    assert [o.stage_at_stake for o in outcomes[:4]] == [6, 5, 2, 2]
    assert [o.best_achievable_score for o in outcomes] == [14, 13, 10, 10, 10]
    # Banked score only moves when a stage completes.
    assert [o.score for o in outcomes] == [0, 0, 0, 0, 2]
    assert outcomes[-1].stage_score == 2
    assert outcomes[0].placed[0].placement == "guessed"
    assert {p.species_id for p in outcomes[0].placed} == {A, B}
    assert {p.species_id for p in outcomes[2].placed} == {D, E, F}


def test_deep_wrong_guesses_cost_more_than_shallow_ones() -> None:
    game = _documented_game()
    state = initial_state(game)

    _, shallow = apply_guess(game, state, A)
    _, deep = apply_guess(game, state, F)

    assert shallow.penalty == 1
    assert deep.penalty == 6  # itself plus A, B, C, D and E
    assert deep.penalty > shallow.penalty


def test_a_guess_never_eliminates_a_possibly_deeper_relative() -> None:
    game = _documented_game()
    stage = game.stages[0]
    depth = {m.species_id: m.tier_index for m in stage.members}

    for guess in (A, B, C, D, E, F, G):
        _, outcome = apply_guess(game, initial_state(game), guess)
        placed = {p.species_id for p in outcome.placed}
        deeper = {
            species_id
            for species_id, tier in depth.items()
            if tier > depth[guess]
        }
        assert not (placed & deeper), guess
        assert deeper <= set(outcome.remaining_species_ids), guess


def test_polytomy_peers_stay_active_and_cost_nothing_extra() -> None:
    game = _documented_game()

    state, first = apply_guess(game, initial_state(game), C)

    # D and E share C's tier, so the guess established no order among them.
    assert set(first.remaining_species_ids) >= {D, E}
    assert first.penalty == 1 + 2  # itself plus the shallower A and B
    assert {p.species_id for p in first.placed} == {A, B, C}

    _, peer = apply_guess(game, state, D)
    assert peer.penalty == 1  # nothing shallower is still standing
    assert E in peer.remaining_species_ids


def test_advances_stages_and_completes_the_game() -> None:
    game = _documented_game()

    state, unlock = apply_guess(game, initial_state(game), H)

    assert unlock.stage_completed and not unlock.game_completed
    assert state.current_stage_index == 1
    assert not state.completed
    assert set(state.active_species_ids) == {
        member.species_id for member in game.stages[1].members
    }
    assert state.stage_penalty == 0 and state.stage_bonus == 0

    state, final = apply_guess(game, state, TARGET)
    assert final.role == "target"
    assert final.stage_completed and final.game_completed
    assert state.completed
    assert state.active_species_ids == ()
    assert state.current_stage_index == len(game.stages)


def test_rejects_invalid_guesses() -> None:
    game = _documented_game()
    state = initial_state(game)

    with pytest.raises(GameplayError, match="not in the active stage"):
        apply_guess(game, state, 999)
    with pytest.raises(GameplayError, match="not in the active stage"):
        apply_guess(game, state, TARGET)  # a card of a later stage

    state, _ = apply_guess(game, state, B)
    with pytest.raises(GameplayError, match="already placed"):
        apply_guess(game, state, A)  # revealed by the guess above
    with pytest.raises(GameplayError, match="already placed"):
        apply_guess(game, state, B)

    finished, _ = replay(game, perfect_guesses(game))
    with pytest.raises(GameplayError, match="already complete"):
        apply_guess(game, finished, A)

    other = replace(game, game_id="1" * 64)
    with pytest.raises(GameplayError, match="different game"):
        apply_guess(other, initial_state(game), A)


def test_every_guess_order_scores_within_the_structural_bounds() -> None:
    game = _small_game()
    config = game.configuration
    stage = game.stages[0]
    ending = stage.unlock_species_ids[0]
    # Only decoys can ever be charged: the stage-ending card is free, and a
    # mulligan's flat cost is cancelled by its bonus while its reveals are
    # free. So the floor is the stage maximum minus the decoy count.
    floor = (
        config.unlock_species_per_transition_stage
        + config.mulligan_species_per_stage
    )
    observed = set()

    for order in permutations(m.species_id for m in stage.members):
        state = initial_state(game)
        for species_id in order:
            # A guess may already have revealed a later card in this order.
            if species_id not in state.active_species_ids:
                continue
            state, _ = apply_guess(game, state, species_id)
            if species_id == ending:
                break
        stage_score = state.stage_scores[0]
        assert floor <= stage_score <= stage_maximum(game), order
        observed.add(stage_score)

    assert max(observed) == stage_maximum(game) == 5
    assert min(observed) == floor == 2
    # A stage score is therefore always positive; the engine's clamp at zero
    # is a guard, never a routine outcome.
    assert all(stage_score > 0 for stage_score in observed)


def test_records_placements_for_the_cladogram() -> None:
    game = _documented_game()

    state, _ = replay(game, [B, H, TARGET])

    by_id = {placed.species_id: placed for placed in state.placements}
    assert by_id[B].placement == "guessed"
    assert by_id[A].placement == "revealed"
    assert by_id[B].tier_index == 2
    assert by_id[B].ancestor_node_id == 1002
    assert by_id[B].stage_index == 0
    assert by_id[TARGET].role == "target"
    assert by_id[TARGET].tier_index is None
    assert len(state.placements) == 16


def test_tracks_forfeited_and_best_achievable_score() -> None:
    game = _documented_game()
    state = initial_state(game)

    assert forfeited_score(game, state) == 0
    assert best_achievable_score(game, state) == maximum_score(game)
    assert stage_at_stake(game, state) == stage_maximum(game)

    state, _ = apply_guess(game, state, F)  # costs 6
    assert forfeited_score(game, state) == 6
    assert best_achievable_score(game, state) == 10
    assert stage_at_stake(game, state) == 2
    assert score(game, state) == 0  # nothing banked until the stage ends

    state, _ = replay(game, [F, H, TARGET])
    assert state.completed
    assert score(game, state) == 10
    assert forfeited_score(game, state) == 6
    assert stage_at_stake(game, state) == 0


def test_round_trips_player_state() -> None:
    game = _documented_game()
    state, _ = replay(game, [B, C, G])

    payload = json.loads(json.dumps(state.to_dict()))
    restored = restore_state(game, payload)

    assert restored == state
    assert score(game, restored) == score(game, state)
    # Play continues identically from the restored position.
    resumed, _ = apply_guess(game, restored, H)
    expected, _ = apply_guess(game, state, H)
    assert resumed == expected


def test_rejects_invalid_restored_state() -> None:
    game = _documented_game()
    state, _ = replay(game, [B])
    payload = json.loads(json.dumps(state.to_dict()))

    validate_state(game, state)

    for mutate, message in (
        (lambda p: p.update(game_id="1" * 64), "different game"),
        (lambda p: p.update(engine_version=99), "unsupported engine version"),
        (lambda p: p.update(completed=True), "completion flag"),
        (lambda p: p.update(stage_scores=[8]), "banked stage scores"),
        (lambda p: p.update(stage_penalty=-1), "must not be negative"),
        (lambda p: p["active_species_ids"].append(999), "not cards of the current"),
        (lambda p: p["active_species_ids"].pop(), "do not cover the stage"),
        (lambda p: p.pop("placements"), "invalid serialized game state"),
    ):
        broken = json.loads(json.dumps(payload))
        mutate(broken)
        with pytest.raises(GameplayError, match=message):
            restore_state(game, broken)


def test_rejects_a_state_that_repeats_a_placement() -> None:
    game = _documented_game()
    state, _ = replay(game, [B])
    duplicated = replace(
        state, placements=(*state.placements, state.placements[0])
    )

    with pytest.raises(GameplayError, match="placed more than once"):
        validate_state(game, duplicated)


def test_initial_state_is_valid_and_serializable() -> None:
    game = _documented_game()
    state = initial_state(game)

    validate_state(game, state)
    assert state.engine_version == GAMEPLAY_ENGINE_VERSION
    assert isinstance(GameState(**{**state.to_dict(), "placements": ()}), GameState)
    assert score(game, state) == 0
    assert stage_at_stake(game, state) == stage_maximum(game)
    assert best_achievable_score(game, state) == maximum_score(game)
    assert len(state.active_species_ids) == stage_maximum(game)


def test_command_line_plays_a_game(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    game = _playable_game()
    validate_game_structure(game)
    path = tmp_path / "game.json"
    path.write_text(json.dumps(game.to_dict(), sort_keys=True), encoding="utf-8")
    state_path = tmp_path / "player" / "state.json"

    main([str(path), "--state", str(state_path)])

    printed = capsys.readouterr().out
    assert "score 16 / 16" in printed
    assert "completed" in printed
    resumed = restore_state(game, json.loads(state_path.read_text(encoding="utf-8")))
    assert resumed.completed
    assert score(game, resumed) == 16

    main([str(path), "--guess", str(B), "--guess", str(H), "--guess", str(TARGET)])
    assert "score 14 / 16" in capsys.readouterr().out

    with pytest.raises(SystemExit, match="not in the active stage"):
        main([str(path), "--guess", "999"])
