"""Evaluate Wikimedia working assets against the project's rights policy."""

from __future__ import annotations

import argparse
import json
import re
import shlex
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from phylogenomica.data.onezoom_download import sha256_file
from phylogenomica.data.wikimedia import _atomic_json
from phylogenomica.data.wikimedia_download import (
    WIKIMEDIA_DOWNLOAD_MANIFEST_SCHEMA_VERSION,
)

WIKIMEDIA_RIGHTS_POLICY_VERSION = 1
WIKIMEDIA_RIGHTS_MANIFEST_SCHEMA_VERSION = 1

PromotionStatus = Literal["ready", "conditional", "blocked"]


class WikimediaRightsError(RuntimeError):
    """Raised when media rights cannot be classified reproducibly."""


@dataclass(frozen=True)
class RightsRule:
    identifier: str
    category: str
    canonical_url: str | None
    promotion_status: PromotionStatus
    requires_attribution: bool
    requires_share_alike: bool
    requirements: tuple[str, ...]
    rationale: str


def _cc_by(version: str, *, port: str | None = None) -> RightsRule:
    suffix = f"-{port.upper()}" if port is not None else ""
    path = f"{version}/{port}/" if port is not None else f"{version}/"
    return RightsRule(
        identifier=f"CC-BY-{version}{suffix}",
        category="standard-open-license",
        canonical_url=f"https://creativecommons.org/licenses/by/{path}",
        promotion_status="ready",
        requires_attribution=True,
        requires_share_alike=False,
        requirements=(
            "Credit the supplied creator or attribution party.",
            "Link the Commons source and exact Creative Commons license.",
            "Indicate cropping, resizing, or other modifications.",
        ),
        rationale="Standard Creative Commons attribution license.",
    )


def _cc_by_sa(version: str, *, port: str | None = None) -> RightsRule:
    suffix = f"-{port.upper()}" if port is not None else ""
    path = f"{version}/{port}/" if port is not None else f"{version}/"
    return RightsRule(
        identifier=f"CC-BY-SA-{version}{suffix}",
        category="standard-open-license",
        canonical_url=f"https://creativecommons.org/licenses/by-sa/{path}",
        promotion_status="ready",
        requires_attribution=True,
        requires_share_alike=True,
        requirements=(
            "Credit the supplied creator or attribution party.",
            "Link the Commons source and exact Creative Commons license.",
            "Indicate cropping, resizing, or other modifications.",
            "Distribute copyrightable adaptations under the same or a "
            "compatible ShareAlike license.",
        ),
        rationale="Standard Creative Commons attribution-share-alike license.",
    )


STANDARD_RULES = {
    "CC BY 2.0": _cc_by("2.0"),
    "CC BY 2.5": _cc_by("2.5"),
    "CC BY 3.0": _cc_by("3.0"),
    "CC BY 4.0": _cc_by("4.0"),
    "CC BY-SA 2.0": _cc_by_sa("2.0"),
    "CC BY-SA 2.0 de": _cc_by_sa("2.0", port="de"),
    "CC BY-SA 2.5": _cc_by_sa("2.5"),
    "CC BY-SA 3.0": _cc_by_sa("3.0"),
    "CC BY-SA 4.0": _cc_by_sa("4.0"),
    "CC0": RightsRule(
        identifier="CC0-1.0",
        category="public-domain-dedication",
        canonical_url="https://creativecommons.org/publicdomain/zero/1.0/",
        promotion_status="ready",
        requires_attribution=False,
        requires_share_alike=False,
        requirements=(
            "Preserve creator and source as project provenance even though "
            "CC0 does not require attribution.",
            "Indicate cropping, resizing, or other modifications.",
        ),
        rationale="Standard worldwide public-domain dedication and fallback.",
    ),
}

# Commons labels jurisdiction ports as `CC BY 3.0 us` or `CC BY-SA 2.0 fr`.
# These are the same licences carrying the same obligations under a national
# port, so they are parsed rather than enumerated: every version-and-port pair
# would make the table long enough to keep leaving gaps in.
_PORTED_CC_LABEL = re.compile(
    r"^CC (?P<kind>BY|BY-SA) (?P<version>1\.0|2\.0|2\.5|3\.0|4\.0)"
    r"(?: (?P<port>[a-z]{2,3}))?$"
)


def _parsed_cc_rule(label: object) -> RightsRule | None:
    """Return a rule for a standard Creative Commons label, ported or not."""
    if not isinstance(label, str):
        return None
    match = _PORTED_CC_LABEL.match(label.strip())
    if match is None:
        return None
    builder = _cc_by if match["kind"] == "BY" else _cc_by_sa
    return builder(match["version"], port=match["port"])


CONDITIONAL_RULES = {
    "Public domain": RightsRule(
        identifier="Public-Domain-Claim",
        category="public-domain-claim",
        canonical_url=None,
        promotion_status="conditional",
        requires_attribution=False,
        requires_share_alike=False,
        requirements=(
            "Capture the exact Commons public-domain tag and its rationale.",
            "Record applicable source-country and United States evidence.",
            "Preserve creator, institutional credit, and Commons source.",
        ),
        rationale=(
            "The short label is usable for working review but does not identify "
            "the file-specific public-domain basis or jurisdiction."
        ),
    ),
    "GFDL 1.2": RightsRule(
        identifier="GFDL-1.2-only",
        category="legacy-free-license",
        canonical_url="https://www.gnu.org/licenses/old-licenses/fdl-1.2.html",
        promotion_status="conditional",
        requires_attribution=True,
        requires_share_alike=True,
        requirements=(
            "Credit the supplied creator and Commons source.",
            "Include an accessible unaltered copy or full link to GFDL 1.2.",
            "Keep this image and distributed derivatives under GFDL 1.2.",
            "Confirm any file-specific license notice before promotion.",
        ),
        rationale=(
            "Free redistribution is permitted, but compliance is more burdensome "
            "than the standard Creative Commons path."
        ),
    ),
    "Copyrighted free use": RightsRule(
        identifier="Copyrighted-Free-Use",
        category="nonstandard-permission",
        canonical_url=None,
        promotion_status="conditional",
        requires_attribution=False,
        requires_share_alike=False,
        requirements=(
            "Preserve the complete Commons permission statement and review record.",
            "Capture a stable copy of the cited original-source permission.",
            "Preserve creator, credit, and Commons source.",
        ),
        rationale=(
            "Commons reports unrestricted permission, but there is no standardized "
            "license deed in the downloaded metadata."
        ),
    ),
    "No restrictions": RightsRule(
        identifier="No-Known-Copyright-Restrictions",
        category="rights-risk-statement",
        canonical_url="https://www.flickr.com/commons/usage/",
        promotion_status="conditional",
        requires_attribution=False,
        requires_share_alike=False,
        requirements=(
            "Preserve the institution, original source, and Commons source.",
            "Capture the item-specific rights statement and historical evidence.",
            "Record that the statement is not a warranty of public-domain status.",
        ),
        rationale=(
            "No-known-restrictions is a risk statement rather than a copyright "
            "license or worldwide public-domain determination."
        ),
    ),
}


def _now() -> datetime:
    return datetime.now(UTC)


def _load_manifest(path: Path) -> Mapping[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WikimediaRightsError(f"invalid download manifest: {path}") from error
    if not isinstance(manifest, Mapping):
        raise WikimediaRightsError("download manifest is not an object")
    if (
        manifest.get("schema_version")
        != WIKIMEDIA_DOWNLOAD_MANIFEST_SCHEMA_VERSION
    ):
        raise WikimediaRightsError(
            f"unsupported download manifest schema: {manifest.get('schema_version')!r}"
        )
    records = manifest.get("records")
    if not isinstance(records, Sequence) or isinstance(records, str):
        raise WikimediaRightsError("download manifest has no records array")
    return manifest


def _blocked_rule(label: object, reason: str) -> RightsRule:
    rendered = str(label) if label is not None else "missing"
    return RightsRule(
        identifier=f"Unrecognized:{rendered}",
        category="unrecognized-rights",
        canonical_url=None,
        promotion_status="blocked",
        requires_attribution=True,
        requires_share_alike=False,
        requirements=("Resolve and document a recognized rights basis.",),
        rationale=reason,
    )


def classify_rights(record: Mapping[str, Any]) -> dict[str, Any]:
    """Classify one downloaded media record under policy version 1."""
    label = record.get("license_name")
    rule = None
    if isinstance(label, str):
        rule = (
            STANDARD_RULES.get(label)
            or CONDITIONAL_RULES.get(label)
            or _parsed_cc_rule(label)
        )
    if rule is None:
        rule = _blocked_rule(label, "The license label is not in the reviewed policy.")
    creator = record.get("creator") or record.get("credit")
    source = record.get("commons_page_url")
    status = rule.promotion_status
    extra_requirements: list[str] = []
    if not isinstance(source, str) or not source:
        status = "blocked"
        extra_requirements.append("Restore the canonical Commons source URL.")
    if rule.requires_attribution and (
        not isinstance(creator, str) or not creator.strip()
    ):
        status = "blocked"
        extra_requirements.append("Restore the required creator or credit.")
    title = record.get("commons_title")
    rendered_title = title if isinstance(title, str) and title else "Untitled image"
    rendered_creator = (
        creator if isinstance(creator, str) and creator else "creator not supplied"
    )
    rights_url = rule.canonical_url or source
    attribution = (
        f'"{rendered_title}" by {rendered_creator}; source: Wikimedia Commons; '
        f"rights: {rule.identifier}."
    )
    transformation = record.get("transformation")
    if isinstance(transformation, str) and transformation:
        attribution = f"{attribution} {transformation}"
    return {
        "identifier": rule.identifier,
        "source_label": label,
        "category": rule.category,
        "rights_url": rights_url,
        "source_rights_url": record.get("license_url"),
        "promotion_status": status,
        "working_use_allowed": status != "blocked",
        "promotion_ready": status == "ready",
        "requires_attribution": rule.requires_attribution,
        "requires_share_alike": rule.requires_share_alike,
        "requirements": [*rule.requirements, *extra_requirements],
        "rationale": rule.rationale,
        "suggested_review_decision": (
            "accept"
            if status == "ready"
            else "conditional"
            if status == "conditional"
            else "pending"
        ),
        "attribution_text": attribution,
    }


def evaluate_wikimedia_rights(
    download_manifest_path: Path,
    *,
    output: Path | None = None,
    clock: Callable[[], datetime] = _now,
    reproduction_command: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Evaluate every downloaded record and write a pinned rights manifest."""
    source = _load_manifest(download_manifest_path)
    dataset_version = source.get("dataset_version")
    game_id = source.get("game_id")
    if not isinstance(dataset_version, str) or not isinstance(game_id, str):
        raise WikimediaRightsError("download manifest lacks dataset or game identity")
    evaluated: list[dict[str, Any]] = []
    seen_species: set[int] = set()
    for raw_record in source["records"]:
        if not isinstance(raw_record, Mapping):
            raise WikimediaRightsError("download record is not an object")
        species_id = raw_record.get("species_id")
        if not isinstance(species_id, int) or species_id <= 0:
            raise WikimediaRightsError("download record has an invalid species ID")
        if species_id in seen_species:
            raise WikimediaRightsError(f"duplicate species ID: {species_id}")
        seen_species.add(species_id)
        evaluated.append(
            {
                "species_id": species_id,
                "scientific_name": raw_record.get("scientific_name"),
                "commons_title": raw_record.get("commons_title"),
                "sha256": raw_record.get("sha256"),
                **classify_rights(raw_record),
            }
        )
    status_counts = Counter(record["promotion_status"] for record in evaluated)
    identifier_counts = Counter(record["identifier"] for record in evaluated)
    manifest: dict[str, Any] = {
        "schema_version": WIKIMEDIA_RIGHTS_MANIFEST_SCHEMA_VERSION,
        "rights_policy_version": WIKIMEDIA_RIGHTS_POLICY_VERSION,
        "generated_at": clock().astimezone(UTC).isoformat(),
        "dataset_version": dataset_version,
        "game_id": game_id,
        "source": {
            "download_manifest": str(download_manifest_path),
            "download_manifest_sha256": sha256_file(download_manifest_path),
        },
        "policy": {
            "working_use": (
                "Allow every recognized standard or conditional rights claim in "
                "ignored local prototype assets."
            ),
            "promotion": (
                "Promote standard open licenses immediately; retain conditional "
                "claims only after their listed evidence or packaging requirements."
            ),
            "commercial_intent": False,
            "commercial_intent_note": (
                "Current noncommercial intent does not waive license conditions."
            ),
        },
        "status_counts": dict(sorted(status_counts.items())),
        "identifier_counts": dict(sorted(identifier_counts.items())),
        "records": evaluated,
        "reproduction_command": reproduction_command,
    }
    output_path = output or download_manifest_path.with_name("rights.json")
    _atomic_json(output_path, manifest)
    return output_path, manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate a Wikimedia download manifest under the rights policy."
    )
    parser.add_argument("manifest", type=Path, help="download manifest")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    command = ["phylogenomica-evaluate-wikimedia-rights", str(args.manifest)]
    if args.output is not None:
        command.extend(("--output", str(args.output)))
    try:
        output, manifest = evaluate_wikimedia_rights(
            args.manifest,
            output=args.output,
            reproduction_command=shlex.join(command),
        )
    except WikimediaRightsError as error:
        raise SystemExit(str(error)) from error
    counts = ", ".join(
        f"{status}={count}" for status, count in manifest["status_counts"].items()
    )
    print(f"wrote {output} ({counts})")


if __name__ == "__main__":
    main()
