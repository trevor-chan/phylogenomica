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

Owns active relatives, guesses, reveals, scoring, stage transitions, game
completion, and serializable player state. It operates on an already validated
game and does not query upstream databases.

Gameplay engine version 1 is a pure transition function over immutable state:
`apply_guess(game, state, species_id)` returns the next `GameState` and a
`GuessOutcome` carrying every placement, the remaining relatives, the cost and
bonus, banked score, the open stage's standing value, and completion flags. The
engine does not revalidate the game; it consumes one already checked by
`validate_game_structure`. `restore_state` rebuilds player state and confirms
it is a reachable position in that specific game.

### `phylogenomica.prototype`

Owns the local browser prototype: a dependency-free `http.server` application
that holds one session and serves a static page. It is the first instance of
the separate presentation layer, and it decides nothing. Every guess is
resolved by the gameplay engine on the server; the page renders the returned
transition.

Its API is deliberately stage-scoped. It serves only the open stage's cards,
and a card's tier and role are added to the payload only once that card has
been placed. The concealed target and the answer to the open stage therefore
never cross the wire early, so stage-scoped target visibility holds against a
player reading network traffic, not merely against one reading the screen.

The cladogram is the interface. An SVG trunk runs left to right from the root
to the target, one column per placed tier, with branches alternating above and
below so neighbouring columns never collide. Cards for the open stage occupy a
single row beneath it. The four distinctions the design requires are encoded
independently: stroke style separates resolved from unresolved, tip fill
separates inferred from revealed, a shared trunk node renders a polytomy as a
rake with no implied order, and the dashed trunk ending in `???` is the
concealed continuation. The tree is built only from placed species, so it
cannot leak the open stage's answer.

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
relative capacity, and target quality belong in later precomputed tables or
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
the relative pool for one divergence tier. Sampling several representatives
from branches at the same event does not create additional tiers.

### Tree query interface

`phylogenomica.tree.query.BiologicalTree` opens the derived database read-only
and keeps node and leaf references explicit because their numeric IDs overlap.
It supports:

- deterministic child and child-capacity queries;
- direct biological or monotypic-collapsed parentage;
- root-to-taxon lineages and node depth;
- descendant-leaf count and deterministic leaf iteration;
- lowest common ancestors; and
- root-to-target candidate-bearing sister groups.

A sister group contains one target-continuation branch and every off-target
child branch at the same ancestor. Those off-target branches remain one tier
even when the ancestor is a polytomy. Each branch carries its descendant-leaf
count so feasibility and selection code can reason about capacity without
enumerating every leaf. The `phylogenomica-lineage` command serializes this
diagnostic view for an arbitrary leaf.

These are topology queries, not selection policy. Metadata preferences,
eligibility thresholds, seeded sampling, and stage allocation remain in
`phylogenomica.generation`.

## Target eligibility

Eligibility is derived, versioned data rather than a hard-coded leaf list.

```text
TargetEligibility
├── target_id
├── usable_depth
├── total_relative_capacity
├── completed_stages
├── name/image coverage flags
├── metadata and topology eligibility
├── zero or more explicit reason codes
└── eligible
```

Species that fail target eligibility remain selectable as relatives.
Eligibility policy is configuration so audit evidence can change thresholds
without rewriting topology code.

Eligibility index schema version 1 stores this evidence in
`target_eligibility.sqlite3`. The primary table contains one row for every leaf
in the configured target universe, and `target_reasons` stores normalized,
documented failure codes. The initial rich-card policy indexes 44,361 potential
targets rather than copying 2,190,715 leaves that already fail its presentation
requirements. Counts for excluded source leaves and their overlapping metadata
reasons remain in the manifest.

`phylogenomica.generation.eligibility` builds the directory atomically after
verifying the normalized and tree database manifests and checksums. Its own
manifest pins the complete feasibility configuration, audit/index/schema
versions, source checksums, row counts, reason definitions, validation results,
database checksum, and reproduction command. The SQLite bytes are deterministic
for identical versioned inputs. `TargetEligibilityIndex` provides read-only
lookup and ordered eligible-target iteration for the selector.

### Batch feasibility audit

`phylogenomica.generation.feasibility` evaluates topology and target metadata
without issuing a lineage query for every leaf. It propagates path state once
per retained internal node and streams the leaf table once. This makes exact
dataset-wide distributions practical while keeping the audit separate from the
tree-query layer.

Audit version 4 evaluates the playable-lineage model directly. For `M` stages
of `N` members it requires one target and `M * N - 1` unique relatives. Each of
the first `M - 1` stages assigns `N - 2` decoys, one deeper mulligan, and one
deepest unlock. The ultimate stage assigns `N - 2` decoys, one deepest selected-
relative mulligan, and the visible selectable target at the endpoint.

The propagated audit state greedily assigns each nonempty ordered tier to the
earliest unfinished role. Decoy, mulligan, and unlock roles occupy distinct
tiers within transition stages; the ultimate stage completes once its mulligan
tier is assigned because the target supplies the final selectable card. Excess
species and whole tiers may remain unselected. This earliest-role construction
maximizes the suffix available to later stages and tests hierarchy without
requiring any stage to end at a literal closest-sister event.

The optional rich-card audit changes the species universe rather than changing
topological correctness. It marks leaves with a scientific name, preferred
English vernacular, and `overall_best_any` image whose URL, rights, and licence
are nonempty. Descendant capacity is recomputed bottom-up using only those
leaves, and both targets and off-target relative representatives are restricted
to that set. Thus a large sister clade contributes only its actually
presentable species, and the target is still excluded from every off-target
capacity. With the root of life as the game root, every other included leaf
diverges from the target at exactly one backbone tier, so total relative
capacity is the size of the filtered universe minus one. Ordered stage
structure—not raw total capacity—is the remaining eligibility test.

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
    ├── members[]
    │   ├── species_id
    │   ├── tier_index
    │   ├── role: decoy | mulligan | unlock | target
    │   └── player-facing metadata reference
    ├── tiers[]
    │   ├── source_node_id
    │   └── relative_ids[]
    ├── mulligan_species_id
    ├── unlock_species_id (transition stages only)
    └── target_species_id (ultimate stage only)
```

Mutable play state is separate:

```text
GameState
├── current_stage_index
├── active_species_ids
├── revealed_species_ids
├── resolved tree fragments
├── score and score remaining
└── completed
```

Separating the immutable puzzle from play state makes validation, replay,
sharing, persistence, and deterministic regression tests simpler.

## Generation

Inputs are a target or target-selection policy, members per stage `N`, stage
count `M`, random seed, dataset version, generator version, and quality
configuration.

For an eligible target, generation:

1. extracts ordered relative-bearing tiers;
2. removes unusable tiers without inventing depth;
3. samples `M * N - 1` unique relatives across the usable backbone;
4. assigns decoy, mulligan, and unlock roles on strictly ordered distinct tiers
   in transition stages, then assigns decoys and a deepest-relative mulligan in
   the ultimate stage;
5. prefers small polytomies and a broad, approximately uniform depth sample;
6. ensures every later stage lies within the target-containing continuation of
   the previous stage;
7. validates and serializes the complete game.

Relative selector version 1 implements steps 1–5 as an immutable intermediate
selection. The tree layer batch-maps the eligibility index's compact candidate
universe to lowest-common-ancestor tiers without scanning every source leaf.
The selector then uses dynamic programming to count feasible continuations and
samples an ordered allocation from the minimum-polytomy-penalty solutions.
Selected groups of one or two at a tier have no penalty; larger selected
polytomies are used only when the requested game shape requires them.

After tier allocation, representatives are sampled without replacement. All
rich-card candidates remain possible, while decile-bucketed OneZoom popularity
rank supplies a moderate, nonzero soft weight. This keeps metadata quality
subordinate to topology and avoids turning recognizability into an eligibility
rule. The output contains 49 role-assigned relatives; the global target supplies
the tenth member of the ultimate stage during game assembly.

Game generator version 1 implements steps 6–7 as the immutable `GeneratedGame`
above. It resolves cards, appends the target to the ultimate stage, records
each stage's backbone boundary nodes, shuffles members, and validates.

`phylogenomica.data.cards` owns card resolution. `CardMetadataStore` batches
leaf, vernacular, and image lookups over the normalized database and applies
the same predicates as the rich-card feasibility policy: a scientific name, a
`preferred` English vernacular, and an `overall_best_any` image whose URL,
rights, and licence are nonempty after trimming. A species may be described by
an OTT-keyed or a name-keyed record; OTT-keyed records win, and duplicates
resolve by source table and row ID so one species always yields one card. The
store raises rather than emitting a partial card, so a presentation gap fails
generation instead of reaching a player.

Stage boundaries are recorded as backbone node IDs. A transition stage starts
at its shallowest selected tier's ancestor and ends at its deepest; the
ultimate stage ends at the target's own collapsed parent, which may lie deeper
than its deepest selected tier when trailing tiers go unused. Because a tier
index is a position on the collapsed root-to-target path, validation proves
continuity from the recorded tiers alone and additionally anchors every node to
a real lineage position when the backbone is supplied.

Randomness comes only from an explicit local generator. Given identical data,
generator version, target, configuration, and seed, output bytes should be
stable where practical. Each stage's member shuffle draws from its own stream,
keyed by the generation record plus the stage index, so stages never share one
permutation and no role occupies a fixed visible slot.

### Serialized games

`to_dict` and `game_from_dict` are inverses, and `load_game` reads one from
disk. Deserialization is the boundary at which an untrusted file becomes a game
object, so it always validates.

A game carries every field its identifier digests, so a game read back from
disk recomputes its own `game_id` and stage shuffles without the selection that
produced it. `validate_game_structure` therefore applies every rule except
agreement with a specific selection: role counts and tier ordering, target
visibility, the tier projection, the per-stage shuffle, card completeness,
duplicates, and backbone continuity. `validate_generated_game` adds the
selection cross-check and is what generation uses. A serialized configuration
is parsed from its five inputs alone; the derived totals are recomputed and
compared so a stale or edited policy is rejected rather than reinterpreted.

The gameplay engine consumes a loaded game and never reopens the upstream
databases.

The selector derives its local random state from a canonical SHA-256 record of
dataset version, selector version, target, complete feasibility configuration,
and user seed. It never uses global random state. Changing the seed can change
both feasible tier allocation and representatives, so one target can support a
large combinatorial family of games.

## Guess transition

For chosen species `x`, inspect its immutable stage role.

- If it is an unlock species, complete the transition stage and incorporate its
  remaining structure.
- If it is the target, complete the ultimate stage and the game.
- If it is the mulligan, award one bonus point, place its relationship, and
  keep the stage-ending unlock or target active. Scoring makes this route
  equivalent to choosing that card immediately.
- If it is a decoy, reveal only the information implied by its tier; preserve
  same-tier peers and every deeper relative as required by the game rules.

The engine returns a transition describing placements, remaining relatives,
score change, and completion. The frontend renders that transition and never
recomputes it.

## Validation

Every generated stage requires automated checks:

- **Role ordering:** every transition stage has one unlock deeper than one
  mulligan, which is deeper than every decoy; the ultimate stage has one
  mulligan deeper than every decoy and the target as its endpoint.
- **Role separation:** no selected tier contains multiple stage roles.
- **Mulligan score:** `mulligan → unlock` or `mulligan → target` and the
  corresponding immediate stage-ending choice finish with the same score.
- **Ordering:** every earlier tier is strictly more distant than every later
  tier.
- **Polytomy:** relatives sharing a tier share the relevant divergence event.
- **Target visibility:** the target is absent from transition stages and occurs
  exactly once as a normal selectable card in the ultimate stage.
- **Duplicates:** a species is not reused unless configuration explicitly
  permits it.
- **Continuity:** successive stages descend along one target-containing lineage,
  each tier index maps to exactly one backbone node, and every recorded stage
  boundary sits at its own tier's backbone position.
- **Cards:** every member carries a matching, complete player-facing card.
- **Reveal safety:** a guess never eliminates a possibly deeper relative.
- **Determinism:** identical versioned inputs reproduce the same game, including
  the per-stage member shuffle and the serialized bytes.

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
