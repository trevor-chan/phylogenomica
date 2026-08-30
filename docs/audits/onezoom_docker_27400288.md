# OneZoom Docker snapshot 27400288

Audit date: 2026-08-30

## Acquisition

The official `onezoom/oztree` Docker image pinned at
`sha256:8d45f6f91bf0e9370803642334eb172778bcfad70ad1f7b4513d5db2cbc5dd3e`
was created on 2022-02-07. It contains a populated MySQL database and the
static viewer files generated for that database.

The root `ordered_nodes` record stores `parent = -27400288`. OneZoom's server
code documents this negative root parent as the tree-version marker, and the
image contains the corresponding `completetree`, cut-map, and date files.

Extraction ran in a container with network mode `none`, no published ports,
and `/sbin/my_init` substituted for the image's default command. This starts
the embedded services without running the default IUCN download task.

The tracked extractor produced 128,594,240 bytes under ignored raw storage.
It exported ordered rows as gzip-compressed MySQL batch TSV and verified the
row count while streaming each table.

| File | Rows | Bytes | SHA-256 |
|---|---:|---:|---|
| `ordered_leaves.tsv.gz` | 2,235,076 | 75,562,975 | `6c43f97a6e7776511c18f50ad534beb4f87370a7a272f56a288237da3dc56d9e` |
| `ordered_nodes.tsv.gz` | 2,235,075 | 36,079,832 | `eaaf769aaf0f170dbca6a777e5db9b86f3806115ceb2fe5401bb50a9badfea63` |
| `vernacular_by_ott.tsv.gz` | 812,065 | 12,036,708 | `fa6cbdc8b73977098b3ddb46e481264f1e4e2188ae62fe4129dadb053ea240aa` |
| `vernacular_by_name.tsv.gz` | 509 | 7,559 | `cf03964b05069ca7d5bbc82a63b582b1183d19419357a3633318f5906afe3068` |
| `images_by_ott.tsv.gz` | 105,344 | 3,747,352 | `57b872e8a8968fe01851523c7d35e8fabaa164a6a85c37e6066a07226bfc3a4a` |
| `images_by_name.tsv.gz` | 0 | 107 | `0a55c44af759965231f4fa17f0563aa104d918ffc7774a853b3fe508213e5ba0` |
| `completetree_27400288.js.gz` | — | 674,474 | `af95d0510445ef197a82d2cd55851d2394a3c88434bac746be1413c573424833` |
| `cut_position_map_27400288.js.gz` | — | 368,450 | `670e882c9dd5645cf2e1b4618ae7f8bf512597ca5bd42e13c3fa39ed9dca9e3d` |
| `dates_27400288.js.gz` | — | 116,783 | `80833fc0f6a98e67a9a7e1abce6f6ecebc883bf97ca5381e71f477a37eaddeb8` |

The local `manifest.json` additionally records the complete observed schemas,
selected columns, excluded columns, image metadata, MySQL version, and exact
row counts.

## Scope controls

The extractor uses a column allowlist. It excludes every unlisted database
table, including authentication, reservations, donations, sponsorship,
prices, API-use, and IUCN tables. It also excludes:

- `ordered_leaves.iucn` and `ordered_leaves.price`;
- the nine `ordered_nodes.iucn*` descendant-count columns.

The raw extraction is for local engineering and audit. Its availability in an
official public image does not resolve whether a compact derived gameplay
bundle may be redistributed.

## Structural findings

| Measure | Docker tree 27400288 | Current tree 29194525 | Change |
|---|---:|---:|---:|
| Leaves | 2,235,076 | 2,228,001 | -7,075 |
| Display internal nodes | 2,235,075 | 2,228,000 | -7,075 |
| Biological internal nodes | 201,578 | 228,574 | +26,996 |
| Biological bifurcations | 104,142 | 130,991 | +26,849 |
| Genuine multifurcations | 97,436 | 97,583 | +147 |
| Artificial brace nodes removed | 2,033,497 | 1,999,426 | -34,071 |
| Maximum biological leaf depth | 231 | 233 | +2 |
| Largest multifurcation | 11,554 | 11,554 | 0 |

These corrected counts treat every negative-`real_parent` internal record as
display scaffolding and fold its frontier into the enclosing nonnegative
record. The historical database's direct biological child degrees exactly
match the corrected static-bracket audit. The historical database and current
static viewer must not be combined: the differences are not limited to
additions or removals, and biological node counts and resolutions changed.

The Docker snapshot contains dates for 15,562 internal nodes and extinction
dates for 5 leaves.

## Metadata findings

Every leaf has a scientific name and popularity value, and nearly every leaf
has an OTT identifier. External-ID coverage is also strong enough to support
later ID-based enrichment.

| Leaf field | Records | Coverage |
|---|---:|---:|
| Scientific name | 2,235,076 | 100.00% |
| OTT | 2,234,836 | 99.99% |
| Wikidata | 1,653,298 | 73.97% |
| EOL | 2,060,024 | 92.17% |
| GBIF | 1,998,269 | 89.40% |
| NCBI | 478,880 | 21.43% |
| Popularity | 2,235,076 | 100.00% |

Internal-node metadata is much sparser: 6.06% have names, 5.50% have OTT IDs,
34.78% have popularity values, and 0.70% have ages. This does not affect
topological correctness but limits player-facing labels and dates.

The vernacular table has 812,065 records covering 191,547 OTT IDs. Among
leaves, 109,156 (4.88%) have a preferred English vernacular. The image table
has 105,344 records covering 85,811 OTT IDs; all exported records contain URL,
rights, and licence values. Among leaves, 77,633 (3.47%) have an
`overall_best_any` image, and only 44,361 (1.98%) have both that image and a
preferred English name.

The complete scientific-name and popularity coverage is encouraging for
topology and representative selection. Card-ready English-name-and-image
coverage is too sparse to require both fields globally; enrichment or a
curated playable subset will be necessary for a visually rich release.

## Decision

Snapshot 27400288 is suitable as a matched development source for implementing
schema parsing, normalized ingestion, topology reconstruction, and feasibility
audits. It is not the release dataset. A current production dump and explicit
derived-data reuse terms remain the upgrade and redistribution gates.

## Normalized ingestion result

On 2026-08-30, ingester version 2 built SQLite schema version 1 successfully
from the verified raw snapshot. The ignored processed artifact is
`data/processed/onezoom/27400288/onezoom.sqlite3`.

| Measure | Result |
|---|---:|
| SQLite bytes | 728,903,680 |
| Nodes | 2,235,075 |
| Leaves | 2,235,076 |
| Normalized representative rows | 2,497,401 |
| Normalized vernacular rows | 812,574 |
| Normalized image rows | 105,344 |
| Node rows with negative `real_parent` | 2,033,497 |
| Leaf rows with negative `real_parent` | 18 |

The corrected ingester-version-2 build used Python 3.14.7 and SQLite 3.53.2.
Its database SHA-256 is
`98b6bbb69e13646bffefa15c7c5cc4920be004a26fe48e3e8645ead76f5b7ee0`;
the exact runtime, input-manifest checksum, validation results, and reproduction
command are retained in the local processed manifest.

Validation confirmed contiguous node and leaf IDs, one expected root, no
missing display or biological parent references, no self-parented biological
nodes, successful SQLite integrity, and exact database/static-topology node and
leaf counts. No display-polytomy or monotypic-chain collapse was performed.

One exported vernacular contains a literal carriage return followed by an
escaped line feed. The ingester consequently treats only LF as a physical TSV
record separator, then decodes reviewed MySQL batch escapes within fields. A
regression test preserves this observed source convention.

## Biological-tree preprocessing result

Tree builder version 1 produced the ignored
`tree-v1/biological_tree.sqlite3` artifact from the normalized database. It is
113,790,976 bytes with SHA-256
`105c30d322bbe180c44175477f4021e262e227508acaabe8c1c0e3c6c5b87c0e`.

The build retained 201,578 biological internal nodes and all 2,235,076 leaves,
while excluding 2,033,497 negative-`real_parent` display nodes. It found one
root, no cycles, no orphan parents, no childless or unreachable biological
nodes, and a root descendant-leaf count of 2,235,076. The derived database
passed SQLite integrity validation and matched the corrected static topology.

There are 104,142 bifurcations and 97,436 genuine polytomies. The largest has
11,554 children. There are no monotypic biological internal nodes in this
snapshot, so the generic chain-collapse pass removed zero nodes and preserved
the maximum leaf depth of 231.

## Query-layer smoke test

The read-only query layer was exercised against named leaves in the normalized
metadata. Historical leaf `887269` is `Homo sapiens`, leaf `887270` is
`Pan troglodytes`, and leaf `889395` is `Mus musculus`.

The collapsed `Homo sapiens` lineage contains 44 internal nodes and 44
candidate-bearing sister-group tiers. Tier capacity ranges from 2 to 1,324,318
off-target leaves. The lowest common ancestor of human and chimpanzee is node
`887274`; the human/mouse lowest common ancestor is node `887020`. At the
human/chimpanzee LCA, the human leaf and the internal branch containing the
chimpanzee are returned as sibling branches. These results exercise
lineage ordering, mixed node/leaf children, descendant capacity, and LCA over
the full artifact; they are diagnostics rather than an eligibility decision.

## Batch target-feasibility result

Feasibility audit version 4 evaluates the playable-lineage definition rather
than requiring a closest-sister endpoint. For `M=5` and `N=10`, a lineage has
49 unique relative species and one target. Each of the first four stages has
eight decoys, one deeper mulligan, and one deepest unlock. The ultimate stage
has eight decoys, one deepest selected-relative mulligan, and the target as a
normal visible, selectable card. Clicking the target ends the game.

The rich-card pass restricts both targets and relatives to leaves with a
scientific name, preferred English name, and `overall_best_any` image whose
URL, rights, and licence fields are nonempty. It recomputes capacity from that
restricted universe.

| Measure | Result |
|---|---:|
| Rich-card species considered as targets | 44,361 |
| Targets supporting the full playable lineage | 43,032 (97.0041%) |
| Failure: insufficient total relatives | 0 (0.0000%) |
| Failure: insufficient ordered stage-role structure | 1,329 (2.9959%) |
| Total rich-card relatives available to every target | 44,360 |
| Median usable rich-card tiers | 39 |
| Median rich-card capacity per target-tier instance | 27 |

Total capacity is constant because every non-target rich-card leaf diverges
from the target at exactly one backbone event when the game root is the root of
life. The only failures are therefore distribution failures: the valid species
cannot be arranged into the requested ordered role shape. No literal closest
sister tier is required, and source tiers or excess species may remain
unselected. Mulligan and unlock roles must occupy distinct tiers in transition
stages so a source polytomy is never split arbitrarily across the two outcomes.
In the ultimate stage, only the selected-relative mulligan requires its own
tier; the target is the endpoint rather than an off-target tier representative.

The ignored detailed result is generated with:

```bash
phylogenomica-audit-targets data/processed/onezoom/27400288 \
  --require-rich-cards \
  --output data/processed/onezoom/27400288/target-feasibility-v4/rich-cards.json
```

### Superseded models

Audit version 1 required the ultimate stage to reach the literal closest
sister event and initially required two presentable terminal representatives.
It retained 20,399 rich-card targets; lowering the terminal minimum to one
retained 33,013. Those results demonstrated that closest-sister capacity, not
overall topology, caused most exclusions. Version 2 removed that endpoint and
used two unlock species in each transition stage, retaining 43,381 targets.
Version 3 replaces the second unlock with a non-advancing, score-neutral
mulligan and applies the same ordered role pattern to the ultimate stage,
retaining 42,933 targets. Version 4 treats the target as the final stage-ending
card and removes the unnecessary ultimate-stage unlock. These earlier results
are preserved as decision evidence, not current eligibility counts.
