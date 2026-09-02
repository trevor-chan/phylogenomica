"""Maintain a reusable, incremental Wikipedia description library.

The library is keyed by OneZoom species ID and scoped to one dataset version,
exactly like the media library. Text is small, so records hold their extract
inline rather than pointing at files on disk.

It remains ignored working storage: an entry here is not promotion into the
reviewed, tracked runtime bundle. Runtime code reads this local library but
never performs network requests.
"""

from __future__ import annotations

import argparse
import json
import shlex
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from phylogenomica.data.onezoom_download import sha256_file
from phylogenomica.data.wikimedia import Clock, _atomic_json, _now
from phylogenomica.data.wikipedia import (
    TEXT_LICENSE_NAME,
    TEXT_LICENSE_URL,
    WIKIPEDIA_MANIFEST_SCHEMA_VERSION,
)

WIKIPEDIA_LIBRARY_SCHEMA_VERSION = 1
WIKIPEDIA_LIBRARY_BUILDER_VERSION = 1
DEFAULT_LIBRARY_ROOT = Path("assets/processed/wikipedia-library")


class WikipediaLibraryError(RuntimeError):
    """Raised when a description library cannot be safely read or updated."""


@dataclass(frozen=True)
class WikipediaDescription:
    """One species' lead-section text and the attribution it must carry."""

    species_id: int
    title: str
    url: str | None
    extract: str
    truncated: bool
    revision_id: int | None
    license_name: str
    license_url: str
    attribution_text: str


@dataclass(frozen=True)
class WikipediaLibrary:
    """A validated, dataset-scoped index of local species descriptions."""

    manifest_path: Path
    dataset_version: str
    descriptions: Mapping[int, WikipediaDescription]

    def description(self, species_id: int) -> WikipediaDescription | None:
        return self.descriptions.get(species_id)


def _read_json_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WikipediaLibraryError(f"invalid {label}: {path}") from error
    if not isinstance(payload, Mapping):
        raise WikipediaLibraryError(f"{label} is not an object")
    return payload


def _records(source: Mapping[str, Any], label: str) -> Sequence[object]:
    records = source.get("records")
    if not isinstance(records, Sequence) or isinstance(records, str):
        raise WikipediaLibraryError(f"{label} has no records array")
    return records


def _description_from_record(record: Mapping[str, Any]) -> WikipediaDescription:
    species_id = record.get("species_id")
    if not isinstance(species_id, int) or species_id <= 0:
        raise WikipediaLibraryError("library record has an invalid species ID")
    title = record.get("title")
    extract = record.get("extract")
    attribution = record.get("attribution_text")
    license_name = record.get("license_name")
    license_url = record.get("license_url")
    if not all(
        isinstance(value, str) and value
        for value in (title, extract, attribution, license_name, license_url)
    ):
        raise WikipediaLibraryError(
            f"library record {species_id} lacks text or attribution metadata"
        )
    url = record.get("url")
    if url is not None and not isinstance(url, str):
        raise WikipediaLibraryError(
            f"library record {species_id} has an invalid article URL"
        )
    revision_id = record.get("revision_id")
    if revision_id is not None and not isinstance(revision_id, int):
        raise WikipediaLibraryError(
            f"library record {species_id} has an invalid revision ID"
        )
    return WikipediaDescription(
        species_id=species_id,
        title=str(title),
        url=url,
        extract=str(extract),
        truncated=bool(record.get("extract_truncated")),
        revision_id=revision_id,
        license_name=str(license_name),
        license_url=str(license_url),
        attribution_text=str(attribution),
    )


def load_wikipedia_library(
    manifest_path: Path, *, expected_dataset_version: str | None = None
) -> WikipediaLibrary:
    """Load and fully validate a local description library for runtime use."""
    manifest = _read_json_object(manifest_path, "Wikipedia library manifest")
    if manifest.get("schema_version") != WIKIPEDIA_LIBRARY_SCHEMA_VERSION:
        raise WikipediaLibraryError(
            f"unsupported library schema: {manifest.get('schema_version')!r}"
        )
    dataset_version = manifest.get("dataset_version")
    if not isinstance(dataset_version, str) or not dataset_version:
        raise WikipediaLibraryError("library has no dataset version")
    if (
        expected_dataset_version is not None
        and dataset_version != expected_dataset_version
    ):
        raise WikipediaLibraryError(
            f"library dataset {dataset_version!r} does not match game dataset "
            f"{expected_dataset_version!r}"
        )
    descriptions: dict[int, WikipediaDescription] = {}
    for raw_record in _records(manifest, "library manifest"):
        if not isinstance(raw_record, Mapping):
            raise WikipediaLibraryError("library record is not an object")
        description = _description_from_record(raw_record)
        if description.species_id in descriptions:
            raise WikipediaLibraryError(
                f"duplicate library species ID: {description.species_id}"
            )
        descriptions[description.species_id] = description
    return WikipediaLibrary(manifest_path, dataset_version, descriptions)


def _read_existing_records(
    manifest_path: Path, dataset_version: str
) -> tuple[dict[int, dict[str, Any]], list[dict[str, object]], list[int]]:
    if not manifest_path.exists():
        return {}, [], []
    manifest = _read_json_object(manifest_path, "Wikipedia library manifest")
    if manifest.get("schema_version") != WIKIPEDIA_LIBRARY_SCHEMA_VERSION:
        raise WikipediaLibraryError("unsupported existing library schema")
    if manifest.get("dataset_version") != dataset_version:
        raise WikipediaLibraryError("existing library has a different dataset")
    existing: dict[int, dict[str, Any]] = {}
    invalid: list[int] = []
    for raw_record in _records(manifest, "existing library manifest"):
        if not isinstance(raw_record, Mapping):
            raise WikipediaLibraryError("library record is not an object")
        species_id = raw_record.get("species_id")
        if not isinstance(species_id, int) or species_id <= 0:
            raise WikipediaLibraryError("library record has an invalid species ID")
        if species_id in existing:
            raise WikipediaLibraryError(f"duplicate species ID: {species_id}")
        try:
            _description_from_record(raw_record)
        except WikipediaLibraryError:
            invalid.append(species_id)
            continue
        existing[species_id] = dict(raw_record)
    raw_sources = manifest.get("source_manifests", [])
    sources = [dict(item) for item in raw_sources if isinstance(item, Mapping)]
    return existing, sources, invalid


def _source_fingerprint(record: Mapping[str, Any]) -> str:
    """Identify the resolved text a library record was built from.

    A new revision of the same article is a new fingerprint, so refreshing the
    resolver replaces stale prose instead of silently reusing it.
    """
    text = record.get("text")
    if not isinstance(text, Mapping):
        raise WikipediaLibraryError("resolved record has no text object")
    return "|".join(
        str(text.get(key)) for key in ("title", "revision_id", "extract_truncated")
    )


def _library_record(
    record: Mapping[str, Any],
    *,
    fingerprint: str,
    source_sha256: str,
) -> dict[str, Any]:
    text = record.get("text")
    if not isinstance(text, Mapping):
        raise WikipediaLibraryError("resolved record has no text object")
    return {
        "species_id": record.get("species_id"),
        "scientific_name": record.get("scientific_name"),
        "wikidata_qid": record.get("wikidata_qid"),
        "title": text.get("title"),
        "page_id": text.get("page_id"),
        "revision_id": text.get("revision_id"),
        "url": text.get("url"),
        "extract": text.get("extract"),
        "extract_truncated": bool(text.get("extract_truncated")),
        "license_name": text.get("license_name") or TEXT_LICENSE_NAME,
        "license_url": text.get("license_url") or TEXT_LICENSE_URL,
        "attribution_text": text.get("attribution_text"),
        "source_fingerprint": fingerprint,
        "source_manifest": {"kind": "resolver", "sha256": source_sha256},
    }


def update_wikipedia_library(
    source_manifest_path: Path,
    *,
    library_root: Path = DEFAULT_LIBRARY_ROOT,
    limit: int | None = None,
    species_ids: Collection[int] | None = None,
    clock: Clock = _now,
    reproduction_command: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Merge one Wikipedia resolver manifest into a dataset description library."""
    if limit is not None and limit <= 0:
        raise WikipediaLibraryError("limit must be positive")
    requested_species_ids: set[int] | None = None
    if species_ids is not None:
        requested_species_ids = set(species_ids)
        if not requested_species_ids or any(
            not isinstance(species_id, int) or species_id <= 0
            for species_id in requested_species_ids
        ):
            raise WikipediaLibraryError("species selection is invalid")
    source = _read_json_object(source_manifest_path, "source manifest")
    if "resolver_version" not in source:
        raise WikipediaLibraryError("source must be a Wikipedia resolver manifest")
    if source.get("schema_version") != WIKIPEDIA_MANIFEST_SCHEMA_VERSION:
        raise WikipediaLibraryError("unsupported resolver manifest schema")
    dataset_version = source.get("dataset_version")
    game_id = source.get("game_id")
    if not isinstance(dataset_version, str) or not dataset_version:
        raise WikipediaLibraryError("source manifest has no dataset version")
    if not isinstance(game_id, str) or not game_id:
        raise WikipediaLibraryError("source manifest has no game ID")

    destination = library_root / dataset_version
    manifest_path = destination / "manifest.json"
    existing, source_manifests, invalid_prior = _read_existing_records(
        manifest_path, dataset_version
    )
    raw_records = _records(source, "source manifest")
    selected = [
        record
        for record in raw_records
        if isinstance(record, Mapping) and record.get("status") == "resolved"
    ]
    if requested_species_ids is not None:
        selected = [
            record
            for record in selected
            if record.get("species_id") in requested_species_ids
        ]
    selected.sort(key=lambda record: int(record.get("species_id", 0)))
    if limit is not None:
        selected = selected[:limit]
    if not selected:
        raise WikipediaLibraryError("source manifest has no resolved descriptions")

    source_checksum = sha256_file(source_manifest_path)
    seen_species: set[int] = set()
    counts: Counter[str] = Counter()
    for raw_record in selected:
        species_id = raw_record.get("species_id")
        if not isinstance(species_id, int) or species_id <= 0:
            raise WikipediaLibraryError("source record has an invalid species ID")
        if species_id in seen_species:
            raise WikipediaLibraryError(f"duplicate species ID: {species_id}")
        seen_species.add(species_id)
        fingerprint = _source_fingerprint(raw_record)
        prior = existing.get(species_id)
        if prior is not None and prior.get("source_fingerprint") == fingerprint:
            counts["reused"] += 1
            continue
        record = _library_record(
            raw_record, fingerprint=fingerprint, source_sha256=source_checksum
        )
        # Validate before storing: an unreadable record would otherwise be
        # written now and dropped at load time.
        _description_from_record(record)
        existing[species_id] = record
        counts["imported"] += 1

    source_entry = {
        "kind": "resolver",
        "path": str(source_manifest_path),
        "sha256": source_checksum,
        "game_id": game_id,
    }
    source_manifests = [
        item for item in source_manifests if item.get("sha256") != source_checksum
    ]
    source_manifests.append(source_entry)
    source_manifests.sort(key=lambda item: str(item.get("sha256")))
    records = [existing[key] for key in sorted(existing)]
    unresolved = Counter(
        str(record.get("status"))
        for record in raw_records
        if isinstance(record, Mapping) and record.get("status") != "resolved"
    )
    manifest: dict[str, Any] = {
        "schema_version": WIKIPEDIA_LIBRARY_SCHEMA_VERSION,
        "library_builder_version": WIKIPEDIA_LIBRARY_BUILDER_VERSION,
        "generated_at": clock().astimezone(UTC).isoformat(),
        "dataset_version": dataset_version,
        "source_manifests": source_manifests,
        "text_license": {"name": TEXT_LICENSE_NAME, "url": TEXT_LICENSE_URL},
        "record_count": len(records),
        "records": records,
        "last_update": {
            "game_id": game_id,
            "selected_count": len(selected),
            "imported_count": counts["imported"],
            "reused_count": counts["reused"],
            "unresolved_status_counts": dict(sorted(unresolved.items())),
            "invalidated_prior_species_ids": sorted(invalid_prior),
        },
        "review_status": "ignored-working-library-not-a-promoted-runtime-bundle",
        "reproduction_command": reproduction_command,
    }
    _atomic_json(manifest_path, manifest)
    return manifest_path, manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Incrementally merge a Wikipedia resolver manifest into the ignored "
            "local description library."
        )
    )
    parser.add_argument("manifest", type=Path, help="Wikipedia resolver manifest")
    parser.add_argument("--library-root", type=Path, default=DEFAULT_LIBRARY_ROOT)
    parser.add_argument("--limit", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    command_parts = [
        "phylogenomica-update-wikipedia-library",
        str(args.manifest),
        "--library-root",
        str(args.library_root),
    ]
    if args.limit is not None:
        command_parts.extend(("--limit", str(args.limit)))
    try:
        manifest_path, manifest = update_wikipedia_library(
            args.manifest,
            library_root=args.library_root,
            limit=args.limit,
            reproduction_command=shlex.join(command_parts),
        )
    except WikipediaLibraryError as error:
        raise SystemExit(str(error)) from error
    update = manifest["last_update"]
    print(
        f"wrote {manifest_path} ({manifest['record_count']} total; "
        f"imported={update['imported_count']}, reused={update['reused_count']})"
    )


if __name__ == "__main__":
    main()
