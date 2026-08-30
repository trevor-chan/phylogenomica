"""Read-only queries over a preprocessed biological-tree database."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from phylogenomica.tree.preprocess import (
    DEFAULT_NORMALIZED_DIR,
    TREE_DATABASE_FILENAME,
    TREE_SCHEMA_VERSION,
)

Topology = Literal["biological", "collapsed"]
TaxonKind = Literal["node", "leaf"]
DEFAULT_TREE_DATABASE = (
    DEFAULT_NORMALIZED_DIR / f"tree-v{TREE_SCHEMA_VERSION}" / TREE_DATABASE_FILENAME
)


class TreeQueryError(ValueError):
    """Raised when a requested taxon or topology query is invalid."""


@dataclass(frozen=True, order=True)
class TaxonRef:
    """A stable reference in OneZoom's separate node and leaf namespaces."""

    kind: TaxonKind
    taxon_id: int

    def __post_init__(self) -> None:
        if self.kind not in ("node", "leaf"):
            raise TreeQueryError(f"invalid taxon kind: {self.kind!r}")
        if self.taxon_id <= 0:
            raise TreeQueryError(f"taxon ID must be positive: {self.taxon_id}")


@dataclass(frozen=True)
class ChildBranch:
    """One child branch and its candidate-leaf capacity."""

    taxon: TaxonRef
    descendant_leaf_count: int


@dataclass(frozen=True)
class SisterGroup:
    """All off-target branches sharing one divergence event."""

    ancestor_node_id: int
    ancestor_depth: int
    target_branch: TaxonRef
    sister_branches: tuple[ChildBranch, ...]

    @property
    def candidate_leaf_capacity(self) -> int:
        """Return the total number of leaves in all off-target branches."""
        return sum(branch.descendant_leaf_count for branch in self.sister_branches)


class BiologicalTree:
    """Query a validated tree without mutating its SQLite artifact."""

    def __init__(
        self, connection: sqlite3.Connection, *, owns_connection: bool = False
    ):
        self._connection = connection
        self._owns_connection = owns_connection
        self._root_id: int | None = None
        schema_version = self._connection.execute("PRAGMA user_version").fetchone()
        if schema_version != (TREE_SCHEMA_VERSION,):
            raise TreeQueryError(
                "unsupported tree database schema: "
                f"expected {TREE_SCHEMA_VERSION}, found {schema_version!r}"
            )

    @classmethod
    def open(cls, path: Path = DEFAULT_TREE_DATABASE) -> BiologicalTree:
        """Open a tree database in SQLite read-only mode."""
        if not path.is_file():
            raise TreeQueryError(f"tree database does not exist: {path}")
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return cls(connection, owns_connection=True)
        except Exception:
            connection.close()
            raise

    def close(self) -> None:
        """Close a connection opened by :meth:`open`."""
        if self._owns_connection:
            self._connection.close()
            self._owns_connection = False

    def __enter__(self) -> BiologicalTree:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def root_id(self) -> int:
        """Return the single validated biological root ID."""
        if self._root_id is None:
            rows = self._connection.execute(
                "SELECT node_id FROM biological_nodes WHERE parent_node_id IS NULL"
            ).fetchall()
            if len(rows) != 1:
                raise TreeQueryError(
                    f"expected one biological root in tree database; found {len(rows)}"
                )
            self._root_id = int(rows[0][0])
        return self._root_id

    @staticmethod
    def _validate_topology(topology: Topology) -> None:
        if topology not in ("biological", "collapsed"):
            raise TreeQueryError(f"invalid topology: {topology!r}")

    def _node_row(self, node_id: int) -> tuple[int | None, int, int | None, int]:
        row = self._connection.execute(
            "SELECT parent_node_id, retained_after_collapse, "
            "collapsed_parent_node_id, biological_depth "
            "FROM biological_nodes WHERE node_id = ?",
            (node_id,),
        ).fetchone()
        if row is None:
            raise TreeQueryError(f"unknown biological node: {node_id}")
        parent, retained, collapsed_parent, biological_depth = row
        return (
            None if parent is None else int(parent),
            int(retained),
            None if collapsed_parent is None else int(collapsed_parent),
            int(biological_depth),
        )

    def _leaf_row(self, leaf_id: int) -> tuple[int, int, int]:
        row = self._connection.execute(
            "SELECT parent_node_id, collapsed_parent_node_id, biological_depth "
            "FROM biological_leaves WHERE leaf_id = ?",
            (leaf_id,),
        ).fetchone()
        if row is None:
            raise TreeQueryError(f"unknown biological leaf: {leaf_id}")
        return int(row[0]), int(row[1]), int(row[2])

    def _validate_ref(self, taxon: TaxonRef, topology: Topology) -> None:
        self._validate_topology(topology)
        if taxon.kind == "leaf":
            self._leaf_row(taxon.taxon_id)
            return
        _, retained, _, _ = self._node_row(taxon.taxon_id)
        if topology == "collapsed" and not retained:
            raise TreeQueryError(
                f"node {taxon.taxon_id} is absent from the collapsed topology"
            )

    def node_depth(self, node_id: int, *, topology: Topology = "collapsed") -> int:
        """Return a node's root-relative depth in the requested topology."""
        self._validate_topology(topology)
        row = self._connection.execute(
            "SELECT biological_depth, retained_after_collapse, collapsed_depth "
            "FROM biological_nodes WHERE node_id = ?",
            (node_id,),
        ).fetchone()
        if row is None:
            raise TreeQueryError(f"unknown biological node: {node_id}")
        biological_depth, retained, collapsed_depth = row
        if topology == "collapsed":
            if not retained:
                raise TreeQueryError(
                    f"node {node_id} is absent from the collapsed topology"
                )
            assert collapsed_depth is not None
            return int(collapsed_depth)
        return int(biological_depth)

    def children(
        self, node_id: int, *, topology: Topology = "collapsed"
    ) -> tuple[TaxonRef, ...]:
        """Return deterministic direct children, preserving polytomy peers."""
        return tuple(
            branch.taxon
            for branch in self.child_branches(node_id, topology=topology)
        )

    def child_branches(
        self, node_id: int, *, topology: Topology = "collapsed"
    ) -> tuple[ChildBranch, ...]:
        """Return direct children together with descendant-leaf counts."""
        self._validate_ref(TaxonRef("node", node_id), topology)
        if topology == "biological":
            node_parent_column = "parent_node_id"
            leaf_parent_column = "parent_node_id"
            retained_clause = ""
        else:
            node_parent_column = "collapsed_parent_node_id"
            leaf_parent_column = "collapsed_parent_node_id"
            retained_clause = "AND retained_after_collapse = 1"
        rows = self._connection.execute(
            f"""
            SELECT kind, taxon_id, leaf_count FROM (
                SELECT 0 AS kind_order, 'node' AS kind, node_id AS taxon_id,
                       descendant_leaf_count AS leaf_count
                FROM biological_nodes
                WHERE {node_parent_column} = ? {retained_clause}
                UNION ALL
                SELECT 1 AS kind_order, 'leaf' AS kind, leaf_id AS taxon_id,
                       1 AS leaf_count
                FROM biological_leaves
                WHERE {leaf_parent_column} = ?
            )
            ORDER BY kind_order, taxon_id
            """,
            (node_id, node_id),
        ).fetchall()
        return tuple(
            ChildBranch(TaxonRef(kind, int(taxon_id)), int(leaf_count))
            for kind, taxon_id, leaf_count in rows
        )

    def parent(
        self, taxon: TaxonRef, *, topology: Topology = "collapsed"
    ) -> int | None:
        """Return a taxon's direct parent node, or ``None`` for the root."""
        self._validate_ref(taxon, topology)
        if taxon.kind == "leaf":
            biological_parent, collapsed_parent, _ = self._leaf_row(taxon.taxon_id)
            return biological_parent if topology == "biological" else collapsed_parent
        biological_parent, _, collapsed_parent, _ = self._node_row(taxon.taxon_id)
        return biological_parent if topology == "biological" else collapsed_parent

    def lineage_node_ids(
        self, taxon: TaxonRef, *, topology: Topology = "collapsed"
    ) -> tuple[int, ...]:
        """Return root-to-taxon internal nodes.

        For a node, the lineage includes that node. For a leaf, it ends at the
        leaf's parent node.
        """
        self._validate_ref(taxon, topology)
        if taxon.kind == "node":
            current: int | None = taxon.taxon_id
        else:
            current = self.parent(taxon, topology=topology)
        reversed_lineage: list[int] = []
        visited: set[int] = set()
        while current is not None:
            if current in visited:
                raise TreeQueryError(f"cycle encountered while querying node {current}")
            visited.add(current)
            reversed_lineage.append(current)
            current = self.parent(TaxonRef("node", current), topology=topology)
        lineage = tuple(reversed(reversed_lineage))
        if not lineage or lineage[0] != self.root_id:
            raise TreeQueryError(f"taxon is not reachable from root: {taxon}")
        return lineage

    def descendant_leaf_count(
        self, taxon: TaxonRef, *, topology: Topology = "collapsed"
    ) -> int:
        """Return the number of descendant leaves under a taxon."""
        self._validate_ref(taxon, topology)
        if taxon.kind == "leaf":
            return 1
        row = self._connection.execute(
            "SELECT descendant_leaf_count FROM biological_nodes WHERE node_id = ?",
            (taxon.taxon_id,),
        ).fetchone()
        assert row is not None
        return int(row[0])

    def iter_descendant_leaf_ids(
        self,
        taxon: TaxonRef,
        *,
        topology: Topology = "collapsed",
        limit: int | None = None,
    ) -> Iterator[int]:
        """Yield descendant leaf IDs in deterministic ascending order."""
        self._validate_ref(taxon, topology)
        if limit is not None and limit <= 0:
            raise TreeQueryError(f"limit must be positive: {limit}")
        if taxon.kind == "leaf":
            yield taxon.taxon_id
            return

        if topology == "biological":
            node_parent_column = "parent_node_id"
            leaf_parent_column = "parent_node_id"
            retained_clause = ""
        else:
            node_parent_column = "collapsed_parent_node_id"
            leaf_parent_column = "collapsed_parent_node_id"
            retained_clause = "AND child.retained_after_collapse = 1"
        limit_clause = "LIMIT ?" if limit is not None else ""
        parameters: tuple[int, ...] = (
            (taxon.taxon_id, limit) if limit is not None else (taxon.taxon_id,)
        )
        cursor = self._connection.execute(
            f"""
            WITH RECURSIVE descendant_nodes(node_id) AS (
                SELECT ?
                UNION ALL
                SELECT child.node_id
                FROM biological_nodes child
                JOIN descendant_nodes parent
                  ON child.{node_parent_column} = parent.node_id
                WHERE 1 = 1 {retained_clause}
            )
            SELECT leaf.leaf_id
            FROM biological_leaves leaf
            JOIN descendant_nodes parent
              ON leaf.{leaf_parent_column} = parent.node_id
            ORDER BY leaf.leaf_id
            {limit_clause}
            """,
            parameters,
        )
        for (leaf_id,) in cursor:
            yield int(leaf_id)

    def lowest_common_ancestor(
        self,
        left: TaxonRef,
        right: TaxonRef,
        *,
        topology: Topology = "collapsed",
    ) -> int:
        """Return the deepest internal node shared by two taxa."""
        left_lineage = self.lineage_node_ids(left, topology=topology)
        right_lineage = self.lineage_node_ids(right, topology=topology)
        common = self.root_id
        for left_node, right_node in zip(left_lineage, right_lineage, strict=False):
            if left_node != right_node:
                break
            common = left_node
        return common

    def candidate_sister_groups(
        self,
        target_leaf_id: int,
        *,
        topology: Topology = "collapsed",
    ) -> tuple[SisterGroup, ...]:
        """Return candidate-bearing divergence tiers from root to target."""
        target = TaxonRef("leaf", target_leaf_id)
        self._validate_ref(target, topology)
        groups_from_target: list[SisterGroup] = []
        target_branch = target
        while True:
            ancestor = self.parent(target_branch, topology=topology)
            if ancestor is None:
                break
            sisters = tuple(
                branch
                for branch in self.child_branches(ancestor, topology=topology)
                if branch.taxon != target_branch
            )
            if sisters:
                groups_from_target.append(
                    SisterGroup(
                        ancestor_node_id=ancestor,
                        ancestor_depth=self.node_depth(ancestor, topology=topology),
                        target_branch=target_branch,
                        sister_branches=sisters,
                    )
                )
            target_branch = TaxonRef("node", ancestor)
        return tuple(reversed(groups_from_target))


def _render_group(group: SisterGroup) -> dict[str, object]:
    value = asdict(group)
    value["candidate_leaf_capacity"] = group.candidate_leaf_capacity
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect a target lineage and candidate-bearing sister groups."
    )
    parser.add_argument("target_leaf_id", type=int)
    parser.add_argument("--tree", type=Path, default=DEFAULT_TREE_DATABASE)
    parser.add_argument(
        "--topology",
        choices=("biological", "collapsed"),
        default="collapsed",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        with BiologicalTree.open(args.tree) as tree:
            target = TaxonRef("leaf", args.target_leaf_id)
            result = {
                "target": asdict(target),
                "topology": args.topology,
                "lineage_node_ids": tree.lineage_node_ids(
                    target, topology=args.topology
                ),
                "candidate_sister_groups": [
                    _render_group(group)
                    for group in tree.candidate_sister_groups(
                        args.target_leaf_id, topology=args.topology
                    )
                ],
            }
    except (sqlite3.Error, TreeQueryError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
