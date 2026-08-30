"""Extract a reviewed OneZoom snapshot from the official Docker image.

The historical OneZoom image contains a complete MySQL data directory together
with the static viewer files generated from that database.  It also contains
tables and columns that Phylogenomica must not acquire.  This module therefore
exports an explicit allowlist rather than dumping the database wholesale.
"""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from phylogenomica.data.onezoom_download import sha256_file, validate_payload

DEFAULT_CONTAINER = "phylogenomica-onezoom-2022"
DEFAULT_OUTPUT_DIR = Path("data/raw/onezoom/docker-2022-02-07")
EXPECTED_IMAGE_ID = (
    "sha256:8d45f6f91bf0e9370803642334eb172778bcfad70ad1f7b4513d5db2cbc5dd3e"
)
EXPECTED_TREE_VERSION = "27400288"
STATIC_DATA_DIR = "/opt/web2py/applications/OZtree/static/FinalOutputs/data"
STATIC_STEMS = ("completetree", "cut_position_map", "dates")


class DockerExtractionError(RuntimeError):
    """Raised when the Docker source cannot be exported safely."""


@dataclass(frozen=True)
class ExportSpec:
    """A reviewed table projection permitted in the raw snapshot."""

    table: str
    columns: tuple[str, ...]

    @property
    def filename(self) -> str:
        return f"{self.table}.tsv.gz"


EXPORT_SPECS = (
    ExportSpec(
        "ordered_leaves",
        (
            "id",
            "parent",
            "real_parent",
            "name",
            "extinction_date",
            "ott",
            "wikidata",
            "wikipedia_lang_flag",
            "eol",
            "raw_popularity",
            "popularity",
            "popularity_rank",
            "ncbi",
            "ifung",
            "worms",
            "irmng",
            "gbif",
            "ipni",
        ),
    ),
    ExportSpec(
        "ordered_nodes",
        (
            "id",
            "parent",
            "real_parent",
            "node_rgt",
            "leaf_lft",
            "leaf_rgt",
            "name",
            "age",
            "ott",
            "wikidata",
            "wikipedia_lang_flag",
            "eol",
            "raw_popularity",
            "popularity",
            "ncbi",
            "ifung",
            "worms",
            "irmng",
            "gbif",
            "ipni",
            "vern_synth",
            "rep1",
            "rep2",
            "rep3",
            "rep4",
            "rep5",
            "rep6",
            "rep7",
            "rep8",
            "rtr1",
            "rtr2",
            "rtr3",
            "rtr4",
            "rtr5",
            "rtr6",
            "rtr7",
            "rtr8",
            "rpd1",
            "rpd2",
            "rpd3",
            "rpd4",
            "rpd5",
            "rpd6",
            "rpd7",
            "rpd8",
            "popleaf",
            "popleaf_ott",
        ),
    ),
    ExportSpec(
        "vernacular_by_ott",
        (
            "id",
            "ott",
            "vernacular",
            "lang_primary",
            "lang_full",
            "preferred",
            "src",
            "src_id",
            "updated",
        ),
    ),
    ExportSpec(
        "vernacular_by_name",
        (
            "id",
            "name",
            "vernacular",
            "lang_primary",
            "lang_full",
            "preferred",
            "src",
            "src_id",
            "updated",
        ),
    ),
    ExportSpec(
        "images_by_ott",
        (
            "id",
            "ott",
            "src",
            "src_id",
            "url",
            "rating",
            "rating_confidence",
            "rights",
            "licence",
            "updated",
            "best_any",
            "overall_best_any",
            "best_verified",
            "overall_best_verified",
            "best_pd",
            "overall_best_pd",
        ),
    ),
    ExportSpec(
        "images_by_name",
        (
            "id",
            "name",
            "src",
            "src_id",
            "url",
            "rating",
            "rating_confidence",
            "rights",
            "licence",
            "updated",
            "best_any",
            "overall_best_any",
            "best_verified",
            "overall_best_verified",
            "best_pd",
            "overall_best_pd",
        ),
    ),
)

# These are deliberately documented here so a future schema update cannot add
# them to an export projection without an obvious policy change.
FORBIDDEN_COLUMNS = {
    "ordered_leaves": ("iucn", "price"),
    "ordered_nodes": (
        "iucnNE",
        "iucnDD",
        "iucnLC",
        "iucnNT",
        "iucnVU",
        "iucnEN",
        "iucnCR",
        "iucnEW",
        "iucnEX",
    ),
}


def _docker(
    args: Sequence[str], *, text: bool = True
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    command = ["docker", *args]
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=text,
        )
    except FileNotFoundError as error:
        raise DockerExtractionError("docker executable was not found") from error
    except subprocess.CalledProcessError as error:
        stderr = error.stderr
        if isinstance(stderr, bytes):
            detail = stderr.decode("utf-8", errors="replace").strip()
        else:
            detail = (stderr or "").strip()
        raise DockerExtractionError(
            f"docker command failed: {' '.join(command)}: {detail}"
        ) from error


def validate_container(
    inspection: dict[str, Any], *, expected_image_id: str
) -> None:
    """Reject a container that could contact or expose the OneZoom service."""
    if inspection.get("Image") != expected_image_id:
        raise DockerExtractionError(
            "container image mismatch: "
            f"expected {expected_image_id}, found {inspection.get('Image')}"
        )
    if not inspection.get("State", {}).get("Running"):
        raise DockerExtractionError("OneZoom extraction container is not running")
    if inspection.get("HostConfig", {}).get("NetworkMode") != "none":
        raise DockerExtractionError(
            "OneZoom extraction container must use network none"
        )
    if inspection.get("HostConfig", {}).get("PortBindings"):
        raise DockerExtractionError("OneZoom extraction container publishes host ports")
    if inspection.get("Config", {}).get("Cmd") != ["/sbin/my_init"]:
        raise DockerExtractionError(
            "container must override OneZoom's default IUCN command with /sbin/my_init"
        )


def parse_tree_version(root_parent: str) -> str:
    """Decode OneZoom's negative root-parent tree-version marker."""
    try:
        parent = int(root_parent)
    except ValueError as error:
        raise DockerExtractionError(
            f"invalid OneZoom root parent: {root_parent!r}"
        ) from error
    if parent >= 0:
        raise DockerExtractionError(
            f"expected a negative tree version in root parent, found {parent}"
        )
    return str(-parent)


def build_select_sql(spec: ExportSpec) -> str:
    """Build a deterministic projection for a reviewed export specification."""
    columns = ", ".join(f"`{column}`" for column in spec.columns)
    return f"SELECT {columns} FROM `{spec.table}` ORDER BY `id`"


def _mysql(container: str, sql: str) -> str:
    result = _docker(
        (
            "exec",
            container,
            "mysql",
            "--protocol=socket",
            "--default-character-set=utf8mb4",
            "--batch",
            "--skip-column-names",
            "OneZoom",
            "--execute",
            sql,
        )
    )
    assert isinstance(result.stdout, str)
    return result.stdout


def _inspect_schema(container: str) -> dict[str, list[dict[str, Any]]]:
    table_names = ", ".join(f"'{spec.table}'" for spec in EXPORT_SPECS)
    rows = _mysql(
        container,
        "SELECT table_name, ordinal_position, column_name, column_type "
        "FROM information_schema.columns "
        "WHERE table_schema = 'OneZoom' "
        f"AND table_name IN ({table_names}) "
        "ORDER BY table_name, ordinal_position",
    )
    schema: dict[str, list[dict[str, Any]]] = {}
    for line in rows.splitlines():
        table, position, column, column_type = line.split("\t", maxsplit=3)
        schema.setdefault(table, []).append(
            {
                "ordinal_position": int(position),
                "name": column,
                "type": column_type,
            }
        )

    for spec in EXPORT_SPECS:
        observed = {column["name"] for column in schema.get(spec.table, [])}
        missing = set(spec.columns) - observed
        if missing:
            raise DockerExtractionError(
                f"{spec.table} is missing required columns: {sorted(missing)}"
            )
        forbidden = set(FORBIDDEN_COLUMNS.get(spec.table, ())) & set(spec.columns)
        if forbidden:
            raise DockerExtractionError(
                f"{spec.table} export includes forbidden columns: {sorted(forbidden)}"
            )
    return schema


def _row_counts(container: str) -> dict[str, int]:
    parts = [
        f"SELECT '{spec.table}', COUNT(*) FROM `{spec.table}`"
        for spec in EXPORT_SPECS
    ]
    output = _mysql(container, " UNION ALL ".join(parts))
    return {
        table: int(count)
        for table, count in (
            line.split("\t", maxsplit=1) for line in output.splitlines()
        )
    }


def _stream_export(
    container: str, spec: ExportSpec, destination: Path, expected_rows: int
) -> None:
    command = [
        "docker",
        "exec",
        container,
        "mysql",
        "--protocol=socket",
        "--default-character-set=utf8mb4",
        "--batch",
        "--quick",
        "OneZoom",
        "--execute",
        build_select_sql(spec),
    ]
    with tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr)
        except FileNotFoundError as error:
            raise DockerExtractionError("docker executable was not found") from error
        assert process.stdout is not None
        line_count = 0
        try:
            with destination.open("wb") as raw_output:
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    fileobj=raw_output,
                    compresslevel=6,
                    mtime=0,
                ) as compressed:
                    for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
                        line_count += chunk.count(b"\n")
                        compressed.write(chunk)
        finally:
            process.stdout.close()
        return_code = process.wait()
        if return_code:
            stderr.seek(0)
            detail = stderr.read().decode("utf-8", errors="replace").strip()
            destination.unlink(missing_ok=True)
            raise DockerExtractionError(
                f"failed to export {spec.table}: {detail or return_code}"
            )

    exported_rows = max(0, line_count - 1)
    if exported_rows != expected_rows:
        destination.unlink(missing_ok=True)
        raise DockerExtractionError(
            f"{spec.table} row mismatch: expected {expected_rows}, "
            f"exported {exported_rows}"
        )


def _copy_from_container(container: str, source: str, destination: Path) -> None:
    command = ["docker", "exec", container, "cat", source]
    with destination.open("wb") as output:
        try:
            result = subprocess.run(
                command,
                check=True,
                stdout=output,
                stderr=subprocess.PIPE,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            destination.unlink(missing_ok=True)
            detail = getattr(error, "stderr", b"") or b""
            raise DockerExtractionError(
                f"failed to copy {source}: "
                f"{detail.decode('utf-8', errors='replace').strip()}"
            ) from error
    if result.returncode:
        raise DockerExtractionError(f"failed to copy {source}")


def _file_record(path: Path, **extra: Any) -> dict[str, Any]:
    return {
        "name": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        **extra,
    }


def extract_snapshot(
    *,
    container: str = DEFAULT_CONTAINER,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    expected_image_id: str = EXPECTED_IMAGE_ID,
    expected_tree_version: str = EXPECTED_TREE_VERSION,
) -> tuple[Path, dict[str, Any]]:
    """Export one immutable, filtered snapshot from a safe running container."""
    if output_dir.exists():
        raise DockerExtractionError(f"output snapshot already exists: {output_dir}")

    container_result = _docker(("container", "inspect", container))
    assert isinstance(container_result.stdout, str)
    inspections = json.loads(container_result.stdout)
    if len(inspections) != 1:
        raise DockerExtractionError(f"expected one container named {container}")
    inspection = inspections[0]
    validate_container(inspection, expected_image_id=expected_image_id)

    root_parent = _mysql(
        container, "SELECT parent FROM ordered_nodes WHERE id = 1"
    ).strip()
    tree_version = parse_tree_version(root_parent)
    if tree_version != expected_tree_version:
        raise DockerExtractionError(
            f"tree version mismatch: expected {expected_tree_version}, "
            f"found {tree_version}"
        )

    mysql_version = _mysql(container, "SELECT VERSION()").strip()
    schema = _inspect_schema(container)
    row_counts = _row_counts(container)

    image_result = _docker(("image", "inspect", expected_image_id))
    assert isinstance(image_result.stdout, str)
    image_inspection = json.loads(image_result.stdout)[0]

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}.", dir=output_dir.parent
    ) as temporary:
        temporary_dir = Path(temporary)
        files: list[dict[str, Any]] = []

        for spec in EXPORT_SPECS:
            destination = temporary_dir / spec.filename
            print(
                f"exporting {spec.table} ({row_counts[spec.table]:,} rows)...",
                flush=True,
            )
            _stream_export(container, spec, destination, row_counts[spec.table])
            files.append(
                _file_record(
                    destination,
                    kind="mysql_projection",
                    table=spec.table,
                    columns=list(spec.columns),
                    rows=row_counts[spec.table],
                )
            )

        for stem in STATIC_STEMS:
            filename = f"{stem}_{tree_version}.js.gz"
            destination = temporary_dir / filename
            source = f"{STATIC_DATA_DIR}/{filename}"
            print(f"copying {filename}...", flush=True)
            _copy_from_container(container, source, destination)
            validate_payload(destination)
            files.append(
                _file_record(destination, kind="matched_static_viewer_data")
            )

        selected = {spec.table: set(spec.columns) for spec in EXPORT_SPECS}
        excluded_columns = {
            table: [
                column["name"]
                for column in columns
                if column["name"] not in selected[table]
            ]
            for table, columns in schema.items()
        }
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "source": "OneZoom official Docker image",
            "tree_version": tree_version,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "redistribution_status": "review-required",
            "license_url": (
                "https://www.onezoom.org/OZtree/static/downloads/"
                "OneZoom_License_V1.pdf"
            ),
            "data_sources_url": "https://www.onezoom.org/data_sources.html",
            "docker_source_url": "https://hub.docker.com/r/onezoom/oztree",
            "docker_build_repository": "https://github.com/OneZoom/OZtree-docker",
            "docker_image": {
                "id": expected_image_id,
                "created": image_inspection.get("Created"),
                "architecture": image_inspection.get("Architecture"),
                "os": image_inspection.get("Os"),
                "size": image_inspection.get("Size"),
                "repo_digests": image_inspection.get("RepoDigests", []),
            },
            "database": {
                "name": "OneZoom",
                "mysql_version": mysql_version,
                "row_counts": row_counts,
                "observed_schema": schema,
                "excluded_columns": excluded_columns,
                "excluded_table_policy": (
                    "Every table not explicitly represented in files is excluded."
                ),
            },
            "export_format": {
                "type": "gzip-compressed MySQL batch TSV",
                "encoding": "utf-8",
                "header": True,
                "row_order": "ascending primary id",
                "gzip_mtime": 0,
            },
            "files": files,
        }
        (temporary_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_dir.rename(output_dir)

    return output_dir, manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a filtered OneZoom snapshot from an isolated container."
    )
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--expected-image-id", default=EXPECTED_IMAGE_ID)
    parser.add_argument("--expected-tree-version", default=EXPECTED_TREE_VERSION)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        output_dir, manifest = extract_snapshot(
            container=args.container,
            output_dir=args.output_dir,
            expected_image_id=args.expected_image_id,
            expected_tree_version=args.expected_tree_version,
        )
    except (DockerExtractionError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error

    total_bytes = sum(record["bytes"] for record in manifest["files"])
    print(
        f"exported OneZoom tree {manifest['tree_version']} to {output_dir} "
        f"({total_bytes:,} bytes)",
        flush=True,
    )


if __name__ == "__main__":
    main()
