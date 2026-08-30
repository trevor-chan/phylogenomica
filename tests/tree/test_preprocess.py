import sqlite3
from pathlib import Path

import pytest

from phylogenomica.tree.preprocess import (
    TreePreprocessingError,
    _create_tree_database,
    analyze_parent_graph,
)


def test_analyzes_bifurcations_and_polytomies() -> None:
    analysis = analyze_parent_graph(
        {1: None, 2: 1, 3: 1},
        {2: 2, 3: 3},
    )

    assert analysis.root_id == 1
    assert analysis.depth_by_node == {1: 0, 2: 1, 3: 1}
    assert analysis.child_count_by_node == {1: 2, 2: 2, 3: 3}
    assert analysis.descendant_leaf_count_by_node == {1: 5, 2: 2, 3: 3}
    assert analysis.metrics()["bifurcations"] == 2
    assert analysis.metrics()["polytomies"] == 1
    assert analysis.retained_after_collapse == {1, 2, 3}


def test_collapses_entire_monotypic_chain_but_preserves_root() -> None:
    analysis = analyze_parent_graph(
        {1: None, 2: 1, 3: 2, 4: 1},
        {3: 1, 4: 2},
    )

    assert analysis.retained_after_collapse == {1, 4}
    assert analysis.collapsed_parent_by_node == {
        1: None,
        2: 1,
        3: 1,
        4: 1,
    }
    assert analysis.collapsed_depth_by_node == {1: 0, 2: None, 3: None, 4: 1}
    assert analysis.collapsed_child_count_by_node == {
        1: 2,
        2: None,
        3: None,
        4: 2,
    }


def test_rejects_multiple_roots() -> None:
    with pytest.raises(TreePreprocessingError, match="expected one biological root"):
        analyze_parent_graph({1: None, 2: None}, {1: 1, 2: 1})


def test_rejects_internal_and_leaf_orphans() -> None:
    with pytest.raises(TreePreprocessingError, match="non-biological nodes"):
        analyze_parent_graph({1: None, 2: 99}, {1: 1})

    with pytest.raises(TreePreprocessingError, match="leaf_parents"):
        analyze_parent_graph({1: None}, {1: 1, 99: 1})


def test_rejects_disconnected_cycle() -> None:
    with pytest.raises(TreePreprocessingError, match="cycle"):
        analyze_parent_graph(
            {1: None, 2: 3, 3: 2},
            {1: 1, 2: 1},
        )


def test_rejects_childless_internal_node() -> None:
    with pytest.raises(TreePreprocessingError, match="no children"):
        analyze_parent_graph({1: None, 2: 1}, {1: 1})


def test_writes_collapsed_leaf_projection(tmp_path: Path) -> None:
    source = sqlite3.connect(":memory:")
    source.execute(
        "CREATE TABLE leaves (leaf_id INTEGER, biological_parent_id INTEGER)"
    )
    source.executemany(
        "INSERT INTO leaves VALUES (?, ?)",
        ((1, 3), (2, 4), (3, 4)),
    )
    analysis = analyze_parent_graph(
        {1: None, 2: 1, 3: 2, 4: 1},
        {3: 1, 4: 2},
    )
    path = tmp_path / "tree.sqlite3"

    leaf_count, max_depth = _create_tree_database(
        path,
        source=source,
        analysis=analysis,
        dataset_version="test-1",
        normalized_database_sha256="abc",
    )
    source.close()

    assert leaf_count == 3
    assert max_depth == 2
    output = sqlite3.connect(path)
    try:
        leaves = output.execute(
            "SELECT leaf_id, parent_node_id, biological_depth, "
            "collapsed_parent_node_id, collapsed_depth, skipped_monotypic_nodes "
            "FROM biological_leaves ORDER BY leaf_id"
        ).fetchall()
    finally:
        output.close()
    assert leaves == [
        (1, 3, 3, 1, 1, 2),
        (2, 4, 2, 4, 2, 0),
        (3, 4, 2, 4, 2, 0),
    ]
