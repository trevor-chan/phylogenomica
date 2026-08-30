import json
from pathlib import Path

import pytest

from phylogenomica.data.audit import snapshot_tree_version


def test_uses_numeric_snapshot_directory_name(tmp_path: Path) -> None:
    snapshot = tmp_path / "27400288"

    assert snapshot_tree_version(snapshot) == "27400288"


def test_uses_manifest_version_for_named_snapshot(tmp_path: Path) -> None:
    snapshot = tmp_path / "docker-2022-02-07"
    snapshot.mkdir()
    (snapshot / "manifest.json").write_text(
        json.dumps({"tree_version": "27400288"}), encoding="utf-8"
    )

    assert snapshot_tree_version(snapshot) == "27400288"


@pytest.mark.parametrize("version", [None, 27400288, "../27400288"])
def test_rejects_invalid_manifest_version(tmp_path: Path, version: object) -> None:
    snapshot = tmp_path / "named"
    snapshot.mkdir()
    (snapshot / "manifest.json").write_text(
        json.dumps({"tree_version": version}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="invalid tree version"):
        snapshot_tree_version(snapshot)
