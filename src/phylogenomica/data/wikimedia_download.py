"""Download and validate resolved Wikimedia candidates into working assets.

This is an offline bundle-building step, not a runtime media client. Only
records marked ``resolved`` by the metadata resolver are eligible, and the
result remains an ignored working copy until visual and license review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import ssl
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from phylogenomica.data.onezoom_download import build_ssl_context, sha256_file
from phylogenomica.data.wikimedia import (
    USER_AGENT,
    WIKIMEDIA_MANIFEST_SCHEMA_VERSION,
    _absolute_http_url,
    _atomic_json,
)

WIKIMEDIA_DOWNLOADER_VERSION = 1
WIKIMEDIA_DOWNLOAD_MANIFEST_SCHEMA_VERSION = 1
DEFAULT_OUTPUT_ROOT = Path("assets/processed/wikimedia")
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
SUPPORTED_MIME_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
}


class WikimediaDownloadError(RuntimeError):
    """Raised when a candidate cannot be downloaded or validated safely."""


@dataclass(frozen=True)
class BinaryResponse:
    body: bytes
    content_type: str | None
    final_url: str


@dataclass(frozen=True)
class ImageDetails:
    mime_type: str
    width: int
    height: int


BinaryFetcher = Callable[[str, ssl.SSLContext, int], BinaryResponse]
Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


def _normalized_mime_type(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    rendered = value.partition(";")[0].strip().casefold()
    if rendered == "image/jpg":
        return "image/jpeg"
    return rendered or None


def _png_details(data: bytes) -> ImageDetails | None:
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    if data[12:16] != b"IHDR":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        return None
    return ImageDetails("image/png", width, height)


def _jpeg_details(data: bytes) -> ImageDetails | None:
    if len(data) < 4 or not data.startswith(b"\xff\xd8\xff"):
        return None
    offset = 2
    start_of_frame = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while offset < len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0x01, *range(0xD0, 0xDA)}:
            continue
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(data):
            break
        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            break
        if marker in start_of_frame:
            if segment_length < 7:
                break
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            if width > 0 and height > 0:
                return ImageDetails("image/jpeg", width, height)
            break
        offset += segment_length
    return None


def _gif_details(data: bytes) -> ImageDetails | None:
    """Read a GIF logical screen descriptor.

    Commons serves GIF sources as GIF even at thumbnail sizes rather than
    transcoding them, so the format has to be read here to be usable at all.
    """
    if len(data) < 10 or data[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    width = int.from_bytes(data[6:8], "little")
    height = int.from_bytes(data[8:10], "little")
    if width <= 0 or height <= 0:
        return None
    return ImageDetails("image/gif", width, height)


def _image_details(data: bytes) -> ImageDetails:
    details = _png_details(data) or _jpeg_details(data) or _gif_details(data)
    if details is None:
        raise WikimediaDownloadError(
            "downloaded bytes are not a supported PNG, JPEG, or GIF image"
        )
    return details


def _fetch_binary(
    url: str, context: ssl.SSLContext, max_bytes: int
) -> BinaryResponse:
    request = Request(url, headers={"Accept": "image/*", "User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=60, context=context) as response:  # noqa: S310
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > max_bytes:
                raise WikimediaDownloadError(
                    f"response exceeds the {max_bytes}-byte limit"
                )
            body = response.read(max_bytes + 1)
            content_type = response.headers.get("Content-Type")
            final_url = response.geturl()
    except (OSError, ValueError) as error:
        raise WikimediaDownloadError(f"failed to download {url}: {error}") from error
    if len(body) > max_bytes:
        raise WikimediaDownloadError(f"response exceeds the {max_bytes}-byte limit")
    return BinaryResponse(body, content_type, final_url)


def _fetch_binary_curl(
    url: str, _context: ssl.SSLContext, max_bytes: int
) -> BinaryResponse:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="phylogenomica-wikimedia-") as output:
            temporary_path = Path(output.name)
            command = [
                "curl",
                "-fsSL",
                "--connect-timeout",
                "15",
                "--max-time",
                "90",
                "--retry",
                "2",
                "--retry-all-errors",
                "--max-filesize",
                str(max_bytes),
                "--header",
                "Accept: image/*",
                "--user-agent",
                USER_AGENT,
                "--output",
                str(temporary_path),
                "--write-out",
                "%{content_type}\n%{url_effective}\n",
                url,
            ]
            completed = subprocess.run(
                command, check=False, capture_output=True, text=True, timeout=105
            )
            if completed.returncode:
                detail = completed.stderr.strip() or (
                    f"exit status {completed.returncode}"
                )
                raise WikimediaDownloadError(f"curl failed for {url}: {detail}")
            body = temporary_path.read_bytes()
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WikimediaDownloadError(
            f"failed to run curl for {url}: {error}"
        ) from error
    lines = completed.stdout.splitlines()
    if len(lines) < 2:
        raise WikimediaDownloadError(f"curl returned incomplete metadata for {url}")
    if len(body) > max_bytes:
        raise WikimediaDownloadError(f"response exceeds the {max_bytes}-byte limit")
    return BinaryResponse(body, lines[0] or None, lines[1])


def _read_resolver_manifest(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WikimediaDownloadError(f"invalid resolver manifest: {path}") from error
    if not isinstance(payload, Mapping):
        raise WikimediaDownloadError("resolver manifest is not an object")
    if payload.get("schema_version") != WIKIMEDIA_MANIFEST_SCHEMA_VERSION:
        raise WikimediaDownloadError(
            f"unsupported resolver manifest schema: {payload.get('schema_version')!r}"
        )
    if not isinstance(payload.get("records"), Sequence) or isinstance(
        payload.get("records"), str
    ):
        raise WikimediaDownloadError("resolver manifest has no records array")
    return payload


def _download_source(record: Mapping[str, Any]) -> tuple[str, str]:
    media = record.get("media")
    if not isinstance(media, Mapping):
        raise WikimediaDownloadError("resolved record has no media object")
    thumbnail_url = _absolute_http_url(media.get("thumbnail_url"))
    original_url = _absolute_http_url(media.get("original_url"))
    if thumbnail_url is not None:
        return thumbnail_url, "thumbnail"
    if original_url is not None:
        return original_url, "original"
    raise WikimediaDownloadError("resolved record has no usable media URL")


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(data)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _validated_download(
    response: BinaryResponse,
    expected_mime_type: object,
    source_variant: str = "original",
) -> ImageDetails:
    """Validate downloaded bytes against what was actually requested.

    A thumbnail is a rendition Commons produces on demand, so its format is
    the server's choice and need not match the source file: a TIFF or SVG
    original is served as JPEG or PNG. What must agree there is the response
    content type and the bytes themselves. Only an original download is
    required to match the media type the resolver recorded.
    """
    details = _image_details(response.body)
    expected = _normalized_mime_type(expected_mime_type)
    received = _normalized_mime_type(response.content_type)
    if received not in SUPPORTED_MIME_TYPES:
        raise WikimediaDownloadError(
            f"server returned unsupported content type {received!r}"
        )
    if details.mime_type != received:
        raise WikimediaDownloadError(
            "image signature and response content type differ"
        )
    if source_variant == "original":
        if expected not in SUPPORTED_MIME_TYPES:
            raise WikimediaDownloadError(
                f"resolver selected unsupported media type {expected!r}"
            )
        if details.mime_type != expected:
            raise WikimediaDownloadError(
                "image signature, resolver media type, and response content "
                "type differ"
            )
    if _absolute_http_url(response.final_url) is None:
        raise WikimediaDownloadError("download ended at an invalid response URL")
    return details


def download_resolved_record(
    record: Mapping[str, Any],
    *,
    context: ssl.SSLContext,
    max_bytes: int,
    fetch_binary: BinaryFetcher = _fetch_binary,
    clock: Clock = _now,
) -> tuple[dict[str, object], bytes]:
    """Download one resolver record and return normalized metadata and bytes."""
    species_id = record.get("species_id")
    scientific_name = record.get("scientific_name")
    media = record.get("media")
    if not isinstance(species_id, int) or species_id <= 0:
        raise WikimediaDownloadError("resolved record has an invalid species ID")
    if not isinstance(scientific_name, str) or not isinstance(media, Mapping):
        raise WikimediaDownloadError(
            f"resolved record {species_id} has incomplete identity metadata"
        )
    url, source_variant = _download_source(record)
    response = fetch_binary(url, context, max_bytes)
    details = _validated_download(response, media.get("mime_type"), source_variant)
    extension = SUPPORTED_MIME_TYPES[details.mime_type]
    relative_path = Path("files") / f"{species_id}{extension}"
    downloaded_at = clock().astimezone(UTC).isoformat()
    return (
        {
            "species_id": species_id,
            "scientific_name": scientific_name,
            "wikidata_qid": record.get("wikidata_qid"),
            "commons_title": media.get("commons_title"),
            "commons_page_url": media.get("commons_page_url"),
            "original_url": media.get("original_url"),
            "thumbnail_url": media.get("thumbnail_url"),
            "download_url": url,
            "download_url_variant": source_variant,
            "final_url": response.final_url,
            "response_content_type": _normalized_mime_type(response.content_type),
            "mime_type": details.mime_type,
            "width": details.width,
            "height": details.height,
            "source_width": media.get("width"),
            "source_height": media.get("height"),
            "requested_thumbnail_width": media.get("thumbnail_width"),
            "requested_thumbnail_height": media.get("thumbnail_height"),
            "bytes": len(response.body),
            "sha256": hashlib.sha256(response.body).hexdigest(),
            "local_path": relative_path.as_posix(),
            "creator": media.get("creator"),
            "credit": media.get("credit"),
            "license_name": media.get("license_name"),
            "license_url": media.get("license_url"),
            "usage_terms": media.get("usage_terms"),
            "attribution_required": media.get("attribution_required"),
            "original_source_sha1": media.get("source_sha1"),
            "downloaded_at": downloaded_at,
            "transformation": (
                "No local transformation; downloaded the Wikimedia-generated "
                f"{source_variant} rendition."
            ),
        },
        response.body,
    )


def download_wikimedia_assets(
    resolver_manifest_path: Path,
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    ca_file: Path | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    limit: int | None = None,
    fetch_binary: BinaryFetcher = _fetch_binary,
    clock: Clock = _now,
    reproduction_command: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Download resolved records and write an auditable working-copy manifest."""
    if max_bytes <= 0:
        raise WikimediaDownloadError("max_bytes must be positive")
    if limit is not None and limit <= 0:
        raise WikimediaDownloadError("limit must be positive")
    source = _read_resolver_manifest(resolver_manifest_path)
    dataset_version = source.get("dataset_version")
    game_id = source.get("game_id")
    if not isinstance(dataset_version, str) or not dataset_version:
        raise WikimediaDownloadError("resolver manifest has no dataset version")
    if not isinstance(game_id, str) or not game_id:
        raise WikimediaDownloadError("resolver manifest has no game ID")

    raw_records = source["records"]
    records = [
        record
        for record in raw_records
        if isinstance(record, Mapping) and record.get("status") == "resolved"
    ]
    records.sort(key=lambda record: int(record.get("species_id", 0)))
    if limit is not None:
        records = records[:limit]
    if not records:
        raise WikimediaDownloadError(
            "resolver manifest has no selected resolved records"
        )

    destination = output_root / dataset_version / game_id
    context = build_ssl_context(ca_file)
    downloaded: list[dict[str, object]] = []
    seen_species: set[int] = set()
    for record in records:
        species_id = record.get("species_id")
        if not isinstance(species_id, int) or species_id <= 0:
            raise WikimediaDownloadError("resolved record has an invalid species ID")
        if species_id in seen_species:
            raise WikimediaDownloadError(f"duplicate species ID: {species_id}")
        seen_species.add(species_id)
        downloaded_record, body = download_resolved_record(
            record,
            context=context,
            max_bytes=max_bytes,
            fetch_binary=fetch_binary,
            clock=clock,
        )
        _atomic_bytes(destination / downloaded_record["local_path"], body)  # type: ignore[arg-type]
        downloaded.append(downloaded_record)

    skipped = Counter(
        str(record.get("status"))
        for record in raw_records
        if isinstance(record, Mapping) and record.get("status") != "resolved"
    )
    checksums = Counter(str(record["sha256"]) for record in downloaded)
    duplicate_checksums = sorted(
        checksum for checksum, count in checksums.items() if count > 1
    )
    manifest: dict[str, Any] = {
        "schema_version": WIKIMEDIA_DOWNLOAD_MANIFEST_SCHEMA_VERSION,
        "downloader_version": WIKIMEDIA_DOWNLOADER_VERSION,
        "generated_at": clock().astimezone(UTC).isoformat(),
        "dataset_version": dataset_version,
        "game_id": game_id,
        "source": {
            "resolver_manifest": str(resolver_manifest_path),
            "resolver_manifest_sha256": sha256_file(resolver_manifest_path),
            "resolver_version": source.get("resolver_version"),
        },
        "configuration": {"max_bytes": max_bytes, "limit": limit},
        "eligible_resolved_count": sum(
            1
            for record in raw_records
            if isinstance(record, Mapping) and record.get("status") == "resolved"
        ),
        "downloaded_count": len(downloaded),
        "skipped_status_counts": dict(sorted(skipped.items())),
        "duplicate_sha256": duplicate_checksums,
        "records": downloaded,
        "review_status": "working-copy-requires-visual-and-license-review",
        "reproduction_command": reproduction_command,
    }
    manifest_path = destination / "manifest.json"
    _atomic_json(manifest_path, manifest)
    return manifest_path, manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download resolved Wikimedia images into ignored working assets."
    )
    parser.add_argument("manifest", type=Path, help="Wikimedia resolver manifest")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--transport",
        choices=("urllib", "curl"),
        default="urllib",
        help="verified HTTPS client (curl can use the macOS system trust store)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    command_parts = [
        "phylogenomica-download-wikimedia",
        str(args.manifest),
        "--output-root",
        str(args.output_root),
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
        manifest_path, manifest = download_wikimedia_assets(
            args.manifest,
            output_root=args.output_root,
            ca_file=args.ca_file,
            max_bytes=args.max_bytes,
            limit=args.limit,
            fetch_binary=(
                _fetch_binary_curl if args.transport == "curl" else _fetch_binary
            ),
            reproduction_command=shlex.join(command_parts),
        )
    except WikimediaDownloadError as error:
        raise SystemExit(str(error)) from error
    print(f"wrote {manifest_path} ({manifest['downloaded_count']} images)")


if __name__ == "__main__":
    main()
