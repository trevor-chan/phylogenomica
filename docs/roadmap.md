# Roadmap

## Current scope assessment

The concept is well specified enough to begin engineering. The critical risk is
no longer missing game rules; it is whether a real, legally redistributable
OneZoom snapshot supports the assumed topology, metadata coverage, target
depth, ordered stage roles, and compact clone-and-play bundle.

The shortest path to a trustworthy prototype is therefore:

1. establish the actual source and schema;
2. reconstruct and audit topology;
3. validate `N=10`, `M=5` feasibility;
4. implement generation and rules without a UI;
5. build the visual prototype only after correctness tests pass.

## Implementation snapshot — 2026-08-30

Completed engineering foundations:

- repository, packaging, data lifecycle, and correctness documentation;
- versioned acquisition of the current static tree and a matched historical
  Docker database for development;
- allowlisted, checksum-verified raw extraction;
- streaming normalized SQLite ingestion with an atomic manifest;
- corrected reconstruction of OneZoom biological topology from `real_parent`;
- root, reference, cycle, reachability, degree, polytomy, and SQLite validation;
- direct and collapsed parent/depth/descendant indexes;
- read-only children, ancestry, LCA, descendant, and ordered sister-group
  queries, plus a target-lineage diagnostic command;
- an exact batch feasibility audit over every historical leaf, including
  topology distributions, explicit failure categories, and metadata coverage;
  and
- a deterministic, manifest-backed target-eligibility index with a read-only
  query interface;
- a validated, deterministic seeded relative selector that varies tier layouts
  and species representatives per target; and
- batch player-facing card resolution, validated immutable game assembly, and
  self-validating game deserialization.

The historical development snapshot has 2,235,076 leaves, 201,578 biological
internal nodes, 104,142 bifurcations, and 97,436 genuine polytomies. It has no
monotypic biological nodes, although the generic collapse behavior is tested.
The full normalized and derived databases remain ignored, reproducible
intermediates rather than the eventual gameplay bundle.

Feasibility audit version 4 uses the playable-lineage definition. A default
lineage contains 50 species: 49 unique relatives and one target. Each of the
first four stages contains eight decoys, one deeper mulligan, and one deepest
unlock. The ultimate stage contains eight decoys, one deepest selected-relative
mulligan, and the visible selectable target. Tiers and excess species may be
skipped, and no stage must end at a literal closest-sister event.

Of 44,361 species with a preferred English name and complete best-image record,
43,032 (97.0041%) support this full ordered stage shape. Every target has 44,360
total rich-card relatives, so none fail raw capacity. The remaining 1,329 fail
only because their relative-bearing tiers cannot supply the requested ordered
transition stages plus the ultimate decoy/mulligan structure. The initial
`M=5`, `N=10` shape is therefore retained for generator testing.

Earlier audit versions remain in the snapshot audit as evidence for removing
closest-sister terminal requirements and refining the stage roles. They are not
current target eligibility counts.

Eligibility index version 1 now stores all 44,361 metadata-valid potential
targets and their per-target metrics. It marks 43,032 eligible and assigns
`insufficient_ordered_stage_structure` to the remaining 1,329. The deterministic
SQLite artifact is 3,055,616 bytes; the manifest pins the source checksums,
policy, versions, reason definitions, and validation counts. Metadata-invalid
source leaves are represented by aggregate manifest evidence rather than
generator-facing rows.

Game generator version 1 assembles those selections into immutable games. A
60-target random sample of the eligible set generated 60 valid `M=5`, `N=10`
games of 50 unique species at roughly 313 ms each, and repeated runs reproduced
identical JSON bytes. Card resolution applies the same rich-card predicates as
the feasibility policy, so a presentation gap fails generation loudly rather
than reaching a player. Serialized games round-trip exactly and revalidate on
load without their selection, which is the interface the gameplay engine will
consume.

Gameplay engine version 1 implements guess, reveal, scoring, and stage
transitions over a loaded game. Its scoring is reveal-weighted: a stage is
worth `N`, the stage-ending card is free, and any other card costs itself plus
every still-active shallower relative it exposes. Against 25 random real
targets, perfect play and the `mulligan → stage-ending` route both score 50,
random play always terminates with all 50 species placed, and player state
survives a serialization round trip.

A local browser prototype now plays a generated game end to end. The page is a
renderer: every guess is resolved by the engine over a small JSON API, which is
stage-scoped so a card's tier and role reach the browser only once it has been
placed. Playing the historical snapshot surfaced one material data finding —
the stored `media.eol.org` image URLs now answer with a Cloudflare
interstitial, so cards render without images.

The cladogram is now the interface: it grows left to right from the root toward
the target with the open stage's cards in a row beneath it, and encodes
resolved/unresolved, inferred/revealed, and polytomy structure on independent
visual channels. Completed stages compact into history bands while the current
stage receives most of the viewport. Game schema version 3 carries both
`age_ma` and optional `clade_name` on every tier; the generator version moved
to 3 with it, so earlier games are refused on load and must be regenerated.

The next implementation sequence is:

1. resolve and cache licensed media so cards can show images again; and
2. compact a reviewed, licensed gameplay-ready subset before frontend work.

A current production dump and explicit derived-data redistribution terms remain
parallel release gates; development continues against the matched historical
snapshot without treating it as release data.

## Phase 0 — Repository setup

Status: complete as an initial skeleton.

- `src/` package boundaries;
- focused documentation;
- tracked versus ignored data and asset policy;
- Python packaging, lint, and test configuration;
- stable ingestion and audit script locations.

The placeholder scripts intentionally contain no fabricated schema logic.

## Phase 1 — Source acquisition and ingestion

Status: in progress. Static topology acquisition and its initial structural
audit are complete for tree version `29194525`. A matched historical database
and topology `27400288` have been safely extracted from the official 2022
Docker image for development; the current production SQL dump has not yet been
obtained.

- ~~Verify the official static-data mechanism and license references.~~
- ~~Preserve one immutable static snapshot and its checksums locally.~~
- ~~Inspect the current source definitions for relevant database tables.~~
- ~~Extract an allowlisted historical database snapshot from the official
  Docker image.~~
- ~~Verify its database/static-tree version match and source schema.~~
- Request the current public production SQL dump and its specific reuse terms.
- ~~Inspect actual node, leaf, image, name, age, and external-ID values.~~
- ~~Implement versioned schema parsing and normalized output.~~
- ~~Fail clearly on unknown source schema or mismatched input checksums.~~

Deliverable: a reproducible normalized local dataset plus source manifest.

## Phase 2 — Tree preprocessing

Status: complete for the first derived-tree schema and query interface.

- ~~Reconstruct biological topology using the verified `real_parent`
  equivalent.~~
- ~~Detect roots, cycles, missing links, and unreachable records.~~
- ~~Build reusable child-adjacency and ancestry operations.~~
- ~~Collapse monotypic chains without losing source metadata.~~
- ~~Preserve genuine polytomies.~~
- ~~Compute descendant counts and collapsed depth.~~
- ~~Compute sister-group pools.~~

Deliverable: a heavily unit-tested derived tree representation.

## Phase 3 — Dataset audit

Status: in progress. Historical structural, metadata, and batch target
feasibility results are recorded. Runtime-bundle sizing and release-source
review remain.

- ~~Implement the topology-only structural audit available from static data.~~
- ~~Produce structural and metadata coverage reports for the historical
  development snapshot.~~
- ~~Calculate lineage depth and candidate capacity distributions.~~
- ~~Estimate the fraction of targets supporting `N=10`, `M=5`.~~
- ~~Quantify valid terminal groups and candidate-quality coverage.~~
- ~~Recompute target feasibility using only image-and-English-name candidate
  representatives.~~
- ~~Replace closest-sister eligibility with the playable-lineage role model.~~
- Measure prospective runtime-bundle size and review redistribution terms.

Deliverable: a committed summary report and an explicit keep/change decision on
the initial game parameters.

## Phase 4 — Target lineage and eligibility

Status: complete for the historical development snapshot. A future production
snapshot will rebuild the same versioned artifact.

- ~~Extract a collapsed root-to-target path.~~
- ~~Enumerate off-target sister pools as ordered divergence tiers.~~
- ~~Calculate target eligibility and topology evidence with reason codes.~~
- ~~Provide diagnostic output for arbitrary targets.~~

Deliverable: tested target-lineage extraction and an eligible-target index.

## Phase 5 — Representative selection

Status: complete for the initial selector.

- ~~Implement seeded sampling across ordered relative-bearing tiers.~~
- ~~Prefer complete metadata, licensed images, vernacular names, and a
  documented popularity signal without requiring recognizability.~~
- ~~Prevent target and game-level duplicates.~~
- ~~Record selected tiers, roles, versions, configuration, target, and seed for
  debugging and replay.~~

Deliverable: reproducible relative pools for representative targets.

## Phase 6 — Stage and game generation

Status: complete for the first game schema and generator.

- ~~Sample stages across the full usable evolutionary depth.~~
- ~~Guarantee decoy/unlock role separation and stage continuity.~~
- ~~Resolve one complete player-facing card per selected species.~~
- ~~Serialize complete immutable games with version and seed metadata.~~
- ~~Read serialized games back, revalidating them without their selection.~~
- ~~Apply all generated-game validators automatically.~~

Deliverable: deterministic sample games generated from the real dataset.

## Phase 7 — Gameplay engine

Status: complete for the first engine and the reveal-weighted scoring model.

- ~~Implement active relatives, role-based guess processing, and reveals.~~
- ~~Handle polytomy peers correctly.~~
- ~~Track inferred versus revealed relationships and positive score.~~
- ~~Complete stages and the full game without UI dependencies.~~

Deliverable: command-line or test-driven complete games with correct state
transitions.

## Phase 8 — Minimal playable prototype

Status: playable. Clean-clone playback is blocked on the gameplay bundle.

- ~~Show cards with common and scientific name.~~ Images are shown when they
  load, but the snapshot's stored URLs are no longer retrievable; see
  [data sources](data_sources.md). Cards fall back to a placeholder glyph.
- ~~Make relatives selectable and explain transition feedback.~~
- ~~Display score and an initially simple growing cladogram.~~
- Run clean-clone playback using only the tracked gameplay bundle. Blocked:
  the bundle does not exist yet, so the prototype still reads the ignored
  processed dataset or a serialized game.

Deliverable: a functional prototype for evaluating the core mechanic.

## Phase 9 — Tree visualization

Status: complete.

- ~~Distinguish resolved, unresolved, inferred, and revealed structure.~~ A
  solid trunk is resolved and a dashed one is the concealed continuation;
  filled tips are inferred by the player and hollow tips were revealed.
- ~~Render polytomies without false ordering.~~ Same-tier relatives branch from
  a single trunk node as a rake, so no order is implied among them.
- ~~Preserve readable history while zooming toward later stages.~~ The tree
  grows left to right within the board viewport. Completed stages compact into
  narrow, tooltip-labelled history bands while the current stage receives the
  remaining space and readable leaf labels.
- ~~Add optional divergence-age labels.~~ Game schema version 2 carries
  `age_ma` on every tier, so a serialized game is self-contained. Ages are
  sparse (about 46% of tiers) and the label is omitted where none exists.
- ~~Replace internal tier indexes with optional clade names.~~ Game schema
  version 3 carries the normalized node scientific name as `clade_name`; the
  renderer omits it when OneZoom has no name for that branching point.

Deliverable: a continuous cladogram that functions as board, feedback, and
history.

## Phase 10 — Metadata enrichment

Status: in progress. Source-ID coverage for the historical rich-card universe
is now measured: 44,185 of 44,361 species have a Wikidata ID. Resolver version
2, its fixture-based cache/failure audit, a validating working-asset downloader,
and an incremental dataset-level media library are implemented. The initial
pilot operates on one generated game's 50 unique species before any
dataset-wide media request.

- Add ID-based Wikidata/Wikipedia fallbacks only where useful.
- Improve image choice and attribution presentation.
- Improve vernacular-name handling.
- Optionally add post-game descriptions.

The first implementation increment is a reproducible, resumable metadata-only
resolver:

1. ~~Batch Wikidata entity lookups by Q-ID and record ranked `P18` Commons
   filenames.~~
2. ~~Query Commons `imageinfo` for the canonical file page, content URL, media
   type, dimensions, creator/credit, and explicit license fields.~~
3. ~~Write raw responses and normalized resolved/unresolved records to ignored
   cache storage with retrieval timestamps and checksums.~~
4. ~~Run the live 50-species metadata pilot and audit its status, dimensions,
   formats, and license fields.~~ The pilot found 43 fully resolved records,
   three incomplete-attribution records, and four records without a Wikidata
   `P18` path.
5. ~~Fix URL normalization, rebuild from cached evidence, and validate a small
   live download into ignored working assets.~~ All 46 candidate URL pairs were
   restored from six cache hits. Three resolved JPEGs passed content-type,
   signature, dimension, size, and checksum validation and were visually
   confirmed as plausible images of their requested taxa.
6. ~~Download the fully attributed pilot set and create an explicit visual
   review surface.~~ All 43 resolved candidates passed machine validation with
   no duplicate checksums. A local static page verifies the files again, saves
   accept/conditional/reject/alternate decisions and notes in the browser, and
   exports review JSON pinned to the download and rights manifests.
7. ~~Implement an explicit liberal rights policy for working and promoted
   assets.~~ Policy version 1 permits all recognized claims in ignored prototype
   storage, classifies 33 pilot records as promotion-ready and 10 as
   conditional, normalizes canonical rights URLs and identifiers, generates
   attribution text, and makes the review page default to the policy decision.
8. ~~Build an incremental species-keyed working library and connect it to the
   prototype.~~ The builder imports prior downloads or consumes new resolver
   manifests, reuses unchanged validated assets across games, retrieves only
   missing or changed recognized-rights records, and merges atomically. The
   prototype serves local images and attribution without runtime network
   access. The pilot seeded 43 records, and a repeat import reused all 43.

Missing Wikidata IDs, missing `P18`, missing Commons pages, unsupported media,
and incomplete attribution must remain distinct unresolved outcomes. The
resolver must not silently fall back to a name search.

The human/seed-42 pilot contains 48 Wikidata-linked species and two explicit
missing-ID records (`Brucella abortus NCTC 8038` and `Escherichia coli
99.1753`). The completed live run is documented in the
[pilot audit](audits/wikimedia_human_seed_42.md). Wikimedia returned media for
46 species, but three lack creator/credit. Resolver version 2 preserves the
tracking query strings and reports usable original and thumbnail URLs for all
46.

Deliverable: richer cards without changing topology or answer semantics.

## Phase 11 — Gameplay testing

Measure stage duration, guesses, perceived difficulty, card count, total game
length, late-stage obscurity, image effects, polytomy comprehension, reveal
clarity, scoring clarity, and navigation of the continuous tree. Adjust `N`,
`M`, eligibility, and sampling based on evidence.

## Decision gates

### After source inspection

Confirm that the topology can be reconstructed, required fields are stable
enough to ingest, and source/asset terms permit the intended use.

### After audit

Decide whether `N=10`, `M=5`, one mulligan in every stage, one unlock in each
transition stage, and the planned relative-quality preferences work for a
sufficiently broad target set.

### Before committing gameplay data

Confirm validation, compactness, licensing, manifest completeness,
reproducibility, and clean-clone functionality.

### Before frontend investment

Demonstrate deterministic generated games and fully tested guess/reveal/scoring
logic through a nonvisual interface.

## Deferred work

Do not expand the initial implementation into alternate dating/taxonomy
integration, extinct taxa, biological clue modes, target-name guessing,
multiplayer, user-authored trees, or alternate phylogenetic hypotheses until
the core loop has passed data and gameplay validation.

## Immediate next action

Capture exact public-domain tags and source evidence for the seven conditional
PD records, package the GFDL notice, and preserve the two nonstandard permission
statements. Then build a promotion step that verifies the download, rights, and
exported visual-review manifests before creating tracked runtime assets and an
attribution index. In parallel, generate additional seeded games and feed their
resolver manifests through the incremental library to measure reuse, coverage,
and growth. Preserve explicit unresolved records and evaluate EOL/GBIF IDs as
secondary paths rather than silently using fuzzy name matching. Finally compact
a reviewed, licensed subset under `assets/gameplay/` and `data/gameplay/`, so a
clean clone can play with images without ignored intermediates. Keep the current
production dump request open as the release-data upgrade path.
