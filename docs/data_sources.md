# Data sources and lifecycle

## Status

OneZoom is the operational source for the initial game database. On 2026-08-28,
the project acquired and audited the live viewer's versioned static tree
snapshot `29194525`. It contains the complete compact topology, viewer cut map,
and sparse divergence dates. See the
[snapshot audit](audits/onezoom_29194525.md) for checksums and findings.

The static files do not contain the complete taxon metadata required by the
game. OneZoom's current developer guidance directs downstream projects to
request a public production SQL dump. Obtaining that dump and confirming its
license and attribution terms are the remaining source-acquisition gate.

The project should not commit an upstream database merely because it is
downloadable. Redistribution of any derived gameplay bundle must be reviewed
against the source terms first.

## Planned source roles

### OneZoom

OneZoom is expected to provide a largely integrated working snapshot with:

- node and leaf identifiers;
- display-parent and biological `real_parent` relationships;
- scientific and vernacular names;
- taxonomic rank and OTT identifiers;
- Wikidata, EOL, GBIF, NCBI, WoRMS, IRMNG, or IPNI cross-references where
  available;
- representative image metadata and licensing information;
- divergence-age estimates on some internal nodes;
- popularity or related signals useful for representative selection.

The current source schema confirms `ordered_leaves`, `ordered_nodes`,
`vernacular_by_ott`, `vernacular_by_name`, `images_by_ott`, and
`images_by_name` as the relevant tables. The SQL dump remains authoritative for
the exact exported columns and values, so ingestion must still validate its
schema before loading data.

OneZoom may contain display-oriented artificial bifurcations. Gameplay topology
must be reconstructed from the biological parent represented by `real_parent`
or its verified equivalent, preserving the underlying polytomies.

### Open Tree of Life

Open Tree of Life remains the principal external phylogenetic reference, while
the OneZoom snapshot is the internally consistent operational dataset. Preserve
OTT IDs wherever available for provenance, validation, investigation, and
future migration.

Do not dynamically mix a current OpenTree topology into a game generated from
a different OneZoom snapshot. Every game must have one explicit topology
version.

### Wikidata, Wikipedia, and Wikimedia Commons

Wikidata is the preferred optional enrichment bridge when OneZoom supplies a
Wikidata identifier. Stable IDs are preferred to fuzzy name matching. Possible
fallback metadata includes Wikipedia links, Commons media, vernacular names,
and short post-game descriptions.

Enrichment follows the working topology; it does not change game correctness.
It comes after ingestion, preprocessing, generation, and engine validation.

### Divergence times

OneZoom node ages are sufficient for the initial version when present. Missing
ages remain missing. Numerical ages are display metadata, never a correctness
criterion; topology and recency of common ancestry define every answer.

External dating sources such as TimeTree and DateLife are deferred unless the
audit or gameplay testing demonstrates a concrete need.

### Images

Prefer representative-image metadata already associated with the OneZoom
taxon. Wikimedia may be a later fallback. Every image record or bundled asset
must preserve:

- taxon and source identifier;
- original source URL;
- creator and required attribution;
- license name and license URL;
- retrieval date;
- transformation or derivative information.

Licensing is a release requirement. Image availability can influence candidate
quality but does not alter topology.

### Vernacular names

The initial preference order is OneZoom, then ID-based Wikidata/Wikipedia
enrichment, then scientific name alone. Missing common names do not make a
species topologically unusable.

## Local data lifecycle

```text
documented upstream snapshot
          │
          ▼
data/raw/                 ignored, immutable
          │ ingest
          ▼
data/processed/           ignored, rebuildable
          │ validate + compact + license review
          ▼
data/gameplay/            tracked, runtime-ready
```

`data/cache/` holds disposable downloads or computed caches and is never part
of the pipeline's source of truth.

Assets follow the equivalent flow from ignored `assets/raw/` and
`assets/processed/` into tracked `assets/gameplay/`.

### Raw snapshots

Each locally downloaded snapshot must record:

- upstream project and canonical URL;
- snapshot or release version;
- retrieval date;
- content checksum and byte size;
- license and attribution requirements;
- expected immutable filenames.

Raw inputs are never edited in place. A changed upstream snapshot is a new
input version.

### Processed intermediates

Normalized tables, indexes, full derived trees, and exploratory audit outputs
are rebuildable local artifacts. They remain untracked unless a small report is
deliberately promoted into documentation.

The build must fail clearly if its input checksum or schema is unexpected. A
data upgrade must not silently change old generated games.

### Tracked gameplay bundle

The eventual committed bundle should contain only the smallest representation
needed to generate or play supported games without further downloads. It may be
a compact database plus a small curated media set. The exact storage format is
an empirical architecture decision after ingestion and audit.

Before promotion, the bundle must:

1. pass structural and gameplay validation;
2. be acceptably small for normal Git clones;
3. contain no credentials, caches, or unnecessary source fields;
4. have redistribution and media licensing reviewed;
5. update `data/gameplay/manifest.json` with provenance, schema, source and
   generator versions, file checksums, and the exact reproduction command;
6. work in a clean-clone smoke test without the ignored directories.

If a viable bundle is too large for normal Git, reconsider the runtime subset
or distribution mechanism rather than committing raw data or silently relying
on local state.

## First ingestion and audit

The first data milestone is intentionally investigative:

1. ~~Identify the official static distribution and source documentation.~~
2. ~~Download and checksum one versioned topology snapshot in `data/raw/`.~~
3. ~~Implement a first structural audit that collapses display polytomies.~~
4. Request and obtain the public production SQL dump from OneZoom.
5. Validate the dump's schema and value conventions.
6. Implement normalized ingestion and reconcile SQL IDs with static topology.
7. Run metadata and target-viability audits.
8. Revise assumptions before implementing full generation.

### Production dump request

The dump request should go to `mail@onezoom.org` and include:

- that Phylogenomica is an open-source educational phylogeny game;
- a request for the current public production SQL dump without sponsorship,
  reservation, personal, or IUCN-restricted data;
- a request for the snapshot/version date and a supplied checksum;
- confirmation of the dump and derived-data license and required attribution;
- explicit clarification on whether a compact, processed, gameplay-only
  derivative may be redistributed in the public Git repository;
- a request for any current schema or import notes relevant to
  `ordered_nodes`, `ordered_leaves`, vernacular names, and image metadata.

Do not place a provided download URL, credential, or private correspondence in
Git. Store the dump itself under ignored `data/raw/onezoom/<version>/` and add
its non-sensitive provenance and checksum to the local raw manifest.

### Structural audit

Measure total leaves and internal nodes, root structure, monotypic-node count,
bifurcation count, polytomy count and size distribution, and maximum/median
collapsed lineage depth.

### Metadata audit

Measure leaf coverage for scientific and vernacular names, images, Wikidata,
OTT and other useful IDs. Measure internal-node coverage for names, ages, and
OTT IDs. Audit licensing completeness independently of mere image presence.

### Game-feasibility audit

Measure usable lineage depth, candidate capacity, terminal-group capacity, and
the fraction of leaves able to support the initial `N=10`, `M=5` design.

The audit is the decision point for changing candidate count, stage count,
target requirements, selection heuristics, or the contents of the committed
runtime subset.

## Open data questions

- What official snapshot format and update cadence are available?
- What are the redistribution terms for normalized and compact derivatives?
- Which field reliably restores pre-display biological parentage?
- How complete and consistently licensed is representative-image metadata?
- What fraction of leaves have useful names, cross-references, and images?
- What is the real distribution of polytomy sizes and collapsed depth?
- What compact format best balances repository size and runtime query needs?
