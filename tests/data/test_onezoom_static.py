import gzip
from pathlib import Path

import pytest

from phylogenomica.data.onezoom_static import (
    StaticDataError,
    read_dates,
    read_topology,
)


def _write_gzip(path: Path, payload: bytes) -> None:
    with gzip.open(path, "wb") as output:
        output.write(payload)


def test_reads_topology_assignment(tmp_path: Path) -> None:
    path = tmp_path / "completetree_1.js.gz"
    _write_gzip(path, b"var rawData = '({})';\nvar metadata = {};\n")

    assert read_topology(path) == "({})"


def test_rejects_invalid_topology_character(tmp_path: Path) -> None:
    path = tmp_path / "completetree_1.js.gz"
    _write_gzip(path, b"var rawData = '(x)';\n")

    with pytest.raises(StaticDataError, match="invalid topology"):
        read_topology(path)


def test_reads_dates_assignment(tmp_path: Path) -> None:
    path = tmp_path / "dates_1.js.gz"
    _write_gzip(
        path,
        b'var tree_date = {"leaves":{"1":0.1},"nodes":{"2":5.0}};\n',
    )

    assert read_dates(path) == {
        "leaves": {"1": 0.1},
        "nodes": {"2": 5.0},
    }
