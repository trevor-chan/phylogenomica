"""Batch topology and metadata feasibility audits for game targets."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from phylogenomica.data.onezoom_ingest import DATABASE_FILENAME
from phylogenomica.tree.preprocess import (
    DEFAULT_NORMALIZED_DIR,
    TREE_DATABASE_FILENAME,
    TREE_SCHEMA_VERSION,
)

FEASIBILITY_AUDIT_VERSION = 2
DEFAULT_TREE_DATABASE = (
    DEFAULT_NORMALIZED_DIR / f"tree-v{TREE_SCHEMA_VERSION}" / TREE_DATABASE_FILENAME
)


class FeasibilityAuditError(RuntimeError):
    """Raised when inputs cannot produce a valid target-feasibility audit."""


@dataclass(frozen=True)
class FeasibilityConfig:
    """Shape and card requirements for a playable lineage."""

    members_per_stage: int = 10
    stages_per_game: int = 5
    unlock_species_per_transition: int = 2
    require_rich_card_metadata: bool = False

    def __post_init__(self) -> None:
        if self.members_per_stage <= 0:
            raise ValueError("members_per_stage must be positive")
        if self.stages_per_game <= 0:
            raise ValueError("stages_per_game must be positive")
        if self.unlock_species_per_transition <= 0:
            raise ValueError("unlock_species_per_transition must be positive")
        if self.unlock_species_per_transition > self.members_per_stage:
            raise ValueError(
                "unlock_species_per_transition cannot exceed members_per_stage"
            )

    @property
    def lineage_species(self) -> int:
        return self.members_per_stage * self.stages_per_game

    @property
    def relative_species(self) -> int:
        return self.lineage_species - 1

    @property
    def transition_stages(self) -> int:
        return self.stages_per_game - 1

    @property
    def decoys_per_transition_stage(self) -> int:
        return self.members_per_stage - self.unlock_species_per_transition

    @property
    def final_stage_relatives(self) -> int:
        return self.members_per_stage - 1


@dataclass(frozen=True)
class _LineageState:
    usable_depth: int
    completed_transition_stages: int
    current_decoys: int
    current_unlocks: int
    final_stage_relatives: int


@dataclass(frozen=True)
class TargetLineageMetrics:
    """Playable-lineage metrics for one target leaf."""

    usable_depth: int
    total_relative_capacity: int
    completed_transition_stages: int
    final_stage_relative_capacity: int

    def supports(self, config: FeasibilityConfig) -> bool:
        return (
            self.total_relative_capacity >= config.relative_species
            and self.completed_transition_stages >= config.transition_stages
            and self.final_stage_relative_capacity >= config.final_stage_relatives
        )


def _advance_lineage(
    state: _LineageState,
    *,
    tier_capacity: int,
    config: FeasibilityConfig,
) -> _LineageState:
    """Assign one ordered tier to the earliest unfinished lineage role.

    A tier may contain decoys or unlock species within a transition stage, but
    never both. Unused species are allowed. Greedily completing each role at
    its earliest tier leaves the largest possible suffix for later stages.
    """
    if tier_capacity <= 0:
        return state
    depth = state.usable_depth + 1
    completed = state.completed_transition_stages
    decoys = state.current_decoys
    unlocks = state.current_unlocks
    final_relatives = state.final_stage_relatives

    if completed >= config.transition_stages:
        final_relatives += tier_capacity
    elif decoys < config.decoys_per_transition_stage:
        decoys = min(
            config.decoys_per_transition_stage, decoys + tier_capacity
        )
    else:
        unlocks = min(
            config.unlock_species_per_transition, unlocks + tier_capacity
        )
        if unlocks == config.unlock_species_per_transition:
            completed += 1
            decoys = 0
            unlocks = 0

    return _LineageState(depth, completed, decoys, unlocks, final_relatives)


def _target_metrics(
    state: _LineageState,
    *,
    final_tier_capacity: int,
    total_relative_capacity: int,
    config: FeasibilityConfig,
) -> TargetLineageMetrics:
    """Consume the last source tier without requiring it to end the game."""
    final_state = _advance_lineage(
        state, tier_capacity=final_tier_capacity, config=config
    )
    return TargetLineageMetrics(
        usable_depth=final_state.usable_depth,
        total_relative_capacity=total_relative_capacity,
        completed_transition_stages=final_state.completed_transition_stages,
        final_stage_relative_capacity=final_state.final_stage_relatives,
    )


def _counter_percentile(counter: Mapping[int, int], percentile: float) -> int:
    if not counter:
        raise FeasibilityAuditError("cannot summarize an empty distribution")
    total = sum(counter.values())
    rank = max(1, int(percentile * total + 0.999999999999))
    cumulative = 0
    for value in sorted(counter):
        cumulative += counter[value]
        if cumulative >= rank:
            return value
    raise AssertionError("percentile rank exceeded distribution")


def summarize_distribution(counter: Mapping[int, int]) -> dict[str, int]:
    """Return exact count and nearest-rank percentiles for integer values."""
    if not counter:
        raise FeasibilityAuditError("cannot summarize an empty distribution")
    return {
        "count": sum(counter.values()),
        "min": min(counter),
        "p10": _counter_percentile(counter, 0.10),
        "p25": _counter_percentile(counter, 0.25),
        "median": _counter_percentile(counter, 0.50),
        "p75": _counter_percentile(counter, 0.75),
        "p90": _counter_percentile(counter, 0.90),
        "p95": _counter_percentile(counter, 0.95),
        "p99": _counter_percentile(counter, 0.99),
        "max": max(counter),
    }


def _read_metadata_subjects(
    connection: sqlite3.Connection,
    query: str,
) -> tuple[set[int], set[str]]:
    ott_ids: set[int] = set()
    names: set[str] = set()
    for subject_type, ott_id, scientific_name in connection.execute(query):
        if subject_type == "ott":
            ott_ids.add(int(ott_id))
        else:
            names.add(str(scientific_name))
    return ott_ids, names


def _has_subject(
    ott_id: int | None,
    scientific_name: str | None,
    subjects: tuple[set[int], set[str]],
) -> bool:
    ott_ids, names = subjects
    return (ott_id is not None and ott_id in ott_ids) or (
        scientific_name is not None and scientific_name in names
    )


def _percentage(count: int, total: int) -> float:
    return round(100.0 * count / total, 4) if total else 0.0


def _coverage_record(count: int, total: int) -> dict[str, int | float]:
    return {"count": count, "percent": _percentage(count, total)}


def _metadata_coverage(
    counts: Mapping[str, int], total: int
) -> dict[str, dict[str, int | float]]:
    return {key: _coverage_record(value, total) for key, value in counts.items()}


def _database_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    try:
        return {
            str(key): str(value)
            for key, value in connection.execute(
                "SELECT key, value FROM dataset_metadata"
            )
        }
    except sqlite3.Error as error:
        raise FeasibilityAuditError("cannot read database metadata") from error


def _validate_inputs(
    tree: sqlite3.Connection,
    normalized: sqlite3.Connection,
) -> str:
    tree_version = tree.execute("PRAGMA user_version").fetchone()
    normalized_version = normalized.execute("PRAGMA user_version").fetchone()
    if tree_version != (TREE_SCHEMA_VERSION,):
        raise FeasibilityAuditError(
            f"unsupported tree schema: expected {TREE_SCHEMA_VERSION}, "
            f"found {tree_version!r}"
        )
    if normalized_version != (1,):
        raise FeasibilityAuditError(
            f"unsupported normalized schema: expected 1, found {normalized_version!r}"
        )
    tree_metadata = _database_metadata(tree)
    normalized_metadata = _database_metadata(normalized)
    tree_dataset = tree_metadata.get("dataset_version")
    if not tree_dataset or tree_dataset != normalized_metadata.get("dataset_version"):
        raise FeasibilityAuditError(
            "tree and normalized databases have different dataset versions"
        )
    return tree_dataset


def _build_node_states(
    tree: sqlite3.Connection,
    config: FeasibilityConfig,
    tier_capacities: Counter[int],
    *,
    relative_descendants: Mapping[int, int] | None = None,
    target_descendants: Mapping[int, int] | None = None,
) -> tuple[dict[int, tuple[int, _LineageState]], int]:
    states: dict[int, tuple[int, _LineageState]] = {}
    rows = tree.execute(
        "SELECT node_id, collapsed_parent_node_id, descendant_leaf_count "
        "FROM biological_nodes WHERE retained_after_collapse = 1 "
        "ORDER BY collapsed_depth, node_id"
    )
    root_count = 0
    for node_id, parent_id, source_descendant_count in rows:
        node_id = int(node_id)
        source_descendant_count = int(source_descendant_count)
        descendant_count = (
            source_descendant_count
            if relative_descendants is None
            else relative_descendants.get(node_id, 0)
        )
        if parent_id is None:
            states[node_id] = (
                descendant_count,
                _LineageState(0, 0, 0, 0, 0),
            )
            root_count += 1
            continue
        parent_id = int(parent_id)
        try:
            parent_descendants, parent_state = states[parent_id]
        except KeyError as error:
            raise FeasibilityAuditError(
                f"collapsed parent {parent_id} was not read before node {node_id}"
            ) from error
        capacity = parent_descendants - descendant_count
        if capacity < 0:
            raise FeasibilityAuditError(
                f"negative sister capacity on edge {parent_id}->{node_id}"
            )
        if capacity:
            target_weight = (
                source_descendant_count
                if target_descendants is None
                else target_descendants.get(node_id, 0)
            )
            if target_weight:
                tier_capacities[capacity] += target_weight
        states[node_id] = (
            descendant_count,
            _advance_lineage(parent_state, tier_capacity=capacity, config=config),
        )
    if root_count != 1:
        raise FeasibilityAuditError(f"expected one collapsed root; found {root_count}")
    return states, root_count


def _metadata_ready_leaf_ids(
    normalized: sqlite3.Connection,
    preferred_english: tuple[set[int], set[str]],
    licensed_overall_best_image: tuple[set[int], set[str]],
) -> set[int]:
    ready: set[int] = set()
    for leaf_id, scientific_name, ott_id in normalized.execute(
        "SELECT leaf_id, scientific_name, ott_id FROM leaves ORDER BY leaf_id"
    ):
        name = None if scientific_name is None else str(scientific_name)
        ott = None if ott_id is None else int(ott_id)
        if (
            name
            and _has_subject(ott, name, preferred_english)
            and _has_subject(ott, name, licensed_overall_best_image)
        ):
            ready.add(int(leaf_id))
    return ready


def _filtered_descendant_counts(
    tree: sqlite3.Connection,
    included_leaf_ids: set[int],
) -> dict[int, int]:
    """Count included leaves below every retained node, bottom-up."""
    nodes = [
        (int(node_id), None if parent_id is None else int(parent_id))
        for node_id, parent_id in tree.execute(
            "SELECT node_id, collapsed_parent_node_id FROM biological_nodes "
            "WHERE retained_after_collapse = 1 ORDER BY collapsed_depth, node_id"
        )
    ]
    counts: Counter[int] = Counter()
    for leaf_id, parent_id in tree.execute(
        "SELECT leaf_id, collapsed_parent_node_id FROM biological_leaves"
    ):
        if int(leaf_id) in included_leaf_ids:
            counts[int(parent_id)] += 1
    for node_id, parent_id in reversed(nodes):
        if parent_id is not None:
            counts[parent_id] += counts[node_id]
    roots = [node_id for node_id, parent_id in nodes if parent_id is None]
    if len(roots) != 1:
        raise FeasibilityAuditError(
            f"expected one collapsed root while filtering; found {len(roots)}"
        )
    if counts[roots[0]] != len(included_leaf_ids):
        raise FeasibilityAuditError(
            "metadata-ready leaves do not reconcile with the collapsed root"
        )
    return dict(counts)


def audit_target_feasibility(
    *,
    tree_database: Path = DEFAULT_TREE_DATABASE,
    normalized_database: Path = DEFAULT_NORMALIZED_DIR / DATABASE_FILENAME,
    config: FeasibilityConfig = FeasibilityConfig(),
) -> dict[str, object]:
    """Audit every leaf using one internal-node pass and one leaf stream."""
    if not tree_database.is_file():
        raise FeasibilityAuditError(f"tree database does not exist: {tree_database}")
    if not normalized_database.is_file():
        raise FeasibilityAuditError(
            f"normalized database does not exist: {normalized_database}"
        )

    tree = sqlite3.connect(f"file:{tree_database}?mode=ro", uri=True)
    normalized = sqlite3.connect(f"file:{normalized_database}?mode=ro", uri=True)
    try:
        dataset_version = _validate_inputs(tree, normalized)
        preferred_english = _read_metadata_subjects(
            normalized,
            "SELECT DISTINCT subject_type, ott_id, scientific_name "
            "FROM vernacular_names WHERE preferred = 1 "
            "AND language_primary = 'en'",
        )
        overall_best_image = _read_metadata_subjects(
            normalized,
            "SELECT DISTINCT subject_type, ott_id, scientific_name "
            "FROM images WHERE overall_best_any = 1",
        )
        licensed_overall_best_image = _read_metadata_subjects(
            normalized,
            "SELECT DISTINCT subject_type, ott_id, scientific_name "
            "FROM images WHERE overall_best_any = 1 "
            "AND NULLIF(TRIM(url), '') IS NOT NULL "
            "AND NULLIF(TRIM(rights), '') IS NOT NULL "
            "AND NULLIF(TRIM(license), '') IS NOT NULL",
        )

        metadata_ready_leaf_ids = _metadata_ready_leaf_ids(
            normalized, preferred_english, licensed_overall_best_image
        )
        filtered_descendants = (
            _filtered_descendant_counts(tree, metadata_ready_leaf_ids)
            if config.require_rich_card_metadata
            else None
        )

        tier_capacities: Counter[int] = Counter()
        node_states, _ = _build_node_states(
            tree,
            config,
            tier_capacities,
            relative_descendants=filtered_descendants,
            target_descendants=filtered_descendants,
        )
        usable_depths: Counter[int] = Counter()
        total_relative_capacities: Counter[int] = Counter()
        completed_transition_stages: Counter[int] = Counter()
        final_stage_capacities: Counter[int] = Counter()
        failure_reasons: Counter[str] = Counter()
        metadata_counts: Counter[str] = Counter()
        supported_metadata_counts: Counter[str] = Counter()
        supported_targets = 0
        leaf_count = 0
        source_leaf_count = 0
        relative_universe_size = (
            len(metadata_ready_leaf_ids)
            if config.require_rich_card_metadata
            else int(
                tree.execute("SELECT COUNT(*) FROM biological_leaves").fetchone()[0]
            )
        )

        tree.execute("ATTACH DATABASE ? AS normalized", (str(normalized_database),))
        leaf_rows: Iterable[tuple[object, ...]] = tree.execute(
            "SELECT tree_leaf.leaf_id, tree_leaf.collapsed_parent_node_id, "
            "source_leaf.scientific_name, source_leaf.ott_id "
            "FROM biological_leaves AS tree_leaf "
            "JOIN normalized.leaves AS source_leaf "
            "ON source_leaf.leaf_id = tree_leaf.leaf_id "
            "ORDER BY tree_leaf.leaf_id"
        )
        for leaf_id, parent_id, scientific_name, ott_id in leaf_rows:
            leaf_id = int(leaf_id)
            source_leaf_count += 1
            if (
                config.require_rich_card_metadata
                and leaf_id not in metadata_ready_leaf_ids
            ):
                continue
            parent_id = int(parent_id)
            try:
                parent_descendants, parent_state = node_states[parent_id]
            except KeyError as error:
                raise FeasibilityAuditError(
                    f"leaf {leaf_id} has unknown collapsed parent {parent_id}"
                ) from error
            relative_leaf_weight = (
                int(leaf_id in metadata_ready_leaf_ids)
                if config.require_rich_card_metadata
                else 1
            )
            final_tier_capacity = parent_descendants - relative_leaf_weight
            if final_tier_capacity < 0:
                raise FeasibilityAuditError(
                    f"leaf {leaf_id} has negative final-tier relative capacity"
                )
            total_relative_capacity = (
                relative_universe_size - relative_leaf_weight
            )
            metrics = _target_metrics(
                parent_state,
                final_tier_capacity=final_tier_capacity,
                total_relative_capacity=total_relative_capacity,
                config=config,
            )
            if final_tier_capacity:
                tier_capacities[final_tier_capacity] += 1
            usable_depths[metrics.usable_depth] += 1
            total_relative_capacities[metrics.total_relative_capacity] += 1
            completed_transition_stages[
                metrics.completed_transition_stages
            ] += 1
            final_stage_capacities[metrics.final_stage_relative_capacity] += 1

            name = None if scientific_name is None else str(scientific_name)
            ott = None if ott_id is None else int(ott_id)
            flags = {
                "scientific_name": bool(name),
                "ott_id": ott is not None,
                "preferred_english_vernacular": _has_subject(
                    ott, name, preferred_english
                ),
                "overall_best_image": _has_subject(ott, name, overall_best_image),
                "licensed_overall_best_image": _has_subject(
                    ott, name, licensed_overall_best_image
                ),
            }
            flags["card_ready"] = flags["scientific_name"] and flags[
                "licensed_overall_best_image"
            ]
            flags["rich_card_ready"] = flags["card_ready"] and flags[
                "preferred_english_vernacular"
            ]
            for key, present in flags.items():
                metadata_counts[key] += int(present)

            supported = metrics.supports(config)
            if supported:
                supported_targets += 1
                for key, present in flags.items():
                    supported_metadata_counts[key] += int(present)
            elif metrics.total_relative_capacity < config.relative_species:
                failure_reasons["insufficient_total_relatives"] += 1
            elif (
                metrics.completed_transition_stages < config.transition_stages
            ):
                failure_reasons["insufficient_ordered_transition_structure"] += 1
            else:
                failure_reasons["insufficient_final_stage_relatives"] += 1
            leaf_count += 1
    except sqlite3.Error as error:
        raise FeasibilityAuditError(f"SQLite audit failed: {error}") from error
    finally:
        normalized.close()
        tree.close()

    if not leaf_count:
        raise FeasibilityAuditError("tree contains no leaves")
    if supported_targets + sum(failure_reasons.values()) != leaf_count:
        raise FeasibilityAuditError("target failure categories are inconsistent")
    return {
        "schema_version": 1,
        "feasibility_audit_version": FEASIBILITY_AUDIT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_version": dataset_version,
        "configuration": {
            "members_per_stage": config.members_per_stage,
            "stages_per_game": config.stages_per_game,
            "lineage_species": config.lineage_species,
            "relative_species": config.relative_species,
            "unlock_species_per_transition": (
                config.unlock_species_per_transition
            ),
            "transition_stages": config.transition_stages,
            "total_unlock_species": (
                config.unlock_species_per_transition * config.transition_stages
            ),
            "require_rich_card_metadata": config.require_rich_card_metadata,
        },
        "interpretation": {
            "playable_lineage": (
                "M*N species: one hidden target and M*N-1 unique relatives"
            ),
            "transition_stage": (
                "N minus unlock-count decoys on shallower selected tiers, then "
                "the configured unlock species on deeper selected tiers"
            ),
            "ultimate_stage": "N-1 relatives followed by the hidden target",
            "tier_use": (
                "empty and unselected source tiers may be skipped; no literal "
                "closest-sister endpoint is required"
            ),
            "polytomy_roles": (
                "a selected tier may contain decoys or unlock species within a "
                "stage, but not both"
            ),
            "species_universe": (
                "targets and relatives require a preferred English name and "
                "a licensed overall-best image"
                if config.require_rich_card_metadata
                else "all leaves may be targets and relatives"
            ),
        },
        "targets": {
            "source_leaves": source_leaf_count,
            "total": leaf_count,
            "supporting_configuration": _coverage_record(
                supported_targets, leaf_count
            ),
            "failure_reasons": {
                reason: _coverage_record(failure_reasons[reason], leaf_count)
                for reason in (
                    "insufficient_total_relatives",
                    "insufficient_ordered_transition_structure",
                    "insufficient_final_stage_relatives",
                )
            },
        },
        "topology": {
            "usable_depth": summarize_distribution(usable_depths),
            "relative_tier_capacity": summarize_distribution(tier_capacities),
            "total_relative_capacity": summarize_distribution(
                total_relative_capacities
            ),
            "completed_transition_stages": summarize_distribution(
                completed_transition_stages
            ),
            "final_stage_relative_capacity": summarize_distribution(
                final_stage_capacities
            ),
            "target_tier_instances": sum(tier_capacities.values()),
        },
        "metadata_coverage": {
            "all_targets": _metadata_coverage(metadata_counts, leaf_count),
            "topology_supported_targets": _metadata_coverage(
                supported_metadata_counts, supported_targets
            ),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit target feasibility over a processed OneZoom tree."
    )
    parser.add_argument(
        "normalized_dir", nargs="?", type=Path, default=DEFAULT_NORMALIZED_DIR
    )
    parser.add_argument("--tree", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--members-per-stage", type=int, default=10)
    parser.add_argument("--stages-per-game", type=int, default=5)
    parser.add_argument("--unlock-species", type=int, default=2)
    parser.add_argument(
        "--require-rich-cards",
        action="store_true",
        help=(
            "restrict targets and relatives to leaves with a preferred English "
            "name and complete licensed overall-best image"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    tree_database = args.tree or (
        args.normalized_dir
        / f"tree-v{TREE_SCHEMA_VERSION}"
        / TREE_DATABASE_FILENAME
    )
    try:
        config = FeasibilityConfig(
            members_per_stage=args.members_per_stage,
            stages_per_game=args.stages_per_game,
            unlock_species_per_transition=args.unlock_species,
            require_rich_card_metadata=args.require_rich_cards,
        )
        result = audit_target_feasibility(
            tree_database=tree_database,
            normalized_database=args.normalized_dir / DATABASE_FILENAME,
            config=config,
        )
    except (ValueError, FeasibilityAuditError) as error:
        raise SystemExit(str(error)) from error
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
