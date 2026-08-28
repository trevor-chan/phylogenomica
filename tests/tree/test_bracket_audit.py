import pytest

from phylogenomica.tree.bracket_audit import TopologyError, audit_bracket_topology


def test_audits_binary_tree() -> None:
    audit = audit_bracket_topology("(())")

    assert audit.display_internal_nodes == 2
    assert audit.biological_internal_nodes == 2
    assert audit.leaves == 3
    assert audit.bifurcations == 2
    assert audit.polytomies == 0
    assert audit.max_biological_leaf_depth == 2


def test_collapses_brace_scaffold_into_polytomy() -> None:
    audit = audit_bracket_topology("{{}}")

    assert audit.display_internal_nodes == 2
    assert audit.biological_internal_nodes == 1
    assert audit.leaves == 3
    assert audit.bifurcations == 0
    assert audit.polytomies == 1
    assert audit.artificial_polytomy_nodes == 1
    assert audit.polytomy_size_histogram == {3: 1}
    assert audit.max_biological_leaf_depth == 1


def test_handles_polytomy_below_binary_root() -> None:
    audit = audit_bracket_topology("({{}})")

    assert audit.leaves == 4
    assert audit.biological_internal_nodes == 2
    assert audit.polytomy_size_histogram == {3: 1}
    assert audit.max_biological_node_depth == 1
    assert audit.max_biological_leaf_depth == 2


def test_counts_two_child_brace_group_as_bifurcation() -> None:
    audit = audit_bracket_topology("{}")

    assert audit.biological_internal_nodes == 1
    assert audit.bifurcations == 1
    assert audit.polytomies == 0
    assert audit.polytomy_marker_groups == 1
    assert audit.binary_polytomy_markers == 1
    assert audit.polytomy_size_histogram == {}


@pytest.mark.parametrize("topology", ["", "(()", "())", "(}"])
def test_rejects_malformed_topology(topology: str) -> None:
    with pytest.raises(TopologyError):
        audit_bracket_topology(topology)
