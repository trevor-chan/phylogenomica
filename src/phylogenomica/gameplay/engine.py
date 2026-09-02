"""UI-independent guess, reveal, score, and stage-transition rules.

The engine operates on an already validated :class:`GeneratedGame` and never
queries an upstream database. It returns a transition describing every
placement, the remaining relatives, and the score change, so a frontend renders
that transition rather than recomputing correctness.

One generated game supports several difficulties. A difficulty decides which of
a stage's generated cards are dealt, which of those the player may choose, and
which one ends the stage; it never changes the game's topology, so the same
seed produces the same lineage in every mode. See :data:`Difficulty`.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

from phylogenomica.generation.game import (
    GameGenerationError,
    GameMember,
    GameRole,
    GeneratedGame,
    GeneratedStage,
    load_game,
)

# Bumped when player state gains a field or a rule changes what a recorded
# state means: version 2 added the difficulty a state is being played under,
# and version 3 replaced deducted stage stakes with accrued stage points.
GAMEPLAY_ENGINE_VERSION = 3

Placement = Literal["guessed", "revealed"]

#: How much the game tells the player before the first guess.
#:
#: ``expert`` conceals the target until the ultimate stage, where choosing it
#: ends the game, and deals a mulligan in every stage.
#:
#: ``guided`` reveals the target from the opening stage. Naming the closest
#: relative is then the whole task, so the mulligan — which exists only to make
#: the second-deepest relative tempting — is not dealt, and the revealed target
#: is shown in the ultimate stage without being selectable. The closest
#: relative ends every stage, including the last.
Difficulty = Literal["expert", "guided"]
DIFFICULTIES: tuple[Difficulty, ...] = ("expert", "guided")
DEFAULT_DIFFICULTY: Difficulty = "expert"


class GameplayError(RuntimeError):
    """Raised when a guess or a restored state is invalid for the game in play."""

def parse_difficulty(value: object) -> Difficulty:
    """Return a known difficulty, or raise for anything else."""
    if value not in DIFFICULTIES:
        raise GameplayError(
            f"unknown difficulty {value!r}; "
            f"expected one of {', '.join(DIFFICULTIES)}"
        )
    return value  # type: ignore[return-value]


def target_is_revealed(difficulty: Difficulty) -> bool:
    """Return whether the target is shown before the ultimate stage."""
    return parse_difficulty(difficulty) == "guided"


@dataclass(frozen=True)
class PlacedSpecies:
    """One species fixed into the persistent cladogram."""

    species_id: int
    stage_index: int
    tier_index: int | None
    ancestor_node_id: int | None
    role: GameRole
    placement: Placement


@dataclass(frozen=True)
class GameState:
    """Serializable player state for one game in progress."""

    game_id: str
    engine_version: int
    difficulty: Difficulty
    current_stage_index: int
    active_species_ids: tuple[int, ...]
    placements: tuple[PlacedSpecies, ...]
    stage_scores: tuple[int, ...]
    stage_points: int
    stage_misses: int
    completed: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class GuessOutcome:
    """The complete, renderable consequence of one guess."""

    stage_index: int
    species_id: int
    role: GameRole
    placed: tuple[PlacedSpecies, ...]
    remaining_species_ids: tuple[int, ...]
    earned: int
    missed: bool
    score: int
    score_change: int
    stage_completed: bool
    stage_score: int | None
    perfect_stage: bool
    game_completed: bool


def _target_depth() -> float:
    return math.inf


def _depth(member: GameMember) -> float:
    """Order members by represented depth; the target is the deepest endpoint."""
    return _target_depth() if member.tier_index is None else float(member.tier_index)


def _members_by_id(stage: GeneratedStage) -> dict[int, GameMember]:
    return {member.species_id: member for member in stage.members}


def stage_ending_ids(
    stage: GeneratedStage, difficulty: Difficulty = DEFAULT_DIFFICULTY
) -> frozenset[int]:
    """Return the cards that complete a stage when chosen.

    In expert play a transition stage ends on its unlock and the ultimate stage
    ends on the target the player has just been shown. Guided play never offers
    the target as a choice, so the closest relative ends every stage: the
    unlock wherever one exists, and the ultimate stage's deepest relative —
    generated as its mulligan — where none does.
    """
    if parse_difficulty(difficulty) == "guided":
        ending = stage.unlock_species_ids or stage.mulligan_species_ids
        if not ending:
            raise GameplayError("stage has no closest relative to end it")
        return frozenset(ending)
    if stage.target_species_id is not None:
        return frozenset({stage.target_species_id})
    return frozenset(stage.unlock_species_ids)


def dealt_members(
    stage: GeneratedStage, difficulty: Difficulty = DEFAULT_DIFFICULTY
) -> tuple[GameMember, ...]:
    """Return the cards a difficulty puts on the table for one stage."""
    if parse_difficulty(difficulty) != "guided":
        return stage.members
    # The mulligan exists to make the second-deepest relative tempting. A
    # revealed target leaves no such ambiguity, so it is dealt only in the
    # ultimate stage, where it is itself the closest relative.
    ending = stage_ending_ids(stage, difficulty)
    return tuple(
        member
        for member in stage.members
        if member.role != "mulligan" or member.species_id in ending
    )


def selectable_members(
    stage: GeneratedStage, difficulty: Difficulty = DEFAULT_DIFFICULTY
) -> tuple[GameMember, ...]:
    """Return the dealt cards the player may choose from.

    Guided play shows the revealed target among the ultimate stage's cards so
    the player can see what they are placing relatives against, but choosing it
    is not the task and is not allowed.
    """
    return tuple(
        member
        for member in dealt_members(stage, difficulty)
        if not (parse_difficulty(difficulty) == "guided" and member.role == "target")
    )


def scoring_members(
    stage: GeneratedStage, difficulty: Difficulty = DEFAULT_DIFFICULTY
) -> tuple[GameMember, ...]:
    """Return the cards a stage's score is counted over.

    The mulligan sits outside the scoring system: choosing it costs nothing and
    resolving it earns nothing. Until a stage ends there is no distinguishing
    the deepest relative from the second-deepest, so naming either one is the
    same achievement and neither the guess nor the reveal should move the
    score. Every stage therefore scores over its decoys plus the card that ends
    it, which is the same nine cards in both difficulties.
    """
    ending = stage_ending_ids(stage, difficulty)
    return tuple(
        member
        for member in selectable_members(stage, difficulty)
        if member.role != "mulligan" or member.species_id in ending
    )


def revealed_target(
    game: GeneratedGame, difficulty: Difficulty = DEFAULT_DIFFICULTY
) -> GameMember | None:
    """Return the target card a difficulty shows from the opening stage."""
    if not target_is_revealed(difficulty):
        return None
    for member in game.stages[-1].members:
        if member.role == "target":
            return member
    return None


def stage_maximum(
    game: GeneratedGame, difficulty: Difficulty = DEFAULT_DIFFICULTY
) -> int:
    """Return the points one stage is worth.

    A stage is worth one point per card it scores over. Ending it on the first
    guess earns every one of them: one for each card the guess resolves
    without the player spending a guess on it, and one more for a stage with
    no wrong guess in it. Both difficulties score over the same cards — the
    decoys plus the one that ends the stage — so a stage is worth the same in
    either mode.
    """
    counted = {len(scoring_members(stage, difficulty)) for stage in game.stages}
    if len(counted) != 1:
        raise GameplayError("stages do not score over a uniform number of cards")
    return counted.pop()


def maximum_score(
    game: GeneratedGame, difficulty: Difficulty = DEFAULT_DIFFICULTY
) -> int:
    """Return the score a player achieves by ending every stage immediately."""
    return stage_maximum(game, difficulty) * game.configuration.stages_per_game


def score(game: GeneratedGame, state: GameState) -> int:
    """Return the running score, the stage in progress included.

    The score only ever rises. A guess earns a point for every card it resolves
    that the player did not have to choose, so a wrong guess still pays for
    what it revealed — it simply earns less than the guess that would have
    ended the stage, and forfeits the clean-stage point. Nothing already earned
    is ever taken back.
    """
    return sum(state.stage_scores) + state.stage_points


def initial_state(
    game: GeneratedGame, difficulty: Difficulty = DEFAULT_DIFFICULTY
) -> GameState:
    """Open a game with every selectable card of its first stage active."""
    difficulty = parse_difficulty(difficulty)
    return GameState(
        game_id=game.game_id,
        engine_version=GAMEPLAY_ENGINE_VERSION,
        difficulty=difficulty,
        current_stage_index=0,
        active_species_ids=tuple(
            member.species_id
            for member in selectable_members(game.stages[0], difficulty)
        ),
        placements=(),
        stage_scores=(),
        stage_points=0,
        stage_misses=0,
        completed=False,
    )


def _placed(
    members: Sequence[GameMember], *, stage_index: int, guessed_id: int
) -> tuple[PlacedSpecies, ...]:
    return tuple(
        PlacedSpecies(
            species_id=member.species_id,
            stage_index=stage_index,
            tier_index=member.tier_index,
            ancestor_node_id=member.ancestor_node_id,
            role=member.role,
            placement="guessed" if member.species_id == guessed_id else "revealed",
        )
        for member in members
    )


def apply_guess(
    game: GeneratedGame, state: GameState, species_id: int
) -> tuple[GameState, GuessOutcome]:
    """Apply one guess, returning the new state and its renderable transition.

    A guess places the chosen card and every still-active relative on a
    strictly shallower tier, because choosing a card asserts that everything
    more distant than it is already ordered. Same-tier peers and every deeper
    relative stay active: the guess established no order among them.
    """
    if state.completed:
        raise GameplayError("game is already complete")
    if state.game_id != game.game_id:
        raise GameplayError("state belongs to a different game")

    stage = game.stages[state.current_stage_index]
    dealt = dealt_members(stage, state.difficulty)
    member_by_id = {member.species_id: member for member in dealt}
    selectable_ids = {
        member.species_id
        for member in selectable_members(stage, state.difficulty)
    }
    if species_id not in state.active_species_ids:
        if species_id in member_by_id and species_id not in selectable_ids:
            raise GameplayError(
                f"species {species_id} is the revealed target and cannot be chosen"
            )
        if species_id in member_by_id:
            raise GameplayError(f"species {species_id} is already placed")
        raise GameplayError(f"species {species_id} is not in the active stage")

    member = member_by_id[species_id]
    still_active = [
        member_by_id[active_id]
        for active_id in state.active_species_ids
        if active_id != species_id
    ]
    exposed = sorted(
        (other for other in still_active if _depth(other) < _depth(member)),
        key=lambda other: (_depth(other), other.species_id),
    )

    ends_stage = species_id in stage_ending_ids(stage, state.difficulty)
    if ends_stage:
        # The stage resolves in full. Every dealt card lands, including one the
        # player was never allowed to choose: guided play's revealed target is
        # the deepest of them and closes the cladogram as the endpoint it
        # always was.
        stage_placed = {
            placed.species_id
            for placed in state.placements
            if placed.stage_index == stage.stage_index
        }
        unplaced = [
            other
            for other in dealt
            if other.species_id != species_id
            and other.species_id not in stage_placed
        ]
        newly_placed = [
            member,
            *sorted(unplaced, key=lambda other: (_depth(other), other.species_id)),
        ]
    else:
        newly_placed = [member, *exposed]

    # A guess earns a point for every scored card it resolves that the player
    # did not have to choose, and spends one for the scored card it chose. A
    # stage therefore costs exactly the number of wrong guesses in it, however
    # near or far each one was. The mulligan is not scored either way, so
    # choosing it neither costs a point nor breaks a clean stage, and the stage
    # that resolves it earns nothing for it.
    scoring_ids = {
        member.species_id
        for member in scoring_members(stage, state.difficulty)
    }
    earned = sum(
        1 for placed in newly_placed if placed.species_id in scoring_ids
    ) - (1 if species_id in scoring_ids else 0)
    missed = not ends_stage and species_id in scoring_ids

    placements = state.placements + _placed(
        newly_placed, stage_index=stage.stage_index, guessed_id=species_id
    )
    placed_ids = {placed.species_id for placed in newly_placed}
    remaining = tuple(
        active_id
        for active_id in state.active_species_ids
        if active_id not in placed_ids
    )

    stage_points = state.stage_points + earned
    stage_misses = state.stage_misses + (1 if missed else 0)
    perfect_stage = ends_stage and stage_misses == 0
    if ends_stage:
        # One more point for a stage with no wrong guess in it, so ending a
        # stage cleanly is worth every card it offered.
        stage_score = stage_points + (1 if perfect_stage else 0)
        next_index = stage.stage_index + 1
        completed = next_index == len(game.stages)
        new_state = GameState(
            game_id=state.game_id,
            engine_version=state.engine_version,
            difficulty=state.difficulty,
            current_stage_index=next_index,
            active_species_ids=(
                ()
                if completed
                else tuple(
                    member.species_id
                    for member in selectable_members(
                        game.stages[next_index], state.difficulty
                    )
                )
            ),
            placements=placements,
            stage_scores=(*state.stage_scores, stage_score),
            stage_points=0,
            stage_misses=0,
            completed=completed,
        )
    else:
        stage_score = None
        new_state = replace(
            state,
            active_species_ids=remaining,
            placements=placements,
            stage_points=stage_points,
            stage_misses=stage_misses,
        )

    outcome = GuessOutcome(
        stage_index=stage.stage_index,
        species_id=species_id,
        role=member.role,
        placed=placements[len(state.placements) :],
        remaining_species_ids=remaining,
        earned=earned,
        missed=missed,
        score=score(game, new_state),
        score_change=score(game, new_state) - score(game, state),
        stage_completed=ends_stage,
        stage_score=stage_score,
        perfect_stage=perfect_stage,
        game_completed=new_state.completed,
    )
    return new_state, outcome


def replay(
    game: GeneratedGame,
    species_ids: Sequence[int],
    difficulty: Difficulty = DEFAULT_DIFFICULTY,
) -> tuple[GameState, tuple[GuessOutcome, ...]]:
    """Apply an ordered sequence of guesses to a fresh game."""
    state = initial_state(game, difficulty)
    outcomes: list[GuessOutcome] = []
    for species_id in species_ids:
        state, outcome = apply_guess(game, state, species_id)
        outcomes.append(outcome)
    return state, tuple(outcomes)


def perfect_guesses(
    game: GeneratedGame, difficulty: Difficulty = DEFAULT_DIFFICULTY
) -> tuple[int, ...]:
    """Return the stage-ending card of every stage, in order."""
    return tuple(
        sorted(stage_ending_ids(stage, difficulty))[0] for stage in game.stages
    )


def restore_state(
    game: GeneratedGame, payload: Mapping[str, object]
) -> GameState:
    """Rebuild player state and confirm it belongs to this game."""
    # Check the version before the fields, so a state written by an older
    # engine reports the mismatch rather than a missing-field error for
    # whichever field that engine happened to lack.
    engine_version = payload.get("engine_version")
    if engine_version != GAMEPLAY_ENGINE_VERSION:
        raise GameplayError(
            f"state has an unsupported engine version: {engine_version!r}; "
            f"this build reads version {GAMEPLAY_ENGINE_VERSION}"
        )
    try:
        state = GameState(
            game_id=str(payload["game_id"]),
            engine_version=int(payload["engine_version"]),  # type: ignore[arg-type]
            difficulty=parse_difficulty(payload["difficulty"]),
            current_stage_index=int(payload["current_stage_index"]),  # type: ignore[arg-type]
            active_species_ids=tuple(
                int(value) for value in payload["active_species_ids"]  # type: ignore[union-attr]
            ),
            placements=tuple(
                PlacedSpecies(
                    species_id=int(placed["species_id"]),
                    stage_index=int(placed["stage_index"]),
                    tier_index=(
                        None
                        if placed["tier_index"] is None
                        else int(placed["tier_index"])
                    ),
                    ancestor_node_id=(
                        None
                        if placed["ancestor_node_id"] is None
                        else int(placed["ancestor_node_id"])
                    ),
                    role=placed["role"],
                    placement=placed["placement"],
                )
                for placed in payload["placements"]  # type: ignore[union-attr]
            ),
            stage_scores=tuple(
                int(value) for value in payload["stage_scores"]  # type: ignore[union-attr]
            ),
            stage_points=int(payload["stage_points"]),  # type: ignore[arg-type]
            stage_misses=int(payload["stage_misses"]),  # type: ignore[arg-type]
            completed=bool(payload["completed"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GameplayError(f"invalid serialized game state: {error}") from error
    validate_state(game, state)
    return state


def validate_state(game: GeneratedGame, state: GameState) -> None:
    """Validate that a state is a reachable position in this game."""
    if state.game_id != game.game_id:
        raise GameplayError("state belongs to a different game")
    if state.engine_version != GAMEPLAY_ENGINE_VERSION:
        raise GameplayError("state has an unsupported engine version")
    parse_difficulty(state.difficulty)
    if state.completed != (state.current_stage_index == len(game.stages)):
        raise GameplayError("completion flag disagrees with the current stage")
    if len(state.stage_scores) != state.current_stage_index:
        raise GameplayError("completed stage scores disagree with the current stage")
    if state.stage_points < 0 or state.stage_misses < 0:
        raise GameplayError("stage points and misses must not be negative")

    placed_ids = [placed.species_id for placed in state.placements]
    if len(placed_ids) != len(set(placed_ids)):
        raise GameplayError("a species is placed more than once")
    if state.completed:
        if state.active_species_ids:
            raise GameplayError("a completed game has no active species")
    else:
        stage = game.stages[state.current_stage_index]
        # An open stage accounts for the cards this difficulty lets the player
        # choose. A dealt but unselectable card is neither active nor placed
        # until the stage ends, so it is not part of that accounting.
        selectable_ids = {
            member.species_id
            for member in selectable_members(stage, state.difficulty)
        }
        active = set(state.active_species_ids)
        if not active <= selectable_ids:
            raise GameplayError("active species are not cards of the current stage")
        stage_placed = {
            placed.species_id
            for placed in state.placements
            if placed.stage_index == state.current_stage_index
        }
        if active | stage_placed != selectable_ids:
            raise GameplayError("active and placed species do not cover the stage")
        if not active:
            raise GameplayError("an open stage has at least one active species")

    # Placements preserve guess order: every transition appends its guessed
    # species first, followed by anything that guess revealed. Replaying those
    # guesses reconstructs the only state they can reach and verifies topology,
    # placement provenance, scores, active cards, and completion together.
    guesses = tuple(
        placed.species_id
        for placed in state.placements
        if placed.placement == "guessed"
    )
    try:
        reachable, _ = replay(game, guesses, state.difficulty)
    except GameplayError as error:
        raise GameplayError(f"game state is not reachable: {error}") from error
    if state != reachable:
        raise GameplayError("game state is not reachable from its recorded guesses")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Play a generated phylogenomica game without a user interface."
    )
    parser.add_argument("game", type=Path)
    parser.add_argument(
        "--difficulty",
        choices=DIFFICULTIES,
        default=DEFAULT_DIFFICULTY,
        help=(
            "expert conceals the target and deals a mulligan; "
            f"guided reveals it and does not (default: {DEFAULT_DIFFICULTY})"
        ),
    )
    parser.add_argument(
        "--guess",
        type=int,
        action="append",
        dest="guesses",
        help="species ID to guess; repeat to script a run (default: perfect play)",
    )
    parser.add_argument("--state", type=Path, help="write final player state here")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        game = load_game(args.game)
        guesses = args.guesses or list(perfect_guesses(game, args.difficulty))
        state, outcomes = replay(game, guesses, args.difficulty)
    except (GameGenerationError, GameplayError) as error:
        raise SystemExit(str(error)) from error

    names = {
        member.species_id: member.card.english_name
        for stage in game.stages
        for member in stage.members
    }
    current_stage = -1
    for outcome in outcomes:
        if outcome.stage_index != current_stage:
            current_stage = outcome.stage_index
            print(f"\nstage {current_stage}")
        change = f"+{outcome.earned}" if outcome.earned else "0"
        if outcome.missed:
            change += " (wrong)"
        print(
            f"  {outcome.role:<8} {names[outcome.species_id][:32]:<32} "
            f"{change:>14}  score {outcome.score:>3}  "
            f"{len(outcome.remaining_species_ids)} left"
        )
        if outcome.stage_completed:
            perfect = " (clean stage, +1)" if outcome.perfect_stage else ""
            print(f"  -> stage score {outcome.stage_score}{perfect}")

    print(
        f"\nscore {score(game, state)} / {maximum_score(game, args.difficulty)}"
        f"  (stages {list(state.stage_scores)})"
    )
    print("completed" if state.completed else "in progress")
    if args.state:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(
            json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {args.state}")


if __name__ == "__main__":
    main()
