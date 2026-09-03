"""Endgame rank titles selected from score and target-lineage metadata."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

RankTier = Literal["needs_improvement", "good", "excellent", "perfect"]
RANK_TIERS: tuple[RankTier, ...] = (
    "needs_improvement",
    "good",
    "excellent",
    "perfect",
)
DEFAULT_RANK_TITLES_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "gameplay" / "rank_titles.json"
)


class RankTitleError(RuntimeError):
    """Raised when the curated title catalog is missing or malformed."""


@dataclass(frozen=True)
class RankTitle:
    title: str
    tier: RankTier
    taxa: tuple[str, ...]


@dataclass(frozen=True)
class AttainedTitle:
    tier: RankTier
    title: str
    matched_taxon: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "tier": self.tier,
            "title": self.title,
            "matched_taxon": self.matched_taxon,
        }


@dataclass(frozen=True)
class RankTitleCatalog:
    schema_version: int
    catalog_version: int
    aliases: Mapping[str, tuple[str, ...]]
    titles: tuple[RankTitle, ...]

    def attained_title(
        self,
        *,
        score: int,
        maximum: int,
        game_id: str,
        target_clade_names: Sequence[str],
    ) -> AttainedTitle:
        """Choose a stable title through a random applicable taxon label."""
        tier = score_tier(score, maximum)
        clades = {clade.casefold() for clade in target_clade_names}
        titles_by_taxon: dict[str, set[str]] = {}
        generic: list[str] = []
        for record in self.titles:
            if record.tier != tier:
                continue
            if "generic" in record.taxa:
                generic.append(record.title)
            for taxon in record.taxa:
                if taxon == "generic":
                    continue
                accepted = self.aliases.get(taxon, (taxon,))
                if any(name.casefold() in clades for name in accepted):
                    titles_by_taxon.setdefault(taxon, set()).add(record.title)

        digest = hashlib.sha256(
            f"title-selection-v2:{self.catalog_version}:{game_id}:{tier}".encode()
        ).digest()
        if titles_by_taxon:
            applicable_taxa = sorted(titles_by_taxon)
            matched_taxon = applicable_taxa[
                int.from_bytes(digest[:8], "big") % len(applicable_taxa)
            ]
            matching_titles = sorted(titles_by_taxon[matched_taxon])
            title = matching_titles[
                int.from_bytes(digest[8:16], "big") % len(matching_titles)
            ]
        else:
            if not generic:  # pragma: no cover - catalog validation prevents this
                raise RankTitleError(f"rank tier {tier!r} has no selectable titles")
            matched_taxon = None
            generic.sort()
            title = generic[int.from_bytes(digest[:8], "big") % len(generic)]
        return AttainedTitle(tier, title, matched_taxon)


def score_tier(score: int, maximum: int) -> RankTier:
    """Map score loss to the four bands used by a default 45-point game."""
    if maximum <= 0:
        raise RankTitleError("maximum score must be positive")
    if score < 0 or score > maximum:
        raise RankTitleError("score must fall between zero and its maximum")
    lost = maximum - score
    if lost == 0:
        return "perfect"
    if lost <= 5:
        return "excellent"
    if lost <= 10:
        return "good"
    return "needs_improvement"


def _strings(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RankTitleError(f"{field} must be a list of strings")
    if any(not isinstance(item, str) for item in value):
        raise RankTitleError(f"{field} must be a list of strings")
    strings = tuple(value)
    if not strings or any(not item.strip() or item != item.strip() for item in strings):
        raise RankTitleError(f"{field} contains a blank or unnormalized value")
    if len(strings) != len(set(strings)):
        raise RankTitleError(f"{field} contains duplicate values")
    return strings


def load_rank_title_catalog(
    path: Path = DEFAULT_RANK_TITLES_PATH,
) -> RankTitleCatalog:
    """Read and validate the tracked gameplay rank-title catalog."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RankTitleError(f"cannot read rank-title catalog: {error}") from error
    except json.JSONDecodeError as error:
        raise RankTitleError(
            f"rank-title catalog is not valid JSON: {error}"
        ) from error
    if not isinstance(payload, Mapping):
        raise RankTitleError("rank-title catalog must be a JSON object")
    if payload.get("schema_version") != 1:
        raise RankTitleError("rank-title catalog has an unsupported schema version")
    try:
        catalog_version = int(payload["catalog_version"])
        aliases_payload = payload["taxon_clade_aliases"]
        titles_payload = payload["titles"]
    except (KeyError, TypeError, ValueError) as error:
        raise RankTitleError(f"invalid rank-title catalog: {error}") from error
    if catalog_version <= 0:
        raise RankTitleError("rank-title catalog version must be positive")
    if not isinstance(aliases_payload, Mapping):
        raise RankTitleError("taxon_clade_aliases must be an object")
    if not isinstance(titles_payload, Mapping):
        raise RankTitleError("titles must be an object")

    aliases: dict[str, tuple[str, ...]] = {}
    for taxon, clades in aliases_payload.items():
        name = str(taxon)
        if not name.strip() or name != name.strip():
            raise RankTitleError("taxon alias key is blank or unnormalized")
        aliases[name] = _strings(clades, field=f"alias {name!r}")

    titles: list[RankTitle] = []
    for title, record in titles_payload.items():
        name = str(title)
        if not name.strip() or name != name.strip():
            raise RankTitleError("title is blank or unnormalized")
        if not isinstance(record, Mapping):
            raise RankTitleError(f"title {name!r} must be an object")
        tiers = _strings(record.get("tiers"), field=f"title {name!r} tiers")
        if len(tiers) != 1 or tiers[0] not in RANK_TIERS:
            raise RankTitleError(f"title {name!r} has an unknown rank tier")
        taxa = _strings(record.get("taxa"), field=f"title {name!r} taxa")
        titles.append(RankTitle(name, tiers[0], taxa))  # type: ignore[arg-type]

    if not titles:
        raise RankTitleError("rank-title catalog is empty")
    for tier in RANK_TIERS:
        if not any(
            record.tier == tier and "generic" in record.taxa for record in titles
        ):
            raise RankTitleError(f"rank tier {tier!r} has no generic fallback")
    known_taxa = {taxon for record in titles for taxon in record.taxa}
    unknown_aliases = sorted(set(aliases) - known_taxa)
    if unknown_aliases:
        raise RankTitleError(
            f"aliases reference unknown taxa: {unknown_aliases[:5]!r}"
        )
    return RankTitleCatalog(1, catalog_version, aliases, tuple(titles))
