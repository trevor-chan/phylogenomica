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
    dealt_members,
    initial_state,
    main,
    maximum_score,
    parse_difficulty,
    perfect_guesses,
    replay,
    restore_state,
    revealed_target,
    score,
    selectable_members,
    stage_ending_ids,
    stage_maximum,
    target_is_revealed,
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

    state, outcomes = replay(
        game, perfect_guesses(game, "expert"), "expert"
    )

    assert score(game, state) == maximum_score(game, "expert") == 14
    assert state.completed
    assert state.stage_scores == (7, 7)
    assert len(state.placements) == 16
    # Six decoys resolved plus the clean-stage point; the mulligan the stage
    # resolves alongside them is not scored.
    assert all(outcome.earned == 6 for outcome in outcomes)
    assert all(outcome.perfect_stage and not outcome.missed for outcome in outcomes)
    assert outcomes[-1].game_completed
    # Ending a stage resolves every remaining card at no cost.
    assert len(outcomes[0].placed) == 8


def test_mulligan_then_stage_ending_card_ties_perfect_play() -> None:
    game = _documented_game()
    immediate, _ = replay(game, perfect_guesses(game, "expert"), "expert")

    routed, outcomes = replay(game, [G, H, 15, TARGET], "expert")

    assert (
        score(game, routed)
        == score(game, immediate)
        == maximum_score(game, "expert")
    )
    assert routed.stage_scores == immediate.stage_scores
    mulligan = outcomes[0]
    # The mulligan is not scored: choosing it costs nothing, leaves the stage
    # clean, and earns only the decoys it resolved.
    assert mulligan.earned == 6 and not mulligan.missed
    assert mulligan.score_change == 6
    assert not mulligan.stage_completed
    # The mulligan places the shallower decoys but is never charged for them.
    assert {placed.species_id for placed in mulligan.placed} == {A, B, C, D, E, F, G}
    assert mulligan.remaining_species_ids == (H,)


def test_scores_the_documented_worked_example() -> None:
    game = _documented_game()

    state, outcomes = replay(game, [B, C, F, G, H], "expert")

    # A guess earns the relationships it resolved for you; the card you chose
    # scores nothing itself.
    assert [o.earned for o in outcomes] == [
        1,  # B resolves A
        0,  # C resolves nothing; D and E share its tier
        2,  # F resolves D and E
        0,  # the mulligan is not scored, and resolves nothing still standing
        0,  # the unlock ends a stage with nothing left to resolve
    ]
    assert [o.missed for o in outcomes] == [True, True, True, False, False]
    # The score rises with every guess and is never taken back.
    assert [o.score for o in outcomes] == [1, 1, 3, 3, 3]
    # Three wrong guesses, so three points off the stage and no clean bonus.
    assert outcomes[-1].stage_score == 3 == stage_maximum(game, "expert") - 1 - 3
    assert not outcomes[-1].perfect_stage
    assert state.stage_scores == (3,)
    assert outcomes[0].placed[0].placement == "guessed"
    assert {p.species_id for p in outcomes[0].placed} == {A, B}
    assert {p.species_id for p in outcomes[2].placed} == {D, E, F}


def test_every_wrong_guess_costs_one_however_near_it_was() -> None:
    game = _documented_game()
    state = initial_state(game, "expert")

    _, shallow = apply_guess(game, state, A)
    _, near = apply_guess(game, state, F)

    # Narrowing the field is rewarded, not charged: the near miss resolves five
    # relationships and banks them immediately, while the distant guess
    # resolves nothing. Both cost the stage exactly one point.
    assert (shallow.earned, near.earned) == (0, 5)
    assert shallow.missed and near.missed
    shallow_stage, _ = replay(game, [A, H], "expert")
    near_stage, _ = replay(game, [F, H], "expert")
    assert shallow_stage.stage_scores == near_stage.stage_scores == (5,)


def test_a_guess_never_eliminates_a_possibly_deeper_relative() -> None:
    game = _documented_game()
    stage = game.stages[0]
    depth = {m.species_id: m.tier_index for m in stage.members}

    for guess in (A, B, C, D, E, F, G):
        _, outcome = apply_guess(game, initial_state(game, "expert"), guess)
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

    state, first = apply_guess(game, initial_state(game, "expert"), C)

    # D and E share C's tier, so the guess established no order among them.
    assert set(first.remaining_species_ids) >= {D, E}
    assert first.earned == 2  # A and B, resolved as more distant
    assert {p.species_id for p in first.placed} == {A, B, C}

    _, peer = apply_guess(game, state, D)
    assert peer.earned == 0  # nothing shallower is still standing
    assert E in peer.remaining_species_ids


def test_advances_stages_and_completes_the_game() -> None:
    game = _documented_game()

    state, unlock = apply_guess(game, initial_state(game, "expert"), H)

    assert unlock.stage_completed and not unlock.game_completed
    assert state.current_stage_index == 1
    assert not state.completed
    assert set(state.active_species_ids) == {
        member.species_id for member in game.stages[1].members
    }
    assert state.stage_points == 0 and state.stage_misses == 0

    state, final = apply_guess(game, state, TARGET)
    assert final.role == "target"
    assert final.stage_completed and final.game_completed
    assert state.completed
    assert state.active_species_ids == ()
    assert state.current_stage_index == len(game.stages)


def test_normal_play_deals_every_card_but_the_mulligan() -> None:
    game = _documented_game()
    transition, ultimate = game.stages

    dealt = {member.species_id for member in dealt_members(transition, "normal")}
    selectable = {
        member.species_id for member in selectable_members(transition, "normal")
    }

    # The mulligan exists to make the second-deepest relative tempting, which a
    # revealed target settles outright.
    assert dealt == selectable == {A, B, C, D, E, F, H}
    assert G not in dealt
    assert stage_ending_ids(transition, "normal") == {H}
    # Expert play is untouched: one generated game serves both difficulties.
    assert {m.species_id for m in dealt_members(transition, "expert")} == {
        member.species_id for member in transition.members
    }
    assert stage_ending_ids(transition, "expert") == {H}
    assert stage_ending_ids(ultimate, "expert") == {TARGET}


def test_normal_ultimate_stage_shows_the_target_but_never_offers_it() -> None:
    game = _documented_game()
    ultimate = game.stages[1]

    dealt = {member.species_id for member in dealt_members(ultimate, "normal")}
    selectable = {
        member.species_id for member in selectable_members(ultimate, "normal")
    }

    # With no unlock to find, the deepest relative is the closest relative and
    # ends the game; the target is dealt only so the player can see it.
    assert TARGET in dealt
    assert selectable == dealt - {TARGET}
    assert 15 in selectable
    assert stage_ending_ids(ultimate, "normal") == {15}
    assert revealed_target(game, "normal").species_id == TARGET
    assert revealed_target(game, "expert") is None
    assert target_is_revealed("normal") and not target_is_revealed("expert")


def test_normal_play_scores_one_choice_per_card_it_offers() -> None:
    game = _documented_game()

    state, outcomes = replay(game, perfect_guesses(game, "normal"), "normal")

    # Seven choices per stage rather than expert's eight: no mulligan is dealt,
    # and the target the ultimate stage shows cannot be chosen.
    # Both difficulties score over the same cards — the decoys plus the one
    # that ends the stage — so a stage is worth the same in either mode.
    assert stage_maximum(game, "normal") == stage_maximum(game) == 7
    assert score(game, state) == maximum_score(game, "normal") == 14
    assert state.stage_scores == (7, 7)
    assert state.completed
    assert perfect_guesses(game, "normal") == (H, 15)
    # Six relatives resolved plus the clean-stage point; the revealed target
    # lands with them but is not one of the stage's choices.
    assert [(o.earned, o.perfect_stage) for o in outcomes] == [(6, True), (6, True)]


def test_normal_play_scores_decoys_exactly_as_expert_play_does() -> None:
    game = _documented_game()

    _, outcomes = replay(game, [B, C, F, H], "normal")

    assert [o.earned for o in outcomes] == [
        1,  # B resolves A
        0,  # C resolves nothing; D and E share its tier
        2,  # F resolves D and E
        0,  # the closest relative ends a stage with nothing left to resolve
    ]
    assert [o.missed for o in outcomes] == [True, True, True, False]
    # Three wrong guesses off a seven-point stage, and no clean-stage point.
    assert outcomes[-1].stage_score == 3
    assert not outcomes[-1].perfect_stage
    # G is never dealt, so no guess of it is ever charged or revealed.
    assert all(
        G not in {placed.species_id for placed in outcome.placed}
        for outcome in outcomes
    )


def test_normal_play_places_the_revealed_target_as_the_endpoint() -> None:
    game = _documented_game()

    state, outcomes = replay(game, [H, 15], "normal")

    by_id = {placed.species_id: placed for placed in state.placements}
    assert state.completed
    assert by_id[TARGET].placement == "revealed"
    assert by_id[TARGET].role == "target"
    assert by_id[TARGET].tier_index is None
    # The closing guess resolves the stage, so the target lands last, deepest.
    assert outcomes[-1].placed[-1].species_id == TARGET
    assert G not in by_id and 15 in by_id


def test_normal_play_rejects_the_target_and_the_undealt_mulligan() -> None:
    game = _documented_game()
    state = initial_state(game, "normal")

    with pytest.raises(GameplayError, match="not in the active stage"):
        apply_guess(game, state, G)

    state, _ = apply_guess(game, state, H)
    with pytest.raises(GameplayError, match="revealed target and cannot be chosen"):
        apply_guess(game, state, TARGET)
    # Expert play still ends on the target it has just shown.
    expert, _ = apply_guess(game, initial_state(game, "expert"), H)
    _, final = apply_guess(game, expert, TARGET)
    assert final.game_completed


def test_state_carries_the_difficulty_it_is_played_under() -> None:
    game = _documented_game()
    state, _ = replay(game, [B, H], "normal")

    restored = restore_state(game, json.loads(json.dumps(state.to_dict())))

    assert restored == state
    assert restored.difficulty == "normal"
    validate_state(game, restored)
    # Each difficulty deals its own cards, so a state relabelled as the other
    # one no longer accounts for the stage it claims to be playing.
    with pytest.raises(GameplayError, match="do not cover the stage"):
        validate_state(game, replace(state, difficulty="expert"))
    with pytest.raises(GameplayError, match="unknown difficulty"):
        parse_difficulty("easy")
    with pytest.raises(GameplayError, match="unknown difficulty"):
        parse_difficulty("guided")
    with pytest.raises(GameplayError, match="unknown difficulty"):
        restore_state(
            game, {**json.loads(json.dumps(state.to_dict())), "difficulty": "easy"}
        )


def test_rejects_invalid_guesses() -> None:
    game = _documented_game()
    state = initial_state(game, "expert")

    with pytest.raises(GameplayError, match="not in the active stage"):
        apply_guess(game, state, 999)
    with pytest.raises(GameplayError, match="not in the active stage"):
        apply_guess(game, state, TARGET)  # a card of a later stage

    state, _ = apply_guess(game, state, B)
    with pytest.raises(GameplayError, match="already placed"):
        apply_guess(game, state, A)  # revealed by the guess above
    with pytest.raises(GameplayError, match="already placed"):
        apply_guess(game, state, B)

    finished, _ = replay(
        game, perfect_guesses(game, "expert"), "expert"
    )
    with pytest.raises(GameplayError, match="already complete"):
        apply_guess(game, finished, A)

    other = replace(game, game_id="1" * 64)
    with pytest.raises(GameplayError, match="different game"):
        apply_guess(other, initial_state(game, "expert"), A)


@pytest.mark.parametrize("difficulty", ["expert", "normal"])
def test_every_order_of_play_costs_exactly_one_point_per_wrong_guess(
    difficulty: str,
) -> None:
    """Pin the whole scoring model to one identity, over every order of play.

    A stage scores ``(N - 1) - wrong + clean``: one point per card it offers
    less one for each wrong guess, plus one for a stage with no wrong guess in
    it. Nothing about which card was guessed changes that — not its depth, not
    how much it revealed. Enumerating every order proves it without trusting
    any single worked example.
    """
    game = _small_game()
    stage = game.stages[0]
    maximum = stage_maximum(game, difficulty)
    ids = [member.species_id for member in selectable_members(stage, difficulty)]
    observed = set()

    for order in permutations(ids):
        state = initial_state(game, difficulty)
        wrong = 0
        for species_id in order:
            # A guess may already have revealed a later card in this order.
            if species_id not in state.active_species_ids:
                continue
            state, outcome = apply_guess(game, state, species_id)
            wrong += 1 if outcome.missed else 0
            if outcome.stage_completed:
                break
        clean = 1 if wrong == 0 else 0
        stage_score = state.stage_scores[0]
        assert stage_score == (maximum - 1) - wrong + clean, (order, wrong)
        assert 0 <= stage_score <= maximum
        observed.add(stage_score)

    # The floor is the stage's own decoy count spent, one point each.
    decoys = sum(1 for member in stage.members if member.role == "decoy")
    assert max(observed) == maximum
    assert min(observed) == (maximum - 1) - decoys == 0
    # No order scores maximum - 1: a stage with no wrong guess always earns the
    # clean-stage point, so the value just below a perfect stage is unreachable.
    assert maximum - 1 not in observed


def test_two_wrong_guesses_cost_exactly_two_points() -> None:
    """The reported case, pinned against the shape of the wrong guesses."""
    game = _documented_game()
    maximum = stage_maximum(game, "expert")

    shallow, _ = replay(game, [A, B, H], "expert")  # neither reveals anything
    deep, _ = replay(
        game, [C, D, H], "expert"
    )  # C reveals A and B; D is its tier peer

    # Two wrong guesses cost two points whether they revealed five
    # relationships between them or none.
    assert shallow.stage_scores[0] == deep.stage_scores[0] == maximum - 1 - 2

    # Two clicks are not always two wrong guesses. A card an earlier guess
    # already revealed cannot be chosen, and the mulligan is not scored at all.
    single, outcomes = replay(game, [F, G, H], "expert")
    assert [o.missed for o in outcomes] == [True, False, False]
    assert single.stage_scores[0] == maximum - 1 - 1


def test_records_placements_for_the_cladogram() -> None:
    game = _documented_game()

    state, _ = replay(game, [B, H, TARGET], "expert")

    by_id = {placed.species_id: placed for placed in state.placements}
    assert by_id[B].placement == "guessed"
    assert by_id[A].placement == "revealed"
    assert by_id[B].tier_index == 2
    assert by_id[B].ancestor_node_id == 1002
    assert by_id[B].stage_index == 0
    assert by_id[TARGET].role == "target"
    assert by_id[TARGET].tier_index is None
    assert len(state.placements) == 16


def test_the_running_score_only_ever_rises() -> None:
    game = _documented_game()
    state = initial_state(game, "expert")

    assert score(game, state) == 0

    running = [score(game, state)]
    for species_id in (F, C, H, 15, TARGET):
        if species_id not in state.active_species_ids:
            continue
        state, _ = apply_guess(game, state, species_id)
        running.append(score(game, state))

    # The open stage counts toward the score as it is played, so there is no
    # separate banked total to reconcile against.
    assert running == sorted(running)
    assert running[1] == 5  # the first guess resolved five relationships
    assert state.completed
    assert score(game, state) == sum(state.stage_scores) == 5 + 7


def test_round_trips_player_state() -> None:
    game = _documented_game()
    state, _ = replay(game, [B, C, G], "expert")

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
    state, _ = replay(game, [B], "expert")
    payload = json.loads(json.dumps(state.to_dict()))

    validate_state(game, state)

    for mutate, message in (
        (lambda p: p.update(game_id="1" * 64), "different game"),
        (lambda p: p.update(engine_version=3), "unsupported engine version"),
        (lambda p: p.update(completed=True), "completion flag"),
        (lambda p: p.update(stage_scores=[8]), "stage scores disagree"),
        (lambda p: p.update(stage_points=-1), "must not be negative"),
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
    state, _ = replay(game, [B], "expert")
    duplicated = replace(
        state, placements=(*state.placements, state.placements[0])
    )

    with pytest.raises(GameplayError, match="placed more than once"):
        validate_state(game, duplicated)


def test_rejects_well_shaped_but_unreachable_restored_states() -> None:
    game = _documented_game()
    completed, _ = replay(
        game, perfect_guesses(game, "expert"), "expert"
    )

    for broken in (
        # A completed game cannot omit every placement or award arbitrary score.
        replace(completed, placements=(), stage_scores=(999, 999)),
        # Immutable topology and role data must agree with the generated game.
        replace(
            completed,
            placements=(
                replace(completed.placements[0], role="target"),  # type: ignore[arg-type]
                *completed.placements[1:],
            ),
        ),
    ):
        with pytest.raises(GameplayError, match="not reachable"):
            restore_state(game, json.loads(json.dumps(broken.to_dict())))


def test_initial_state_is_valid_and_serializable() -> None:
    game = _documented_game()
    state = initial_state(game)

    validate_state(game, state)
    assert state.engine_version == GAMEPLAY_ENGINE_VERSION
    assert state.difficulty == "normal"
    assert isinstance(GameState(**{**state.to_dict(), "placements": ()}), GameState)
    assert score(game, state) == 0
    # Normal is the default and omits the transition-stage mulligan, so every
    # selectable card is part of the stage score.
    assert len(state.active_species_ids) == len(
        selectable_members(game.stages[0])
    ) == stage_maximum(game)


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
    assert "score 14 / 14" in printed
    assert "completed" in printed
    resumed = restore_state(game, json.loads(state_path.read_text(encoding="utf-8")))
    assert resumed.completed
    assert score(game, resumed) == 14

    # One wrong guess in the opening stage, then clean play.
    main([str(path), "--guess", str(B), "--guess", str(H), "--guess", "15"])
    assert "score 12 / 14" in capsys.readouterr().out

    with pytest.raises(SystemExit, match="not in the active stage"):
        main([str(path), "--guess", "999"])
