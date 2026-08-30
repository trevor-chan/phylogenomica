import gzip
import json
import sqlite3
from pathlib import Path

import pytest

from phylogenomica.data.onezoom_docker import EXPORT_SPECS, ExportSpec
from phylogenomica.data.onezoom_download import sha256_file
from phylogenomica.data.onezoom_ingest import (
    OneZoomIngestionError,
    ingest_snapshot,
    load_and_verify_manifest,
    read_mysql_batch_tsv,
)


def _mysql_encode(value: object | None) -> str:
    if value is None:
        return "NULL"
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\0", "\\0")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _write_tsv(
    path: Path, spec: ExportSpec, rows: list[dict[str, object | None]]
) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as output:
        output.write("\t".join(spec.columns) + "\n")
        for overrides in rows:
            encoded = (_mysql_encode(overrides.get(column)) for column in spec.columns)
            output.write("\t".join(encoded) + "\n")


def _write_gzip(path: Path, payload: bytes) -> None:
    with gzip.open(path, "wb") as output:
        output.write(payload)


def _make_snapshot(tmp_path: Path) -> Path:
    snapshot = tmp_path / "raw"
    snapshot.mkdir()
    rows = {
        "ordered_nodes": [
            {
                "id": 1,
                "parent": -1,
                "real_parent": 0,
                "node_rgt": 2,
                "leaf_lft": 1,
                "leaf_rgt": 3,
                "name": "Life",
                "rep1": 101,
                "popleaf": 1,
                "popleaf_ott": 101,
            },
            {
                "id": 2,
                "parent": 1,
                "real_parent": -1,
                "node_rgt": 1,
                "leaf_lft": 1,
                "leaf_rgt": 2,
            },
        ],
        "ordered_leaves": [
            {
                "id": 1,
                "parent": 2,
                "real_parent": 2,
                "name": "Alpha\tbeta",
                "ott": 101,
                "popularity_rank": 1,
            },
            {"id": 2, "parent": 2, "real_parent": -2, "name": "Gamma"},
            {"id": 3, "parent": 1, "real_parent": 1, "name": "Delta"},
        ],
        "vernacular_by_ott": [
            {
                "id": 1,
                "ott": 101,
                "vernacular": "alpha",
                "lang_primary": "en",
                "lang_full": "en",
                "preferred": 1,
            }
        ],
        "vernacular_by_name": [],
        "images_by_ott": [
            {
                "id": 1,
                "ott": 101,
                "src": 2,
                "src_id": 3,
                "url": "https://example.test/image.jpg",
                "rights": "Example creator",
                "licence": "CC BY 4.0",
                "best_any": 1,
                "overall_best_any": 1,
                "best_verified": 0,
                "overall_best_verified": 0,
                "best_pd": 0,
                "overall_best_pd": 0,
            }
        ],
        "images_by_name": [],
    }

    records: list[dict[str, object]] = []
    row_counts: dict[str, int] = {}
    for spec in EXPORT_SPECS:
        table_rows = rows[spec.table]
        path = snapshot / spec.filename
        _write_tsv(path, spec, table_rows)
        row_counts[spec.table] = len(table_rows)
        records.append(
            {
                "name": path.name,
                "kind": "mysql_projection",
                "table": spec.table,
                "columns": list(spec.columns),
                "rows": len(table_rows),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )

    static_payloads = {
        "completetree_1.js.gz": b"var rawData = '(())';\n",
        "cut_position_map_1.js.gz": b"var cut_position_map = {};\n",
        "dates_1.js.gz": b"var tree_date = {};\n",
    }
    for filename, payload in static_payloads.items():
        path = snapshot / filename
        _write_gzip(path, payload)
        records.append(
            {
                "name": filename,
                "kind": "matched_static_viewer_data",
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )

    manifest = {
        "schema_version": 1,
        "source": "test OneZoom snapshot",
        "tree_version": "1",
        "database": {"row_counts": row_counts},
        "export_format": {
            "type": "gzip-compressed MySQL batch TSV",
            "encoding": "utf-8",
            "header": True,
            "row_order": "ascending primary id",
            "gzip_mtime": 0,
        },
        "files": records,
    }
    (snapshot / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return snapshot


def test_decodes_mysql_batch_nulls_and_escapes(tmp_path: Path) -> None:
    path = tmp_path / "example.tsv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as output:
        output.write("id\tvalue\n1\tline\\nwith\\ttab\\\\slash\n2\tNULL\n")

    assert list(read_mysql_batch_tsv(path, ("id", "value"))) == [
        ("1", "line\nwith\ttab\\slash"),
        ("2", None),
    ]


def test_does_not_split_on_literal_carriage_return(tmp_path: Path) -> None:
    path = tmp_path / "example.tsv.gz"
    with gzip.open(path, "wb") as output:
        output.write(b"id\tvalue\n1\tfirst\r\\nsecond\n")

    assert list(read_mysql_batch_tsv(path, ("id", "value"))) == [
        ("1", "first\r\nsecond")
    ]


def test_rejects_unexpected_tsv_columns(tmp_path: Path) -> None:
    path = tmp_path / "example.tsv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as output:
        output.write("wrong\nvalue\n")

    with pytest.raises(OneZoomIngestionError, match="unexpected TSV columns"):
        list(read_mysql_batch_tsv(path, ("expected",)))


def test_rejects_unknown_source_projection(tmp_path: Path) -> None:
    snapshot = _make_snapshot(tmp_path)
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["columns"].append("unreviewed_column")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(OneZoomIngestionError, match="unsupported ordered_leaves"):
        load_and_verify_manifest(snapshot)


def test_ingests_normalized_snapshot_atomically(tmp_path: Path) -> None:
    snapshot = _make_snapshot(tmp_path)
    output = tmp_path / "processed" / "1"

    output_dir, manifest = ingest_snapshot(snapshot_dir=snapshot, output_dir=output)

    assert output_dir == output
    assert manifest["dataset_version"] == "onezoom-1"
    assert manifest["source_row_counts"]["ordered_leaves"] == 3
    assert manifest["validation"]["matched_static_topology"]["leaves"] == 3
    assert manifest["derived_row_counts"] == {
        "images": 1,
        "leaves_with_negative_real_parent": 1,
        "node_representatives": 1,
        "nodes_with_negative_real_parent": 1,
        "vernacular_names": 1,
    }

    connection = sqlite3.connect(output / "onezoom.sqlite3")
    try:
        root = connection.execute(
            "SELECT source_parent, display_parent_id, biological_parent_id "
            "FROM nodes WHERE node_id = 1"
        ).fetchone()
        scaffold = connection.execute(
            "SELECT source_real_parent, biological_parent_id, "
            "is_polytomy_scaffold FROM nodes WHERE node_id = 2"
        ).fetchone()
        leaf = connection.execute(
            "SELECT scientific_name, source_real_parent, biological_parent_id, "
            "is_polytomy_member FROM leaves WHERE leaf_id = 2"
        ).fetchone()
        representative = connection.execute(
            "SELECT category, position, ott_id FROM node_representatives"
        ).fetchone()
    finally:
        connection.close()

    assert root == (-1, None, None)
    assert scaffold == (-1, 1, 1)
    assert leaf == ("Gamma", -2, 2, 1)
    assert representative == ("any", 1, 101)
    assert (output / "manifest.json").is_file()

    with pytest.raises(OneZoomIngestionError, match="already exists"):
        ingest_snapshot(snapshot_dir=snapshot, output_dir=output)
