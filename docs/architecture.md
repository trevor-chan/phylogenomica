# Architecture

## Principles

The first system is a Python library with thin developer scripts and a future
frontend. Data transformation, topology algorithms, game generation, and game
state remain independently testable. The UI consumes generated game objects and
state transitions; it does not decide phylogenetic correctness.

```text
upstream snapshot
      │
      ▼
data ingestion and normalization
      │
      ▼
biological tree preprocessing
      │
      ├── audit and target eligibility
      ▼
seeded game generation
      │
      ▼
UI-independent gameplay engine
      │
      ▼
future API / frontend / cladogram view
```

## Module boundaries

### `phylogenomica.data`

Owns dump parsing, schema adapters, metadata normalization, validation,
manifests, serialization, and quantitative audits. Source-specific field names
should stop at this boundary.

### `phylogenomica.tree`

Owns root and child construction, biological-parent reconstruction,
monotypic-chain collapse, genuine polytomy preservation, ancestry, descendant
statistics, depth, sister groups, and lowest-common-ancestor utilities if
needed. It must not know about player actions or UI layout.

### `phylogenomica.generation`

Owns target eligibility, lineage-tier extraction, representative selection,
stage allocation, game assembly, seeded randomness, and generated-game
validation. It consumes normalized tree and metadata interfaces.

### `phylogenomica.gameplay`

Owns active candidates, guesses, reveals, scoring, stage transitions, game
completion, and serializable player state. It operates on an already validated
game and does not query upstream databases.

Developer scripts in `scripts/` call these modules but contain no reusable
business logic. A future API and frontend should be separate layers rather than
new responsibilities inside the engine.

## Normalized records

The first implemented normalized format is SQLite schema version 1. It keeps
source nodes and leaves in separate tables because OneZoom assigns their IDs in
separate namespaces. Ingestion does not collapse or otherwise reinterpret the
tree.

```text
onezoom.sqlite3
├── dataset_metadata
├── nodes
│   ├── node_id
│   ├── display_parent_id / biological_parent_id
│   ├── source_parent / source_real_parent
│   ├── is_polytomy_scaffold
│   ├── scientific_name / age_ma / popularity
│   └── OTT, Wikidata, EOL, GBIF, NCBI, and other source IDs
├── leaves
│   ├── leaf_id
│   ├── display_parent_id / biological_parent_id
│   ├── source_parent / source_real_parent
│   ├── is_polytomy_member
│   ├── scientific_name / extinction_date_ma / popularity rank
│   └── source IDs
├── node_representatives
├── vernacular_names
└── images
```

For the root, both normalized parents are `NULL`; its negative source parent is
retained as OneZoom's tree-version marker. Everywhere else,
`biological_parent_id = abs(source_real_parent)`. A negative
`source_real_parent` is also retained and flagged because it records membership
in a polytomy or its display scaffold. `display_parent_id` retains the display
tree edge independently.

The historical snapshot has a leaf popularity-rank field but no taxonomic-rank
field. Missing taxonomy rank is preserved as missing rather than inferred from
names. Representative sets are normalized from their eight repeated source
columns into ordered `(node, category, position, OTT ID)` rows. Name- and
OTT-keyed vernacular/image tables share normalized tables while retaining their
source table and row ID.

Derived statistics such as child IDs, descendant-leaf count, collapsed depth,
candidate capacity, and target quality belong in later precomputed tables or
indexes. Do not mutate source records to represent collapsed gameplay topology.

Each processed directory also contains a manifest with the input-manifest
checksum, source and schema versions, runtime versions, row counts, validation
results, database checksum, and reproduction command. The directory is built
under a temporary name and renamed only after validation succeeds.

## Derived tree

Preprocessing:

1. loads normalized nodes and leaves;
2. excludes negative-`real_parent` internal display scaffolds and reconstructs
   biological edges using `biological_parent_id`;
3. validates roots, missing references, cycles, and reachability;
4. constructs child adjacency;
5. collapses single-child chains for gameplay;
6. preserves multi-child nodes as genuine polytomies;
7. computes ancestry, depth, and descendant statistics;
8. identifies candidate-bearing sister groups.

Tree schema version 1 implements steps 1–7 as a separate
`biological_tree.sqlite3` artifact. Its `biological_nodes` table records direct
parent, degree, depth, descendant-leaf count, polytomy status, and the collapsed
parent/depth projection. `biological_leaves` records both direct and collapsed
parents and depths. Parent indexes provide child adjacency without duplicating
the normalized metadata tables. The source node/leaf namespaces remain
separate.

At each node on a target lineage, children that do not contain the target form
the candidate pool for one divergence tier. Sampling several representatives
from branches at the same event does not create additional tiers.

## Target eligibility

Eligibility is derived, versioned data rather than a hard-coded leaf list.

```text
TargetEligibility
├── target_id
├── usable_depth
├── candidate_capacity
├── terminal_group_capacity
├── constructible_stage_count
├── name/image/age coverage
├── quality score and reasons
└── eligible
```

Species that fail target eligibility remain selectable as candidate relatives.
Eligibility policy is configuration so audit evidence can change thresholds
without rewriting topology code.

## Game and stage representation

```text
Game
├── dataset_version
├── generator_version
├── random_seed
├── target_id
├── generation_config
└── stages[]
    ├── stage_index
    ├── start_node_id
    ├── end_node_id
    ├── candidates[]
    │   ├── species_id
    │   ├── tier_index
    │   └── player-facing metadata reference
    ├── tiers[]
    │   ├── source_node_id
    │   └── candidate_ids[]
    └── terminal_tier_index
```

Mutable play state is separate:

```text
GameState
├── current_stage_index
├── active_candidate_ids
├── revealed_candidate_ids
├── resolved tree fragments
├── score and score remaining
└── completed
```

Separating the immutable puzzle from play state makes validation, replay,
sharing, persistence, and deterministic regression tests simpler.

## Generation

Inputs are a target or target-selection policy, candidate count `N`, stage count
`M`, random seed, dataset version, generator version, and quality configuration.

For an eligible target, generation:

1. extracts ordered candidate-bearing tiers;
2. removes unusable tiers without inventing depth;
3. partitions the usable lineage into approximately `M` regions;
4. samples roughly `N` unique candidates per region;
5. ensures each deepest visible tier is valid, preferably with multiple
   representatives;
6. ensures every later stage lies within the target-containing continuation of
   the previous stage;
7. validates and serializes the complete game.

Randomness comes only from an explicit local generator. Given identical data,
generator version, target, configuration, and seed, output bytes should be
stable where practical.

## Guess transition

For chosen candidate `x`, compare its tier with the deepest active tier.

- If equal, complete the stage and incorporate its remaining structure.
- If earlier, reveal the chosen candidate and only the information implied by
  that tier; preserve same-tier peers and all deeper candidates as required by
  the game rules.

The engine returns a transition describing placements, remaining candidates,
score change, and completion. The frontend renders that transition and never
recomputes it.

## Validation

Every generated stage requires automated checks:

- **Correct answer:** all terminal candidates have the same represented
  relationship to the target.
- **Ordering:** every earlier tier is strictly more distant than every later
  tier.
- **Polytomy:** candidates sharing a tier share the relevant divergence event.
- **Target hiding:** the target is absent from candidate cards.
- **Duplicates:** a species is not reused unless configuration explicitly
  permits it.
- **Continuity:** successive stages descend along one target-containing lineage.
- **Reveal safety:** a guess never eliminates a possibly closer candidate.
- **Determinism:** identical versioned inputs reproduce the same game.

Tree preprocessing also needs malformed-input tests for cycles, orphans,
multiple roots, missing identifiers, and pathological monotypic chains.

## Versioning

A generated puzzle belongs to a specific data and algorithm snapshot. Saved or
shared identifiers should eventually include:

```text
dataset_version + generator_version + target_id + config + seed
```

A data or generator upgrade must not silently reinterpret an old puzzle. Schema
migrations and compatibility guarantees can be designed once the first
serialized game format exists.
