"""Maintain a reusable, incremental Wikimedia working-asset library.

The library is keyed by OneZoom species ID and scoped to one dataset version.
It remains ignored working storage: adding an asset here is not promotion into
the reviewed, tracked runtime bundle. Runtime code reads this local library but
never performs network requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import ssl
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from phylogenomica.data.onezoom_download import build_ssl_context, sha256_file
from phylogenomica.data.wikimedia import (
    WIKIMEDIA_MANIFEST_SCHEMA_VERSION,
    _atomic_json,
)
from phylogenomica.data.wikimedia_download import (
    DEFAULT_MAX_BYTES,
    SUPPORTED_MIME_TYPES,
    WIKIMEDIA_DOWNLOAD_MANIFEST_SCHEMA_VERSION,
    BinaryFetcher,
    Clock,
    WikimediaDownloadError,
    _atomic_bytes,
    _fetch_binary,
    _fetch_binary_curl,
    _image_details,
    _normalized_mime_type,
    _now,
    download_resolved_record,
)
from phylogenomica.data.wikimedia_rights import (
    WIKIMEDIA_RIGHTS_POLICY_VERSION,
    classify_rights,
)

WIKIMEDIA_LIBRARY_SCHEMA_VERSION = 1
WIKIMEDIA_LIBRARY_BUILDER_VERSION = 1
DEFAULT_LIBRARY_ROOT = Path("assets/processed/wikimedia-library")


class WikimediaLibraryError(RuntimeError):
    """Raised when a working media library cannot be safely read or updated."""


@dataclass(frozen=True)
class WikimediaAsset:
    """One validated local image and its player-facing rights information."""

    species_id: int
    path: Path
    mime_type: str
    sha256: str
    attribution_text: str
    license_name: str
    rights_url: str | None
    commons_page_url: str


@dataclass(frozen=True)
class WikimediaLibrary:
    """A validated, dataset-scoped index of local working images."""

    manifest_path: Path
    dataset_version: str
    assets: Mapping[int, WikimediaAsset]

    def asset(self, species_id: int) -> WikimediaAsset | None:
        return self.assets.get(species_id)


def _read_json_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WikimediaLibraryError(f"invalid {label}: {path}") from error
    if not isinstance(payload, Mapping):
        raise WikimediaLibraryError(f"{label} is not an object")
    return payload


def _source_kind(source: Mapping[str, Any]) -> str:
    if "downloader_version" in source:
        if (
            source.get("schema_version")
            != WIKIMEDIA_DOWNLOAD_MANIFEST_SCHEMA_VERSION
        ):
            raise WikimediaLibraryError("unsupported download manifest schema")
        return "download"
    if "resolver_version" in source:
        if source.get("schema_version") != WIKIMEDIA_MANIFEST_SCHEMA_VERSION:
            raise WikimediaLibraryError("unsupported resolver manifest schema")
        return "resolver"
    raise WikimediaLibraryError(
        "source must be a Wikimedia resolver or download manifest"
    )


def _records(source: Mapping[str, Any], label: str) -> Sequence[object]:
    records = source.get("records")
    if not isinstance(records, Sequence) or isinstance(records, str):
        raise WikimediaLibraryError(f"{label} has no records array")
    return records


def _source_fields(record: Mapping[str, Any], kind: str) -> dict[str, object]:
    if kind == "resolver":
        media = record.get("media")
        if not isinstance(media, Mapping):
            raise WikimediaLibraryError("resolved record has no media object")
        return {
            "species_id": record.get("species_id"),
            "scientific_name": record.get("scientific_name"),
            "wikidata_qid": record.get("wikidata_qid"),
            "commons_title": media.get("commons_title"),
            "commons_page_url": media.get("commons_page_url"),
            "original_url": media.get("original_url"),
            "thumbnail_url": media.get("thumbnail_url"),
            "mime_type": _normalized_mime_type(media.get("mime_type")),
            "source_sha1": media.get("source_sha1"),
            "creator": media.get("creator"),
            "credit": media.get("credit"),
            "license_name": media.get("license_name"),
            "license_url": media.get("license_url"),
            "usage_terms": media.get("usage_terms"),
        }
    return {
        "species_id": record.get("species_id"),
        "scientific_name": record.get("scientific_name"),
        "wikidata_qid": record.get("wikidata_qid"),
        "commons_title": record.get("commons_title"),
        "commons_page_url": record.get("commons_page_url"),
        "original_url": record.get("original_url"),
        "thumbnail_url": record.get("thumbnail_url"),
        "mime_type": _normalized_mime_type(record.get("mime_type")),
        "source_sha1": record.get("original_source_sha1"),
        "creator": record.get("creator"),
        "credit": record.get("credit"),
        "license_name": record.get("license_name"),
        "license_url": record.get("license_url"),
        "usage_terms": record.get("usage_terms"),
    }


def _source_fingerprint(record: Mapping[str, Any], kind: str) -> str:
    encoded = json.dumps(
        _source_fields(record, kind),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_local_path(base: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise WikimediaLibraryError("asset has no local path")
    base_resolved = base.resolve()
    path = (base / value).resolve()
    if not path.is_relative_to(base_resolved):
        raise WikimediaLibraryError(f"asset path escapes its library: {value}")
    return path


def _validate_record_file(
    record: Mapping[str, Any], base: Path, *, max_bytes: int | None = None
) -> Path:
    path = _safe_local_path(base, record.get("local_path"))
    try:
        size = path.stat().st_size
        data = path.read_bytes()
    except OSError as error:
        raise WikimediaLibraryError(f"cannot read media asset: {path}") from error
    expected_size = record.get("bytes")
    if not isinstance(expected_size, int) or size != expected_size or len(data) != size:
        raise WikimediaLibraryError(f"media byte count differs: {path}")
    if max_bytes is not None and size > max_bytes:
        raise WikimediaLibraryError(
            f"media asset exceeds the {max_bytes}-byte limit: {path}"
        )
    expected_sha256 = record.get("sha256")
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if not isinstance(expected_sha256, str) or actual_sha256 != expected_sha256:
        raise WikimediaLibraryError(f"media checksum differs: {path}")
    try:
        details = _image_details(data)
    except WikimediaDownloadError as error:
        raise WikimediaLibraryError(f"invalid media asset: {path}") from error
    if details.mime_type != _normalized_mime_type(record.get("mime_type")):
        raise WikimediaLibraryError(f"media type differs: {path}")
    if details.width != record.get("width") or details.height != record.get("height"):
        raise WikimediaLibraryError(f"media dimensions differ: {path}")
    return path


def _asset_from_record(record: Mapping[str, Any], base: Path) -> WikimediaAsset:
    species_id = record.get("species_id")
    rights = record.get("rights")
    if not isinstance(species_id, int) or species_id <= 0:
        raise WikimediaLibraryError("library record has an invalid species ID")
    if not isinstance(rights, Mapping) or not rights.get("working_use_allowed"):
        raise WikimediaLibraryError(
            f"library record {species_id} lacks working-use rights"
        )
    attribution = rights.get("attribution_text")
    license_name = rights.get("identifier")
    commons_page_url = record.get("commons_page_url")
    if not all(
        isinstance(value, str) and value
        for value in (attribution, license_name, commons_page_url)
    ):
        raise WikimediaLibraryError(
            f"library record {species_id} lacks attribution metadata"
        )
    rights_url = rights.get("rights_url")
    if rights_url is not None and not isinstance(rights_url, str):
        raise WikimediaLibraryError(
            f"library record {species_id} has an invalid rights URL"
        )
    mime_type = _normalized_mime_type(record.get("mime_type"))
    checksum = record.get("sha256")
    if mime_type not in SUPPORTED_MIME_TYPES or not isinstance(checksum, str):
        raise WikimediaLibraryError(
            f"library record {species_id} has invalid media metadata"
        )
    return WikimediaAsset(
        species_id=species_id,
        path=_validate_record_file(record, base),
        mime_type=mime_type,
        sha256=checksum,
        attribution_text=attribution,
        license_name=license_name,
        rights_url=rights_url,
        commons_page_url=commons_page_url,
    )


def load_wikimedia_library(
    manifest_path: Path, *, expected_dataset_version: str | None = None
) -> WikimediaLibrary:
    """Load and fully validate a local media library for runtime use."""
    manifest = _read_json_object(manifest_path, "Wikimedia library manifest")
    if manifest.get("schema_version") != WIKIMEDIA_LIBRARY_SCHEMA_VERSION:
        raise WikimediaLibraryError(
            f"unsupported library schema: {manifest.get('schema_version')!r}"
        )
    dataset_version = manifest.get("dataset_version")
    if not isinstance(dataset_version, str) or not dataset_version:
        raise WikimediaLibraryError("library has no dataset version")
    if (
        expected_dataset_version is not None
        and dataset_version != expected_dataset_version
    ):
        raise WikimediaLibraryError(
            f"library dataset {dataset_version!r} does not match game dataset "
            f"{expected_dataset_version!r}"
        )
    assets: dict[int, WikimediaAsset] = {}
    for raw_record in _records(manifest, "library manifest"):
        if not isinstance(raw_record, Mapping):
            raise WikimediaLibraryError("library record is not an object")
        asset = _asset_from_record(raw_record, manifest_path.parent)
        if asset.species_id in assets:
            raise WikimediaLibraryError(
                f"duplicate library species ID: {asset.species_id}"
            )
        assets[asset.species_id] = asset
    return WikimediaLibrary(manifest_path, dataset_version, assets)


def _read_existing_records(
    manifest_path: Path, dataset_version: str
) -> tuple[dict[int, dict[str, Any]], list[dict[str, object]], list[int]]:
    if not manifest_path.exists():
        return {}, [], []
    manifest = _read_json_object(manifest_path, "Wikimedia library manifest")
    if manifest.get("schema_version") != WIKIMEDIA_LIBRARY_SCHEMA_VERSION:
        raise WikimediaLibraryError("unsupported existing library schema")
    if manifest.get("dataset_version") != dataset_version:
        raise WikimediaLibraryError("existing library has a different dataset")
    existing: dict[int, dict[str, Any]] = {}
    invalid: list[int] = []
    for raw_record in _records(manifest, "existing library manifest"):
        if not isinstance(raw_record, Mapping):
            raise WikimediaLibraryError("existing library record is not an object")
        species_id = raw_record.get("species_id")
        if not isinstance(species_id, int) or species_id <= 0:
            raise WikimediaLibraryError("existing record has an invalid species ID")
        if species_id in existing:
            raise WikimediaLibraryError(f"duplicate species ID: {species_id}")
        try:
            _asset_from_record(raw_record, manifest_path.parent)
        except WikimediaLibraryError:
            invalid.append(species_id)
            continue
        existing[species_id] = dict(raw_record)
    raw_sources = manifest.get("source_manifests", [])
    sources = [
        dict(item)
        for item in raw_sources
        if isinstance(item, Mapping)
    ]
    return existing, sources, invalid


def _working_rights_candidate(record: Mapping[str, Any], kind: str) -> dict[str, Any]:
    fields = _source_fields(record, kind)
    return {
        **fields,
        "transformation": (
            "No local transformation; use the downloaded Wikimedia-generated "
            "rendition."
        ),
    }


def _library_record(
    downloaded: Mapping[str, Any],
    *,
    fingerprint: str,
    rights: Mapping[str, Any],
    source_kind: str,
    source_sha256: str,
) -> dict[str, Any]:
    mime_type = _normalized_mime_type(downloaded.get("mime_type"))
    checksum = downloaded.get("sha256")
    species_id = downloaded.get("species_id")
    if (
        mime_type not in SUPPORTED_MIME_TYPES
        or not isinstance(checksum, str)
        or not isinstance(species_id, int)
    ):
        raise WikimediaLibraryError("download has incomplete file identity")
    extension = SUPPORTED_MIME_TYPES[mime_type]
    relative_path = Path("files") / f"{species_id}-{checksum[:16]}{extension}"
    return {
        **downloaded,
        "local_path": relative_path.as_posix(),
        "source_fingerprint": fingerprint,
        "source_manifest": {
            "kind": source_kind,
            "sha256": source_sha256,
        },
        "rights_policy_version": WIKIMEDIA_RIGHTS_POLICY_VERSION,
        "rights": dict(rights),
    }


def _imported_record(
    record: Mapping[str, Any], source_base: Path, max_bytes: int
) -> tuple[dict[str, Any], bytes]:
    path = _validate_record_file(record, source_base, max_bytes=max_bytes)
    return dict(record), path.read_bytes()


def update_wikimedia_library(
    source_manifest_path: Path,
    *,
    library_root: Path = DEFAULT_LIBRARY_ROOT,
    ca_file: Path | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    limit: int | None = None,
    species_ids: Collection[int] | None = None,
    fetch_binary: BinaryFetcher = _fetch_binary,
    clock: Clock = _now,
    reproduction_command: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Merge one resolver or download manifest into a dataset media library."""
    if max_bytes <= 0:
        raise WikimediaLibraryError("max_bytes must be positive")
    if limit is not None and limit <= 0:
        raise WikimediaLibraryError("limit must be positive")
    requested_species_ids: set[int] | None = None
    if species_ids is not None:
        requested_species_ids = set(species_ids)
        if not requested_species_ids or any(
            not isinstance(species_id, int) or species_id <= 0
            for species_id in requested_species_ids
        ):
            raise WikimediaLibraryError("species selection is invalid")
    source = _read_json_object(source_manifest_path, "source manifest")
    kind = _source_kind(source)
    dataset_version = source.get("dataset_version")
    game_id = source.get("game_id")
    if not isinstance(dataset_version, str) or not dataset_version:
        raise WikimediaLibraryError("source manifest has no dataset version")
    if not isinstance(game_id, str) or not game_id:
        raise WikimediaLibraryError("source manifest has no game ID")

    destination = library_root / dataset_version
    manifest_path = destination / "manifest.json"
    existing, source_manifests, invalid_prior = _read_existing_records(
        manifest_path, dataset_version
    )
    raw_records = _records(source, "source manifest")
    if kind == "resolver":
        selected = [
            record
            for record in raw_records
            if isinstance(record, Mapping) and record.get("status") == "resolved"
        ]
    else:
        selected = [record for record in raw_records if isinstance(record, Mapping)]
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
        raise WikimediaLibraryError("source manifest has no eligible media records")

    context: ssl.SSLContext | None = None
    if kind == "resolver":
        context = build_ssl_context(ca_file)
    source_checksum = sha256_file(source_manifest_path)
    seen_species: set[int] = set()
    pending_files: list[tuple[Path, bytes]] = []
    counts: Counter[str] = Counter()
    blocked_species: list[int] = []
    for raw_record in selected:
        species_id = raw_record.get("species_id")
        if not isinstance(species_id, int) or species_id <= 0:
            raise WikimediaLibraryError("source record has an invalid species ID")
        if species_id in seen_species:
            raise WikimediaLibraryError(f"duplicate species ID: {species_id}")
        seen_species.add(species_id)
        fingerprint = _source_fingerprint(raw_record, kind)
        prior = existing.get(species_id)
        if prior is not None and prior.get("source_fingerprint") == fingerprint:
            counts["reused"] += 1
            continue

        preliminary_rights = classify_rights(
            _working_rights_candidate(raw_record, kind)
        )
        if not preliminary_rights["working_use_allowed"]:
            counts["blocked"] += 1
            blocked_species.append(species_id)
            continue
        try:
            if kind == "resolver":
                downloaded, body = download_resolved_record(
                    raw_record,
                    context=context,  # type: ignore[arg-type]
                    max_bytes=max_bytes,
                    fetch_binary=fetch_binary,
                    clock=clock,
                )
                action = "downloaded"
            else:
                downloaded, body = _imported_record(
                    raw_record, source_manifest_path.parent, max_bytes
                )
                action = "imported"
        except WikimediaDownloadError as error:
            raise WikimediaLibraryError(str(error)) from error
        rights = classify_rights(downloaded)
        if not rights["working_use_allowed"]:
            counts["blocked"] += 1
            blocked_species.append(species_id)
            continue
        library_record = _library_record(
            downloaded,
            fingerprint=fingerprint,
            rights=rights,
            source_kind=kind,
            source_sha256=source_checksum,
        )
        pending_files.append((destination / library_record["local_path"], body))
        existing[species_id] = library_record
        counts[action] += 1

    source_entry = {
        "kind": kind,
        "path": str(source_manifest_path),
        "sha256": source_checksum,
        "game_id": game_id,
    }
    source_manifests = [
        item for item in source_manifests if item.get("sha256") != source_checksum
    ]
    source_manifests.append(source_entry)
    source_manifests.sort(
        key=lambda item: (str(item.get("kind")), str(item.get("sha256")))
    )
    records = [existing[key] for key in sorted(existing)]
    rights_counts = Counter(
        str(record["rights"]["promotion_status"]) for record in records
    )
    unresolved = Counter(
        str(record.get("status"))
        for record in raw_records
        if kind == "resolver"
        and isinstance(record, Mapping)
        and record.get("status") != "resolved"
    )
    manifest: dict[str, Any] = {
        "schema_version": WIKIMEDIA_LIBRARY_SCHEMA_VERSION,
        "library_builder_version": WIKIMEDIA_LIBRARY_BUILDER_VERSION,
        "rights_policy_version": WIKIMEDIA_RIGHTS_POLICY_VERSION,
        "generated_at": clock().astimezone(UTC).isoformat(),
        "dataset_version": dataset_version,
        "source_manifests": source_manifests,
        "configuration": {"max_bytes": max_bytes},
        "record_count": len(records),
        "rights_status_counts": dict(sorted(rights_counts.items())),
        "records": records,
        "last_update": {
            "source_kind": kind,
            "game_id": game_id,
            "selected_count": len(selected),
            "downloaded_count": counts["downloaded"],
            "imported_count": counts["imported"],
            "reused_count": counts["reused"],
            "blocked_count": counts["blocked"],
            "blocked_species_ids": blocked_species,
            "unresolved_status_counts": dict(sorted(unresolved.items())),
            "invalidated_prior_species_ids": sorted(invalid_prior),
        },
        "review_status": "ignored-working-library-not-a-promoted-runtime-bundle",
        "reproduction_command": reproduction_command,
    }
    for path, body in pending_files:
        _atomic_bytes(path, body)
    _atomic_json(manifest_path, manifest)
    return manifest_path, manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Incrementally merge a Wikimedia resolver or download manifest into "
            "the ignored local media library."
        )
    )
    parser.add_argument("manifest", type=Path, help="resolver or download manifest")
    parser.add_argument("--library-root", type=Path, default=DEFAULT_LIBRARY_ROOT)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--transport",
        choices=("urllib", "curl"),
        default="urllib",
        help="verified HTTPS client used only for resolver-manifest cache misses",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    command_parts = [
        "phylogenomica-update-wikimedia-library",
        str(args.manifest),
        "--library-root",
        str(args.library_root),
        "--max-bytes",
        str(args.max_bytes),
        "--transport",
        args.transport,
    ]
    if args.ca_file is not None:
        command_parts.extend(("--ca-file", str(args.ca_file)))
    if args.limit is not None:
        command_parts.extend(("--limit", str(args.limit)))
    try:
        manifest_path, manifest = update_wikimedia_library(
            args.manifest,
            library_root=args.library_root,
            ca_file=args.ca_file,
            max_bytes=args.max_bytes,
            limit=args.limit,
            fetch_binary=(
                _fetch_binary_curl if args.transport == "curl" else _fetch_binary
            ),
            reproduction_command=shlex.join(command_parts),
        )
    except WikimediaLibraryError as error:
        raise SystemExit(str(error)) from error
    update = manifest["last_update"]
    print(
        f"wrote {manifest_path} ({manifest['record_count']} total; "
        f"downloaded={update['downloaded_count']}, "
        f"imported={update['imported_count']}, reused={update['reused_count']}, "
        f"blocked={update['blocked_count']})"
    )


if __name__ == "__main__":
    main()
