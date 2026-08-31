"""Build and query a versioned target-eligibility index."""

from __future__ import annotations

import argparse
import json
import secrets
import sqlite3
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from phylogenomica.data.onezoom_download import sha256_file
from phylogenomica.data.onezoom_ingest import DATABASE_FILENAME
from phylogenomica.generation.feasibility import (
    FEASIBILITY_AUDIT_VERSION,
    FeasibilityAuditError,
    FeasibilityConfig,
    TargetEvaluation,
    TargetLineageMetrics,
    audit_target_feasibility,
    feasibility_configuration,
)
from phylogenomica.tree.preprocess import (
    DEFAULT_NORMALIZED_DIR,
    TREE_DATABASE_FILENAME,
    TREE_SCHEMA_VERSION,
)

ELIGIBILITY_INDEX_VERSION = 1
ELIGIBILITY_SCHEMA_VERSION = 1
ELIGIBILITY_DATABASE_FILENAME = "target_eligibility.sqlite3"
INSERT_BATCH_SIZE = 20_000

REASON_DEFINITIONS = {
    "insufficient_ordered_stage_structure": (
        "topology",
        "Relative-bearing tiers cannot fill the configured ordered stage roles.",
    ),
    "insufficient_total_relatives": (
        "topology",
        "The configured relative universe contains too few unique relatives.",
    ),
    "missing_licensed_overall_best_image": (
        "metadata",
        "No overall-best image has complete URL, rights, and licence fields.",
    ),
    "missing_preferred_english_vernacular": (
        "metadata",
        "No preferred English vernacular name is available.",
    ),
    "missing_scientific_name": (
        "metadata",
        "The source leaf has no scientific name.",
    ),
}

SCHEMA_SQL = """
PRAGMA application_id = 0x5048454C;
PRAGMA user_version = 1;
PRAGMA foreign_keys = ON;

CREATE TABLE dataset_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE reason_definitions (
    reason_code TEXT PRIMARY KEY,
    category TEXT NOT NULL CHECK (category IN ('metadata', 'topology')),
    description TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE target_eligibility (
    target_id INTEGER PRIMARY KEY,
    eligible INTEGER NOT NULL CHECK (eligible IN (0, 1)),
    metadata_eligible INTEGER NOT NULL CHECK (metadata_eligible IN (0, 1)),
    topology_supported INTEGER NOT NULL CHECK (topology_supported IN (0, 1)),
    usable_depth INTEGER NOT NULL CHECK (usable_depth >= 0),
    total_relative_capacity INTEGER NOT NULL CHECK (total_relative_capacity >= 0),
    completed_stages INTEGER NOT NULL CHECK (completed_stages >= 0),
    scientific_name_present INTEGER NOT NULL
        CHECK (scientific_name_present IN (0, 1)),
    ott_id_present INTEGER NOT NULL CHECK (ott_id_present IN (0, 1)),
    preferred_english_vernacular INTEGER NOT NULL
        CHECK (preferred_english_vernacular IN (0, 1)),
    overall_best_image INTEGER NOT NULL CHECK (overall_best_image IN (0, 1)),
    licensed_overall_best_image INTEGER NOT NULL
        CHECK (licensed_overall_best_image IN (0, 1)),
    card_ready INTEGER NOT NULL CHECK (card_ready IN (0, 1)),
    rich_card_ready INTEGER NOT NULL CHECK (rich_card_ready IN (0, 1))
);

CREATE TABLE target_reasons (
    target_id INTEGER NOT NULL REFERENCES target_eligibility(target_id),
    reason_code TEXT NOT NULL REFERENCES reason_definitions(reason_code),
    PRIMARY KEY (target_id, reason_code)
) WITHOUT ROWID;
"""

INDEX_SQL = """
CREATE INDEX target_eligibility_by_eligible
    ON target_eligibility (eligible, target_id);
CREATE INDEX target_eligibility_by_metadata
    ON target_eligibility (metadata_eligible, target_id);
CREATE INDEX target_eligibility_by_topology
    ON target_eligibility (topology_supported, target_id);
CREATE INDEX target_reasons_by_code
    ON target_reasons (reason_code, target_id);
"""


class TargetEligibilityError(RuntimeError):
    """Raised when an eligibility index cannot be built or validated."""


def _load_manifest(path: Path, label: str) -> tuple[dict[str, Any], str]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TargetEligibilityError(f"cannot read {label} manifest: {path}") from error
    if not isinstance(manifest, dict):
        raise TargetEligibilityError(f"invalid {label} manifest: {path}")
    return manifest, sha256_file(path)


def _verify_database_record(
    *,
    directory: Path,
    manifest: dict[str, Any],
    expected_name: str,
    label: str,
) -> tuple[Path, str]:
    record = manifest.get("database")
    if not isinstance(record, dict) or record.get("name") != expected_name:
        raise TargetEligibilityError(f"invalid {label} database manifest record")
    path = directory / expected_name
    try:
        size = path.stat().st_size
    except OSError as error:
        raise TargetEligibilityError(f"missing {label} database: {path}") from error
    if size != record.get("bytes"):
        raise TargetEligibilityError(f"{label} database size does not match manifest")
    checksum = sha256_file(path)
    if checksum != record.get("sha256"):
        raise TargetEligibilityError(
            f"{label} database checksum does not match manifest"
        )
    return path, checksum


def _validated_sources(
    normalized_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], Path, Path, dict[str, str]]:
    normalized_manifest_path = normalized_dir / "manifest.json"
    normalized_manifest, normalized_manifest_sha256 = _load_manifest(
        normalized_manifest_path, "normalized"
    )
    if (
        normalized_manifest.get("schema_version") != 1
        or normalized_manifest.get("database_schema_version") != 1
    ):
        raise TargetEligibilityError("unsupported normalized manifest or schema")
    normalized_database, normalized_database_sha256 = _verify_database_record(
        directory=normalized_dir,
        manifest=normalized_manifest,
        expected_name=DATABASE_FILENAME,
        label="normalized",
    )

    tree_dir = normalized_dir / f"tree-v{TREE_SCHEMA_VERSION}"
    tree_manifest_path = tree_dir / "manifest.json"
    tree_manifest, tree_manifest_sha256 = _load_manifest(tree_manifest_path, "tree")
    if (
        tree_manifest.get("schema_version") != 1
        or tree_manifest.get("tree_schema_version") != TREE_SCHEMA_VERSION
    ):
        raise TargetEligibilityError("unsupported tree manifest or schema")
    if tree_manifest.get("dataset_version") != normalized_manifest.get(
        "dataset_version"
    ):
        raise TargetEligibilityError(
            "tree and normalized manifests have different dataset versions"
        )
    tree_database, tree_database_sha256 = _verify_database_record(
        directory=tree_dir,
        manifest=tree_manifest,
        expected_name=TREE_DATABASE_FILENAME,
        label="tree",
    )
    if (
        tree_manifest.get("source", {}).get("normalized_database_sha256")
        != normalized_database_sha256
    ):
        raise TargetEligibilityError(
            "tree manifest does not reference the normalized database"
        )
    checksums = {
        "normalized_manifest_sha256": normalized_manifest_sha256,
        "normalized_database_sha256": normalized_database_sha256,
        "tree_manifest_sha256": tree_manifest_sha256,
        "tree_database_sha256": tree_database_sha256,
    }
    return (
        normalized_manifest,
        tree_manifest,
        normalized_database,
        tree_database,
        checksums,
    )


def _evaluation_row(evaluation: TargetEvaluation) -> tuple[int, ...]:
    metrics = evaluation.metrics
    return (
        evaluation.target_id,
        int(evaluation.eligible),
        int(evaluation.metadata_eligible),
        int(evaluation.topology_supported),
        metrics.usable_depth,
        metrics.total_relative_capacity,
        metrics.completed_stages,
        int(evaluation.scientific_name_present),
        int(evaluation.ott_id_present),
        int(evaluation.preferred_english_vernacular),
        int(evaluation.overall_best_image),
        int(evaluation.licensed_overall_best_image),
        int(evaluation.card_ready),
        int(evaluation.rich_card_ready),
    )


class _IndexWriter:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.target_rows: list[tuple[int, ...]] = []
        self.reason_rows: list[tuple[int, str]] = []
        self.source_targets = 0
        self.exclusion_reason_counts: Counter[str] = Counter()

    def add(self, evaluation: TargetEvaluation) -> None:
        self.source_targets += 1
        if not evaluation.metadata_eligible:
            self.exclusion_reason_counts.update(
                reason
                for reason in evaluation.reason_codes
                if REASON_DEFINITIONS[reason][0] == "metadata"
            )
            return
        self.target_rows.append(_evaluation_row(evaluation))
        self.reason_rows.extend(
            (evaluation.target_id, reason) for reason in evaluation.reason_codes
        )
        if len(self.target_rows) >= INSERT_BATCH_SIZE:
            self.flush()

    def flush(self) -> None:
        if not self.target_rows:
            return
        self.connection.executemany(
            "INSERT INTO target_eligibility VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            self.target_rows,
        )
        self.connection.executemany(
            "INSERT INTO target_reasons VALUES (?, ?)", self.reason_rows
        )
        self.target_rows.clear()
        self.reason_rows.clear()


def _reason_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        str(reason): int(count)
        for reason, count in connection.execute(
            "SELECT reason_code, COUNT(*) FROM target_reasons "
            "GROUP BY reason_code ORDER BY reason_code"
        )
    }


def _validate_index(
    connection: sqlite3.Connection,
    *,
    audit: dict[str, object],
) -> dict[str, int | str]:
    targets = audit["targets"]
    assert isinstance(targets, dict)
    expected_source = int(targets["source_leaves"])
    expected_metadata_eligible = int(targets["total"])
    supporting = targets["supporting_configuration"]
    assert isinstance(supporting, dict)
    expected_eligible = int(supporting["count"])

    indexed_count = int(
        connection.execute("SELECT COUNT(*) FROM target_eligibility").fetchone()[0]
    )
    metadata_eligible = int(
        connection.execute(
            "SELECT COUNT(*) FROM target_eligibility WHERE metadata_eligible = 1"
        ).fetchone()[0]
    )
    eligible = int(
        connection.execute(
            "SELECT COUNT(*) FROM target_eligibility WHERE eligible = 1"
        ).fetchone()[0]
    )
    eligible_with_reasons = int(
        connection.execute(
            "SELECT COUNT(*) FROM target_reasons AS reason "
            "JOIN target_eligibility AS target USING (target_id) "
            "WHERE target.eligible = 1"
        ).fetchone()[0]
    )
    ineligible_without_reasons = int(
        connection.execute(
            "SELECT COUNT(*) FROM target_eligibility AS target "
            "WHERE target.eligible = 0 AND NOT EXISTS ("
            "SELECT 1 FROM target_reasons AS reason "
            "WHERE reason.target_id = target.target_id)"
        ).fetchone()[0]
    )
    if (indexed_count, metadata_eligible, eligible) != (
        expected_metadata_eligible,
        expected_metadata_eligible,
        expected_eligible,
    ):
        raise TargetEligibilityError(
            "eligibility index counts do not match the feasibility audit"
        )
    if eligible_with_reasons or ineligible_without_reasons:
        raise TargetEligibilityError("eligibility reason assignments are inconsistent")
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        raise TargetEligibilityError(
            f"eligibility index has foreign-key violations: {foreign_keys[:3]!r}"
        )
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity != ("ok",):
        raise TargetEligibilityError(
            f"eligibility SQLite integrity check failed: {integrity!r}"
        )
    return {
        "source_targets": expected_source,
        "indexed_targets": indexed_count,
        "metadata_excluded_targets": expected_source - indexed_count,
        "eligible_targets": eligible,
        "ineligible_indexed_targets": indexed_count - eligible,
        "eligible_targets_with_reasons": eligible_with_reasons,
        "ineligible_targets_without_reasons": ineligible_without_reasons,
        "sqlite_integrity_check": "ok",
    }


def build_target_eligibility_index(
    *,
    normalized_dir: Path = DEFAULT_NORMALIZED_DIR,
    output_dir: Path | None = None,
    config: FeasibilityConfig = FeasibilityConfig(require_rich_card_metadata=True),
) -> tuple[Path, dict[str, Any]]:
    """Build an atomic per-leaf eligibility database and provenance manifest."""
    (
        normalized_manifest,
        tree_manifest,
        normalized_database,
        tree_database,
        source_checksums,
    ) = _validated_sources(normalized_dir)
    destination = output_dir or normalized_dir / (
        f"target-eligibility-v{ELIGIBILITY_INDEX_VERSION}"
    )
    if destination.exists():
        raise TargetEligibilityError(
            f"target eligibility output already exists: {destination}"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}.", dir=destination.parent
    ) as temporary:
        temporary_dir = Path(temporary)
        database_path = temporary_dir / ELIGIBILITY_DATABASE_FILENAME
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("PRAGMA journal_mode = OFF")
            connection.execute("PRAGMA synchronous = OFF")
            connection.execute("PRAGMA temp_store = MEMORY")
            connection.executescript(SCHEMA_SQL)
            configuration = feasibility_configuration(config)
            connection.executemany(
                "INSERT INTO dataset_metadata VALUES (?, ?)",
                (
                    ("dataset_version", str(normalized_manifest["dataset_version"])),
                    ("eligibility_index_version", str(ELIGIBILITY_INDEX_VERSION)),
                    ("eligibility_schema_version", str(ELIGIBILITY_SCHEMA_VERSION)),
                    ("feasibility_audit_version", str(FEASIBILITY_AUDIT_VERSION)),
                    (
                        "feasibility_configuration",
                        json.dumps(
                            configuration, sort_keys=True, separators=(",", ":")
                        ),
                    ),
                    (
                        "normalized_database_sha256",
                        source_checksums["normalized_database_sha256"],
                    ),
                    (
                        "tree_database_sha256",
                        source_checksums["tree_database_sha256"],
                    ),
                ),
            )
            connection.executemany(
                "INSERT INTO reason_definitions VALUES (?, ?, ?)",
                (
                    (code, category, description)
                    for code, (category, description) in sorted(
                        REASON_DEFINITIONS.items()
                    )
                ),
            )
            writer = _IndexWriter(connection)
            audit = audit_target_feasibility(
                tree_database=tree_database,
                normalized_database=normalized_database,
                config=config,
                evaluation_handler=writer.add,
            )
            writer.flush()
            if writer.source_targets != int(audit["targets"]["source_leaves"]):
                raise TargetEligibilityError(
                    "source-leaf callback count does not match the feasibility audit"
                )
            connection.commit()
            connection.executescript(INDEX_SQL)
            connection.commit()
            validation = _validate_index(connection, audit=audit)
            reasons = _reason_counts(connection)
            connection.execute("VACUUM")
        except (FeasibilityAuditError, sqlite3.Error) as error:
            raise TargetEligibilityError(
                f"eligibility index build failed: {error}"
            ) from error
        finally:
            connection.close()

        database_record = {
            "name": ELIGIBILITY_DATABASE_FILENAME,
            "bytes": database_path.stat().st_size,
            "sha256": sha256_file(database_path),
        }
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "dataset_version": normalized_manifest["dataset_version"],
            "source_tree_version": normalized_manifest["source_tree_version"],
            "built_at": datetime.now(UTC).isoformat(),
            "eligibility_index_version": ELIGIBILITY_INDEX_VERSION,
            "eligibility_schema_version": ELIGIBILITY_SCHEMA_VERSION,
            "feasibility_audit_version": FEASIBILITY_AUDIT_VERSION,
            "runtime": {
                "python": sys.version.split()[0],
                "sqlite": sqlite3.sqlite_version,
            },
            "configuration": feasibility_configuration(config),
            "source": {
                "normalized_directory": str(normalized_dir),
                **source_checksums,
                "tree_builder_version": tree_manifest["tree_builder_version"],
                "tree_schema_version": tree_manifest["tree_schema_version"],
            },
            "reason_definitions": {
                code: {"category": category, "description": description}
                for code, (category, description) in sorted(
                    REASON_DEFINITIONS.items()
                )
            },
            "reason_counts": reasons,
            "source_exclusion_reason_counts": dict(
                sorted(writer.exclusion_reason_counts.items())
            ),
            "validation": validation,
            "database": database_record,
            "reproduction_command": (
                f"phylogenomica-build-eligibility {normalized_dir} "
                f"--output-dir {destination} "
                + (
                    "--require-rich-cards"
                    if config.require_rich_card_metadata
                    else "--no-require-rich-cards"
                )
            ),
        }
        (temporary_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_dir.rename(destination)
    return destination, manifest


class TargetEligibilityIndex:
    """Read-only query interface for a validated eligibility database."""

    def __init__(self, database: Path) -> None:
        if not database.is_file():
            raise TargetEligibilityError(
                f"eligibility database does not exist: {database}"
            )
        self._connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        version = self._connection.execute("PRAGMA user_version").fetchone()
        if version != (ELIGIBILITY_SCHEMA_VERSION,):
            self.close()
            raise TargetEligibilityError(
                f"unsupported eligibility schema: {version!r}"
            )
        self._metadata = {
            str(key): str(value)
            for key, value in self._connection.execute(
                "SELECT key, value FROM dataset_metadata"
            )
        }
        if self._metadata.get("eligibility_index_version") != str(
            ELIGIBILITY_INDEX_VERSION
        ):
            self.close()
            raise TargetEligibilityError(
                "unsupported or missing eligibility index version"
            )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> TargetEligibilityIndex:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def dataset_version(self) -> str:
        try:
            return self._metadata["dataset_version"]
        except KeyError as error:
            raise TargetEligibilityError(
                "eligibility index has no dataset version"
            ) from error

    @property
    def feasibility_configuration(self) -> dict[str, int | bool]:
        try:
            value = json.loads(self._metadata["feasibility_configuration"])
        except (KeyError, json.JSONDecodeError) as error:
            raise TargetEligibilityError(
                "eligibility index has invalid feasibility configuration"
            ) from error
        if not isinstance(value, dict):
            raise TargetEligibilityError(
                "eligibility index has invalid feasibility configuration"
            )
        return value

    def get(self, target_id: int) -> TargetEvaluation | None:
        row = self._connection.execute(
            "SELECT target_id, eligible, metadata_eligible, topology_supported, "
            "usable_depth, total_relative_capacity, completed_stages, "
            "scientific_name_present, ott_id_present, "
            "preferred_english_vernacular, overall_best_image, "
            "licensed_overall_best_image, card_ready, rich_card_ready "
            "FROM target_eligibility WHERE target_id = ?",
            (target_id,),
        ).fetchone()
        if row is None:
            return None
        reasons = tuple(
            str(reason)
            for (reason,) in self._connection.execute(
                "SELECT reason_code FROM target_reasons WHERE target_id = ? "
                "ORDER BY reason_code",
                (target_id,),
            )
        )
        return TargetEvaluation(
            target_id=int(row[0]),
            eligible=bool(row[1]),
            metadata_eligible=bool(row[2]),
            topology_supported=bool(row[3]),
            metrics=TargetLineageMetrics(
                usable_depth=int(row[4]),
                total_relative_capacity=int(row[5]),
                completed_stages=int(row[6]),
            ),
            scientific_name_present=bool(row[7]),
            ott_id_present=bool(row[8]),
            preferred_english_vernacular=bool(row[9]),
            overall_best_image=bool(row[10]),
            licensed_overall_best_image=bool(row[11]),
            card_ready=bool(row[12]),
            rich_card_ready=bool(row[13]),
            reason_codes=reasons,
        )

    def iter_eligible_target_ids(self) -> Iterator[int]:
        rows = self._connection.execute(
            "SELECT target_id FROM target_eligibility "
            "WHERE eligible = 1 ORDER BY target_id"
        )
        for (target_id,) in rows:
            yield int(target_id)

    def random_eligible_target_id(
        self, *, randbelow: Callable[[int], int] | None = None
    ) -> int:
        """Choose one indexed eligible target uniformly without loading all IDs."""
        row = self._connection.execute(
            "SELECT COUNT(*) FROM target_eligibility WHERE eligible = 1"
        ).fetchone()
        count = 0 if row is None else int(row[0])
        if count <= 0:
            raise TargetEligibilityError("eligibility index has no eligible targets")
        choose = secrets.randbelow if randbelow is None else randbelow
        offset = choose(count)
        if not 0 <= offset < count:
            raise TargetEligibilityError("random target offset is outside the index")
        selected = self._connection.execute(
            "SELECT target_id FROM target_eligibility WHERE eligible = 1 "
            "ORDER BY target_id LIMIT 1 OFFSET ?",
            (offset,),
        ).fetchone()
        if selected is None:
            raise TargetEligibilityError("could not read the selected eligible target")
        return int(selected[0])

    def iter_indexed_target_ids(self) -> Iterator[int]:
        """Yield the complete configured target/relative card universe."""
        rows = self._connection.execute(
            "SELECT target_id FROM target_eligibility ORDER BY target_id"
        )
        for (target_id,) in rows:
            yield int(target_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a versioned per-target eligibility index."
    )
    parser.add_argument(
        "normalized_dir", nargs="?", type=Path, default=DEFAULT_NORMALIZED_DIR
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--members-per-stage", type=int, default=10)
    parser.add_argument("--stages-per-game", type=int, default=5)
    parser.add_argument("--unlock-species", type=int, default=1)
    parser.add_argument("--mulligan-species", type=int, default=1)
    parser.add_argument(
        "--require-rich-cards",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="require a scientific name, preferred English name, and licensed image",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        config = FeasibilityConfig(
            members_per_stage=args.members_per_stage,
            stages_per_game=args.stages_per_game,
            unlock_species_per_transition_stage=args.unlock_species,
            mulligan_species_per_stage=args.mulligan_species,
            require_rich_card_metadata=args.require_rich_cards,
        )
        output_dir, manifest = build_target_eligibility_index(
            normalized_dir=args.normalized_dir,
            output_dir=args.output_dir,
            config=config,
        )
    except (ValueError, TargetEligibilityError) as error:
        raise SystemExit(str(error)) from error
    database = manifest["database"]
    validation = manifest["validation"]
    print(
        f"built target eligibility index at {output_dir} "
        f"({validation['eligible_targets']:,} eligible; "
        f"{database['bytes']:,} bytes; sha256 {database['sha256']})",
        flush=True,
    )


if __name__ == "__main__":
    main()
