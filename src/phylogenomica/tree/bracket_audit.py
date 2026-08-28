"""Structural audit of OneZoom's compact bracket topology.

Parentheses represent genuine binary nodes. Braces represent the binary
scaffolding OneZoom uses to display a biological polytomy. A connected run of
brace nodes is counted here as one biological node whose frontier contains all
of the original children.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass


class TopologyError(ValueError):
    """Raised when a compact topology is malformed."""


@dataclass(slots=True)
class _Frame:
    opener: str
    biological_depth: int
    internal_children: int = 0
    flattened_frontier: int = 0


@dataclass(frozen=True, slots=True)
class TopologyAudit:
    display_internal_nodes: int
    biological_internal_nodes: int
    leaves: int
    bifurcations: int
    polytomies: int
    polytomy_marker_groups: int
    binary_polytomy_markers: int
    artificial_polytomy_nodes: int
    max_biological_node_depth: int
    max_biological_leaf_depth: int
    polytomy_size_histogram: dict[int, int]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable record."""
        return asdict(self)


def audit_bracket_topology(topology: str) -> TopologyAudit:
    """Validate and summarize a complete OneZoom bracket string in one pass."""
    if not topology:
        raise TopologyError("topology is empty")

    stack: list[_Frame] = []
    display_nodes = 0
    bifurcations = 0
    outer_polytomies = 0
    brace_nodes = 0
    roots = 0
    max_node_depth = 0
    max_leaf_depth = 0
    polytomy_sizes: Counter[int] = Counter()
    expected_opener = {")": "(", "}": "{"}

    for position, character in enumerate(topology):
        if character in "({":
            if stack:
                parent = stack[-1]
                biological_depth = (
                    parent.biological_depth
                    if parent.opener == "{" and character == "{"
                    else parent.biological_depth + 1
                )
            else:
                biological_depth = 0
                roots += 1

            stack.append(_Frame(character, biological_depth))
            display_nodes += 1
            max_node_depth = max(max_node_depth, biological_depth)
            if character == "(":
                bifurcations += 1
            else:
                brace_nodes += 1
            continue

        if character not in ")}":
            raise TopologyError(
                f"invalid topology character {character!r} at position {position}"
            )
        if not stack or stack[-1].opener != expected_opener[character]:
            raise TopologyError(f"unmatched {character!r} at position {position}")

        frame = stack.pop()
        implicit_leaves = 2 - frame.internal_children
        if implicit_leaves < 0:
            raise TopologyError(
                f"node ending at position {position} has more than two display children"
            )
        if implicit_leaves:
            max_leaf_depth = max(max_leaf_depth, frame.biological_depth + 1)

        if frame.opener == "{":
            frontier_size = frame.flattened_frontier + implicit_leaves
            is_outer_polytomy = not stack or stack[-1].opener != "{"
            if is_outer_polytomy:
                outer_polytomies += 1
                polytomy_sizes[frontier_size] += 1
        else:
            frontier_size = 1

        if stack:
            parent = stack[-1]
            parent.internal_children += 1
            parent.flattened_frontier += (
                frontier_size if frame.opener == "{" else 1
            )

    if stack:
        raise TopologyError(f"{len(stack)} topology nodes are unclosed")
    if roots != 1:
        raise TopologyError(f"expected one topology root; found {roots}")

    binary_polytomy_markers = polytomy_sizes.pop(2, 0)
    genuine_polytomies = outer_polytomies - binary_polytomy_markers
    biological_bifurcations = bifurcations + binary_polytomy_markers
    biological_nodes = biological_bifurcations + genuine_polytomies
    return TopologyAudit(
        display_internal_nodes=display_nodes,
        biological_internal_nodes=biological_nodes,
        leaves=display_nodes + 1,
        bifurcations=biological_bifurcations,
        polytomies=genuine_polytomies,
        polytomy_marker_groups=outer_polytomies,
        binary_polytomy_markers=binary_polytomy_markers,
        artificial_polytomy_nodes=brace_nodes - outer_polytomies,
        max_biological_node_depth=max_node_depth,
        max_biological_leaf_depth=max_leaf_depth,
        polytomy_size_histogram=dict(sorted(polytomy_sizes.items())),
    )
