import pytest

from phylogenomica.data.onezoom_docker import (
    EXPECTED_IMAGE_ID,
    EXPORT_SPECS,
    DockerExtractionError,
    ExportSpec,
    build_select_sql,
    parse_tree_version,
    validate_container,
)


def safe_inspection() -> dict:
    return {
        "Image": EXPECTED_IMAGE_ID,
        "State": {"Running": True},
        "HostConfig": {"NetworkMode": "none", "PortBindings": {}},
        "Config": {"Cmd": ["/sbin/my_init"]},
    }


def test_parses_negative_root_parent_as_tree_version() -> None:
    assert parse_tree_version("-27400288") == "27400288"


@pytest.mark.parametrize("value", ["0", "27400288", "not-an-id"])
def test_rejects_invalid_root_parent(value: str) -> None:
    with pytest.raises(DockerExtractionError):
        parse_tree_version(value)


def test_builds_deterministic_reviewed_select() -> None:
    spec = ExportSpec("example", ("id", "name"))

    assert build_select_sql(spec) == (
        "SELECT `id`, `name` FROM `example` ORDER BY `id`"
    )


def test_export_allowlist_omits_restricted_fields() -> None:
    columns = {spec.table: set(spec.columns) for spec in EXPORT_SPECS}

    assert "iucn" not in columns["ordered_leaves"]
    assert "price" not in columns["ordered_leaves"]
    assert not any(
        column.startswith("iucn") for column in columns["ordered_nodes"]
    )


def test_accepts_isolated_container() -> None:
    validate_container(safe_inspection(), expected_image_id=EXPECTED_IMAGE_ID)


@pytest.mark.parametrize(
    ("section", "key", "value", "message"),
    [
        ("HostConfig", "NetworkMode", "bridge", "network none"),
        ("HostConfig", "PortBindings", {"80/tcp": [{"HostPort": "8080"}]}, "ports"),
        ("State", "Running", False, "not running"),
        ("Config", "Cmd", ["/bin/sh", "-c", "download IUCN"], "IUCN"),
    ],
)
def test_rejects_unsafe_container(
    section: str, key: str, value: object, message: str
) -> None:
    inspection = safe_inspection()
    inspection[section][key] = value

    with pytest.raises(DockerExtractionError, match=message):
        validate_container(inspection, expected_image_id=EXPECTED_IMAGE_ID)


def test_rejects_wrong_image() -> None:
    with pytest.raises(DockerExtractionError, match="image mismatch"):
        validate_container(safe_inspection(), expected_image_id="sha256:other")
