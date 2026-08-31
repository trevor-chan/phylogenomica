# Repository guidance

## Project objective

Build a reproducible phylogeny game in which the player reconstructs the path
to an initially concealed target species. Phylogenetic correctness takes precedence over
presentation convenience.

## Read before changing behavior

- `docs/game_design.md` contains gameplay rules and invariants.
- `docs/data_sources.md` contains source, licensing, and data-lifecycle policy.
- `docs/architecture.md` contains model and module boundaries.
- `docs/roadmap.md` contains sequencing and unresolved decisions.

When implementation evidence conflicts with a planning assumption, preserve
the evidence in an audit result and update the relevant document rather than
silently encoding a new rule.

## Package boundaries

- `phylogenomica.data`: source ingestion, normalization, validation, and audit.
- `phylogenomica.tree`: topology reconstruction and tree algorithms.
- `phylogenomica.generation`: target eligibility and deterministic game/stage
  generation.
- `phylogenomica.gameplay`: UI-independent guess, reveal, score, and game state.
- `phylogenomica.prototype`: a local browser prototype over the engine. It is a
  presentation layer only; it must never decide correctness.

Keep scripts thin: reusable logic belongs under `src/phylogenomica/`. Keep the
game engine independent of any future frontend.

## Data policy

- Never edit files in `data/raw/`; replace them only with a newly documented
  upstream snapshot.
- Do not commit raw dumps, rebuildable intermediates, caches, or unreviewed
  downloaded media.
- Commit only compact gameplay-ready data under `data/gameplay/` and curated
  runtime media under `assets/gameplay/`.
- Every committed data or media artifact must have provenance, source-version,
  license, attribution, checksum, and build information in its manifest.
- A clean clone must eventually run with the committed gameplay bundle, while
  maintainers can reproduce that bundle from tracked scripts plus documented
  upstream inputs.

## Correctness requirements

- Reconstruct biological topology from OneZoom's `real_parent`, not artificial
  display bifurcations.
- Collapse monotypic chains and preserve genuine polytomies.
- Assign every selected relative to its correct target-backbone tier.
- In every transition stage, place one mulligan on a distinct tier deeper than
  every decoy and one unlock on a distinct tier deeper than the mulligan; never
  mix relative roles within a selected tier.
- In the ultimate stage, place one mulligan deeper than every decoy and include
  the target as a normal selectable card; clicking it ends the game.
- Do not require any stage to end at the literal closest-sister event.
- Never reveal or eliminate a relative that could be deeper than the guess.
- Exclude the target from transition-stage cards and include it exactly once in
  the ultimate stage.
- Make generation deterministic for a dataset version, generator version,
  target, configuration, and seed.

Add tests for topology, ordering, polytomies, role separation, mulligan scoring,
target visibility, duplicates, continuity, reveal behavior, and determinism as
those components are built.

## Local checks

Run before handing off Python changes:

```bash
ruff check .
pytest
```
