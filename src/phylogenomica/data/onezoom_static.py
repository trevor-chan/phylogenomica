"""Readers for OneZoom's versioned static viewer data."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any


class StaticDataError(ValueError):
    """Raised when a static OneZoom file does not match its expected format."""


def _read_assignment(
    path: Path, prefix: bytes, suffix: bytes | None = None
) -> bytes:
    try:
        with gzip.open(path, "rb") as source:
            payload = source.read()
    except (OSError, EOFError) as error:
        raise StaticDataError(f"cannot read OneZoom gzip file: {path}") from error

    if not payload.startswith(prefix):
        raise StaticDataError(f"unexpected assignment prefix in {path}")
    if suffix is None:
        return payload[len(prefix) :].strip()
    end = payload.find(suffix, len(prefix))
    if end < 0:
        raise StaticDataError(f"missing assignment terminator in {path}")
    return payload[len(prefix) : end]


def read_topology(path: Path) -> str:
    """Read and validate OneZoom's compact bracket topology."""
    encoded = _read_assignment(path, b"var rawData = '", b"';")
    try:
        topology = encoded.decode("ascii")
    except UnicodeDecodeError as error:
        raise StaticDataError(f"non-ASCII topology in {path}") from error

    invalid = set(topology).difference("(){}")
    if invalid:
        rendered = "".join(sorted(invalid))
        raise StaticDataError(f"invalid topology characters in {path}: {rendered!r}")
    if not topology:
        raise StaticDataError(f"empty topology in {path}")
    return topology


def read_dates(path: Path) -> dict[str, dict[str, float]]:
    """Read available leaf and internal-node dates from static JavaScript."""
    encoded = _read_assignment(path, b"var tree_date = ").removesuffix(b";")
    try:
        value: Any = json.loads(encoded)
    except json.JSONDecodeError as error:
        raise StaticDataError(f"invalid dates JSON in {path}") from error
    if not isinstance(value, dict):
        raise StaticDataError(f"unexpected dates value in {path}")

    dates: dict[str, dict[str, float]] = {}
    for key in ("leaves", "nodes"):
        records = value.get(key, {})
        if not isinstance(records, dict):
            raise StaticDataError(f"unexpected {key} dates in {path}")
        dates[key] = records
    return dates


def find_snapshot_file(snapshot_dir: Path, stem: str) -> Path:
    """Find exactly one versioned source file for a given data stem."""
    matches = list(snapshot_dir.glob(f"{stem}_*.js.gz"))
    if len(matches) != 1:
        raise StaticDataError(
            f"expected one {stem} file in {snapshot_dir}; found {len(matches)}"
        )
    return matches[0]
