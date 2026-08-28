"""Acquire a versioned OneZoom static tree snapshot.

OneZoom's live viewer publishes small, versioned files containing the complete
display topology, cut-position index, and available divergence dates. These are
useful for beginning topology work while access to the full production SQL dump
is arranged separately.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import ssl
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://www.onezoom.org"
DEFAULT_OUTPUT_ROOT = Path("data/raw/onezoom")
DISCOVERY_PATH = "/life.html"
DATA_PATH = "/OZtree/static/FinalOutputs/data/"
FILE_STEMS = ("completetree", "cut_position_map", "dates")
SYSTEM_CA_CANDIDATES = (
    Path("/etc/ssl/cert.pem"),
    Path("/etc/ssl/certs/ca-certificates.crt"),
)
TREE_VERSION_PATTERN = re.compile(
    r"FinalOutputs/data/BASEBASE_(?P<version>[0-9]+)\.EXTEXT"
)
USER_AGENT = "phylogenomica-data-acquisition/0.1 (+https://github.com/)"


class AcquisitionError(RuntimeError):
    """Raised when a source cannot be acquired or validated safely."""


def discover_tree_version(page_html: str) -> str:
    """Extract the immutable tree version advertised by the live viewer."""
    versions = set(TREE_VERSION_PATTERN.findall(page_html))
    if len(versions) != 1:
        found = ", ".join(sorted(versions)) or "none"
        raise AcquisitionError(
            f"expected exactly one OneZoom tree version; found {found}"
        )
    return versions.pop()


def snapshot_urls(base_url: str, version: str) -> dict[str, str]:
    """Return canonical static-data URLs for a numeric tree version."""
    if not version.isascii() or not version.isdigit():
        raise AcquisitionError(f"invalid OneZoom tree version: {version!r}")
    base = base_url.rstrip("/") + DATA_PATH
    return {
        f"{stem}_{version}.js.gz": urljoin(base, f"{stem}_{version}.js.gz")
        for stem in FILE_STEMS
    }


def sha256_file(path: Path) -> str:
    """Return a lowercase SHA-256 digest for a file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_payload(path: Path) -> None:
    """Check gzip integrity and the expected OneZoom JavaScript assignment."""
    expected_prefixes = {
        "completetree": b"var rawData = '",
        "cut_position_map": b"var cut_position_map_json_str = '",
        "dates": b"var tree_date = ",
    }
    stem = next((name for name in FILE_STEMS if path.name.startswith(name)), None)
    if stem is None:
        raise AcquisitionError(f"unexpected OneZoom snapshot filename: {path.name}")

    try:
        with gzip.open(path, "rb") as source:
            prefix = source.read(64)
            while source.read(1024 * 1024):
                pass
    except (OSError, EOFError) as error:
        raise AcquisitionError(f"invalid gzip payload: {path}") from error

    if not prefix.startswith(expected_prefixes[stem]):
        raise AcquisitionError(f"unexpected OneZoom payload content: {path}")


def build_ssl_context(ca_file: Path | None = None) -> ssl.SSLContext:
    """Build a verified TLS context, preferring a requested or system CA file."""
    if ca_file is not None:
        if not ca_file.is_file():
            raise AcquisitionError(f"CA file does not exist: {ca_file}")
        return ssl.create_default_context(cafile=ca_file)

    for candidate in SYSTEM_CA_CANDIDATES:
        if candidate.is_file():
            return ssl.create_default_context(cafile=candidate)
    return ssl.create_default_context()


def _request_bytes(url: str, context: ssl.SSLContext) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=60, context=context) as response:  # noqa: S310
            return response.read()
    except OSError as error:
        raise AcquisitionError(f"failed to fetch {url}: {error}") from error


def _download(url: str, destination: Path, context: ssl.SSLContext) -> None:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with (
            urlopen(request, timeout=120, context=context) as response,  # noqa: S310
            destination.open("wb") as output,
        ):
            shutil.copyfileobj(response, output, length=1024 * 1024)
    except OSError as error:
        raise AcquisitionError(f"failed to download {url}: {error}") from error


def _validate_existing_snapshot(snapshot_dir: Path) -> dict[str, Any]:
    manifest_path = snapshot_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AcquisitionError(
            f"existing snapshot lacks a valid manifest: {snapshot_dir}"
        ) from error

    for record in manifest.get("files", []):
        path = snapshot_dir / record["name"]
        if not path.is_file():
            raise AcquisitionError(f"snapshot file is missing: {path}")
        if path.stat().st_size != record["bytes"]:
            raise AcquisitionError(f"snapshot size mismatch: {path}")
        if sha256_file(path) != record["sha256"]:
            raise AcquisitionError(f"snapshot checksum mismatch: {path}")
        validate_payload(path)
    return manifest


def acquire_snapshot(
    *,
    base_url: str = DEFAULT_BASE_URL,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    version: str | None = None,
    ca_file: Path | None = None,
) -> tuple[Path, dict[str, Any], bool]:
    """Download or verify a versioned static snapshot.

    Returns the snapshot directory, parsed manifest, and a boolean indicating
    whether a new snapshot was downloaded.
    """
    discovery_url = urljoin(base_url.rstrip("/") + "/", DISCOVERY_PATH.lstrip("/"))
    context = build_ssl_context(ca_file)
    if version is None:
        page = _request_bytes(discovery_url, context).decode("utf-8")
        version = discover_tree_version(page)

    urls = snapshot_urls(base_url, version)
    snapshot_dir = output_root / version
    if snapshot_dir.exists():
        manifest = _validate_existing_snapshot(snapshot_dir)
        if manifest.get("tree_version") != version:
            raise AcquisitionError(f"snapshot version mismatch: {snapshot_dir}")
        return snapshot_dir, manifest, False

    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{version}.", dir=output_root
    ) as temporary:
        temporary_dir = Path(temporary)
        records: list[dict[str, Any]] = []
        for name, url in urls.items():
            destination = temporary_dir / name
            _download(url, destination, context)
            validate_payload(destination)
            records.append(
                {
                    "name": name,
                    "url": url,
                    "bytes": destination.stat().st_size,
                    "sha256": sha256_file(destination),
                }
            )

        manifest: dict[str, Any] = {
            "schema_version": 1,
            "source": "OneZoom",
            "tree_version": version,
            "discovery_url": discovery_url,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "license_url": (
                "https://www.onezoom.org/OZtree/static/downloads/"
                "OneZoom_License_V1.pdf"
            ),
            "data_sources_url": "https://www.onezoom.org/data_sources.html",
            "redistribution_status": "review-required",
            "files": records,
        }
        (temporary_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_dir.rename(snapshot_dir)

    return snapshot_dir, manifest, True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download and verify the current OneZoom static tree snapshot."
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"OneZoom origin (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"ignored raw-data root (default: {DEFAULT_OUTPUT_ROOT})",
    )
    parser.add_argument(
        "--version",
        help=(
            "download a known numeric tree version instead of discovering the live one"
        ),
    )
    parser.add_argument(
        "--ca-file",
        type=Path,
        help="explicit CA bundle for TLS verification",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        snapshot_dir, manifest, downloaded = acquire_snapshot(
            base_url=args.base_url,
            output_root=args.output_root,
            version=args.version,
            ca_file=args.ca_file,
        )
    except AcquisitionError as error:
        raise SystemExit(str(error)) from error

    action = "downloaded" if downloaded else "verified"
    total_bytes = sum(record["bytes"] for record in manifest["files"])
    print(
        f"{action} OneZoom tree {manifest['tree_version']} at {snapshot_dir} "
        f"({total_bytes:,} bytes)"
    )


if __name__ == "__main__":
    main()
