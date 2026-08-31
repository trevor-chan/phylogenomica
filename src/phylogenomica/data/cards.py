"""Resolve complete player-facing species cards from normalized metadata."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from phylogenomica.data.onezoom_ingest import DATABASE_FILENAME
from phylogenomica.tree.preprocess import DEFAULT_NORMALIZED_DIR

DEFAULT_NORMALIZED_DATABASE = DEFAULT_NORMALIZED_DIR / DATABASE_FILENAME
QUERY_BATCH_SIZE = 900


class CardMetadataError(RuntimeError):
    """Raised when requested species lack deterministic rich-card metadata."""


@dataclass(frozen=True)
class MetadataSource:
    source_table: str
    source_row_id: int


@dataclass(frozen=True)
class CardImage:
    url: str
    rights: str
    license: str
    source_code: int | None
    source_id: str | None
    source: MetadataSource


@dataclass(frozen=True)
class SpeciesCard:
    species_id: int
    scientific_name: str
    english_name: str
    ott_id: int | None
    popularity_rank: int | None
    vernacular_source: MetadataSource
    image: CardImage


def _metadata_source(payload: Mapping[str, object]) -> MetadataSource:
    return MetadataSource(
        source_table=str(payload["source_table"]),
        source_row_id=int(payload["source_row_id"]),  # type: ignore[arg-type]
    )


def _card_image(payload: Mapping[str, object]) -> CardImage:
    source_code = payload["source_code"]
    source_id = payload["source_id"]
    return CardImage(
        url=str(payload["url"]),
        rights=str(payload["rights"]),
        license=str(payload["license"]),
        source_code=None if source_code is None else int(source_code),  # type: ignore[arg-type]
        source_id=None if source_id is None else str(source_id),
        source=_metadata_source(payload["source"]),  # type: ignore[arg-type]
    )


def species_card_from_dict(payload: Mapping[str, object]) -> SpeciesCard:
    """Rebuild one player-facing card from its serialized form."""
    try:
        ott_id = payload["ott_id"]
        popularity_rank = payload["popularity_rank"]
        return SpeciesCard(
            species_id=int(payload["species_id"]),  # type: ignore[arg-type]
            scientific_name=str(payload["scientific_name"]),
            english_name=str(payload["english_name"]),
            ott_id=None if ott_id is None else int(ott_id),  # type: ignore[arg-type]
            popularity_rank=(
                None if popularity_rank is None else int(popularity_rank)  # type: ignore[arg-type]
            ),
            vernacular_source=_metadata_source(
                payload["vernacular_source"]  # type: ignore[arg-type]
            ),
            image=_card_image(payload["image"]),  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CardMetadataError(
            f"invalid serialized species card: {error}"
        ) from error


def _subject_key(
    subject_type: str, ott_id: object, scientific_name: object
) -> tuple[str, int | str]:
    if subject_type == "ott":
        return "ott", int(ott_id)
    return "scientific_name", str(scientific_name)


def _subject_batches(
    ott_ids: Sequence[int], scientific_names: Sequence[str]
) -> Iterator[tuple[str, list[int | str]]]:
    """Yield bounded subject filters so one lookup never exceeds the host limit.

    OTT and scientific-name subjects occupy separate key spaces, and each
    subject appears in exactly one batch, so splitting the lookup preserves the
    ordered first-match resolution of a single query.
    """
    for column, subjects in (
        ("ott_id", ott_ids),
        ("scientific_name", scientific_names),
    ):
        for offset in range(0, len(subjects), QUERY_BATCH_SIZE):
            batch = list(subjects[offset : offset + QUERY_BATCH_SIZE])
            placeholders = ",".join("?" for _ in batch)
            yield f"{column} IN ({placeholders})", batch


class CardMetadataStore:
    """Read-only batch resolver over normalized OneZoom card fields."""

    def __init__(
        self, connection: sqlite3.Connection, *, owns_connection: bool = False
    ):
        self._connection = connection
        self._owns_connection = owns_connection
        try:
            version = connection.execute("PRAGMA user_version").fetchone()
            if version != (1,):
                raise CardMetadataError(
                    f"unsupported normalized database schema: {version!r}"
                )
            metadata = {
                str(key): str(value)
                for key, value in connection.execute(
                    "SELECT key, value FROM dataset_metadata"
                )
            }
        except sqlite3.Error as error:
            raise CardMetadataError(
                f"cannot read normalized card metadata: {error}"
            ) from error
        try:
            self.dataset_version = metadata["dataset_version"]
        except KeyError as error:
            raise CardMetadataError(
                "normalized database has no dataset version"
            ) from error

    @classmethod
    def open(cls, path: Path = DEFAULT_NORMALIZED_DATABASE) -> CardMetadataStore:
        if not path.is_file():
            raise CardMetadataError(f"normalized database does not exist: {path}")
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return cls(connection, owns_connection=True)
        except Exception:
            connection.close()
            raise

    def close(self) -> None:
        if self._owns_connection:
            self._connection.close()
            self._owns_connection = False

    def __enter__(self) -> CardMetadataStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _leaf_rows(
        self, species_ids: Sequence[int]
    ) -> dict[int, tuple[str, int | None, int | None]]:
        rows: dict[int, tuple[str, int | None, int | None]] = {}
        ordered_ids = sorted(species_ids)
        for offset in range(0, len(ordered_ids), QUERY_BATCH_SIZE):
            batch = ordered_ids[offset : offset + QUERY_BATCH_SIZE]
            placeholders = ",".join("?" for _ in batch)
            rows.update(
                {
                    int(species_id): (
                        "" if scientific_name is None else str(scientific_name),
                        None if ott_id is None else int(ott_id),
                        None if popularity_rank is None else int(popularity_rank),
                    )
                    for species_id, scientific_name, ott_id, popularity_rank in (
                        self._connection.execute(
                            "SELECT leaf_id, scientific_name, ott_id, popularity_rank "
                            f"FROM leaves WHERE leaf_id IN ({placeholders})",
                            batch,
                        )
                    )
                }
            )
        missing = sorted(set(species_ids) - rows.keys())
        if missing:
            raise CardMetadataError(f"unknown card species IDs: {missing[:5]!r}")
        return rows

    def _vernaculars(
        self, ott_ids: Sequence[int], scientific_names: Sequence[str]
    ) -> dict[tuple[str, int | str], tuple[str, MetadataSource]]:
        records: dict[tuple[str, int | str], tuple[str, MetadataSource]] = {}
        for clause, parameters in _subject_batches(ott_ids, scientific_names):
            rows = self._connection.execute(
                "SELECT subject_type, ott_id, scientific_name, vernacular_name, "
                "source_table, source_row_id FROM vernacular_names "
                f"WHERE preferred = 1 AND language_primary = 'en' AND {clause} "
                "ORDER BY source_table, source_row_id",
                parameters,
            )
            for row in rows:
                subject_type, ott_id, scientific_name, name, source_table, row_id = row
                key = _subject_key(str(subject_type), ott_id, scientific_name)
                records.setdefault(
                    key,
                    (
                        str(name).strip(),
                        MetadataSource(str(source_table), int(row_id)),
                    ),
                )
        return records

    def _images(
        self, ott_ids: Sequence[int], scientific_names: Sequence[str]
    ) -> dict[tuple[str, int | str], CardImage]:
        records: dict[tuple[str, int | str], CardImage] = {}
        for clause, parameters in _subject_batches(ott_ids, scientific_names):
            rows = self._connection.execute(
                "SELECT subject_type, ott_id, scientific_name, url, rights, license, "
                "source_code, source_id, source_table, source_row_id FROM images "
                "WHERE overall_best_any = 1 "
                "AND NULLIF(TRIM(url), '') IS NOT NULL "
                "AND NULLIF(TRIM(rights), '') IS NOT NULL "
                f"AND NULLIF(TRIM(license), '') IS NOT NULL AND {clause} "
                "ORDER BY source_table, source_row_id",
                parameters,
            )
            for row in rows:
                (
                    subject_type,
                    ott_id,
                    scientific_name,
                    url,
                    rights,
                    license_name,
                    source_code,
                    source_id,
                    source_table,
                    row_id,
                ) = row
                key = _subject_key(str(subject_type), ott_id, scientific_name)
                records.setdefault(
                    key,
                    CardImage(
                        url=str(url).strip(),
                        rights=str(rights).strip(),
                        license=str(license_name).strip(),
                        source_code=None if source_code is None else int(source_code),
                        source_id=None if source_id is None else str(source_id),
                        source=MetadataSource(str(source_table), int(row_id)),
                    ),
                )
        return records

    def divergence_ages(
        self, node_ids: Sequence[int]
    ) -> dict[int, float | None]:
        """Resolve each internal node's divergence age in millions of years.

        The age is display metadata: most nodes carry none, and a missing age
        is reported as ``None`` rather than treated as a defect.
        """
        ordered_ids = sorted({int(node_id) for node_id in node_ids})
        if not ordered_ids:
            return {}
        ages: dict[int, float | None] = {}
        try:
            for offset in range(0, len(ordered_ids), QUERY_BATCH_SIZE):
                batch = ordered_ids[offset : offset + QUERY_BATCH_SIZE]
                placeholders = ",".join("?" for _ in batch)
                ages.update(
                    {
                        int(node_id): None if age is None else float(age)
                        for node_id, age in self._connection.execute(
                            "SELECT node_id, age_ma "
                            f"FROM nodes WHERE node_id IN ({placeholders})",
                            batch,
                        )
                    }
                )
        except sqlite3.Error as error:
            raise CardMetadataError(
                f"cannot read normalized divergence ages: {error}"
            ) from error
        missing = sorted(set(ordered_ids) - ages.keys())
        if missing:
            raise CardMetadataError(f"unknown ancestor node IDs: {missing[:5]!r}")
        return ages

    def resolve(self, species_ids: Sequence[int]) -> dict[int, SpeciesCard]:
        """Resolve one complete rich card for every unique requested species."""
        ordered_ids = tuple(int(species_id) for species_id in species_ids)
        if len(ordered_ids) != len(set(ordered_ids)):
            raise CardMetadataError("card species IDs must be unique")
        if not ordered_ids:
            return {}
        try:
            leaves = self._leaf_rows(ordered_ids)
            ott_ids = sorted(
                {ott_id for _, ott_id, _ in leaves.values() if ott_id is not None}
            )
            scientific_names = sorted(
                {name for name, _, _ in leaves.values() if name}
            )
            vernaculars = self._vernaculars(ott_ids, scientific_names)
            images = self._images(ott_ids, scientific_names)
        except sqlite3.Error as error:
            raise CardMetadataError(
                f"cannot read normalized card metadata: {error}"
            ) from error

        cards: dict[int, SpeciesCard] = {}
        for species_id in sorted(ordered_ids):
            scientific_name, ott_id, popularity_rank = leaves[species_id]
            keys: list[tuple[str, int | str]] = []
            if ott_id is not None:
                keys.append(("ott", ott_id))
            if scientific_name:
                keys.append(("scientific_name", scientific_name))
            vernacular = next(
                (vernaculars[key] for key in keys if key in vernaculars), None
            )
            image = next((images[key] for key in keys if key in images), None)
            missing: list[str] = []
            if not scientific_name:
                missing.append("scientific name")
            if vernacular is None or not vernacular[0]:
                missing.append("preferred English name")
            if image is None:
                missing.append("licensed overall-best image")
            if missing:
                raise CardMetadataError(
                    f"species {species_id} lacks {', '.join(missing)}"
                )
            english_name, vernacular_source = vernacular
            cards[species_id] = SpeciesCard(
                species_id=species_id,
                scientific_name=scientific_name,
                english_name=english_name,
                ott_id=ott_id,
                popularity_rank=popularity_rank,
                vernacular_source=vernacular_source,
                image=image,
            )
        return cards
