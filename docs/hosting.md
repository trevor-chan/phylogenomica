# Hosting plan

## Objective

Host the first public Phylogenomica prototype on a small application server
without weakening topology validation, leaking concealed answers, or making a
player wait for third-party metadata services. This plan reflects the local
historical OneZoom development snapshot measured on 2026-09-05. It is a sizing
baseline, not a commitment to one hosting provider.

The recommended first release is an **offline-built, read-only game catalog**.
Game generation, image resolution, description resolution, rights review, and
asset optimization happen before deployment. The hosted application serves
validated games and compact metadata; card images live in object storage or on
a static CDN.

## Current deployment blockers

The local prototype is intentionally not a production server:

1. `phylogenomica.prototype.server` uses `ThreadingHTTPServer` and one
   process-global `PrototypeSession`. Two visitors would play and mutate the
   same game.
2. Dynamic generation reads the normalized, biological-tree, and eligibility
   SQLite databases. Together they occupy 806.6 MiB uncompressed.
3. The optional image and description workers make outbound requests and write
   growing libraries below ignored working directories. Ephemeral or
   multi-instance application filesystems cannot safely own those writes.
4. No reviewed game catalog, compact runtime metadata index, or promoted media
   bundle exists yet.
5. The server lacks the production concerns a public endpoint needs: isolated
   sessions, request limits, security headers, structured logs, health checks,
   and a deployment-aware process model.

The stage-scoped API and UI-independent gameplay engine remain useful. Hosting
should wrap those boundaries rather than move correctness rules into the
browser.

## Measured storage baseline

All units below are binary MiB/GiB except the rounded `du` totals. Ignored local
artifacts are included because they reveal what must be removed from a deploy.

| Artifact | Measured size | Needed by recommended web runtime? |
|---|---:|---|
| All tracked files | 0.90 MiB | Yes |
| Normalized OneZoom SQLite | 695.1 MiB | No |
| Biological-tree SQLite | 108.5 MiB | No |
| Target-eligibility SQLite | 2.9 MiB | No |
| One generated game JSON | 58.6 KiB | Yes, until catalog compaction |
| Working image files, 1,750 species | 346.2 MiB | No; publish derivatives externally |
| Working image audit manifest | 6.1 MiB | No; publish a compact attribution index |
| Description library, 1,246 species | 1.5 MiB | Yes, compacted or compressed |
| Entire development checkout | about 2.2 GiB | No |

The image sample averages 202.6 KiB per species (median 166.6 KiB). At that
unchanged average, a 50-species game contains at most 9.9 MiB of unique images.
There will be some reuse across games, but the safe no-reuse projections are:

| Catalog size | Game JSON | Images at current average |
|---:|---:|---:|
| 25 games | about 1.4 MiB | about 247 MiB |
| 50 games | about 2.9 MiB | about 495 MiB |
| 100 games | about 5.7 MiB | about 989 MiB |

Extending the current image average to all 44,361 metadata-valid species would
take about 8.57 GiB before manifests. Extending the current full audit-manifest
record shape would add roughly 154 MiB. That is unnecessary for an initial
release and is a strong reason not to deploy a dataset-wide working library.

Descriptions are not the storage risk. Extending the current description
record shape to 44,361 species would be roughly 54 MiB, and a runtime schema
that omits build evidence would be smaller still.

As an indicative optimization experiment, the first 100 sorted JPEG paths in
the working library were resized with FFmpeg to fit within 512 by 512 pixels
and encoded as JPEG with `-q:v 5`. That reduced the sample from 16.0 MiB to 2.8
MiB (17.6% of the original). This is not yet a chosen production encoding or
visual-quality threshold; it demonstrates that card-sized derivatives deserve
a formal visual and size audit.

## Recommended release architecture

```text
release build (offline)
OneZoom snapshot -> validated games -> unique species inventory
                                      |-> descriptions + revision/licence data
                                      `-> images + rights review + derivatives
                                                        |
                                                        v
                                      versioned deployment manifest

hosted runtime
browser -> production web app -> read-only game/description catalog
   |              |
   |              `-> isolated player session
   `-----------------> object storage/CDN image derivative
```

### Release build

Add a deterministic bundle builder that:

1. selects and serializes a finite, reviewed catalog of games;
2. deduplicates cards, clade metadata, descriptions, and attribution across
   those games;
3. requires every referenced image and description to have a valid local audit
   record, while preserving explicit missing-media placeholders;
4. emits card-sized image derivatives with content-addressed names;
5. separates full provenance/reproduction evidence from the compact fields the
   runtime reads;
6. writes a manifest with dataset, generator, schema, gameplay-engine, rights,
   checksums, and build versions; and
7. validates that a clean directory can play every catalog game without the
   normalized databases or network access.

Full source responses, original downloads, and review evidence stay in the
reproducible build archive. Only the compact catalog, runtime attribution, and
optimized derivatives are release artifacts.

### Images

- Resolve and download images only in the offline release build. Never fetch
  Wikimedia, Wikipedia, EOL, or another upstream service during a player
  request.
- Publish one reviewed card-sized derivative per selected source image to
  object storage or a static CDN. Use immutable content hashes and long cache
  lifetimes.
- Keep creator, source page, license identifier, and license URL in a compact
  runtime attribution record. Keep resolver responses and rights rationale in
  the offline audit manifest.
- Choose dimensions, format, and quality only after comparing visual results.
  A 512-pixel maximum edge is a useful first test because the present card
  images do not display near their typical 960-pixel width.
- Preserve a placeholder path so an unresolved or unapproved image never
  blocks a valid game.

External object storage avoids consuming application-disk quotas and allows
the image collection to grow independently. If the selected host serves static
assets cheaply and its quota is sufficient, the same immutable directory can
initially be deployed there without changing the manifest format.

### Descriptions

- Resolve Wikipedia descriptions offline using stable Wikidata links and keep
  the article/revision IDs, canonical URL, and CC BY-SA attribution already
  recorded by the resolver.
- Store the compact text directly in the runtime catalog or a small read-only
  SQLite/JSON index. Do not create one filesystem object per description.
- Deduplicate descriptions by species and compress the catalog in transit.
- Treat missing descriptions as an ordinary presentation state, never as a
  generation or scoring failure.

### Application and sessions

Extract a production-facing adapter from the local `http.server` shell. It
should create a session per browser, retain the current stage-scoped payloads,
and call the existing gameplay engine for every transition. For one process, a
bounded in-memory session store plus a signed opaque cookie is sufficient for
an early test. Multiple processes or instances require a shared store such as
Redis, or a fully validated client-carried state format that reveals no answer.

The hosted process should be stateless with respect to enrichment and release
assets. Deployment files mount read-only; a restart may discard active games
but must not lose or corrupt the catalog.

## Storage choices

For an application-disk limit below 512 MiB, the current dynamic generator is
not viable. Use the pre-generated catalog and external images. The Python app,
100 uncompressed game JSON files, and descriptions can remain well below 50
MiB before ordinary runtime overhead, and catalog normalization should reduce
that further.

At a 1 GiB limit, the 806.6 MiB generator databases technically fit but leave
too little room for installation, temporary files, logs, and safe rollouts.
They also do not solve image growth. This is not a sound initial deployment.

If broad on-demand generation becomes a product requirement, build a pruned
runtime database that contains only eligible species, selected topology, and
card fields. Measure and version it independently; do not copy the current
normalized/build databases into production.

## Delivery milestones

1. **Bundle contract:** define the normalized game/card/description/attribution
   schema and a provider-neutral asset base URL.
2. **Bundle builder:** generate a small catalog, promote reviewed assets, emit
   optimized derivatives, and prove clean-directory playback.
3. **Multi-user web adapter:** replace the shared global session with isolated,
   bounded sessions behind a production server interface.
4. **Deployment hardening:** add health checks, structured logs, request/body
   limits, CSRF protection for state-changing endpoints, security headers, and
   graceful shutdown.
5. **Storage/quality audit:** test candidate image sizes and encodings, measure
   species reuse across a representative catalog, and set explicit app-disk,
   object-storage, and transfer budgets.
6. **Staging:** deploy the exact release bundle, run concurrent-session and
   cache tests, verify attribution, and confirm the app makes no upstream
   metadata calls.

The first hosted release is ready when two concurrent browsers cannot affect
one another, every game works from read-only release artifacts, no request
downloads or writes enrichment data, all media has visible attribution, and
the measured deployment remains comfortably within the selected provider's
limits.
