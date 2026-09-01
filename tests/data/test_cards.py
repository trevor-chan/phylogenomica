import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

import pytest

from phylogenomica.data import cards
from phylogenomica.data.cards import (
    CardMetadataError,
    CardMetadataStore,
    MetadataSource,
    species_card_from_dict,
)

SCHEMA = """
PRAGMA user_version = 1;
CREATE TABLE dataset_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE leaves (
    leaf_id INTEGER PRIMARY KEY,
    scientific_name TEXT,
    ott_id INTEGER,
    popularity_rank INTEGER
);
CREATE TABLE nodes (
    node_id INTEGER PRIMARY KEY,
    scientific_name TEXT,
    age_ma REAL
);
CREATE TABLE vernacular_names (
    source_table TEXT NOT NULL,
    source_row_id INTEGER NOT NULL,
    subject_type TEXT NOT NULL,
    ott_id INTEGER,
    scientific_name TEXT,
    vernacular_name TEXT NOT NULL,
    language_primary TEXT,
    preferred INTEGER NOT NULL
);
CREATE TABLE images (
    source_table TEXT NOT NULL,
    source_row_id INTEGER NOT NULL,
    subject_type TEXT NOT NULL,
    ott_id INTEGER,
    scientific_name TEXT,
    source_code INTEGER,
    source_id TEXT,
    url TEXT,
    rights TEXT,
    license TEXT,
    overall_best_any INTEGER NOT NULL
);
"""


def _connect(
    path: Path, *, dataset_version: str | None = "test-cards-1"
) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    if dataset_version is not None:
        connection.execute(
            "INSERT INTO dataset_metadata VALUES ('dataset_version', ?)",
            (dataset_version,),
        )
    return connection


def _add_leaf(
    connection: sqlite3.Connection,
    leaf_id: int,
    *,
    scientific_name: str | None = None,
    ott_id: int | None = None,
    popularity_rank: int | None = None,
) -> None:
    connection.execute(
        "INSERT INTO leaves VALUES (?, ?, ?, ?)",
        (leaf_id, scientific_name, ott_id, popularity_rank),
    )


def _add_vernacular(
    connection: sqlite3.Connection,
    *,
    source_table: str,
    source_row_id: int,
    subject_type: str = "ott",
    ott_id: int | None = None,
    scientific_name: str | None = None,
    vernacular_name: str = "Common name",
    language_primary: str | None = "en",
    preferred: int = 1,
) -> None:
    connection.execute(
        "INSERT INTO vernacular_names VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source_table,
            source_row_id,
            subject_type,
            ott_id,
            scientific_name,
            vernacular_name,
            language_primary,
            preferred,
        ),
    )


def _add_image(
    connection: sqlite3.Connection,
    *,
    source_table: str,
    source_row_id: int,
    subject_type: str = "ott",
    ott_id: int | None = None,
    scientific_name: str | None = None,
    source_code: int | None = 99,
    source_id: str | None = "image-id",
    url: str | None = "https://example.test/image.jpg",
    rights: str | None = "Test author",
    license_name: str | None = "CC BY 4.0",
    overall_best_any: int = 1,
) -> None:
    connection.execute(
        "INSERT INTO images VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source_table,
            source_row_id,
            subject_type,
            ott_id,
            scientific_name,
            source_code,
            source_id,
            url,
            rights,
            license_name,
            overall_best_any,
        ),
    )


def _complete_species(
    connection: sqlite3.Connection, leaf_id: int, *, ott_id: int | None = None
) -> None:
    ott_id = leaf_id + 100 if ott_id is None else ott_id
    _add_leaf(
        connection,
        leaf_id,
        scientific_name=f"Genus species{leaf_id}",
        ott_id=ott_id,
        popularity_rank=leaf_id,
    )
    _add_vernacular(
        connection,
        source_table="vernacular_by_ott",
        source_row_id=leaf_id,
        ott_id=ott_id,
        vernacular_name=f"Common {leaf_id}",
    )
    _add_image(
        connection,
        source_table="images_by_ott",
        source_row_id=leaf_id,
        ott_id=ott_id,
        url=f"https://example.test/{leaf_id}.jpg",
    )


def _store(tmp_path: Path, build) -> CardMetadataStore:
    database = tmp_path / "onezoom.sqlite3"
    connection = _connect(database)
    build(connection)
    connection.commit()
    connection.close()
    return CardMetadataStore.open(database)


def test_resolves_complete_rich_cards_with_provenance(tmp_path: Path) -> None:
    def build(connection: sqlite3.Connection) -> None:
        _complete_species(connection, 1, ott_id=501)
        _complete_species(connection, 2, ott_id=502)

    with _store(tmp_path, build) as store:
        assert store.dataset_version == "test-cards-1"
        resolved = store.resolve([2, 1])

    assert set(resolved) == {1, 2}
    card = resolved[1]
    assert card.species_id == 1
    assert card.scientific_name == "Genus species1"
    assert card.english_name == "Common 1"
    assert card.ott_id == 501
    assert card.popularity_rank == 1
    assert card.vernacular_source == MetadataSource("vernacular_by_ott", 1)
    assert card.image.url == "https://example.test/1.jpg"
    assert card.image.rights == "Test author"
    assert card.image.license == "CC BY 4.0"
    assert card.image.source_code == 99
    assert card.image.source_id == "image-id"
    assert card.image.source == MetadataSource("images_by_ott", 1)


def test_resolves_name_keyed_metadata_and_prefers_ott_records(tmp_path: Path) -> None:
    def build(connection: sqlite3.Connection) -> None:
        _add_leaf(
            connection, 1, scientific_name="Lutra lutra", ott_id=501, popularity_rank=7
        )
        _add_leaf(connection, 2, scientific_name="Vulpes vulpes", ott_id=502)
        # Species 1 carries both subject types; the OTT record must win.
        _add_vernacular(
            connection,
            source_table="vernacular_by_name",
            source_row_id=1,
            subject_type="scientific_name",
            scientific_name="Lutra lutra",
            vernacular_name="Name-keyed otter",
        )
        _add_vernacular(
            connection,
            source_table="vernacular_by_ott",
            source_row_id=1,
            ott_id=501,
            vernacular_name="  European Otter  ",
        )
        _add_image(
            connection,
            source_table="images_by_name",
            source_row_id=1,
            subject_type="scientific_name",
            scientific_name="Lutra lutra",
            url="https://example.test/name-keyed.jpg",
        )
        _add_image(
            connection,
            source_table="images_by_ott",
            source_row_id=1,
            ott_id=501,
            url="  https://example.test/ott-keyed.jpg  ",
        )
        # Species 2 has only name-keyed metadata and must still resolve.
        _add_vernacular(
            connection,
            source_table="vernacular_by_name",
            source_row_id=2,
            subject_type="scientific_name",
            scientific_name="Vulpes vulpes",
            vernacular_name="Red Fox",
        )
        _add_image(
            connection,
            source_table="images_by_name",
            source_row_id=2,
            subject_type="scientific_name",
            scientific_name="Vulpes vulpes",
            url="https://example.test/fox.jpg",
        )

    with _store(tmp_path, build) as store:
        resolved = store.resolve([1, 2])

    assert resolved[1].english_name == "European Otter"
    assert resolved[1].image.url == "https://example.test/ott-keyed.jpg"
    assert resolved[2].english_name == "Red Fox"
    assert resolved[2].image.source == MetadataSource("images_by_name", 2)
    assert resolved[2].popularity_rank is None


def test_breaks_duplicate_metadata_ties_deterministically(tmp_path: Path) -> None:
    def build(connection: sqlite3.Connection) -> None:
        _add_leaf(connection, 1, scientific_name="Lutra lutra", ott_id=501)
        for source_row_id in (9, 3, 6):
            _add_vernacular(
                connection,
                source_table="vernacular_by_ott",
                source_row_id=source_row_id,
                ott_id=501,
                vernacular_name=f"Otter {source_row_id}",
            )
            _add_image(
                connection,
                source_table="images_by_ott",
                source_row_id=source_row_id,
                ott_id=501,
                url=f"https://example.test/{source_row_id}.jpg",
            )

    with _store(tmp_path, build) as store:
        first = store.resolve([1])
        repeated = store.resolve([1])

    assert first == repeated
    assert first[1].english_name == "Otter 3"
    assert first[1].vernacular_source == MetadataSource("vernacular_by_ott", 3)
    assert first[1].image.url == "https://example.test/3.jpg"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        pytest.param(
            lambda connection: connection.execute(
                "UPDATE leaves SET scientific_name = NULL WHERE leaf_id = 1"
            ),
            "scientific name",
            id="missing-scientific-name",
        ),
        pytest.param(
            lambda connection: connection.execute("DELETE FROM vernacular_names"),
            "preferred English name",
            id="no-vernacular-record",
        ),
        pytest.param(
            lambda connection: connection.execute(
                "UPDATE vernacular_names SET language_primary = 'fr'"
            ),
            "preferred English name",
            id="non-english-vernacular",
        ),
        pytest.param(
            lambda connection: connection.execute(
                "UPDATE vernacular_names SET preferred = 0"
            ),
            "preferred English name",
            id="unpreferred-vernacular",
        ),
        pytest.param(
            lambda connection: connection.execute(
                "UPDATE vernacular_names SET vernacular_name = '   '"
            ),
            "preferred English name",
            id="blank-vernacular",
        ),
        pytest.param(
            lambda connection: connection.execute("DELETE FROM images"),
            "licensed overall-best image",
            id="no-image-record",
        ),
        pytest.param(
            lambda connection: connection.execute(
                "UPDATE images SET overall_best_any = 0"
            ),
            "licensed overall-best image",
            id="not-overall-best",
        ),
        pytest.param(
            lambda connection: connection.execute("UPDATE images SET url = '  '"),
            "licensed overall-best image",
            id="blank-image-url",
        ),
        pytest.param(
            lambda connection: connection.execute("UPDATE images SET rights = NULL"),
            "licensed overall-best image",
            id="missing-image-rights",
        ),
        pytest.param(
            lambda connection: connection.execute("UPDATE images SET license = ''"),
            "licensed overall-best image",
            id="missing-image-license",
        ),
    ],
)
def test_rejects_species_without_complete_card_metadata(
    tmp_path: Path, mutate, message: str
) -> None:
    def build(connection: sqlite3.Connection) -> None:
        _complete_species(connection, 1, ott_id=501)
        mutate(connection)

    with _store(tmp_path, build) as store, pytest.raises(
        CardMetadataError, match=message
    ):
        store.resolve([1])


def test_reports_every_missing_field_for_one_species(tmp_path: Path) -> None:
    def build(connection: sqlite3.Connection) -> None:
        _add_leaf(connection, 1, scientific_name=None, ott_id=501)

    with _store(tmp_path, build) as store, pytest.raises(
        CardMetadataError,
        match="species 1 lacks scientific name, preferred English name, "
        "licensed overall-best image",
    ):
        store.resolve([1])


def test_rejects_unknown_duplicate_and_empty_requests(tmp_path: Path) -> None:
    def build(connection: sqlite3.Connection) -> None:
        _complete_species(connection, 1, ott_id=501)

    with _store(tmp_path, build) as store:
        assert store.resolve([]) == {}
        with pytest.raises(CardMetadataError, match="unknown card species IDs"):
            store.resolve([1, 404])
        with pytest.raises(CardMetadataError, match="must be unique"):
            store.resolve([1, 1])


def test_resolves_more_species_than_one_query_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    species_ids = list(range(1, 26))

    def build(connection: sqlite3.Connection) -> None:
        for leaf_id in species_ids:
            _complete_species(connection, leaf_id)

    with _store(tmp_path, build) as store:
        monkeypatch.setattr(cards, "QUERY_BATCH_SIZE", 3)
        batched = store.resolve(species_ids)
        monkeypatch.setattr(cards, "QUERY_BATCH_SIZE", 900)
        unbatched = store.resolve(species_ids)

    assert batched == unbatched
    assert len(batched) == len(species_ids)
    assert batched[25].english_name == "Common 25"


def test_rejects_unusable_normalized_databases(tmp_path: Path) -> None:
    with pytest.raises(CardMetadataError, match="does not exist"):
        CardMetadataStore.open(tmp_path / "absent.sqlite3")

    wrong_schema = tmp_path / "wrong-schema.sqlite3"
    connection = sqlite3.connect(wrong_schema)
    connection.executescript("PRAGMA user_version = 7;")
    connection.commit()
    connection.close()
    with pytest.raises(CardMetadataError, match="unsupported normalized database"):
        CardMetadataStore.open(wrong_schema)

    no_metadata_table = tmp_path / "no-metadata-table.sqlite3"
    connection = sqlite3.connect(no_metadata_table)
    connection.executescript("PRAGMA user_version = 1;")
    connection.commit()
    connection.close()
    with pytest.raises(CardMetadataError, match="cannot read normalized card metadata"):
        CardMetadataStore.open(no_metadata_table)

    no_version = tmp_path / "no-version.sqlite3"
    connection = _connect(no_version, dataset_version=None)
    connection.commit()
    connection.close()
    with pytest.raises(CardMetadataError, match="no dataset version"):
        CardMetadataStore.open(no_version)


def test_owns_only_connections_it_opened(tmp_path: Path) -> None:
    database = tmp_path / "onezoom.sqlite3"
    connection = _connect(database)
    _complete_species(connection, 1, ott_id=501)
    connection.commit()

    with CardMetadataStore(connection) as store:
        assert store.resolve([1])[1].english_name == "Common 1"
    # A borrowed connection stays usable after the store exits.
    assert connection.execute("SELECT COUNT(*) FROM leaves").fetchone() == (1,)

    opened = CardMetadataStore.open(database)
    opened.close()
    with pytest.raises(sqlite3.ProgrammingError):
        opened._connection.execute("SELECT 1")
    connection.close()


def test_round_trips_a_serialized_species_card(tmp_path: Path) -> None:
    def build(connection: sqlite3.Connection) -> None:
        _complete_species(connection, 1, ott_id=501)
        # A name-keyed species exercises the nullable card fields.
        _add_leaf(connection, 2, scientific_name="Vulpes vulpes")
        _add_vernacular(
            connection,
            source_table="vernacular_by_name",
            source_row_id=2,
            subject_type="scientific_name",
            scientific_name="Vulpes vulpes",
            vernacular_name="Red Fox",
        )
        _add_image(
            connection,
            source_table="images_by_name",
            source_row_id=2,
            subject_type="scientific_name",
            scientific_name="Vulpes vulpes",
            source_code=None,
            source_id=None,
        )

    with _store(tmp_path, build) as store:
        resolved = store.resolve([1, 2])

    for card in resolved.values():
        payload = json.loads(json.dumps(asdict(card)))
        assert species_card_from_dict(payload) == card

    sparse = resolved[2]
    assert sparse.ott_id is None
    assert sparse.popularity_rank is None
    assert sparse.image.source_code is None
    assert sparse.image.source_id is None


def test_rejects_invalid_serialized_species_cards(tmp_path: Path) -> None:
    def build(connection: sqlite3.Connection) -> None:
        _complete_species(connection, 1, ott_id=501)

    with _store(tmp_path, build) as store:
        payload = json.loads(json.dumps(asdict(store.resolve([1])[1])))

    for mutate in (
        lambda p: p.pop("image"),
        lambda p: p.pop("species_id"),
        lambda p: p["image"].pop("source"),
        lambda p: p["image"]["source"].pop("source_row_id"),
        lambda p: p.update(species_id="not-a-number"),
        lambda p: p.update(popularity_rank="not-a-number"),
    ):
        broken = json.loads(json.dumps(payload))
        mutate(broken)
        with pytest.raises(
            CardMetadataError, match="invalid serialized species card"
        ):
            species_card_from_dict(broken)


def test_wraps_sqlite_failures_during_resolution(tmp_path: Path) -> None:
    def build(connection: sqlite3.Connection) -> None:
        _complete_species(connection, 1, ott_id=501)
        connection.execute("DROP TABLE images")

    with _store(tmp_path, build) as store, pytest.raises(
        CardMetadataError, match="cannot read normalized card metadata"
    ):
        store.resolve([1])


def test_resolves_optional_normalized_clade_names(tmp_path: Path) -> None:
    def build(connection: sqlite3.Connection) -> None:
        connection.executemany(
            "INSERT INTO nodes VALUES (?, ?, ?)",
            ((1, " Mammalia ", 10.0), (2, None, None), (3, "", 3.0)),
        )

    with _store(tmp_path, build) as store:
        assert store.clade_names([3, 1, 2, 1]) == {
            1: "Mammalia",
            2: None,
            3: None,
        }
        with pytest.raises(CardMetadataError, match="unknown ancestor node IDs"):
            store.clade_names([4])
