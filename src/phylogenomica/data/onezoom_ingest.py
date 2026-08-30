"""Normalize a verified OneZoom Docker snapshot into SQLite.

The raw Docker extraction is a source artifact: its signed ``real_parent``
values and display-oriented ``parent`` values are preserved exactly.  The
normalized tables add an explicit biological parent, where a negative
``real_parent`` denotes membership in a OneZoom polytomy and its absolute
value identifies the biological parent.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
import tempfile
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from phylogenomica.data.onezoom_docker import (
    DEFAULT_OUTPUT_DIR as DEFAULT_RAW_SNAPSHOT,
)
from phylogenomica.data.onezoom_docker import EXPORT_SPECS, ExportSpec
from phylogenomica.data.onezoom_download import sha256_file
from phylogenomica.data.onezoom_static import find_snapshot_file, read_topology
from phylogenomica.tree.bracket_audit import audit_bracket_topology

INGESTER_VERSION = 2
DATABASE_SCHEMA_VERSION = 1
DATABASE_FILENAME = "onezoom.sqlite3"
DEFAULT_PROCESSED_ROOT = Path("data/processed/onezoom")
INSERT_BATCH_SIZE = 10_000


class OneZoomIngestionError(RuntimeError):
    """Raised when a raw snapshot cannot be normalized safely."""


SCHEMA_SQL = """
PRAGMA application_id = 0x5048594C;
PRAGMA user_version = 1;

CREATE TABLE dataset_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE nodes (
    node_id INTEGER PRIMARY KEY,
    display_parent_id INTEGER,
    biological_parent_id INTEGER,
    source_parent INTEGER NOT NULL,
    source_real_parent INTEGER NOT NULL,
    is_polytomy_scaffold INTEGER NOT NULL CHECK (is_polytomy_scaffold IN (0, 1)),
    node_rgt INTEGER NOT NULL,
    leaf_lft INTEGER NOT NULL,
    leaf_rgt INTEGER NOT NULL,
    scientific_name TEXT,
    age_ma REAL,
    ott_id INTEGER,
    wikidata_id INTEGER,
    wikipedia_languages INTEGER,
    eol_id INTEGER,
    raw_popularity REAL,
    popularity REAL,
    ncbi_id INTEGER,
    ifung_id INTEGER,
    worms_id INTEGER,
    irmng_id INTEGER,
    gbif_id INTEGER,
    ipni_id INTEGER,
    synthesized_vernacular TEXT,
    most_popular_leaf_id INTEGER,
    most_popular_leaf_ott_id INTEGER
);

CREATE TABLE leaves (
    leaf_id INTEGER PRIMARY KEY,
    display_parent_id INTEGER NOT NULL,
    biological_parent_id INTEGER NOT NULL,
    source_parent INTEGER NOT NULL,
    source_real_parent INTEGER NOT NULL,
    is_polytomy_member INTEGER NOT NULL CHECK (is_polytomy_member IN (0, 1)),
    scientific_name TEXT,
    extinction_date_ma REAL,
    ott_id INTEGER,
    wikidata_id INTEGER,
    wikipedia_languages INTEGER,
    eol_id INTEGER,
    raw_popularity REAL,
    popularity REAL,
    popularity_rank INTEGER,
    ncbi_id INTEGER,
    ifung_id INTEGER,
    worms_id INTEGER,
    irmng_id INTEGER,
    gbif_id INTEGER,
    ipni_id INTEGER
);

CREATE TABLE node_representatives (
    node_id INTEGER NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('any', 'verified', 'public_domain')),
    position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 8),
    ott_id INTEGER NOT NULL,
    PRIMARY KEY (node_id, category, position)
) WITHOUT ROWID;

CREATE TABLE vernacular_names (
    source_table TEXT NOT NULL CHECK (
        source_table IN ('vernacular_by_ott', 'vernacular_by_name')
    ),
    source_row_id INTEGER NOT NULL,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('ott', 'scientific_name')),
    ott_id INTEGER,
    scientific_name TEXT,
    vernacular_name TEXT NOT NULL,
    language_primary TEXT,
    language_full TEXT,
    preferred INTEGER NOT NULL CHECK (preferred IN (0, 1)),
    source_code INTEGER,
    source_id TEXT,
    updated_at TEXT,
    PRIMARY KEY (source_table, source_row_id),
    CHECK (
        (subject_type = 'ott' AND ott_id IS NOT NULL AND scientific_name IS NULL)
        OR
        (subject_type = 'scientific_name'
            AND ott_id IS NULL AND scientific_name IS NOT NULL)
    )
) WITHOUT ROWID;

CREATE TABLE images (
    source_table TEXT NOT NULL CHECK (
        source_table IN ('images_by_ott', 'images_by_name')
    ),
    source_row_id INTEGER NOT NULL,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('ott', 'scientific_name')),
    ott_id INTEGER,
    scientific_name TEXT,
    source_code INTEGER,
    source_id TEXT,
    url TEXT,
    rating INTEGER,
    rating_confidence INTEGER,
    rights TEXT,
    license TEXT,
    updated_at TEXT,
    best_any INTEGER NOT NULL CHECK (best_any IN (0, 1)),
    overall_best_any INTEGER NOT NULL CHECK (overall_best_any IN (0, 1)),
    best_verified INTEGER NOT NULL CHECK (best_verified IN (0, 1)),
    overall_best_verified INTEGER NOT NULL CHECK (overall_best_verified IN (0, 1)),
    best_public_domain INTEGER NOT NULL CHECK (best_public_domain IN (0, 1)),
    overall_best_public_domain INTEGER NOT NULL CHECK (
        overall_best_public_domain IN (0, 1)
    ),
    PRIMARY KEY (source_table, source_row_id),
    CHECK (
        (subject_type = 'ott' AND ott_id IS NOT NULL AND scientific_name IS NULL)
        OR
        (subject_type = 'scientific_name'
            AND ott_id IS NULL AND scientific_name IS NOT NULL)
    )
) WITHOUT ROWID;
"""


INDEX_SQL = """
CREATE INDEX nodes_display_parent_idx ON nodes(display_parent_id);
CREATE INDEX nodes_biological_parent_idx ON nodes(biological_parent_id);
CREATE INDEX nodes_ott_idx ON nodes(ott_id) WHERE ott_id IS NOT NULL;
CREATE INDEX leaves_display_parent_idx ON leaves(display_parent_id);
CREATE INDEX leaves_biological_parent_idx ON leaves(biological_parent_id);
CREATE INDEX leaves_ott_idx ON leaves(ott_id) WHERE ott_id IS NOT NULL;
CREATE INDEX leaves_popularity_rank_idx
    ON leaves(popularity_rank) WHERE popularity_rank IS NOT NULL;
CREATE INDEX node_representatives_ott_idx ON node_representatives(ott_id);
CREATE INDEX vernacular_names_ott_idx
    ON vernacular_names(ott_id) WHERE ott_id IS NOT NULL;
CREATE INDEX vernacular_names_scientific_name_idx
    ON vernacular_names(scientific_name) WHERE scientific_name IS NOT NULL;
CREATE INDEX images_ott_idx ON images(ott_id) WHERE ott_id IS NOT NULL;
CREATE INDEX images_scientific_name_idx
    ON images(scientific_name) WHERE scientific_name IS NOT NULL;
"""


NODE_INSERT = """
INSERT INTO nodes VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
"""

LEAF_INSERT = """
INSERT INTO leaves VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
"""

VERNACULAR_INSERT = """
INSERT INTO vernacular_names VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
"""

IMAGE_INSERT = """
INSERT INTO images VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
"""


def _decode_mysql_field(value: str) -> str | None:
    """Decode the escaping used by the MySQL batch client."""
    if value == "NULL":
        return None
    if "\\" not in value:
        return value

    escapes = {
        "0": "\0",
        "b": "\b",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "Z": "\x1a",
        "\\": "\\",
    }
    decoded: list[str] = []
    position = 0
    while position < len(value):
        character = value[position]
        if character != "\\":
            decoded.append(character)
            position += 1
            continue
        if position + 1 == len(value):
            raise OneZoomIngestionError("trailing backslash in MySQL batch field")
        escaped = value[position + 1]
        try:
            decoded.append(escapes[escaped])
        except KeyError as error:
            raise OneZoomIngestionError(
                f"unknown MySQL batch escape: \\{escaped}"
            ) from error
        position += 2
    return "".join(decoded)


def read_mysql_batch_tsv(
    path: Path, expected_columns: Sequence[str]
) -> Iterator[tuple[str | None, ...]]:
    """Yield decoded rows from a gzip-compressed MySQL batch export."""
    try:
        # MySQL's batch client escaped LF in the reviewed snapshot but retained
        # at least one literal CR inside a text field.  Restrict record
        # splitting to LF so Python's universal-newline handling does not turn
        # that embedded CR into a false row boundary.
        source = gzip.open(path, "rt", encoding="utf-8", newline="\n")
        with source:
            header_line = source.readline()
            if not header_line:
                raise OneZoomIngestionError(f"empty TSV export: {path}")
            header = tuple(header_line.rstrip("\r\n").split("\t"))
            if header != tuple(expected_columns):
                raise OneZoomIngestionError(
                    f"unexpected TSV columns in {path.name}: "
                    f"expected {list(expected_columns)!r}, found {list(header)!r}"
                )

            width = len(header)
            for row_number, line in enumerate(source, start=2):
                fields = line.rstrip("\r\n").split("\t")
                if len(fields) != width:
                    raise OneZoomIngestionError(
                        f"{path.name} row {row_number} has {len(fields)} fields; "
                        f"expected {width}"
                    )
                yield tuple(_decode_mysql_field(field) for field in fields)
    except (OSError, EOFError, UnicodeDecodeError) as error:
        raise OneZoomIngestionError(f"cannot read TSV export: {path}") from error


def _required(value: str | None, field: str) -> str:
    if value is None:
        raise OneZoomIngestionError(f"required field {field} is NULL")
    return value


def _integer(value: str | None, field: str, *, required: bool = False) -> int | None:
    if value is None:
        if required:
            _required(value, field)
        return None
    try:
        return int(value)
    except ValueError as error:
        raise OneZoomIngestionError(f"invalid integer in {field}: {value!r}") from error


def _real(value: str | None, field: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError as error:
        raise OneZoomIngestionError(
            f"invalid real number in {field}: {value!r}"
        ) from error


def _boolean(value: str | None, field: str) -> int:
    parsed = _integer(value, field, required=True)
    if parsed not in (0, 1):
        raise OneZoomIngestionError(f"invalid boolean in {field}: {value!r}")
    return parsed


def _row_dict(spec: ExportSpec, row: Sequence[str | None]) -> dict[str, str | None]:
    return dict(zip(spec.columns, row, strict=True))


def _file_records(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = manifest.get("files")
    if not isinstance(records, list):
        raise OneZoomIngestionError("raw manifest files must be a list")
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("name"), str):
            raise OneZoomIngestionError("invalid file record in raw manifest")
        name = record["name"]
        if name in indexed:
            raise OneZoomIngestionError(f"duplicate raw manifest file: {name}")
        indexed[name] = record
    return indexed


def load_and_verify_manifest(snapshot_dir: Path) -> tuple[dict[str, Any], str]:
    """Validate the raw manifest, schemas, sizes, and checksums."""
    manifest_path = snapshot_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OneZoomIngestionError(
            f"cannot read raw manifest: {manifest_path}"
        ) from error

    if manifest.get("schema_version") != 1:
        raise OneZoomIngestionError(
            f"unsupported raw manifest schema: {manifest.get('schema_version')!r}"
        )
    tree_version = manifest.get("tree_version")
    if (
        not isinstance(tree_version, str)
        or not tree_version.isascii()
        or not tree_version.isdigit()
    ):
        raise OneZoomIngestionError(f"invalid tree version: {tree_version!r}")

    export_format = manifest.get("export_format", {})
    expected_format = {
        "type": "gzip-compressed MySQL batch TSV",
        "encoding": "utf-8",
        "header": True,
        "row_order": "ascending primary id",
    }
    for key, expected in expected_format.items():
        if export_format.get(key) != expected:
            raise OneZoomIngestionError(
                f"unsupported export format {key}: {export_format.get(key)!r}"
            )

    records = _file_records(manifest)
    for spec in EXPORT_SPECS:
        record = records.get(spec.filename)
        if record is None:
            raise OneZoomIngestionError(f"raw manifest is missing {spec.filename}")
        if (
            record.get("kind") != "mysql_projection"
            or record.get("table") != spec.table
        ):
            raise OneZoomIngestionError(f"invalid source record for {spec.filename}")
        if record.get("columns") != list(spec.columns):
            raise OneZoomIngestionError(
                f"unsupported {spec.table} projection: {record.get('columns')!r}"
            )

    required_static = (
        f"completetree_{tree_version}.js.gz",
        f"cut_position_map_{tree_version}.js.gz",
        f"dates_{tree_version}.js.gz",
    )
    for filename in (*[spec.filename for spec in EXPORT_SPECS], *required_static):
        record = records.get(filename)
        if record is None:
            raise OneZoomIngestionError(f"raw manifest is missing {filename}")
        path = snapshot_dir / filename
        try:
            size = path.stat().st_size
        except OSError as error:
            raise OneZoomIngestionError(f"raw input is missing: {path}") from error
        if size != record.get("bytes"):
            raise OneZoomIngestionError(
                f"raw input size mismatch for {filename}: expected "
                f"{record.get('bytes')!r}, found {size}"
            )
        checksum = sha256_file(path)
        if checksum != record.get("sha256"):
            raise OneZoomIngestionError(
                f"raw input checksum mismatch for {filename}: expected "
                f"{record.get('sha256')!r}, found {checksum}"
            )
    return manifest, sha256_file(manifest_path)


def _flush(
    connection: sqlite3.Connection,
    sql: str,
    rows: list[tuple[Any, ...]],
) -> None:
    if rows:
        connection.executemany(sql, rows)
        rows.clear()


def _load_nodes(
    connection: sqlite3.Connection, path: Path, spec: ExportSpec
) -> tuple[int, int, int]:
    nodes: list[tuple[Any, ...]] = []
    representatives: list[tuple[Any, ...]] = []
    count = 0
    negative_real_parents = 0
    representative_count = 0
    representative_groups = (
        ("any", "rep"),
        ("verified", "rtr"),
        ("public_domain", "rpd"),
    )

    for row in read_mysql_batch_tsv(path, spec.columns):
        values = _row_dict(spec, row)
        node_id = _integer(values["id"], "ordered_nodes.id", required=True)
        source_parent = _integer(
            values["parent"], "ordered_nodes.parent", required=True
        )
        real_parent = _integer(
            values["real_parent"], "ordered_nodes.real_parent", required=True
        )
        assert node_id is not None
        assert source_parent is not None
        assert real_parent is not None
        if real_parent < 0:
            negative_real_parents += 1

        nodes.append(
            (
                node_id,
                None if node_id == 1 else source_parent,
                abs(real_parent) or None,
                source_parent,
                real_parent,
                int(real_parent < 0),
                _integer(values["node_rgt"], "ordered_nodes.node_rgt", required=True),
                _integer(values["leaf_lft"], "ordered_nodes.leaf_lft", required=True),
                _integer(values["leaf_rgt"], "ordered_nodes.leaf_rgt", required=True),
                values["name"],
                _real(values["age"], "ordered_nodes.age"),
                _integer(values["ott"], "ordered_nodes.ott"),
                _integer(values["wikidata"], "ordered_nodes.wikidata"),
                _integer(
                    values["wikipedia_lang_flag"],
                    "ordered_nodes.wikipedia_lang_flag",
                ),
                _integer(values["eol"], "ordered_nodes.eol"),
                _real(values["raw_popularity"], "ordered_nodes.raw_popularity"),
                _real(values["popularity"], "ordered_nodes.popularity"),
                _integer(values["ncbi"], "ordered_nodes.ncbi"),
                _integer(values["ifung"], "ordered_nodes.ifung"),
                _integer(values["worms"], "ordered_nodes.worms"),
                _integer(values["irmng"], "ordered_nodes.irmng"),
                _integer(values["gbif"], "ordered_nodes.gbif"),
                _integer(values["ipni"], "ordered_nodes.ipni"),
                values["vern_synth"],
                _integer(values["popleaf"], "ordered_nodes.popleaf"),
                _integer(values["popleaf_ott"], "ordered_nodes.popleaf_ott"),
            )
        )
        for category, prefix in representative_groups:
            for position in range(1, 9):
                ott_id = _integer(
                    values[f"{prefix}{position}"],
                    f"ordered_nodes.{prefix}{position}",
                )
                if ott_id is not None:
                    representatives.append((node_id, category, position, ott_id))
                    representative_count += 1

        count += 1
        if len(nodes) >= INSERT_BATCH_SIZE:
            _flush(connection, NODE_INSERT, nodes)
        if len(representatives) >= INSERT_BATCH_SIZE:
            _flush(
                connection,
                "INSERT INTO node_representatives VALUES (?, ?, ?, ?)",
                representatives,
            )

    _flush(connection, NODE_INSERT, nodes)
    _flush(
        connection,
        "INSERT INTO node_representatives VALUES (?, ?, ?, ?)",
        representatives,
    )
    return count, negative_real_parents, representative_count


def _load_leaves(
    connection: sqlite3.Connection, path: Path, spec: ExportSpec
) -> tuple[int, int]:
    leaves: list[tuple[Any, ...]] = []
    count = 0
    negative_real_parents = 0
    for row in read_mysql_batch_tsv(path, spec.columns):
        values = _row_dict(spec, row)
        leaf_id = _integer(values["id"], "ordered_leaves.id", required=True)
        source_parent = _integer(
            values["parent"], "ordered_leaves.parent", required=True
        )
        real_parent = _integer(
            values["real_parent"], "ordered_leaves.real_parent", required=True
        )
        assert leaf_id is not None
        assert source_parent is not None
        assert real_parent is not None
        if real_parent == 0:
            raise OneZoomIngestionError(
                f"ordered_leaves.id={leaf_id} has no biological parent"
            )
        if real_parent < 0:
            negative_real_parents += 1
        leaves.append(
            (
                leaf_id,
                source_parent,
                abs(real_parent),
                source_parent,
                real_parent,
                int(real_parent < 0),
                values["name"],
                _real(values["extinction_date"], "ordered_leaves.extinction_date"),
                _integer(values["ott"], "ordered_leaves.ott"),
                _integer(values["wikidata"], "ordered_leaves.wikidata"),
                _integer(
                    values["wikipedia_lang_flag"],
                    "ordered_leaves.wikipedia_lang_flag",
                ),
                _integer(values["eol"], "ordered_leaves.eol"),
                _real(values["raw_popularity"], "ordered_leaves.raw_popularity"),
                _real(values["popularity"], "ordered_leaves.popularity"),
                _integer(values["popularity_rank"], "ordered_leaves.popularity_rank"),
                _integer(values["ncbi"], "ordered_leaves.ncbi"),
                _integer(values["ifung"], "ordered_leaves.ifung"),
                _integer(values["worms"], "ordered_leaves.worms"),
                _integer(values["irmng"], "ordered_leaves.irmng"),
                _integer(values["gbif"], "ordered_leaves.gbif"),
                _integer(values["ipni"], "ordered_leaves.ipni"),
            )
        )
        count += 1
        if len(leaves) >= INSERT_BATCH_SIZE:
            _flush(connection, LEAF_INSERT, leaves)
    _flush(connection, LEAF_INSERT, leaves)
    return count, negative_real_parents


def _load_vernacular(
    connection: sqlite3.Connection, path: Path, spec: ExportSpec
) -> int:
    rows: list[tuple[Any, ...]] = []
    count = 0
    by_ott = spec.table == "vernacular_by_ott"
    subject_field = "ott" if by_ott else "name"
    for row in read_mysql_batch_tsv(path, spec.columns):
        values = _row_dict(spec, row)
        subject = _required(values[subject_field], f"{spec.table}.{subject_field}")
        vernacular = _required(values["vernacular"], f"{spec.table}.vernacular")
        rows.append(
            (
                spec.table,
                _integer(values["id"], f"{spec.table}.id", required=True),
                "ott" if by_ott else "scientific_name",
                int(subject) if by_ott else None,
                None if by_ott else subject,
                vernacular,
                values["lang_primary"],
                values["lang_full"],
                _boolean(values["preferred"], f"{spec.table}.preferred"),
                _integer(values["src"], f"{spec.table}.src"),
                values["src_id"],
                values["updated"],
            )
        )
        count += 1
        if len(rows) >= INSERT_BATCH_SIZE:
            _flush(connection, VERNACULAR_INSERT, rows)
    _flush(connection, VERNACULAR_INSERT, rows)
    return count


def _load_images(connection: sqlite3.Connection, path: Path, spec: ExportSpec) -> int:
    rows: list[tuple[Any, ...]] = []
    count = 0
    by_ott = spec.table == "images_by_ott"
    subject_field = "ott" if by_ott else "name"
    for row in read_mysql_batch_tsv(path, spec.columns):
        values = _row_dict(spec, row)
        subject = _required(values[subject_field], f"{spec.table}.{subject_field}")
        rows.append(
            (
                spec.table,
                _integer(values["id"], f"{spec.table}.id", required=True),
                "ott" if by_ott else "scientific_name",
                int(subject) if by_ott else None,
                None if by_ott else subject,
                _integer(values["src"], f"{spec.table}.src"),
                values["src_id"],
                values["url"],
                _integer(values["rating"], f"{spec.table}.rating"),
                _integer(
                    values["rating_confidence"], f"{spec.table}.rating_confidence"
                ),
                values["rights"],
                values["licence"],
                values["updated"],
                _boolean(values["best_any"], f"{spec.table}.best_any"),
                _boolean(values["overall_best_any"], f"{spec.table}.overall_best_any"),
                _boolean(values["best_verified"], f"{spec.table}.best_verified"),
                _boolean(
                    values["overall_best_verified"],
                    f"{spec.table}.overall_best_verified",
                ),
                _boolean(values["best_pd"], f"{spec.table}.best_pd"),
                _boolean(values["overall_best_pd"], f"{spec.table}.overall_best_pd"),
            )
        )
        count += 1
        if len(rows) >= INSERT_BATCH_SIZE:
            _flush(connection, IMAGE_INSERT, rows)
    _flush(connection, IMAGE_INSERT, rows)
    return count


def _single_integer(connection: sqlite3.Connection, sql: str) -> int:
    value = connection.execute(sql).fetchone()
    assert value is not None
    return int(value[0])


def _validate_database(
    connection: sqlite3.Connection,
    *,
    manifest: dict[str, Any],
    snapshot_dir: Path,
    loaded_counts: dict[str, int],
) -> dict[str, Any]:
    expected_counts = manifest.get("database", {}).get("row_counts", {})
    for table, loaded in loaded_counts.items():
        expected = expected_counts.get(table)
        if expected != loaded:
            raise OneZoomIngestionError(
                f"{table} row mismatch: expected {expected!r}, loaded {loaded}"
            )

    node_count = loaded_counts["ordered_nodes"]
    leaf_count = loaded_counts["ordered_leaves"]
    node_bounds = connection.execute(
        "SELECT MIN(node_id), MAX(node_id), COUNT(*) FROM nodes"
    ).fetchone()
    leaf_bounds = connection.execute(
        "SELECT MIN(leaf_id), MAX(leaf_id), COUNT(*) FROM leaves"
    ).fetchone()
    if node_bounds != (1, node_count, node_count):
        raise OneZoomIngestionError(f"node IDs are not contiguous: {node_bounds!r}")
    if leaf_bounds != (1, leaf_count, leaf_count):
        raise OneZoomIngestionError(f"leaf IDs are not contiguous: {leaf_bounds!r}")

    root = connection.execute(
        "SELECT node_id, source_parent, source_real_parent, display_parent_id, "
        "biological_parent_id FROM nodes WHERE biological_parent_id IS NULL"
    ).fetchall()
    expected_root_parent = -int(manifest["tree_version"])
    expected_root = [(1, expected_root_parent, 0, None, None)]
    if root != expected_root:
        raise OneZoomIngestionError(
            f"unexpected biological root: expected {expected_root!r}, found {root!r}"
        )

    validation_queries = {
        "orphan_display_nodes": """
            SELECT COUNT(*) FROM nodes child
            LEFT JOIN nodes parent ON parent.node_id = child.display_parent_id
            WHERE child.display_parent_id IS NOT NULL AND parent.node_id IS NULL
        """,
        "orphan_biological_nodes": """
            SELECT COUNT(*) FROM nodes child
            LEFT JOIN nodes parent ON parent.node_id = child.biological_parent_id
            WHERE child.biological_parent_id IS NOT NULL AND parent.node_id IS NULL
        """,
        "orphan_display_leaves": """
            SELECT COUNT(*) FROM leaves child
            LEFT JOIN nodes parent ON parent.node_id = child.display_parent_id
            WHERE parent.node_id IS NULL
        """,
        "orphan_biological_leaves": """
            SELECT COUNT(*) FROM leaves child
            LEFT JOIN nodes parent ON parent.node_id = child.biological_parent_id
            WHERE parent.node_id IS NULL
        """,
        "self_parented_biological_nodes": """
            SELECT COUNT(*) FROM nodes
            WHERE node_id = biological_parent_id
        """,
    }
    reference_checks = {
        name: _single_integer(connection, sql)
        for name, sql in validation_queries.items()
    }
    failures = {name: count for name, count in reference_checks.items() if count}
    if failures:
        raise OneZoomIngestionError(f"parent-reference validation failed: {failures}")

    topology = audit_bracket_topology(
        read_topology(find_snapshot_file(snapshot_dir, "completetree"))
    )
    if topology.display_internal_nodes != node_count or topology.leaves != leaf_count:
        raise OneZoomIngestionError(
            "database/static topology mismatch: "
            f"database has {node_count} nodes and {leaf_count} leaves; static tree "
            f"has {topology.display_internal_nodes} nodes and {topology.leaves} leaves"
        )

    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity != ("ok",):
        raise OneZoomIngestionError(f"SQLite integrity check failed: {integrity!r}")

    return {
        "sqlite_integrity_check": "ok",
        "contiguous_node_ids": True,
        "contiguous_leaf_ids": True,
        "root_node_id": 1,
        "root_source_parent": expected_root_parent,
        "parent_reference_checks": reference_checks,
        "matched_static_topology": topology.to_dict(),
    }


def _spec_by_table(table: str) -> ExportSpec:
    return next(spec for spec in EXPORT_SPECS if spec.table == table)


def _create_database(
    database_path: Path,
    *,
    snapshot_dir: Path,
    manifest: dict[str, Any],
    source_manifest_sha256: str,
) -> tuple[dict[str, int], dict[str, int], dict[str, Any]]:
    connection = sqlite3.connect(database_path)
    loaded_counts: dict[str, int] = {}
    derived_counts: dict[str, int] = {}
    try:
        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.executescript(SCHEMA_SQL)
        connection.executemany(
            "INSERT INTO dataset_metadata VALUES (?, ?)",
            (
                ("dataset_version", f"onezoom-{manifest['tree_version']}"),
                ("source", str(manifest.get("source", "OneZoom"))),
                ("source_tree_version", manifest["tree_version"]),
                ("source_manifest_sha256", source_manifest_sha256),
                ("ingester_version", str(INGESTER_VERSION)),
                ("database_schema_version", str(DATABASE_SCHEMA_VERSION)),
                (
                    "biological_parent_rule",
                    "NULL for root; otherwise abs(source_real_parent)",
                ),
            ),
        )

        print("loading ordered_nodes...", flush=True)
        node_count, negative_nodes, representative_count = _load_nodes(
            connection,
            snapshot_dir / "ordered_nodes.tsv.gz",
            _spec_by_table("ordered_nodes"),
        )
        loaded_counts["ordered_nodes"] = node_count
        derived_counts["nodes_with_negative_real_parent"] = negative_nodes
        derived_counts["node_representatives"] = representative_count

        print("loading ordered_leaves...", flush=True)
        leaf_count, negative_leaves = _load_leaves(
            connection,
            snapshot_dir / "ordered_leaves.tsv.gz",
            _spec_by_table("ordered_leaves"),
        )
        loaded_counts["ordered_leaves"] = leaf_count
        derived_counts["leaves_with_negative_real_parent"] = negative_leaves

        for table in ("vernacular_by_ott", "vernacular_by_name"):
            print(f"loading {table}...", flush=True)
            spec = _spec_by_table(table)
            loaded_counts[table] = _load_vernacular(
                connection, snapshot_dir / spec.filename, spec
            )
        for table in ("images_by_ott", "images_by_name"):
            print(f"loading {table}...", flush=True)
            spec = _spec_by_table(table)
            loaded_counts[table] = _load_images(
                connection, snapshot_dir / spec.filename, spec
            )

        derived_counts["vernacular_names"] = sum(
            loaded_counts[table]
            for table in ("vernacular_by_ott", "vernacular_by_name")
        )
        derived_counts["images"] = sum(
            loaded_counts[table] for table in ("images_by_ott", "images_by_name")
        )
        connection.commit()

        print("building indexes...", flush=True)
        connection.executescript(INDEX_SQL)
        connection.commit()

        print("validating normalized database...", flush=True)
        validation = _validate_database(
            connection,
            manifest=manifest,
            snapshot_dir=snapshot_dir,
            loaded_counts=loaded_counts,
        )
        connection.execute("VACUUM")
    except sqlite3.Error as error:
        raise OneZoomIngestionError(f"SQLite ingestion failed: {error}") from error
    finally:
        connection.close()
    return loaded_counts, derived_counts, validation


def ingest_snapshot(
    *,
    snapshot_dir: Path = DEFAULT_RAW_SNAPSHOT,
    output_dir: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Build an atomic, versioned normalized database from a raw snapshot."""
    manifest, source_manifest_sha256 = load_and_verify_manifest(snapshot_dir)
    tree_version = manifest["tree_version"]
    destination = output_dir or DEFAULT_PROCESSED_ROOT / tree_version
    if destination.exists():
        raise OneZoomIngestionError(f"processed output already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}.", dir=destination.parent
    ) as temporary:
        temporary_dir = Path(temporary)
        database_path = temporary_dir / DATABASE_FILENAME
        loaded_counts, derived_counts, validation = _create_database(
            database_path,
            snapshot_dir=snapshot_dir,
            manifest=manifest,
            source_manifest_sha256=source_manifest_sha256,
        )
        database_record = {
            "name": DATABASE_FILENAME,
            "bytes": database_path.stat().st_size,
            "sha256": sha256_file(database_path),
        }
        output_manifest: dict[str, Any] = {
            "schema_version": 1,
            "dataset_version": f"onezoom-{tree_version}",
            "source_tree_version": tree_version,
            "built_at": datetime.now(UTC).isoformat(),
            "ingester_version": INGESTER_VERSION,
            "database_schema_version": DATABASE_SCHEMA_VERSION,
            "runtime": {
                "python": sys.version.split()[0],
                "sqlite": sqlite3.sqlite_version,
            },
            "source": {
                "snapshot_directory": str(snapshot_dir),
                "manifest": "manifest.json",
                "manifest_sha256": source_manifest_sha256,
            },
            "normalization": {
                "display_parent": "source parent; root marker normalized to NULL",
                "biological_parent": (
                    "NULL for root; otherwise abs(source real_parent)"
                ),
                "negative_real_parent": (
                    "preserved and flagged as OneZoom polytomy membership/scaffolding"
                ),
                "topology_collapse": "not performed during ingestion",
            },
            "source_row_counts": loaded_counts,
            "derived_row_counts": derived_counts,
            "validation": validation,
            "database": database_record,
            "reproduction_command": (
                f"phylogenomica-ingest-onezoom {snapshot_dir} "
                f"--output-dir {destination}"
            ),
        }
        (temporary_dir / "manifest.json").write_text(
            json.dumps(output_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_dir.rename(destination)
    return destination, output_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normalize a verified OneZoom raw snapshot into SQLite."
    )
    parser.add_argument(
        "snapshot",
        nargs="?",
        type=Path,
        default=DEFAULT_RAW_SNAPSHOT,
        help=f"raw snapshot directory (default: {DEFAULT_RAW_SNAPSHOT})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "processed output directory "
            f"(default: {DEFAULT_PROCESSED_ROOT}/<tree-version>)"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        output_dir, manifest = ingest_snapshot(
            snapshot_dir=args.snapshot,
            output_dir=args.output_dir,
        )
    except OneZoomIngestionError as error:
        raise SystemExit(str(error)) from error
    database = manifest["database"]
    print(
        f"normalized {manifest['dataset_version']} to {output_dir} "
        f"({database['bytes']:,} bytes, sha256 {database['sha256']})",
        flush=True,
    )


if __name__ == "__main__":
    main()
