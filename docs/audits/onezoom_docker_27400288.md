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
| Biological internal nodes | 328,736 | 355,429 | +26,693 |
| Biological bifurcations | 237,540 | 265,546 | +28,006 |
| Genuine multifurcations | 91,196 | 89,883 | -1,313 |
| Artificial brace nodes removed | 1,906,339 | 1,872,571 | -33,768 |
| Maximum biological leaf depth | 231 | 233 | +2 |
| Largest multifurcation | 11,551 | 11,552 | +1 |

The historical database and current static viewer must not be combined. The
differences are not limited to additions or removals; inferred biological node
counts and resolutions changed substantially.

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
