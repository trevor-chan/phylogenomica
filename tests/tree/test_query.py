import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from phylogenomica.tree.preprocess import _create_tree_database, analyze_parent_graph
from phylogenomica.tree.query import BiologicalTree, TaxonRef, TreeQueryError


@pytest.fixture
def tree_database(tmp_path: Path) -> Iterator[Path]:
    source = sqlite3.connect(":memory:")
    source.execute(
        "CREATE TABLE leaves (leaf_id INTEGER, biological_parent_id INTEGER)"
    )
    source.executemany(
        "INSERT INTO leaves VALUES (?, ?)",
        ((1, 3), (2, 3), (3, 4), (4, 4), (5, 4)),
    )
    analysis = analyze_parent_graph(
        {1: None, 2: 1, 3: 2, 4: 1},
        {3: 2, 4: 3},
    )
    path = tmp_path / "tree.sqlite3"
    _create_tree_database(
        path,
        source=source,
        analysis=analysis,
        dataset_version="test-1",
        normalized_database_sha256="abc",
    )
    source.close()
    yield path


def test_queries_direct_and_collapsed_children(tree_database: Path) -> None:
    with BiologicalTree.open(tree_database) as tree:
        assert tree.root_id == 1
        assert tree.children(1, topology="biological") == (
            TaxonRef("node", 2),
            TaxonRef("node", 4),
        )
        assert tree.children(1) == (
            TaxonRef("node", 3),
            TaxonRef("node", 4),
        )
        assert tree.children(4) == (
            TaxonRef("leaf", 3),
            TaxonRef("leaf", 4),
            TaxonRef("leaf", 5),
        )
        assert [branch.descendant_leaf_count for branch in tree.child_branches(1)] == [
            2,
            3,
        ]


def test_queries_root_to_target_lineage(tree_database: Path) -> None:
    with BiologicalTree.open(tree_database) as tree:
        target = TaxonRef("leaf", 1)
        assert tree.lineage_node_ids(target, topology="biological") == (1, 2, 3)
        assert tree.lineage_node_ids(target) == (1, 3)
        assert tree.lineage_node_ids(TaxonRef("node", 4)) == (1, 4)


def test_queries_descendant_counts_and_ids(tree_database: Path) -> None:
    with BiologicalTree.open(tree_database) as tree:
        root = TaxonRef("node", 1)
        branch = TaxonRef("node", 4)
        leaf = TaxonRef("leaf", 2)
        assert tree.descendant_leaf_count(root) == 5
        assert tree.descendant_leaf_count(branch) == 3
        assert tree.descendant_leaf_count(leaf) == 1
        assert list(tree.iter_descendant_leaf_ids(branch)) == [3, 4, 5]
        assert list(tree.iter_descendant_leaf_ids(root, limit=2)) == [1, 2]
        assert list(tree.iter_descendant_leaf_ids(leaf)) == [2]


def test_finds_lowest_common_ancestor(tree_database: Path) -> None:
    with BiologicalTree.open(tree_database) as tree:
        assert (
            tree.lowest_common_ancestor(TaxonRef("leaf", 1), TaxonRef("leaf", 2)) == 3
        )
        assert (
            tree.lowest_common_ancestor(TaxonRef("leaf", 1), TaxonRef("leaf", 3)) == 1
        )
        assert (
            tree.lowest_common_ancestor(TaxonRef("node", 4), TaxonRef("leaf", 3)) == 4
        )


def test_groups_all_polytomy_peers_into_one_sister_tier(
    tree_database: Path,
) -> None:
    with BiologicalTree.open(tree_database) as tree:
        groups = tree.candidate_sister_groups(1)

    assert [group.ancestor_node_id for group in groups] == [1, 3]
    assert groups[0].target_branch == TaxonRef("node", 3)
    assert [branch.taxon for branch in groups[0].sister_branches] == [
        TaxonRef("node", 4)
    ]
    assert groups[0].candidate_leaf_capacity == 3
    assert groups[1].target_branch == TaxonRef("leaf", 1)
    assert [branch.taxon for branch in groups[1].sister_branches] == [
        TaxonRef("leaf", 2)
    ]


def test_direct_sister_groups_skip_non_candidate_monotypic_event(
    tree_database: Path,
) -> None:
    with BiologicalTree.open(tree_database) as tree:
        groups = tree.candidate_sister_groups(1, topology="biological")

    assert [group.ancestor_node_id for group in groups] == [1, 3]
    assert groups[0].target_branch == TaxonRef("node", 2)


def test_rejects_unknown_or_removed_taxa(tree_database: Path) -> None:
    with BiologicalTree.open(tree_database) as tree:
        with pytest.raises(TreeQueryError, match="unknown biological leaf"):
            tree.lineage_node_ids(TaxonRef("leaf", 99))
        with pytest.raises(TreeQueryError, match="absent from the collapsed"):
            tree.children(2)
        with pytest.raises(TreeQueryError, match="limit must be positive"):
            list(tree.iter_descendant_leaf_ids(TaxonRef("node", 1), limit=0))
