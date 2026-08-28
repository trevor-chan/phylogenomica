# Roadmap

## Current scope assessment

The concept is well specified enough to begin engineering. The critical risk is
no longer missing game rules; it is whether a real, legally redistributable
OneZoom snapshot supports the assumed topology, metadata coverage, target
depth, terminal groups, and compact clone-and-play bundle.

The shortest path to a trustworthy prototype is therefore:

1. establish the actual source and schema;
2. reconstruct and audit topology;
3. validate `N=10`, `M=5` feasibility;
4. implement generation and rules without a UI;
5. build the visual prototype only after correctness tests pass.

## Phase 0 — Repository setup

Status: complete as an initial skeleton.

- `src/` package boundaries;
- focused documentation;
- tracked versus ignored data and asset policy;
- Python packaging, lint, and test configuration;
- stable ingestion and audit script locations.

The placeholder scripts intentionally contain no fabricated schema logic.

## Phase 1 — Source acquisition and ingestion

- Verify the official OneZoom dump mechanism, version, license, and attribution.
- Preserve one immutable raw snapshot and its checksum locally.
- Inspect node, leaf, topology, image, name, age, and external-ID fields.
- Implement versioned schema parsing and normalized output.
- Fail clearly on unknown source schema or mismatched input checksums.

Deliverable: a reproducible normalized local dataset plus source manifest.

## Phase 2 — Tree preprocessing

- Reconstruct biological topology using the verified `real_parent` equivalent.
- Detect roots, cycles, missing links, and unreachable records.
- Build child adjacency and ancestry operations.
- Collapse monotypic chains without losing optional display metadata.
- Preserve genuine polytomies.
- Compute descendant counts, collapsed depth, and sister-group pools.

Deliverable: a heavily unit-tested derived tree representation.

## Phase 3 — Dataset audit

- Produce structural and metadata coverage reports.
- Calculate lineage depth and candidate capacity distributions.
- Estimate the fraction of targets supporting `N=10`, `M=5`.
- Quantify valid terminal groups and candidate-quality coverage.
- Measure prospective runtime-bundle size and review redistribution terms.

Deliverable: a committed summary report and an explicit keep/change decision on
the initial game parameters.

## Phase 4 — Target lineage and eligibility

- Extract a collapsed root-to-target path.
- Enumerate off-target sister pools as ordered divergence tiers.
- Calculate target eligibility and quality with reason codes.
- Provide compact diagnostic output for arbitrary targets.

Deliverable: tested target-lineage extraction and an eligible-target index.

## Phase 5 — Representative selection

- Implement seeded sampling within sister groups.
- Prefer complete metadata, licensed images, vernacular names, and a documented
  popularity signal without requiring recognizability.
- Prevent target and game-level duplicates.
- Record selection reasons for debugging.

Deliverable: reproducible candidate pools for representative targets.

## Phase 6 — Stage and game generation

- Sample stages across the full usable evolutionary depth.
- Guarantee valid terminal tiers and stage continuity.
- Serialize complete immutable games with version and seed metadata.
- Apply all generated-game validators automatically.

Deliverable: deterministic sample games generated from the real dataset.

## Phase 7 — Gameplay engine

- Implement active candidates, tier-based guess processing, and reveals.
- Handle polytomy peers correctly.
- Track inferred versus revealed relationships and positive score.
- Complete stages and the full game without UI dependencies.

Deliverable: command-line or test-driven complete games with correct state
transitions.

## Phase 8 — Minimal playable prototype

- Show cards with image, common name, and scientific name.
- Make candidates selectable and explain transition feedback.
- Display score and an initially simple growing cladogram.
- Run clean-clone playback using only the tracked gameplay bundle.

Deliverable: a functional prototype for evaluating the core mechanic.

## Phase 9 — Tree visualization

- Distinguish resolved, unresolved, inferred, and revealed structure.
- Render polytomies without false ordering.
- Preserve readable history while zooming toward later stages.
- Add optional divergence-age labels.

Deliverable: a continuous cladogram that functions as board, feedback, and
history.

## Phase 10 — Metadata enrichment

- Add ID-based Wikidata/Wikipedia fallbacks only where useful.
- Improve image choice and attribution presentation.
- Improve vernacular-name handling.
- Optionally add post-game descriptions.

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

Decide whether `N=10`, `M=5`, multiple terminal representatives, and the
planned candidate-quality preferences work for a sufficiently broad target set.

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

Obtain and document one official OneZoom snapshot, inspect its schema, and
replace the placeholder ingestion command with the smallest parser that can
normalize identifiers, parents, names, and metadata for a structural audit.
