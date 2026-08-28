import gzip
from pathlib import Path

import pytest

from phylogenomica.data.onezoom_download import (
    AcquisitionError,
    build_ssl_context,
    discover_tree_version,
    sha256_file,
    snapshot_urls,
    validate_payload,
)


def test_discovers_live_tree_version() -> None:
    html = """
    const template = 'https://www.onezoom.org/OZtree/static/FinalOutputs/data/BASEBASE_29194525.EXTEXT';
    """

    assert discover_tree_version(html) == "29194525"


def test_rejects_ambiguous_tree_versions() -> None:
    html = "BASEBASE_1.EXTEXT BASEBASE_2.EXTEXT"

    with pytest.raises(AcquisitionError, match="exactly one"):
        discover_tree_version(html)


def test_builds_only_numeric_version_urls() -> None:
    urls = snapshot_urls("https://example.test/", "123")

    assert urls["completetree_123.js.gz"] == (
        "https://example.test/OZtree/static/FinalOutputs/data/"
        "completetree_123.js.gz"
    )
    with pytest.raises(AcquisitionError, match="invalid"):
        snapshot_urls("https://example.test", "../123")


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        ("completetree_1.js.gz", b"var rawData = '({})';\n"),
        (
            "cut_position_map_1.js.gz",
            b"var cut_position_map_json_str = '{}';\n",
        ),
        ("dates_1.js.gz", b"var tree_date = {};\n"),
    ],
)
def test_validates_expected_gzip_payloads(
    tmp_path: Path, filename: str, payload: bytes
) -> None:
    path = tmp_path / filename
    with gzip.open(path, "wb") as output:
        output.write(payload)

    validate_payload(path)
    assert len(sha256_file(path)) == 64


def test_rejects_unexpected_payload(tmp_path: Path) -> None:
    path = tmp_path / "completetree_1.js.gz"
    with gzip.open(path, "wb") as output:
        output.write(b"not OneZoom data")

    with pytest.raises(AcquisitionError, match="unexpected"):
        validate_payload(path)


def test_rejects_missing_ca_file(tmp_path: Path) -> None:
    with pytest.raises(AcquisitionError, match="does not exist"):
        build_ssl_context(tmp_path / "missing.pem")
