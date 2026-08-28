"""Command-line structural audit for downloaded OneZoom static data."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from phylogenomica.data.onezoom_static import (
    StaticDataError,
    find_snapshot_file,
    read_dates,
    read_topology,
)
from phylogenomica.tree.bracket_audit import TopologyError, audit_bracket_topology


def latest_snapshot(root: Path) -> Path:
    """Return the highest numeric OneZoom snapshot directory."""
    snapshots = [
        path for path in root.iterdir() if path.is_dir() and path.name.isdigit()
    ]
    if not snapshots:
        raise ValueError(f"no numeric OneZoom snapshots found under {root}")
    return max(snapshots, key=lambda path: int(path.name))


def audit_snapshot(snapshot: Path) -> dict[str, object]:
    """Return a JSON-serializable first-pass snapshot audit."""
    topology = read_topology(find_snapshot_file(snapshot, "completetree"))
    dates = read_dates(find_snapshot_file(snapshot, "dates"))
    return {
        "schema_version": 1,
        "source": "OneZoom static viewer data",
        "tree_version": snapshot.name,
        "topology": audit_bracket_topology(topology).to_dict(),
        "divergence_dates": {
            "leaves": len(dates["leaves"]),
            "nodes": len(dates["nodes"]),
        },
        "limitations": [
            "Static files do not include complete taxon names or external IDs.",
            "Metadata coverage requires the public production SQL dump.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the initial structural audit of OneZoom static data."
    )
    parser.add_argument(
        "snapshot",
        nargs="?",
        type=Path,
        help="snapshot directory (default: newest under data/raw/onezoom)",
    )
    parser.add_argument("--output", type=Path, help="write JSON to this path")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        snapshot = args.snapshot or latest_snapshot(Path("data/raw/onezoom"))
        result = audit_snapshot(snapshot)
    except (OSError, ValueError, StaticDataError, TopologyError) as error:
        raise SystemExit(str(error)) from error

    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(rendered, end="")
