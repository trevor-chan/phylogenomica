"""Seeded selection of playable relatives from ordered target-lineage tiers."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path
from typing import Literal

from phylogenomica.data.onezoom_ingest import DATABASE_FILENAME
from phylogenomica.generation.eligibility import (
    ELIGIBILITY_DATABASE_FILENAME,
    ELIGIBILITY_INDEX_VERSION,
    TargetEligibilityError,
    TargetEligibilityIndex,
)
from phylogenomica.generation.feasibility import (
    FeasibilityConfig,
    feasibility_configuration,
)
from phylogenomica.tree.preprocess import (
    DEFAULT_NORMALIZED_DIR,
    TREE_DATABASE_FILENAME,
    TREE_SCHEMA_VERSION,
)
from phylogenomica.tree.query import BiologicalTree, RelativeTier, TreeQueryError

RELATIVE_SELECTOR_VERSION = 1
POPULARITY_WEIGHT_BUCKETS = 10

RelativeRole = Literal["decoy", "mulligan", "unlock"]


class RelativeSelectionError(RuntimeError):
    """Raised when a target cannot produce a valid relative selection."""


@dataclass(frozen=True)
class SelectedRelative:
    species_id: int
    tier_index: int
    ancestor_node_id: int
    role: RelativeRole


@dataclass(frozen=True)
class StageRelativeSelection:
    stage_index: int
    relatives: tuple[SelectedRelative, ...]


@dataclass(frozen=True)
class RelativeSelection:
    dataset_version: str
    selector_version: int
    eligibility_index_version: int
    target_id: int
    seed: int
    configuration: FeasibilityConfig
    stages: tuple[StageRelativeSelection, ...]

    @property
    def relative_species_ids(self) -> tuple[int, ...]:
        return tuple(
            relative.species_id
            for stage in self.stages
            for relative in stage.relatives
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset_version": self.dataset_version,
            "selector_version": self.selector_version,
            "eligibility_index_version": self.eligibility_index_version,
            "target_id": self.target_id,
            "seed": self.seed,
            "configuration": feasibility_configuration(self.configuration),
            "stages": [asdict(stage) for stage in self.stages],
        }


@dataclass(frozen=True)
class _AllocationState:
    stage_index: int
    phase: int
    remaining: int


@dataclass(frozen=True)
class _TierUse:
    stage_index: int
    role: RelativeRole
    tier: RelativeTier
    selected_count: int


def _decoy_count(config: FeasibilityConfig, stage_index: int) -> int:
    return (
        config.decoys_per_transition_stage
        if stage_index < config.stages_per_game - 1
        else config.decoys_in_ultimate_stage
    )


def _initial_allocation_state(config: FeasibilityConfig) -> _AllocationState:
    return _AllocationState(0, 0, _decoy_count(config, 0))


def _role_for_phase(phase: int) -> RelativeRole:
    return ("decoy", "mulligan", "unlock")[phase]


def _advance_allocation_state(
    state: _AllocationState,
    selected_count: int,
    config: FeasibilityConfig,
) -> _AllocationState:
    if selected_count <= 0 or selected_count > state.remaining:
        raise AssertionError("invalid allocation transition")
    remaining = state.remaining - selected_count
    if remaining:
        return _AllocationState(state.stage_index, state.phase, remaining)

    if state.phase == 0:
        return _AllocationState(
            state.stage_index, 1, config.mulligan_species_per_stage
        )
    if state.phase == 1 and state.stage_index < config.stages_per_game - 1:
        return _AllocationState(
            state.stage_index, 2, config.unlock_species_per_transition_stage
        )

    next_stage = state.stage_index + 1
    if next_stage == config.stages_per_game:
        return _AllocationState(next_stage, 0, 0)
    return _AllocationState(next_stage, 0, _decoy_count(config, next_stage))


def _polytomy_penalty(role: RelativeRole, selected_count: int) -> int:
    if role == "decoy":
        return max(0, selected_count - 2)
    return 0


def _allocate_tiers(
    tiers: Sequence[RelativeTier],
    *,
    config: FeasibilityConfig,
    rng: random.Random,
) -> tuple[_TierUse, ...]:
    """Sample one valid ordered role allocation using completion weights."""
    tier_tuple = tuple(tiers)

    @cache
    def completion_score(
        tier_offset: int, state: _AllocationState
    ) -> tuple[int, int]:
        if state.stage_index == config.stages_per_game:
            return 0, 1
        if tier_offset == len(tier_tuple):
            return 10**9, 0
        options: list[tuple[int, int]] = [
            completion_score(tier_offset + 1, state)
        ]
        tier = tier_tuple[tier_offset]
        role = _role_for_phase(state.phase)
        for selected_count in range(
            1, min(len(tier.candidate_leaf_ids), state.remaining) + 1
        ):
            next_state = _advance_allocation_state(
                state, selected_count, config
            )
            future_penalty, future_count = completion_score(
                tier_offset + 1, next_state
            )
            options.append(
                (
                    _polytomy_penalty(role, selected_count) + future_penalty,
                    future_count,
                )
            )
        valid_options = [
            (penalty, count) for penalty, count in options if count > 0
        ]
        if not valid_options:
            return 10**9, 0
        minimum_penalty = min(penalty for penalty, _ in valid_options)
        return minimum_penalty, sum(
            count
            for penalty, count in valid_options
            if penalty == minimum_penalty
        )

    state = _initial_allocation_state(config)
    if completion_score(0, state)[1] == 0:
        raise RelativeSelectionError(
            "target tiers cannot fill the configured ordered stage roles"
        )

    uses: list[_TierUse] = []
    tier_offset = 0
    while state.stage_index < config.stages_per_game:
        tier = tier_tuple[tier_offset]
        options: list[tuple[int, _AllocationState, int]] = []
        current_penalty = completion_score(tier_offset, state)[0]
        skipped_penalty, skipped_count = completion_score(
            tier_offset + 1, state
        )
        if skipped_count and skipped_penalty == current_penalty:
            options.append((0, state, skipped_count))
        role = _role_for_phase(state.phase)
        for selected_count in range(
            1, min(len(tier.candidate_leaf_ids), state.remaining) + 1
        ):
            next_state = _advance_allocation_state(
                state, selected_count, config
            )
            future_penalty, future_count = completion_score(
                tier_offset + 1, next_state
            )
            penalty = _polytomy_penalty(role, selected_count) + future_penalty
            if future_count and penalty == current_penalty:
                options.append((selected_count, next_state, future_count))
        total_weight = sum(option[2] for option in options)
        choice = rng.randrange(total_weight)
        selected_count = 0
        next_state = state
        for option_count, option_state, option_weight in options:
            if choice < option_weight:
                selected_count = option_count
                next_state = option_state
                break
            choice -= option_weight
        if selected_count:
            uses.append(
                _TierUse(
                    stage_index=state.stage_index,
                    role=role,
                    tier=tier,
                    selected_count=selected_count,
                )
            )
        state = next_state
        tier_offset += 1
    return tuple(uses)


def _seeded_random(
    *,
    dataset_version: str,
    target_id: int,
    seed: int,
    configuration: Mapping[str, int | bool],
) -> random.Random:
    seed_record = {
        "dataset_version": dataset_version,
        "selector_version": RELATIVE_SELECTOR_VERSION,
        "target_id": target_id,
        "seed": seed,
        "configuration": dict(configuration),
    }
    material = json.dumps(
        seed_record, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(material).digest()
    return random.Random(int.from_bytes(digest, byteorder="big"))


def _load_normalized_metadata(
    database: Path,
    candidate_ids: Sequence[int],
) -> tuple[str, dict[int, int | None]]:
    if not database.is_file():
        raise RelativeSelectionError(f"normalized database does not exist: {database}")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()
        if version != (1,):
            raise RelativeSelectionError(
                f"unsupported normalized database schema: {version!r}"
            )
        metadata = {
            str(key): str(value)
            for key, value in connection.execute(
                "SELECT key, value FROM dataset_metadata"
            )
        }
        try:
            dataset_version = metadata["dataset_version"]
        except KeyError as error:
            raise RelativeSelectionError(
                "normalized database has no dataset version"
            ) from error

        ranks: dict[int, int | None] = {}
        ordered_ids = sorted(candidate_ids)
        query_batch_size = 900
        for offset in range(0, len(ordered_ids), query_batch_size):
            batch = ordered_ids[offset : offset + query_batch_size]
            placeholders = ",".join("?" for _ in batch)
            ranks.update(
                {
                    int(leaf_id): None if rank is None else int(rank)
                    for leaf_id, rank in connection.execute(
                        "SELECT leaf_id, popularity_rank FROM leaves "
                        f"WHERE leaf_id IN ({placeholders})",
                        batch,
                    )
                }
            )
    except sqlite3.Error as error:
        raise RelativeSelectionError(
            f"cannot read normalized selection metadata: {error}"
        ) from error
    finally:
        connection.close()
    missing = sorted(set(candidate_ids) - ranks.keys())
    if missing:
        raise RelativeSelectionError(
            f"normalized database is missing candidate leaves: {missing[:5]!r}"
        )
    return dataset_version, ranks


def _weighted_species_sample(
    candidate_ids: Sequence[int],
    count: int,
    *,
    popularity_ranks: Mapping[int, int | None],
    rng: random.Random,
) -> tuple[int, ...]:
    if count > len(candidate_ids):
        raise RelativeSelectionError("tier has fewer candidates than requested")
    ranked = sorted(
        (
            candidate_id
            for candidate_id in candidate_ids
            if popularity_ranks[candidate_id] is not None
        ),
        key=lambda candidate_id: (popularity_ranks[candidate_id], candidate_id),
    )
    weights: dict[int, int] = {
        candidate_id: POPULARITY_WEIGHT_BUCKETS
        - min(
            POPULARITY_WEIGHT_BUCKETS - 1,
            position * POPULARITY_WEIGHT_BUCKETS // max(1, len(ranked)),
        )
        for position, candidate_id in enumerate(ranked)
    }
    pool = [
        (candidate_id, weights.get(candidate_id, 1))
        for candidate_id in sorted(candidate_ids)
    ]
    selected: list[int] = []
    for _ in range(count):
        total_weight = sum(weight for _, weight in pool)
        choice = rng.randrange(total_weight)
        for index, (candidate_id, weight) in enumerate(pool):
            if choice < weight:
                selected.append(candidate_id)
                pool.pop(index)
                break
            choice -= weight
    return tuple(sorted(selected))


def validate_relative_selection(
    selection: RelativeSelection,
    *,
    config: FeasibilityConfig,
    candidate_topology: Mapping[int, tuple[int, int]] | None = None,
) -> None:
    """Validate counts, topology ordering, roles, target hiding, and uniqueness."""
    if selection.configuration != config:
        raise RelativeSelectionError("selection configuration does not match policy")
    if len(selection.stages) != config.stages_per_game:
        raise RelativeSelectionError("selection has the wrong number of stages")

    seen: set[int] = set()
    previous_tier = -1
    for expected_stage, stage in enumerate(selection.stages):
        if stage.stage_index != expected_stage:
            raise RelativeSelectionError("selection stage indexes are not contiguous")
        expected_relatives = (
            config.members_per_stage
            if expected_stage < config.stages_per_game - 1
            else config.members_per_stage - 1
        )
        if len(stage.relatives) != expected_relatives:
            raise RelativeSelectionError("stage has the wrong relative count")
        roles = Counter(relative.role for relative in stage.relatives)
        expected_roles = {
            "decoy": _decoy_count(config, expected_stage),
            "mulligan": config.mulligan_species_per_stage,
            "unlock": (
                config.unlock_species_per_transition_stage
                if expected_stage < config.stages_per_game - 1
                else 0
            ),
        }
        if roles != Counter(expected_roles):
            raise RelativeSelectionError("stage has the wrong role counts")

        tier_roles: dict[int, RelativeRole] = {}
        role_tiers: dict[RelativeRole, list[int]] = {
            "decoy": [],
            "mulligan": [],
            "unlock": [],
        }
        for relative in stage.relatives:
            if relative.species_id == selection.target_id:
                raise RelativeSelectionError("target appears among selected relatives")
            if relative.species_id in seen:
                raise RelativeSelectionError(
                    "relative species is selected more than once"
                )
            seen.add(relative.species_id)
            if candidate_topology is not None and candidate_topology.get(
                relative.species_id
            ) != (relative.tier_index, relative.ancestor_node_id):
                raise RelativeSelectionError("relative has an incorrect topology tier")
            existing_role = tier_roles.setdefault(relative.tier_index, relative.role)
            if existing_role != relative.role:
                raise RelativeSelectionError("one tier contains multiple stage roles")
            role_tiers[relative.role].append(relative.tier_index)

        stage_tiers = [relative.tier_index for relative in stage.relatives]
        if min(stage_tiers) <= previous_tier:
            raise RelativeSelectionError("successive stages are not strictly ordered")
        previous_tier = max(stage_tiers)
        if max(role_tiers["decoy"]) >= min(role_tiers["mulligan"]):
            raise RelativeSelectionError("mulligan is not deeper than every decoy")
        if role_tiers["unlock"] and max(role_tiers["mulligan"]) >= min(
            role_tiers["unlock"]
        ):
            raise RelativeSelectionError("unlock is not deeper than every mulligan")

    if len(seen) != config.relative_species:
        raise RelativeSelectionError("selection has the wrong total relative count")


def select_relatives(
    *,
    target_id: int,
    seed: int,
    normalized_database: Path = DEFAULT_NORMALIZED_DIR / DATABASE_FILENAME,
    tree_database: Path = DEFAULT_NORMALIZED_DIR
    / f"tree-v{TREE_SCHEMA_VERSION}"
    / TREE_DATABASE_FILENAME,
    eligibility_database: Path = DEFAULT_NORMALIZED_DIR
    / f"target-eligibility-v{ELIGIBILITY_INDEX_VERSION}"
    / ELIGIBILITY_DATABASE_FILENAME,
    config: FeasibilityConfig = FeasibilityConfig(require_rich_card_metadata=True),
) -> RelativeSelection:
    """Select one deterministic, seeded set of relatives for an eligible target."""
    try:
        with TargetEligibilityIndex(eligibility_database) as eligibility:
            if eligibility.feasibility_configuration != feasibility_configuration(
                config
            ):
                raise RelativeSelectionError(
                    "selection configuration does not match eligibility index"
                )
            target = eligibility.get(target_id)
            if target is None:
                raise RelativeSelectionError(
                    f"target {target_id} is outside the indexed target universe"
                )
            if not target.eligible:
                reasons = ", ".join(target.reason_codes)
                raise RelativeSelectionError(
                    f"target {target_id} is ineligible: {reasons}"
                )
            candidate_ids = tuple(
                candidate_id
                for candidate_id in eligibility.iter_indexed_target_ids()
                if candidate_id != target_id
            )
            dataset_version = eligibility.dataset_version

        normalized_dataset, popularity_ranks = _load_normalized_metadata(
            normalized_database, candidate_ids
        )
        if normalized_dataset != dataset_version:
            raise RelativeSelectionError(
                "normalized and eligibility dataset versions differ"
            )
        with BiologicalTree.open(tree_database) as tree:
            tiers = tree.relative_tiers(target_id, candidate_ids)
    except (TargetEligibilityError, TreeQueryError) as error:
        raise RelativeSelectionError(str(error)) from error

    configuration = feasibility_configuration(config)
    rng = _seeded_random(
        dataset_version=dataset_version,
        target_id=target_id,
        seed=seed,
        configuration=configuration,
    )
    tier_uses = _allocate_tiers(tiers, config=config, rng=rng)
    stages: list[list[SelectedRelative]] = [
        [] for _ in range(config.stages_per_game)
    ]
    for use in tier_uses:
        species_ids = _weighted_species_sample(
            use.tier.candidate_leaf_ids,
            use.selected_count,
            popularity_ranks=popularity_ranks,
            rng=rng,
        )
        stages[use.stage_index].extend(
            SelectedRelative(
                species_id=species_id,
                tier_index=use.tier.tier_index,
                ancestor_node_id=use.tier.ancestor_node_id,
                role=use.role,
            )
            for species_id in species_ids
        )
    selection = RelativeSelection(
        dataset_version=dataset_version,
        selector_version=RELATIVE_SELECTOR_VERSION,
        eligibility_index_version=ELIGIBILITY_INDEX_VERSION,
        target_id=target_id,
        seed=seed,
        configuration=config,
        stages=tuple(
            StageRelativeSelection(
                stage_index=stage_index,
                relatives=tuple(
                    sorted(
                        relatives,
                        key=lambda relative: (
                            relative.tier_index,
                            relative.role,
                            relative.species_id,
                        ),
                    )
                ),
            )
            for stage_index, relatives in enumerate(stages)
        ),
    )
    candidate_topology = {
        species_id: (tier.tier_index, tier.ancestor_node_id)
        for tier in tiers
        for species_id in tier.candidate_leaf_ids
    }
    validate_relative_selection(
        selection, config=config, candidate_topology=candidate_topology
    )
    return selection


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select a deterministic seeded relative lineage for one target."
    )
    parser.add_argument("target_id", type=int)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument(
        "--normalized-dir", type=Path, default=DEFAULT_NORMALIZED_DIR
    )
    parser.add_argument("--tree", type=Path)
    parser.add_argument("--eligibility", type=Path)
    parser.add_argument("--members-per-stage", type=int, default=10)
    parser.add_argument("--stages-per-game", type=int, default=5)
    parser.add_argument("--unlock-species", type=int, default=1)
    parser.add_argument("--mulligan-species", type=int, default=1)
    parser.add_argument(
        "--require-rich-cards",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    normalized_dir = args.normalized_dir
    try:
        config = FeasibilityConfig(
            members_per_stage=args.members_per_stage,
            stages_per_game=args.stages_per_game,
            unlock_species_per_transition_stage=args.unlock_species,
            mulligan_species_per_stage=args.mulligan_species,
            require_rich_card_metadata=args.require_rich_cards,
        )
        selection = select_relatives(
            target_id=args.target_id,
            seed=args.seed,
            normalized_database=normalized_dir / DATABASE_FILENAME,
            tree_database=args.tree
            or normalized_dir
            / f"tree-v{TREE_SCHEMA_VERSION}"
            / TREE_DATABASE_FILENAME,
            eligibility_database=args.eligibility
            or normalized_dir
            / f"target-eligibility-v{ELIGIBILITY_INDEX_VERSION}"
            / ELIGIBILITY_DATABASE_FILENAME,
            config=config,
        )
    except (RelativeSelectionError, ValueError) as error:
        raise SystemExit(str(error)) from error
    rendered = json.dumps(selection.to_dict(), indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
