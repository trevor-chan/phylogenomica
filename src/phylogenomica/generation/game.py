"""Assemble validated immutable games from seeded relative selections."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from phylogenomica.data.cards import (
    CardMetadataError,
    CardMetadataStore,
    SpeciesCard,
    species_card_from_dict,
)
from phylogenomica.data.onezoom_ingest import DATABASE_FILENAME
from phylogenomica.generation.eligibility import (
    ELIGIBILITY_DATABASE_FILENAME,
    ELIGIBILITY_INDEX_VERSION,
)
from phylogenomica.generation.feasibility import (
    FeasibilityAuditError,
    FeasibilityConfig,
    feasibility_configuration,
    parse_feasibility_configuration,
)
from phylogenomica.generation.selection import (
    RelativeSelection,
    RelativeSelectionError,
    SelectedRelative,
    select_relatives,
    validate_relative_selection,
)
from phylogenomica.tree.preprocess import (
    DEFAULT_NORMALIZED_DIR,
    TREE_DATABASE_FILENAME,
    TREE_SCHEMA_VERSION,
)
from phylogenomica.tree.query import BiologicalTree, TaxonRef, TreeQueryError

GAME_SCHEMA_VERSION = 2
GAME_GENERATOR_VERSION = 2

GameRole = Literal["decoy", "mulligan", "unlock", "target"]
MEMBER_ROLES = frozenset(("decoy", "mulligan", "unlock", "target"))
TIER_ROLES = frozenset(("decoy", "mulligan", "unlock"))


class GameGenerationError(RuntimeError):
    """Raised when a selection cannot become a complete validated game."""


@dataclass(frozen=True)
class GameMember:
    species_id: int
    role: GameRole
    tier_index: int | None
    ancestor_node_id: int | None
    card: SpeciesCard


@dataclass(frozen=True)
class GameTier:
    tier_index: int
    ancestor_node_id: int
    role: Literal["decoy", "mulligan", "unlock"]
    species_ids: tuple[int, ...]
    age_ma: float | None


@dataclass(frozen=True)
class GeneratedStage:
    stage_index: int
    start_node_id: int
    end_node_id: int
    members: tuple[GameMember, ...]
    tiers: tuple[GameTier, ...]
    mulligan_species_ids: tuple[int, ...]
    unlock_species_ids: tuple[int, ...]
    target_species_id: int | None


@dataclass(frozen=True)
class GeneratedGame:
    schema_version: int
    game_id: str
    dataset_version: str
    generator_version: int
    selector_version: int
    eligibility_index_version: int
    target_id: int
    seed: int
    configuration: FeasibilityConfig
    stages: tuple[GeneratedStage, ...]

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["configuration"] = feasibility_configuration(self.configuration)
        return result


def _role(value: object, allowed: frozenset[str]) -> GameRole:
    role = str(value)
    if role not in allowed:
        raise ValueError(f"unknown role {role!r}")
    return role  # type: ignore[return-value]


def _game_member_from_dict(payload: Mapping[str, object]) -> GameMember:
    tier_index = payload["tier_index"]
    ancestor_node_id = payload["ancestor_node_id"]
    return GameMember(
        species_id=int(payload["species_id"]),  # type: ignore[arg-type]
        role=_role(payload["role"], MEMBER_ROLES),
        tier_index=None if tier_index is None else int(tier_index),  # type: ignore[arg-type]
        ancestor_node_id=(
            None if ancestor_node_id is None else int(ancestor_node_id)  # type: ignore[arg-type]
        ),
        card=species_card_from_dict(payload["card"]),  # type: ignore[arg-type]
    )


def _game_tier_from_dict(payload: Mapping[str, object]) -> GameTier:
    return GameTier(
        tier_index=int(payload["tier_index"]),  # type: ignore[arg-type]
        ancestor_node_id=int(payload["ancestor_node_id"]),  # type: ignore[arg-type]
        role=_role(payload["role"], TIER_ROLES),  # type: ignore[arg-type]
        species_ids=tuple(int(value) for value in payload["species_ids"]),  # type: ignore[union-attr]
        age_ma=(
            None if payload["age_ma"] is None else float(payload["age_ma"])  # type: ignore[arg-type]
        ),
    )


def _generated_stage_from_dict(payload: Mapping[str, object]) -> GeneratedStage:
    target_species_id = payload["target_species_id"]
    return GeneratedStage(
        stage_index=int(payload["stage_index"]),  # type: ignore[arg-type]
        start_node_id=int(payload["start_node_id"]),  # type: ignore[arg-type]
        end_node_id=int(payload["end_node_id"]),  # type: ignore[arg-type]
        members=tuple(
            _game_member_from_dict(member) for member in payload["members"]  # type: ignore[union-attr]
        ),
        tiers=tuple(_game_tier_from_dict(tier) for tier in payload["tiers"]),  # type: ignore[union-attr]
        mulligan_species_ids=tuple(
            int(value) for value in payload["mulligan_species_ids"]  # type: ignore[union-attr]
        ),
        unlock_species_ids=tuple(
            int(value) for value in payload["unlock_species_ids"]  # type: ignore[union-attr]
        ),
        target_species_id=(
            None if target_species_id is None else int(target_species_id)  # type: ignore[arg-type]
        ),
    )


def game_from_dict(payload: Mapping[str, object]) -> GeneratedGame:
    """Rebuild and fully validate an immutable game from its serialized form.

    The result is checked with :func:`validate_game_structure`, so a game that
    was truncated, hand-edited, or produced by another generator version fails
    here rather than reaching the gameplay engine.
    """
    # Check the version before the fields, so a game from an older schema
    # reports the mismatch rather than a missing-field error for whichever
    # field that schema happened to lack.
    schema_version = payload.get("schema_version")
    if schema_version != GAME_SCHEMA_VERSION:
        raise GameGenerationError(
            f"game has an unsupported schema version: {schema_version!r}; "
            f"this build reads version {GAME_SCHEMA_VERSION}"
        )
    try:
        game = GeneratedGame(
            schema_version=int(payload["schema_version"]),  # type: ignore[arg-type]
            game_id=str(payload["game_id"]),
            dataset_version=str(payload["dataset_version"]),
            generator_version=int(payload["generator_version"]),  # type: ignore[arg-type]
            selector_version=int(payload["selector_version"]),  # type: ignore[arg-type]
            eligibility_index_version=int(
                payload["eligibility_index_version"]  # type: ignore[arg-type]
            ),
            target_id=int(payload["target_id"]),  # type: ignore[arg-type]
            seed=int(payload["seed"]),  # type: ignore[arg-type]
            configuration=parse_feasibility_configuration(
                payload["configuration"]  # type: ignore[arg-type]
            ),
            stages=tuple(
                _generated_stage_from_dict(stage) for stage in payload["stages"]  # type: ignore[union-attr]
            ),
        )
    except (
        CardMetadataError,
        FeasibilityAuditError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise GameGenerationError(f"invalid serialized game: {error}") from error
    validate_game_structure(game)
    return game


def load_game(path: Path) -> GeneratedGame:
    """Read and validate one serialized game from disk."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise GameGenerationError(f"cannot read game: {error}") from error
    except json.JSONDecodeError as error:
        raise GameGenerationError(f"game is not valid JSON: {error}") from error
    if not isinstance(payload, Mapping):
        raise GameGenerationError("serialized game must be a JSON object")
    return game_from_dict(payload)


def _generation_key(source: RelativeSelection | GeneratedGame) -> dict[str, object]:
    """Render the identity a game and its selection share.

    A finished game carries every field the digest covers, so a game read back
    from disk can recompute its own identifier and stage shuffles without the
    selection that produced it.
    """
    return {
        "dataset_version": source.dataset_version,
        "game_generator_version": GAME_GENERATOR_VERSION,
        "relative_selector_version": source.selector_version,
        "eligibility_index_version": source.eligibility_index_version,
        "target_id": source.target_id,
        "seed": source.seed,
        "configuration": feasibility_configuration(source.configuration),
    }


def _game_id(source: RelativeSelection | GeneratedGame) -> str:
    rendered = json.dumps(
        _generation_key(source), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _stage_random(
    source: RelativeSelection | GeneratedGame, stage_index: int
) -> random.Random:
    record = {
        **_generation_key(source),
        "random_namespace": "stage-member-order",
        "stage_index": stage_index,
    }
    rendered = json.dumps(record, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return random.Random(int.from_bytes(hashlib.sha256(rendered).digest(), "big"))


def _shuffle_members(
    members: Sequence[GameMember],
    *,
    source: RelativeSelection | GeneratedGame,
    stage_index: int,
) -> tuple[GameMember, ...]:
    shuffled = list(members)
    _stage_random(source, stage_index).shuffle(shuffled)
    return tuple(shuffled)


def _member_from_relative(
    relative: SelectedRelative, cards: Mapping[int, SpeciesCard]
) -> GameMember:
    return GameMember(
        species_id=relative.species_id,
        role=relative.role,
        tier_index=relative.tier_index,
        ancestor_node_id=relative.ancestor_node_id,
        card=cards[relative.species_id],
    )


def _game_tiers(
    relatives: Sequence[SelectedRelative],
    ages: Mapping[int, float | None],
) -> tuple[GameTier, ...]:
    grouped: dict[tuple[int, int, str], list[int]] = {}
    for relative in relatives:
        key = (relative.tier_index, relative.ancestor_node_id, relative.role)
        grouped.setdefault(key, []).append(relative.species_id)
    return tuple(
        GameTier(
            tier_index=tier_index,
            ancestor_node_id=ancestor_node_id,
            role=role,  # type: ignore[arg-type]
            species_ids=tuple(sorted(species_ids)),
            age_ma=ages.get(ancestor_node_id),
        )
        for (tier_index, ancestor_node_id, role), species_ids in sorted(
            grouped.items()
        )
    )


def _validate_card(card: SpeciesCard) -> None:
    if not card.scientific_name.strip():
        raise GameGenerationError(f"card {card.species_id} has no scientific name")
    if not card.english_name.strip():
        raise GameGenerationError(f"card {card.species_id} has no English name")
    if not card.image.url.strip():
        raise GameGenerationError(f"card {card.species_id} has no image URL")
    if not card.image.rights.strip() or not card.image.license.strip():
        raise GameGenerationError(
            f"card {card.species_id} has incomplete image attribution"
        )


def _validate_game_identity(
    game: GeneratedGame, selection: RelativeSelection
) -> None:
    if game.schema_version != GAME_SCHEMA_VERSION:
        raise GameGenerationError("game has an unsupported schema version")
    if game.generator_version != GAME_GENERATOR_VERSION:
        raise GameGenerationError("game has an unsupported generator version")
    if game.game_id != _game_id(selection):
        raise GameGenerationError("game ID does not match generation inputs")
    if game.dataset_version != selection.dataset_version:
        raise GameGenerationError("game and selection dataset versions differ")
    if (
        game.selector_version != selection.selector_version
        or game.eligibility_index_version != selection.eligibility_index_version
    ):
        raise GameGenerationError("game and selection component versions differ")
    if game.target_id != selection.target_id or game.seed != selection.seed:
        raise GameGenerationError("game and selection identity differ")
    if game.configuration != selection.configuration:
        raise GameGenerationError("game and selection configurations differ")
    if len(game.stages) != len(selection.stages):
        raise GameGenerationError("game and selection stage counts differ")


def _validate_stage_continuity(
    game: GeneratedGame, backbone_node_ids: Sequence[int] | None
) -> None:
    """Validate that stages descend one strictly ordered target backbone.

    Tier indexes are positions on the collapsed root-to-target path, so a
    consistent index/ancestor pairing plus strictly increasing indexes proves
    continuity without reopening the tree. Supplying the backbone additionally
    anchors every recorded node to a real lineage position.
    """
    ancestor_by_tier: dict[int, int] = {}
    tier_by_ancestor: dict[int, int] = {}
    age_by_tier: dict[int, float | None] = {}
    previous_deepest_tier = -1
    for stage in game.stages:
        if not stage.tiers:
            raise GameGenerationError("game stage has no relative tiers")
        tier_indexes = [tier.tier_index for tier in stage.tiers]
        if tier_indexes != sorted(set(tier_indexes)):
            raise GameGenerationError("stage tiers are not strictly ordered")
        if tier_indexes[0] <= previous_deepest_tier:
            raise GameGenerationError("successive stages do not descend one lineage")
        previous_deepest_tier = tier_indexes[-1]
        for tier in stage.tiers:
            if (
                ancestor_by_tier.setdefault(tier.tier_index, tier.ancestor_node_id)
                != tier.ancestor_node_id
            ):
                raise GameGenerationError("one tier index has conflicting ancestors")
            if (
                tier_by_ancestor.setdefault(tier.ancestor_node_id, tier.tier_index)
                != tier.tier_index
            ):
                raise GameGenerationError("one ancestor node spans multiple tiers")
            if tier.age_ma is not None:
                if not math.isfinite(tier.age_ma):
                    raise GameGenerationError("divergence age is not finite")
                if tier.age_ma < 0:
                    raise GameGenerationError("divergence age is negative")
            # Tier indexes are unique per stage and strictly increasing across
            # them, so each one is seen exactly once here.
            age_by_tier[tier.tier_index] = tier.age_ma

        is_ultimate = stage.stage_index == len(game.stages) - 1
        if stage.start_node_id != stage.tiers[0].ancestor_node_id:
            raise GameGenerationError("stage start node is not its shallowest tier")
        if not is_ultimate and stage.end_node_id != stage.tiers[-1].ancestor_node_id:
            raise GameGenerationError("stage end node is not its deepest tier")

    # A divergence deeper on the backbone is more recent, so ages must not
    # increase toward the target. Ties are common and allowed; absent ages are
    # skipped rather than treated as a break in the sequence.
    previous_age: float | None = None
    for tier_index in sorted(age_by_tier):
        age = age_by_tier[tier_index]
        if age is None:
            continue
        if previous_age is not None and age > previous_age:
            raise GameGenerationError("divergence ages increase toward the target")
        previous_age = age

    if backbone_node_ids is None:
        return
    position_by_node = {
        node_id: position for position, node_id in enumerate(backbone_node_ids)
    }
    if len(position_by_node) != len(backbone_node_ids):
        raise GameGenerationError("target backbone repeats an ancestor node")
    for tier_index, ancestor_node_id in ancestor_by_tier.items():
        if position_by_node.get(ancestor_node_id) != tier_index:
            raise GameGenerationError("tier ancestor is not its backbone position")
    for stage in game.stages:
        for node_id in (stage.start_node_id, stage.end_node_id):
            if node_id not in position_by_node:
                raise GameGenerationError("stage boundary is not on the backbone")
        if position_by_node[stage.start_node_id] > position_by_node[stage.end_node_id]:
            raise GameGenerationError("stage boundaries are inverted")
    ultimate = game.stages[-1]
    if ultimate.end_node_id != backbone_node_ids[-1]:
        raise GameGenerationError("ultimate stage does not end at the target parent")


def _canonical_members(
    stage: GeneratedStage, *, is_ultimate: bool
) -> list[GameMember]:
    """Rebuild the pre-shuffle member order a stage was assembled from."""
    canonical = sorted(
        (member for member in stage.members if member.role != "target"),
        key=lambda member: (member.tier_index, member.role, member.species_id),
    )
    if is_ultimate:
        canonical.extend(
            member for member in stage.members if member.role == "target"
        )
    return canonical


def _validate_stage_roles(stage: GeneratedStage) -> None:
    relatives = [member for member in stage.members if member.role != "target"]
    tier_roles: dict[int, GameRole] = {}
    role_tiers: dict[str, list[int]] = {"decoy": [], "mulligan": [], "unlock": []}
    for relative in relatives:
        if relative.tier_index is None or relative.ancestor_node_id is None:
            raise GameGenerationError("relative member has no lineage tier")
        existing_role = tier_roles.setdefault(relative.tier_index, relative.role)
        if existing_role != relative.role:
            raise GameGenerationError("one tier contains multiple stage roles")
        role_tiers[relative.role].append(relative.tier_index)

    if not role_tiers["mulligan"]:
        raise GameGenerationError("stage has no mulligan species")
    if role_tiers["decoy"] and max(role_tiers["decoy"]) >= min(
        role_tiers["mulligan"]
    ):
        raise GameGenerationError("mulligan is not deeper than every decoy")
    if role_tiers["unlock"] and max(role_tiers["mulligan"]) >= min(
        role_tiers["unlock"]
    ):
        raise GameGenerationError("unlock is not deeper than every mulligan")
    for tier in stage.tiers:
        if tier.age_ma is not None and not math.isfinite(tier.age_ma):
            raise GameGenerationError("divergence age is not finite")
    # Ages are source data the members do not carry, so they are taken as
    # declared here and checked for consistency and ordering by the continuity
    # pass, which sees every tier in the game at once.
    declared_ages = {tier.ancestor_node_id: tier.age_ma for tier in stage.tiers}
    if stage.tiers != _game_tiers(relatives, declared_ages):
        raise GameGenerationError("game tier projection does not match members")

    for role, attribute in (
        ("mulligan", "mulligan_species_ids"),
        ("unlock", "unlock_species_ids"),
    ):
        expected = tuple(
            sorted(
                relative.species_id
                for relative in relatives
                if relative.role == role
            )
        )
        if getattr(stage, attribute) != expected:
            raise GameGenerationError(f"stage {role} IDs are incorrect")


def validate_game_structure(
    game: GeneratedGame, *, backbone_node_ids: Sequence[int] | None = None
) -> None:
    """Validate a game against itself, without the selection that built it.

    Every rule the generator enforces is recomputable from the finished game:
    the identifier is a digest of the game's own identity fields, and the stage
    shuffle, tier projection, and role ordering all re-derive from its members.
    A game read back from disk is therefore checked as strictly as one just
    generated, minus only its agreement with a specific selection.
    """
    if game.schema_version != GAME_SCHEMA_VERSION:
        raise GameGenerationError("game has an unsupported schema version")
    if game.generator_version != GAME_GENERATOR_VERSION:
        raise GameGenerationError("game has an unsupported generator version")
    if game.game_id != _game_id(game):
        raise GameGenerationError("game ID does not match generation inputs")
    config = game.configuration
    if len(game.stages) != config.stages_per_game:
        raise GameGenerationError("game has the wrong number of stages")

    seen: set[int] = set()
    for expected_index, stage in enumerate(game.stages):
        if stage.stage_index != expected_index:
            raise GameGenerationError("game stage indexes are not contiguous")
        if len(stage.members) != config.members_per_stage:
            raise GameGenerationError("game stage has the wrong member count")
        member_by_id = {member.species_id: member for member in stage.members}
        if len(member_by_id) != len(stage.members):
            raise GameGenerationError("game stage contains duplicate species")

        is_ultimate = expected_index == config.stages_per_game - 1
        target_ids = {
            member.species_id for member in stage.members if member.role == "target"
        }
        expected_target_ids = {game.target_id} if is_ultimate else set()
        if target_ids != expected_target_ids or (
            not is_ultimate and game.target_id in member_by_id
        ):
            raise GameGenerationError("target visibility is incorrect")
        if stage.target_species_id != (game.target_id if is_ultimate else None):
            raise GameGenerationError("stage target ID is incorrect")
        if is_ultimate:
            target_member = member_by_id[game.target_id]
            if (
                target_member.tier_index is not None
                or target_member.ancestor_node_id is not None
            ):
                raise GameGenerationError("target must remain the terminal endpoint")

        roles = Counter(member.role for member in stage.members)
        expected_roles = Counter(
            {
                "decoy": (
                    config.decoys_in_ultimate_stage
                    if is_ultimate
                    else config.decoys_per_transition_stage
                ),
                "mulligan": config.mulligan_species_per_stage,
            }
        )
        if is_ultimate:
            expected_roles["target"] = 1
        else:
            expected_roles["unlock"] = config.unlock_species_per_transition_stage
        if roles != Counter({
            role: count for role, count in expected_roles.items() if count
        }):
            raise GameGenerationError("game stage role counts are incorrect")
        _validate_stage_roles(stage)

        expected_order = _shuffle_members(
            _canonical_members(stage, is_ultimate=is_ultimate),
            source=game,
            stage_index=expected_index,
        )
        if tuple(member.species_id for member in stage.members) != tuple(
            member.species_id for member in expected_order
        ):
            raise GameGenerationError("stage member shuffle is not deterministic")

        for member in stage.members:
            if member.species_id in seen:
                raise GameGenerationError("species is reused across game stages")
            seen.add(member.species_id)
            if member.card.species_id != member.species_id:
                raise GameGenerationError("member and card species IDs differ")
            _validate_card(member.card)

    if len(seen) != config.lineage_species:
        raise GameGenerationError("game does not contain the complete lineage")
    _validate_stage_continuity(game, backbone_node_ids)


def validate_generated_game(
    game: GeneratedGame,
    *,
    selection: RelativeSelection,
    backbone_node_ids: Sequence[int] | None = None,
) -> None:
    """Validate a game structurally and against the selection that built it."""
    validate_relative_selection(selection, config=selection.configuration)
    _validate_game_identity(game, selection)
    validate_game_structure(game, backbone_node_ids=backbone_node_ids)

    for stage, selected_stage in zip(game.stages, selection.stages, strict=True):
        if stage.stage_index != selected_stage.stage_index:
            raise GameGenerationError("game stage indexes do not match selection")
        member_by_id = {member.species_id: member for member in stage.members}
        is_ultimate = stage.stage_index == len(game.stages) - 1
        expected_ids = {
            relative.species_id for relative in selected_stage.relatives
        } | ({game.target_id} if is_ultimate else set())
        if set(member_by_id) != expected_ids:
            raise GameGenerationError("stage members do not match selection")
        for relative in selected_stage.relatives:
            member = member_by_id[relative.species_id]
            if (
                member.role != relative.role
                or member.tier_index != relative.tier_index
                or member.ancestor_node_id != relative.ancestor_node_id
            ):
                raise GameGenerationError("relative member topology or role changed")


def assemble_game(
    selection: RelativeSelection,
    *,
    normalized_database: Path = DEFAULT_NORMALIZED_DIR / DATABASE_FILENAME,
    tree_database: Path = DEFAULT_NORMALIZED_DIR
    / f"tree-v{TREE_SCHEMA_VERSION}"
    / TREE_DATABASE_FILENAME,
) -> GeneratedGame:
    """Resolve cards, add the ultimate target, shuffle, and validate a game."""
    validate_relative_selection(selection, config=selection.configuration)
    all_species_ids = selection.relative_species_ids + (selection.target_id,)
    try:
        with CardMetadataStore.open(normalized_database) as metadata:
            if metadata.dataset_version != selection.dataset_version:
                raise GameGenerationError(
                    "card metadata and selection dataset versions differ"
                )
            cards = metadata.resolve(all_species_ids)
            ages = metadata.divergence_ages(
                [
                    relative.ancestor_node_id
                    for stage in selection.stages
                    for relative in stage.relatives
                ]
            )
        with BiologicalTree.open(tree_database) as tree:
            backbone_node_ids = tree.lineage_node_ids(
                TaxonRef("leaf", selection.target_id)
            )
    except (CardMetadataError, TreeQueryError) as error:
        raise GameGenerationError(str(error)) from error

    stages: list[GeneratedStage] = []
    for selected_stage in selection.stages:
        canonical_members = [
            _member_from_relative(relative, cards)
            for relative in selected_stage.relatives
        ]
        tiers = _game_tiers(selected_stage.relatives, ages)
        is_ultimate = selected_stage.stage_index == len(selection.stages) - 1
        if is_ultimate:
            canonical_members.append(
                GameMember(
                    species_id=selection.target_id,
                    role="target",
                    tier_index=None,
                    ancestor_node_id=None,
                    card=cards[selection.target_id],
                )
            )
        stages.append(
            GeneratedStage(
                stage_index=selected_stage.stage_index,
                start_node_id=tiers[0].ancestor_node_id,
                end_node_id=(
                    backbone_node_ids[-1]
                    if is_ultimate
                    else tiers[-1].ancestor_node_id
                ),
                members=_shuffle_members(
                    canonical_members,
                    source=selection,
                    stage_index=selected_stage.stage_index,
                ),
                tiers=tiers,
                mulligan_species_ids=tuple(
                    sorted(
                        relative.species_id
                        for relative in selected_stage.relatives
                        if relative.role == "mulligan"
                    )
                ),
                unlock_species_ids=tuple(
                    sorted(
                        relative.species_id
                        for relative in selected_stage.relatives
                        if relative.role == "unlock"
                    )
                ),
                target_species_id=selection.target_id if is_ultimate else None,
            )
        )
    game = GeneratedGame(
        schema_version=GAME_SCHEMA_VERSION,
        game_id=_game_id(selection),
        dataset_version=selection.dataset_version,
        generator_version=GAME_GENERATOR_VERSION,
        selector_version=selection.selector_version,
        eligibility_index_version=selection.eligibility_index_version,
        target_id=selection.target_id,
        seed=selection.seed,
        configuration=selection.configuration,
        stages=tuple(stages),
    )
    validate_generated_game(
        game, selection=selection, backbone_node_ids=backbone_node_ids
    )
    return game


def generate_game(
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
) -> GeneratedGame:
    """Select and assemble one complete deterministic game."""
    selection = select_relatives(
        target_id=target_id,
        seed=seed,
        normalized_database=normalized_database,
        tree_database=tree_database,
        eligibility_database=eligibility_database,
        config=config,
    )
    return assemble_game(
        selection,
        normalized_database=normalized_database,
        tree_database=tree_database,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate one complete immutable phylogenomica game."
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
    tree_database = (
        args.tree
        or normalized_dir
        / f"tree-v{TREE_SCHEMA_VERSION}"
        / TREE_DATABASE_FILENAME
    )
    eligibility_database = (
        args.eligibility
        or normalized_dir
        / f"target-eligibility-v{ELIGIBILITY_INDEX_VERSION}"
        / ELIGIBILITY_DATABASE_FILENAME
    )
    try:
        config = FeasibilityConfig(
            members_per_stage=args.members_per_stage,
            stages_per_game=args.stages_per_game,
            unlock_species_per_transition_stage=args.unlock_species,
            mulligan_species_per_stage=args.mulligan_species,
            require_rich_card_metadata=args.require_rich_cards,
        )
        game = generate_game(
            target_id=args.target_id,
            seed=args.seed,
            normalized_database=normalized_dir / DATABASE_FILENAME,
            tree_database=tree_database,
            eligibility_database=eligibility_database,
            config=config,
        )
    except (GameGenerationError, RelativeSelectionError, ValueError) as error:
        raise SystemExit(str(error)) from error
    rendered = json.dumps(game.to_dict(), indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
