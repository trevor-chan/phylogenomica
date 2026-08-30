"""Build a validated biological tree from normalized OneZoom records."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from phylogenomica.data.onezoom_download import sha256_file

TREE_BUILDER_VERSION = 1
TREE_SCHEMA_VERSION = 1
TREE_DATABASE_FILENAME = "biological_tree.sqlite3"
DEFAULT_NORMALIZED_DIR = Path("data/processed/onezoom/27400288")
INSERT_BATCH_SIZE = 20_000


class TreePreprocessingError(RuntimeError):
    """Raised when normalized records do not form one valid biological tree."""


@dataclass(frozen=True)
class TreeAnalysis:
    """Computed properties of an internal-node parent graph."""

    root_id: int
    parent_by_node: dict[int, int | None]
    depth_by_node: dict[int, int]
    child_count_by_node: dict[int, int]
    descendant_leaf_count_by_node: dict[int, int]
    retained_after_collapse: frozenset[int]
    collapsed_parent_by_node: dict[int, int | None]
    collapsed_depth_by_node: dict[int, int | None]
    collapsed_child_count_by_node: dict[int, int | None]
    direct_leaf_count_by_node: dict[int, int]

    def metrics(self) -> dict[str, int]:
        """Return structural metrics excluding per-leaf depth."""
        degrees = Counter(self.child_count_by_node.values())
        return {
            "biological_internal_nodes": len(self.parent_by_node),
            "bifurcations": degrees[2],
            "polytomies": sum(count for degree, count in degrees.items() if degree > 2),
            "monotypic_internal_nodes": degrees[1],
            "largest_polytomy": max(self.child_count_by_node.values()),
            "max_biological_node_depth": max(self.depth_by_node.values()),
            "collapsed_internal_nodes": len(self.retained_after_collapse),
            "removed_monotypic_nodes": (
                len(self.parent_by_node) - len(self.retained_after_collapse)
            ),
        }


SCHEMA_SQL = """
PRAGMA application_id = 0x50485452;
PRAGMA user_version = 1;

CREATE TABLE dataset_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE biological_nodes (
    node_id INTEGER PRIMARY KEY,
    parent_node_id INTEGER,
    child_count INTEGER NOT NULL CHECK (child_count > 0),
    biological_depth INTEGER NOT NULL CHECK (biological_depth >= 0),
    descendant_leaf_count INTEGER NOT NULL CHECK (descendant_leaf_count > 0),
    is_polytomy INTEGER NOT NULL CHECK (is_polytomy IN (0, 1)),
    retained_after_collapse INTEGER NOT NULL
        CHECK (retained_after_collapse IN (0, 1)),
    collapsed_parent_node_id INTEGER,
    collapsed_child_count INTEGER,
    collapsed_depth INTEGER,
    CHECK (
        (retained_after_collapse = 1
            AND collapsed_child_count IS NOT NULL
            AND collapsed_depth IS NOT NULL)
        OR
        (retained_after_collapse = 0
            AND collapsed_child_count IS NULL
            AND collapsed_depth IS NULL)
    )
);

CREATE TABLE biological_leaves (
    leaf_id INTEGER PRIMARY KEY,
    parent_node_id INTEGER NOT NULL,
    biological_depth INTEGER NOT NULL CHECK (biological_depth > 0),
    collapsed_parent_node_id INTEGER NOT NULL,
    collapsed_depth INTEGER NOT NULL CHECK (collapsed_depth > 0),
    skipped_monotypic_nodes INTEGER NOT NULL CHECK (skipped_monotypic_nodes >= 0)
);
"""


INDEX_SQL = """
CREATE INDEX biological_nodes_parent_idx ON biological_nodes(parent_node_id);
CREATE INDEX biological_nodes_collapsed_parent_idx
    ON biological_nodes(collapsed_parent_node_id)
    WHERE retained_after_collapse = 1;
CREATE INDEX biological_nodes_depth_idx ON biological_nodes(biological_depth);
CREATE INDEX biological_leaves_parent_idx ON biological_leaves(parent_node_id);
CREATE INDEX biological_leaves_collapsed_parent_idx
    ON biological_leaves(collapsed_parent_node_id);
"""


def analyze_parent_graph(
    parent_by_node: Mapping[int, int | None],
    direct_leaf_count_by_node: Mapping[int, int],
) -> TreeAnalysis:
    """Validate and analyze a rooted internal-node graph.

    Node and leaf IDs may overlap because they occupy distinct namespaces.
    ``direct_leaf_count_by_node`` aggregates leaves by their internal parent.
    """
    parents = dict(parent_by_node)
    if not parents:
        raise TreePreprocessingError("biological tree has no internal nodes")
    roots = sorted(node_id for node_id, parent in parents.items() if parent is None)
    if len(roots) != 1:
        raise TreePreprocessingError(
            f"expected one biological root; found {len(roots)}: {roots[:10]}"
        )
    root_id = roots[0]

    orphans = sorted(
        (node_id, parent)
        for node_id, parent in parents.items()
        if parent is not None and parent not in parents
    )
    leaf_orphans = sorted(
        parent
        for parent, count in direct_leaf_count_by_node.items()
        if count and parent not in parents
    )
    if orphans or leaf_orphans:
        raise TreePreprocessingError(
            "biological parent references non-biological nodes: "
            f"internal={orphans[:5]}, leaf_parents={leaf_orphans[:5]}"
        )

    depth: dict[int, int] = {root_id: 0}
    for start in sorted(parents):
        if start in depth:
            continue
        path: list[int] = []
        path_positions: dict[int, int] = {}
        current = start
        while current not in depth:
            if current in path_positions:
                cycle = path[path_positions[current] :] + [current]
                raise TreePreprocessingError(
                    f"cycle in biological parents: {cycle[:20]}"
                )
            path_positions[current] = len(path)
            path.append(current)
            parent = parents[current]
            if parent is None:
                raise TreePreprocessingError(
                    f"node {current} is an unexpected additional root"
                )
            current = parent

        resolved_depth = depth[current]
        for node_id in reversed(path):
            resolved_depth += 1
            depth[node_id] = resolved_depth

    child_counts = {
        node_id: int(direct_leaf_count_by_node.get(node_id, 0)) for node_id in parents
    }
    for node_id, parent in parents.items():
        if parent is not None:
            child_counts[parent] += 1
    childless = sorted(node_id for node_id, count in child_counts.items() if count == 0)
    if childless:
        raise TreePreprocessingError(
            f"biological internal nodes have no children: {childless[:10]}"
        )

    descendants = {
        node_id: int(direct_leaf_count_by_node.get(node_id, 0)) for node_id in parents
    }
    descending_nodes = sorted(parents, key=lambda node_id: depth[node_id], reverse=True)
    for node_id in descending_nodes:
        parent = parents[node_id]
        if parent is not None:
            descendants[parent] += descendants[node_id]
    leafless = sorted(node_id for node_id, count in descendants.items() if count == 0)
    if leafless:
        raise TreePreprocessingError(
            f"biological nodes have no reachable leaves: {leafless[:10]}"
        )

    retained = frozenset(
        node_id
        for node_id, count in child_counts.items()
        if node_id == root_id or count != 1
    )
    collapsed_parent: dict[int, int | None] = {root_id: None}
    collapsed_depth: dict[int, int | None] = {root_id: 0}
    ascending_nodes = sorted(parents, key=lambda node_id: depth[node_id])
    for node_id in ascending_nodes:
        if node_id == root_id:
            continue
        parent = parents[node_id]
        assert parent is not None
        nearest_retained = parent if parent in retained else collapsed_parent[parent]
        assert nearest_retained is not None
        collapsed_parent[node_id] = nearest_retained
        collapsed_depth[node_id] = (
            collapsed_depth[nearest_retained] + 1 if node_id in retained else None
        )

    collapsed_child_counts: dict[int, int | None] = {
        node_id: 0 if node_id in retained else None for node_id in parents
    }
    for node_id in retained:
        parent = collapsed_parent[node_id]
        if parent is not None:
            count = collapsed_child_counts[parent]
            assert count is not None
            collapsed_child_counts[parent] = count + 1
    for parent, leaf_count in direct_leaf_count_by_node.items():
        if not leaf_count:
            continue
        collapsed_leaf_parent = (
            parent if parent in retained else collapsed_parent[parent]
        )
        assert collapsed_leaf_parent is not None
        count = collapsed_child_counts[collapsed_leaf_parent]
        assert count is not None
        collapsed_child_counts[collapsed_leaf_parent] = count + int(leaf_count)

    return TreeAnalysis(
        root_id=root_id,
        parent_by_node=parents,
        depth_by_node=depth,
        child_count_by_node=child_counts,
        descendant_leaf_count_by_node=descendants,
        retained_after_collapse=retained,
        collapsed_parent_by_node=collapsed_parent,
        collapsed_depth_by_node=collapsed_depth,
        collapsed_child_count_by_node=collapsed_child_counts,
        direct_leaf_count_by_node=dict(direct_leaf_count_by_node),
    )


def _load_normalized_manifest(normalized_dir: Path) -> tuple[dict[str, Any], Path, str]:
    manifest_path = normalized_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TreePreprocessingError(
            f"cannot read normalized manifest: {manifest_path}"
        ) from error
    if (
        manifest.get("schema_version") != 1
        or manifest.get("database_schema_version") != 1
    ):
        raise TreePreprocessingError(
            "unsupported normalized manifest or database schema"
        )
    database_record = manifest.get("database", {})
    database_path = normalized_dir / str(database_record.get("name", ""))
    try:
        size = database_path.stat().st_size
    except OSError as error:
        raise TreePreprocessingError(
            f"normalized database is missing: {database_path}"
        ) from error
    if size != database_record.get("bytes"):
        raise TreePreprocessingError("normalized database size does not match manifest")
    checksum = sha256_file(database_path)
    if checksum != database_record.get("sha256"):
        raise TreePreprocessingError(
            "normalized database checksum does not match manifest"
        )
    return manifest, database_path, sha256_file(manifest_path)


def _read_graph(
    connection: sqlite3.Connection,
) -> tuple[dict[int, int | None], dict[int, int]]:
    parents = {
        int(node_id): None if parent is None else int(parent)
        for node_id, parent in connection.execute(
            "SELECT node_id, biological_parent_id FROM nodes "
            "WHERE source_real_parent >= 0 ORDER BY node_id"
        )
    }
    direct_leaf_counts = {
        int(parent): int(count)
        for parent, count in connection.execute(
            "SELECT biological_parent_id, COUNT(*) FROM leaves "
            "GROUP BY biological_parent_id"
        )
    }
    return parents, direct_leaf_counts


def _create_tree_database(
    output_path: Path,
    *,
    source: sqlite3.Connection,
    analysis: TreeAnalysis,
    dataset_version: str,
    normalized_database_sha256: str,
) -> tuple[int, int]:
    output = sqlite3.connect(output_path)
    leaf_count = 0
    max_collapsed_leaf_depth = 0
    try:
        output.execute("PRAGMA journal_mode = OFF")
        output.execute("PRAGMA synchronous = OFF")
        output.execute("PRAGMA temp_store = MEMORY")
        output.executescript(SCHEMA_SQL)
        output.executemany(
            "INSERT INTO dataset_metadata VALUES (?, ?)",
            (
                ("dataset_version", dataset_version),
                ("tree_builder_version", str(TREE_BUILDER_VERSION)),
                ("tree_schema_version", str(TREE_SCHEMA_VERSION)),
                ("normalized_database_sha256", normalized_database_sha256),
                ("root_node_id", str(analysis.root_id)),
            ),
        )

        node_rows = (
            (
                node_id,
                analysis.parent_by_node[node_id],
                analysis.child_count_by_node[node_id],
                analysis.depth_by_node[node_id],
                analysis.descendant_leaf_count_by_node[node_id],
                int(analysis.child_count_by_node[node_id] > 2),
                int(node_id in analysis.retained_after_collapse),
                analysis.collapsed_parent_by_node[node_id],
                analysis.collapsed_child_count_by_node[node_id],
                analysis.collapsed_depth_by_node[node_id],
            )
            for node_id in sorted(analysis.parent_by_node)
        )
        output.executemany(
            "INSERT INTO biological_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            node_rows,
        )

        leaf_rows: list[tuple[int, int, int, int, int, int]] = []
        for leaf_id, parent in source.execute(
            "SELECT leaf_id, biological_parent_id FROM leaves ORDER BY leaf_id"
        ):
            parent = int(parent)
            collapsed_parent = (
                parent
                if parent in analysis.retained_after_collapse
                else analysis.collapsed_parent_by_node[parent]
            )
            assert collapsed_parent is not None
            biological_depth = analysis.depth_by_node[parent] + 1
            parent_collapsed_depth = analysis.collapsed_depth_by_node[collapsed_parent]
            assert parent_collapsed_depth is not None
            collapsed_leaf_depth = parent_collapsed_depth + 1
            max_collapsed_leaf_depth = max(
                max_collapsed_leaf_depth, collapsed_leaf_depth
            )
            leaf_rows.append(
                (
                    int(leaf_id),
                    parent,
                    biological_depth,
                    collapsed_parent,
                    collapsed_leaf_depth,
                    biological_depth - collapsed_leaf_depth,
                )
            )
            leaf_count += 1
            if len(leaf_rows) >= INSERT_BATCH_SIZE:
                output.executemany(
                    "INSERT INTO biological_leaves VALUES (?, ?, ?, ?, ?, ?)",
                    leaf_rows,
                )
                leaf_rows.clear()
        if leaf_rows:
            output.executemany(
                "INSERT INTO biological_leaves VALUES (?, ?, ?, ?, ?, ?)",
                leaf_rows,
            )
        output.commit()
        output.executescript(INDEX_SQL)
        output.commit()
        integrity = output.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise TreePreprocessingError(
                f"derived SQLite integrity check failed: {integrity!r}"
            )
        output.execute("VACUUM")
    except sqlite3.Error as error:
        raise TreePreprocessingError(f"tree database build failed: {error}") from error
    finally:
        output.close()
    return leaf_count, max_collapsed_leaf_depth


def _validate_against_static_metrics(
    metrics: dict[str, int], normalized_manifest: dict[str, Any]
) -> None:
    static = normalized_manifest.get("validation", {}).get(
        "matched_static_topology", {}
    )
    comparisons = {
        "biological_internal_nodes": "biological_internal_nodes",
        "bifurcations": "bifurcations",
        "polytomies": "polytomies",
        "max_biological_node_depth": "max_biological_node_depth",
    }
    mismatches = {
        metric: (metrics[metric], static.get(static_name))
        for metric, static_name in comparisons.items()
        if metrics[metric] != static.get(static_name)
    }
    if mismatches:
        raise TreePreprocessingError(
            f"biological graph/static topology mismatch: {mismatches}"
        )


def build_biological_tree(
    *,
    normalized_dir: Path = DEFAULT_NORMALIZED_DIR,
    output_dir: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Build an atomic derived biological-tree database."""
    normalized_manifest, normalized_database, normalized_manifest_sha256 = (
        _load_normalized_manifest(normalized_dir)
    )
    tree_version = str(normalized_manifest["source_tree_version"])
    destination = output_dir or normalized_dir / f"tree-v{TREE_SCHEMA_VERSION}"
    if destination.exists():
        raise TreePreprocessingError(f"tree output already exists: {destination}")

    source = sqlite3.connect(f"file:{normalized_database}?mode=ro", uri=True)
    try:
        print("reading biological parent graph...", flush=True)
        parent_by_node, direct_leaf_counts = _read_graph(source)
        print("validating cycles, reachability, and child degrees...", flush=True)
        analysis = analyze_parent_graph(parent_by_node, direct_leaf_counts)
        metrics = analysis.metrics()
        _validate_against_static_metrics(metrics, normalized_manifest)

        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{destination.name}.", dir=destination.parent
        ) as temporary:
            temporary_dir = Path(temporary)
            database_path = temporary_dir / TREE_DATABASE_FILENAME
            print("writing biological nodes and leaves...", flush=True)
            leaf_count, max_collapsed_leaf_depth = _create_tree_database(
                database_path,
                source=source,
                analysis=analysis,
                dataset_version=str(normalized_manifest["dataset_version"]),
                normalized_database_sha256=normalized_manifest["database"]["sha256"],
            )
            source_leaf_count = normalized_manifest["source_row_counts"][
                "ordered_leaves"
            ]
            if leaf_count != source_leaf_count:
                raise TreePreprocessingError(
                    f"leaf row mismatch: expected {source_leaf_count}, "
                    f"wrote {leaf_count}"
                )
            metrics.update(
                {
                    "leaves": leaf_count,
                    "max_biological_leaf_depth": (
                        metrics["max_biological_node_depth"] + 1
                    ),
                    "max_collapsed_leaf_depth": max_collapsed_leaf_depth,
                    "artificial_display_nodes_excluded": (
                        normalized_manifest["source_row_counts"]["ordered_nodes"]
                        - metrics["biological_internal_nodes"]
                    ),
                }
            )
            database_record = {
                "name": TREE_DATABASE_FILENAME,
                "bytes": database_path.stat().st_size,
                "sha256": sha256_file(database_path),
            }
            output_manifest: dict[str, Any] = {
                "schema_version": 1,
                "dataset_version": normalized_manifest["dataset_version"],
                "source_tree_version": tree_version,
                "built_at": datetime.now(UTC).isoformat(),
                "tree_builder_version": TREE_BUILDER_VERSION,
                "tree_schema_version": TREE_SCHEMA_VERSION,
                "runtime": {
                    "python": sys.version.split()[0],
                    "sqlite": sqlite3.sqlite_version,
                },
                "source": {
                    "normalized_directory": str(normalized_dir),
                    "normalized_manifest_sha256": normalized_manifest_sha256,
                    "normalized_database_sha256": normalized_manifest["database"][
                        "sha256"
                    ],
                },
                "rules": {
                    "biological_nodes": "source_real_parent >= 0",
                    "biological_edges": "normalized biological_parent_id",
                    "artificial_nodes": "source_real_parent < 0; excluded",
                    "monotypic_collapse": (
                        "exclude non-root internal nodes with exactly one child"
                    ),
                    "polytomy": "preserve every node with more than two children",
                },
                "metrics": metrics,
                "validation": {
                    "single_root": True,
                    "all_internal_nodes_reachable": True,
                    "all_leaves_reachable": True,
                    "cycles": 0,
                    "orphan_parents": 0,
                    "childless_internal_nodes": 0,
                    "sqlite_integrity_check": "ok",
                    "matched_static_topology": True,
                },
                "database": database_record,
                "reproduction_command": (
                    f"phylogenomica-build-tree {normalized_dir} "
                    f"--output-dir {destination}"
                ),
            }
            (temporary_dir / "manifest.json").write_text(
                json.dumps(output_manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary_dir.rename(destination)
    finally:
        source.close()
    return destination, output_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a validated biological tree from normalized OneZoom data."
    )
    parser.add_argument(
        "normalized_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_NORMALIZED_DIR,
    )
    parser.add_argument("--output-dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        output_dir, manifest = build_biological_tree(
            normalized_dir=args.normalized_dir,
            output_dir=args.output_dir,
        )
    except TreePreprocessingError as error:
        raise SystemExit(str(error)) from error
    database = manifest["database"]
    print(
        f"built biological tree at {output_dir} "
        f"({database['bytes']:,} bytes, sha256 {database['sha256']})",
        flush=True,
    )


if __name__ == "__main__":
    main()
