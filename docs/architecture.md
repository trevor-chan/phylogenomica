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

The exact schema will follow inspection of a real OneZoom snapshot. A likely
normalized node contains:

```text
Node
├── node_id
├── biological_parent_id
├── source_parent_id
├── is_leaf
├── scientific_name
├── vernacular_name
├── rank
├── age_ma
├── source identifiers
│   ├── ott_id
│   ├── wikidata_id
│   ├── eol_id
│   ├── gbif_id
│   └── ncbi_id / others
├── representative_image
│   ├── source and identifier
│   ├── URL
│   ├── creator
│   └── license and attribution
└── source-specific provenance
```

Derived statistics such as child IDs, descendant-leaf count, collapsed depth,
candidate capacity, and target quality can live in precomputed tables or
indexes. Do not mutate raw source records to represent collapsed gameplay
topology.

## Derived tree

Preprocessing:

1. loads normalized nodes and leaves;
2. reconstructs biological edges using the verified `real_parent` equivalent;
3. validates roots, missing references, cycles, and reachability;
4. constructs child adjacency;
5. collapses single-child chains for gameplay;
6. preserves multi-child nodes as genuine polytomies;
7. computes ancestry, depth, and descendant statistics;
8. identifies candidate-bearing sister groups.

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
