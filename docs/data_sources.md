# Data sources and lifecycle

## Status

OneZoom is the operational source for the initial game database. On 2026-08-28,
the project acquired and audited the live viewer's versioned static tree
snapshot `29194525`. It contains the complete compact topology, viewer cut map,
and sparse divergence dates. See the
[snapshot audit](audits/onezoom_29194525.md) for checksums and findings.

On 2026-08-30, the project also extracted a matched historical database and
static tree `27400288` from OneZoom's official Docker image, created on
2022-02-07. The network-disabled, allowlist-based extraction contains the six
relevant tables but omits IUCN, sponsorship, reservations, authentication, and
other unrelated data. See the
[Docker snapshot audit](audits/onezoom_docker_27400288.md).

The current static files do not contain the complete taxon metadata required by
the game, while the matched Docker database is historical. It is sufficient to
unblock ingestion and feasibility work but is not the intended release source.
OneZoom's current developer guidance directs downstream projects to request a
public production SQL dump. Obtaining that dump and confirming its license and
attribution terms remain the release-data gate.

The project should not commit an upstream database merely because it is
downloadable. Redistribution of any derived gameplay bundle must be reviewed
against the source terms first.

## Planned source roles

### OneZoom

OneZoom is expected to provide a largely integrated working snapshot with:

- node and leaf identifiers;
- display-parent and biological `real_parent` relationships;
- scientific and vernacular names;
- OTT identifiers and a leaf popularity rank (the historical snapshot does not
  contain taxonomic rank);
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

Resolver version 2 implements a metadata-only pilot for one generated game. It
batches OneZoom's numeric Wikidata IDs as Q-IDs through `wbgetentities`, prefers
non-deprecated `P18` statements by Wikidata rank, and queries the selected
Commons files through `imageinfo`. Raw response envelopes record the canonical
request URL, retrieval time, and checksum under `data/cache/wikimedia/`.
Normalized records convert the Commons `extmetadata` HTML to plain text for
safe later display while preserving URL query strings and the raw response as
evidence.

The resolver reports distinct statuses for missing or invalid Wikidata IDs,
missing entities, absent `P18`, absent Commons pages or image information,
non-image media, and incomplete creator/license fields. It performs no fuzzy
name lookup and downloads no media. A result marked `resolved` still requires
manual review because Commons itself advises reusers to verify each file's
copyright status and license requirements.

The live human/seed-42 pilot completed on 2026-09-01. It resolved complete
metadata for 43 of 50 species, found three additional image candidates with
missing creator/credit, and left four species without a Wikidata `P18` path.

#### Species descriptions

Description resolver version 1 reuses the same Wikidata bridge for text. It
batches Q-IDs through `wbgetentities` with `props=sitelinks` and
`sitefilter=enwiki`, then requests each article's lead section from the English
Wikipedia `query` API with `exintro`, `explaintext`, and `inprop=url`. It
follows title normalization and redirects, records the page and revision IDs
alongside the canonical article URL, and truncates a long lead on a sentence
boundary. Raw response envelopes are stored under `data/cache/wikipedia/` with
the same request URL, retrieval time, and checksum evidence the media resolver
uses.

It reports distinct statuses for missing or invalid Wikidata IDs, missing
entities, an entity with no English sitelink, a linked article that does not
exist, and an article with no usable extract. It performs no fuzzy name lookup
and never substitutes a genus or family article for an absent species article:
a description of a different taxon would teach the wrong relationship.

Article prose is licensed CC BY-SA 4.0 rather than under the per-file Commons
terms, so every record carries its own title, article link, revision ID, and
license. See `media_rights.md` for the text-attribution rule.

#### Replication lag and `maxlag`

`maxlag` is a courtesy parameter the client sends: it asks the server to refuse
the request rather than serve it while the wiki is behind, so that automated
readers back off instead of adding load. A refusal is therefore self-imposed
and carries no penalty; the same request without the parameter is served
normally.

Wikidata is a special case. Its `maxlag` figure folds in Wikidata Query Service
lag — a SPARQL endpoint this project never reads — and that service routinely
runs minutes to hours behind. Live observations on 2026-09-01 and 2026-09-02
returned `"type": "wikibase-queryservice"` with `queryserviceLag` between 706
and 924 seconds, refusing every `maxlag=5` entity read for as long as it
persisted. No usable value avoids this, because any threshold low enough to
describe database health is far below the Query Service's normal lag.

Both resolvers therefore omit `maxlag` on Wikidata entity reads, which are
cached read-only lookups rather than edits, and keep it on Commons `imageinfo`
and Wikipedia `extracts` requests, where it measures ordinary replica lag and
is almost always satisfied. A lag refusal on those endpoints is a scheduling
signal, not a failure: one shared bounded backoff waits it out over four
attempts before giving up. Every other API error describes the request itself
and is raised immediately.

Note that this parameter is part of the cached request URL. Removing it from
the Wikidata reads changed their cache keys, so the first run after the change
re-fetches entity data once; Commons and Wikipedia responses are unaffected.

#### Coverage

Measured over 150 species in three randomly targeted games on 2026-09-02:
85.3% of species carry an image and 82.7% carry a description. The remaining
loss is entirely at resolution — species with no Wikidata image, no Wikidata
ID, no English article, or no creator/credit on Commons — and every record the
resolvers do produce now reaches the game. OneZoom's own `images` table covers
100% of the species the Wikidata bridge cannot reach and is the documented
route to higher image coverage. See the
[enrichment coverage audit](audits/enrichment_coverage_2026_09_02.md).

The live human/seed-42 description pilot completed on 2026-09-02. It resolved
42 of 50 species. Two species carry no Wikidata ID at all, and six have a
Wikidata item with no English Wikipedia sitelink. Coverage is a presentation
property: an unresolved description leaves the card, the tree, and the endgame
summary intact.
The initial run exposed a URL-normalization defect caused by Wikimedia's
ampersand-delimited tracking parameters. Resolver version 2 fixes it, and a
cache-only rebuild restored all 46 original and thumbnail URLs without new API
traffic. A three-file live download validated the working-asset pipeline, after
which all 43 fully attributed candidates were downloaded and machine-validated.
The maintainer then reviewed the full candidate page favorably. Every file
remains ignored and unapproved pending an exported per-file review. See the
[pilot audit](audits/wikimedia_human_seed_42.md).

Library builder version 1 converts these per-game working assets into an
incremental dataset-level library under
`assets/processed/wikimedia-library/<dataset>/`. It can import an existing
download manifest without network access or consume a resolver manifest and
download only species that are absent or whose normalized Wikimedia source
fingerprint changed. Every reused file is revalidated against byte count,
SHA-256, signature, media type, and dimensions. The current pilot library has
43 records; rerunning the import reused all 43 and transferred no bytes.
Libraries remain ignored working data and are not redistribution approval.

The prototype can invoke the same pipeline with
`--download-missing-images`. This is an explicit network opt-in: it resolves
only game species absent from the loaded library, writes subset manifests with
distinct names so it cannot overwrite a full-game audit, and uses one
background worker to serialize library updates. Current-stage files are
published before later-stage files. The browser receives progress counts, not
future-stage species identities. Runtime guesses never depend on enrichment
success.

Rights policy version 1 classifies media separately from visual review. The
current noncommercial intent does not waive license requirements. Every
recognized record is permitted in ignored local working assets, while promotion
uses `ready`, `conditional`, and `blocked` states. Standard CC BY, CC BY-SA, and
CC0 records can be ready; public-domain short labels, GFDL, copyrighted-free-use,
and no-known-restrictions records remain conditional until their specific
evidence or packaging requirements are met. See the
[media-rights policy](media_rights.md).

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
- normalized rights identifier and canonical license or rights-statement URL;
- retrieval date;
- transformation or derivative information.

Licensing is a release requirement. Image availability can influence candidate
quality but does not alter topology.

#### Stored image URLs are no longer directly retrievable

The historical snapshot's `overall_best_any` records point at
`media.eol.org` content URLs. As of 2026-08-30 those hosts redirect to HTTPS
and answer with a Cloudflare bot interstitial rather than image bytes:

```
$ curl -sIL http://media.eol.org/content/2015/11/17/10/64712_orig.jpg
403  text/html  "Just a moment..."
```

The 403 is not user-agent or referer specific, and an `<img>` subresource
cannot satisfy a JavaScript challenge, so these URLs do not render in a
browser. Image *metadata* coverage is therefore still complete and the
rich-card eligibility policy remains valid; only retrieval is blocked. The
prototype degrades to a placeholder glyph per card.

This is a media-retrieval problem, not a topology or licensing one. The
documented fix is the Phase 10 fallback: 44,185 of the 44,361 current rich-card
species (99.6033%) carry a `wikidata_id`, so Wikimedia Commons can be the
primary source of replacement images with its own attribution and license
fields. Of the remaining 176, 132 carry an EOL ID, 71 carry a GBIF ID, and 44
carry neither; those records need a documented secondary path or exclusion
rather than fuzzy matching hidden inside the resolver. A release bundle would
need to resolve and cache media under `assets/gameplay/` regardless, since
hotlinking a third-party media host is not an acceptable runtime dependency.

#### Divergence ages are sparse but internally consistent

`nodes.age_ma` is populated for 15,562 of the snapshot's internal nodes, which
covers roughly 46% of the tiers a generated game uses. Coverage concentrates on
deep, well-studied backbone nodes, so early stages usually carry ages and later
ones often do not.

Age monotonicity was checked exhaustively rather than sampled. Across all
15,561 parent/child pairs where both nodes carry an age, no parent is younger
than its child, and 1,867 pairs are exactly equal:

```sql
SELECT COUNT(*), SUM(CASE WHEN p.age_ma < c.age_ma THEN 1 ELSE 0 END)
FROM nodes c JOIN nodes p ON c.biological_parent_id = p.node_id
WHERE c.age_ma IS NOT NULL AND p.age_ma IS NOT NULL;
-- 15561, 0
```

Generation therefore enforces non-increasing ages along a game's tiers, with
ties allowed. Ages are display metadata and never determine correctness.

#### Internal clade names are optional

OneZoom's normalized `nodes.scientific_name` supplies the player-facing clade
label for a branching point. In snapshot `27400288`, 135,439 of 2,235,075
internal nodes have a nonblank name. The generator therefore records
`clade_name` as optional display metadata and the prototype simply omits the
name where the source node is unnamed; it never substitutes the internal tier
index.

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
4. ~~Acquire and filter the historical public Docker database for development.~~
5. ~~Validate its schema and value conventions.~~
6. ~~Implement normalized ingestion and reconcile database IDs with its matched
   static topology.~~
7. ~~Run metadata and target-viability audits.~~
8. ~~Revise initial `N=10`, `M=5` assumptions from the audit evidence.~~
9. Upgrade to a current production dump when OneZoom provides it.

The implemented ingestion command verifies the raw manifest and every input
checksum before streaming the six table projections into a versioned SQLite
database:

```bash
conda activate phylogenomica
phylogenomica-ingest-onezoom \
  data/raw/onezoom/docker-2022-02-07
```

The default output is `data/processed/onezoom/27400288/`. It remains ignored
and rebuildable. Ingestion preserves display and signed source parents,
normalizes the biological parent from `abs(real_parent)`, validates all parent
references, and reconciles node and leaf counts against the matched static
topology. Biological chain collapse is deliberately deferred to tree
preprocessing.

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

Measure usable lineage depth, relative capacity, ordered decoy/unlock stage
structure, and the fraction of leaves able to support the initial `N=10`,
`M=5` playable lineage.

Feasibility audit version 4 has completed this measurement for every rich-card
leaf in the historical development snapshot. Of 44,361 leaves with a preferred
English name and complete licensed best-image record, 43,032 support a lineage
of 49 unique relatives and one target. No target fails total relative capacity;
1,329 fail only the ordered transition-stage and ultimate-stage role shape. See
the [Docker snapshot audit](audits/onezoom_docker_27400288.md) for the full
interpretation and the superseded conservative results.

Eligibility index version 1 persists the 44,361 metadata-valid candidates and
their per-target topology evidence under ignored processed storage. It contains
43,032 eligible targets, records the 1,329 topology failures by reason code, and
is 3,055,616 bytes. Its manifest preserves the configuration and checksums for
both source databases; it is rebuildable evidence, not yet a reviewed gameplay
bundle.

The audit is the decision point for changing lineage-member count, stage count,
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
