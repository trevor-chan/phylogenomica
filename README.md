# Phylogenomica

Phylogenomica is a phylogeny trivia and deduction game about reconstructing the
evolutionary path to an initially concealed species. In each stage, the player searches for
the closest selected relative. Every guess reveals part of one persistent
cladogram, and a game progressively narrows from broad branches of life toward
the target, which becomes a normal selectable card in the ultimate stage.

The pipeline, generator, gameplay engine, and a local browser prototype are
implemented and validated against a real OneZoom snapshot, so a maintainer with
the processed dataset can generate and play a game today. It is not yet
distributable: the compact, licensed gameplay bundle that would let a clean
clone play does not exist, and card images are unavailable pending the media
work described below.

## Design invariants

- Correctness is determined by topology, not biological trait clues.
- Complete knowledge of the represented tree guarantees a perfect score.
- All equally related members of a polytomy are treated equivalently.
- Non-branching (monotypic) chains do not create artificial gameplay depth.
- Incorrect guesses reveal valid structure instead of only marking an error.
- Every generated game is tied to a dataset version, generator version, and
  random seed.

## Repository map

| Path | Purpose | Git policy |
|---|---|---|
| `src/phylogenomica/` | Importable data, tree, generation, and gameplay code | Tracked |
| `tests/` | Automated correctness and regression tests | Tracked |
| `scripts/` | Thin entry points for ingestion and auditing | Tracked |
| `docs/` | Game design, data provenance, architecture, and roadmap | Tracked |
| `data/raw/` | Immutable upstream downloads | Ignored |
| `data/processed/` | Rebuildable intermediate datasets | Ignored |
| `data/cache/` | Disposable local caches | Ignored |
| `data/gameplay/` | Small, versioned, runtime-ready dataset | Tracked |
| `assets/raw/` | Original or working media | Ignored |
| `assets/gameplay/` | Curated, licensed runtime assets | Tracked |

The tracked gameplay dataset is the eventual clone-and-play artifact. Raw and
intermediate data are deliberately excluded from Git; their source, version,
license, checksums, and build procedure must remain reproducible from tracked
code and documentation.

## Documentation

- [Game design](docs/game_design.md) defines the rules and player experience.
- [Data sources](docs/data_sources.md) defines provenance, licensing, and the
  local data lifecycle.
- [Media rights](docs/media_rights.md) defines working-use, promotion,
  attribution, and evidence policy for downloaded assets.
- [Architecture](docs/architecture.md) defines the internal model and module
  boundaries.
- [Roadmap](docs/roadmap.md) records phased delivery and open questions.

## Development

Phylogenomica targets Python 3.11 or newer.

```bash
python -m pip install -e '.[dev]'
phylogenomica-download-onezoom
phylogenomica-audit --output data/processed/audits/onezoom.json
pytest
```

The downloader discovers and pins OneZoom's current static tree version, then
stores its topology, viewer index, divergence dates, checksums, and local source
manifest under ignored `data/raw/onezoom/`. Full taxon metadata requires a
separately requested public OneZoom SQL dump; see the data-source documentation.

For development against the historical database bundled in OneZoom's pinned
Docker image, start it without network access or published ports and override
its default IUCN-download command:

```bash
docker run -d --platform linux/amd64 --network none \
  --name phylogenomica-onezoom-2022 \
  phylogenomica/onezoom:2022-02-07 /sbin/my_init
python scripts/extract_onezoom_docker.py
docker stop phylogenomica-onezoom-2022
```

The extractor verifies the image and tree versions, exports only reviewed
columns from six relevant tables, copies the three matched static files, and
writes checksums and provenance under ignored raw storage. It will not overwrite
an existing snapshot. See the
[Docker snapshot audit](docs/audits/onezoom_docker_27400288.md).

Normalize the verified raw extraction into the ignored, rebuildable SQLite
dataset with:

```bash
conda activate phylogenomica
phylogenomica-ingest-onezoom \
  data/raw/onezoom/docker-2022-02-07
```

This verifies source checksums and schemas before writing
`data/processed/onezoom/27400288/onezoom.sqlite3`. See
[architecture](docs/architecture.md) for the normalized schema and parent
semantics.

Build the validated biological topology as a second ignored artifact:

```bash
phylogenomica-build-tree data/processed/onezoom/27400288
```

This removes only OneZoom's artificial display scaffold, validates root,
cycles, reachability, degrees, and polytomies, and writes direct and collapsed
parent/depth indexes under `tree-v1/`.

Inspect the lineage and ordered candidate-bearing sister groups for any source
leaf ID:

```bash
phylogenomica-lineage 887269
```

Audit every leaf against a configurable game shape:

```bash
phylogenomica-audit-targets data/processed/onezoom/27400288 \
  --output data/processed/onezoom/27400288/target-feasibility-v4/audit.json
```

Audit version 4 constructs a playable lineage of `M * N` members: one target
and `M * N - 1` unique relatives. Transition stages have decoys, one deeper
mulligan, and one deepest unlock on distinct tiers. The ultimate stage has
decoys, one deepest selected-relative mulligan, and the visible selectable
target. It does not require a literal closest-sister endpoint.

To require every target and relative card to have both a preferred English
name and a complete licensed best-image record, add `--require-rich-cards`:

```bash
phylogenomica-audit-targets data/processed/onezoom/27400288 \
  --require-rich-cards \
  --output data/processed/onezoom/27400288/target-feasibility-v4/rich-cards.json
```

This audit retains 43,032 of 44,361 fully card-ready target species. The result
remains an ignored, reproducible artifact; reviewed findings are recorded in
the [Docker snapshot audit](docs/audits/onezoom_docker_27400288.md).

Persist those results as a versioned, queryable target index:

```bash
phylogenomica-build-eligibility data/processed/onezoom/27400288
```

Eligibility index version 1 verifies the normalized and tree manifests, writes
one deterministic SQLite row for each metadata-valid potential target, records
explicit topology failure reasons, and writes an atomic manifest with source
checksums and the exact policy configuration. On the historical snapshot the
index contains 44,361 candidates, of which 43,032 are eligible, and occupies
3,055,616 bytes. Source leaves excluded by the card policy are summarized in
the manifest rather than inflating the generator-facing database.

Select a valid relative lineage for an eligible target and explicit seed:

```bash
phylogenomica-select-relatives 887269 \
  --seed 42 \
  --normalized-dir data/processed/onezoom/27400288 \
  --output data/processed/onezoom/27400288/selections/human-seed-42.json
```

Relative selector version 1 maps every metadata-valid candidate to its correct
target-backbone tier, samples only feasible ordered stage layouts, minimizes
large selected polytomies, and uses OneZoom popularity rank as a soft weighted
preference. The dataset version, selector version, target, full configuration,
and seed determine the result. The same inputs reproduce identical JSON, while
different seeds can change both selected tiers and representative species for
the same target.

Assemble a complete, validated, immutable game for an eligible target:

```bash
phylogenomica-generate-game 887269 \
  --seed 42 \
  --normalized-dir data/processed/onezoom/27400288 \
  --output data/processed/onezoom/27400288/games/human-seed-42.json
```

Game generator version 3 resolves one player-facing card per species from the
normalized metadata, adds the target as a normal selectable card in the
ultimate stage, shuffles each stage on its own seeded stream, and runs every
generated-game validator before returning. A card requires a scientific name, a
preferred English vernacular name, and an `overall_best_any` image with
nonempty URL, rights, and licence; OTT-keyed records take precedence over
name-keyed records, and ties resolve by source table and row ID.

Game schema version 3 stores each tier's divergence age (`age_ma`) and optional
scientific clade name (`clade_name`) on the game itself, so a serialized game
is self-contained. Both are display metadata and never affect correctness;
roughly 46% of tiers carry an age, and generation checks that the ages present
never increase toward the target. Games written under earlier schemas are
refused on load and must be regenerated.

The game ID is a SHA-256 digest of the dataset version, generator and selector
versions, target, complete configuration, and seed. Identical inputs reproduce
identical JSON bytes, while a different seed produces a different game for the
same target. On the historical snapshot a default `M=5`, `N=10` game contains 50
unique species and takes roughly 0.3 seconds to generate.

Serialized games round-trip through `phylogenomica.generation.game.load_game`
and `game_from_dict`. Because a game carries every field its identifier
digests, a loaded game recomputes its own ID and stage shuffles and is
revalidated in full without the selection that produced it. A truncated,
hand-edited, or foreign-version game is rejected at load rather than reaching
the gameplay engine.

Play a generated game without a user interface:

```bash
phylogenomica-play data/processed/onezoom/27400288/games/human-seed-42.json \
  --guess 812045 --guess 830629 \
  --state /tmp/player-state.json
```

With no `--guess` the command plays perfectly, choosing each stage's ending
card immediately. Gameplay engine version 1 is a pure transition over immutable
player state: each guess returns every placement, the remaining relatives, its
cost and bonus, and completion flags.

Scoring is reveal-weighted. A stage is worth `N`, so a perfect game scores
`M * N`. The stage-ending card is free; any other card costs one for itself
plus one for every still-active relative on a strictly shallower tier, since
choosing it exposes those as more distant. A mulligan is a flat cost of one
cancelled by its bonus, which is what makes `mulligan → unlock` tie an
immediate unlock. Same-tier peers are never charged and never placed, so a
polytomy never forfeits points for its unresolved members.

Play in a browser with the local prototype:

```bash
phylogenomica-prototype --target 887269 --seed 42 \
  --normalized-dir data/processed/onezoom/27400288
```

Omit both `--target` and `--game` to choose uniformly from the eligible-target
index on each launch:

```bash
phylogenomica-prototype \
  --normalized-dir data/processed/onezoom/27400288
```

The randomly chosen target remains concealed, including in terminal output. A
random launch chooses a new target nondeterministically; `--seed` still makes
relative selection reproducible once that target has been chosen. Use an
explicit `--target` when the entire game must be reproducible.

After a completed game, **Play again** generates another game with the next
seed. Random-target sessions also choose another eligible target; sessions
started with `--target` or `--game` retain their target and vary the selected
relatives.

It serves `http://127.0.0.1:8000/` and opens a window. Pass `--game FILE.json`
to play a serialized game instead. The page is a renderer: every guess is
resolved by the gameplay engine over a small JSON API, and the API is
stage-scoped, so a card's tier and role reach the browser only once that card
has been placed. The concealed target never crosses the wire early.

The cladogram is the board. It grows left to right from the root as a
conventional rectangular tree, with every species ending as a leaf on the same
right-hand boundary and the open stage's cards in a separate row below the
board. The complete current-stage geometry appears immediately as anonymous
slots; species populate stable slots without resizing the tree. Card-to-slot
mapping, roles, clade names, and ages remain hidden until placement. Cards also
remain in their original tray positions and size for the whole stage, becoming
disabled rather than disappearing after placement. Completed stages collapse
into moderately compact history bands while the current stage receives most of
the available space and readable species labels. Solid branches are resolved
structure and a dashed branch is the concealed continuation; a filled tip is a
relationship you inferred and a hollow one was revealed to you. Same-tier
relatives branch from a single node as a rake, so the display never implies an
order the topology does not support. Branching points show the scientific clade
name when OneZoom supplies one and the divergence age when known; internal tier
indexes are not player-facing.

Cards use validated files from an ignored, dataset-level Wikimedia working
library when one is present. The prototype auto-detects
`assets/processed/wikimedia-library/<dataset>/manifest.json`, serves its files
locally, and shows the normalized rights identifier plus full attribution in
the card tooltip. By default it never hotlinks or downloads while a game is
running. Species absent from the library retain the placeholder glyph. Pass
`--media-library MANIFEST.json` to select a library explicitly.

To fill missing images while playing, opt into the background downloader:

```bash
phylogenomica-prototype \
  --download-missing-images \
  --media-transport curl
```

The game opens immediately with cached images and placeholders. A single
background worker resolves only game species absent from the local library,
downloads and publishes opening-stage images first, then finishes the remaining
stages. The header reports current-stage progress and the page replaces visible
placeholders as validated files arrive. “Play again” queues the new game behind
any request already in flight. Network or rights failures never block guesses;
unavailable cards keep their placeholders. Downloads remain disabled unless
the flag is supplied.

The historical snapshot's stored `media.eol.org` URLs now answer with a
Cloudflare bot interstitial rather than image bytes. See
[data sources](docs/data_sources.md) for the evidence and the Wikidata-based
replacement workflow.

Resolve replacement-image metadata for one generated game without downloading
any media:

```bash
phylogenomica-resolve-wikimedia \
  data/processed/onezoom/27400288/games/human-seed-42.json \
  --normalized-dir data/processed/onezoom/27400288
```

The resolver batches stable Wikidata IDs into `wbgetentities` requests, resolves
ranked `P18` filenames through Commons `imageinfo`, and writes raw response
evidence plus a normalized audit under ignored `data/cache/wikimedia/`. It
distinguishes missing IDs, entities, images, Commons pages, unsupported media,
and incomplete attribution. It intentionally downloads no image bytes.

Merge that resolver output into the reusable working library:

```bash
phylogenomica-update-wikimedia-library \
  data/cache/wikimedia/<dataset>/<game>/manifest.json \
  --transport curl
```

The update is incremental across games. It validates and reuses an existing
species asset when its normalized source fingerprint still matches, downloads
only missing or changed recognized-rights records, preserves species from prior
games, and atomically merges the new manifest. Content-addressed filenames keep
a failed update from invalidating the prior library. Existing per-game download
manifests can also be supplied to import and revalidate their local files
without network traffic. Use `--transport curl` when Conda Python cannot use the
host system's trusted CA chain; both transports keep TLS verification enabled.

This library is still ignored working storage, not the final reviewed bundle
under `assets/gameplay/`. The seeded pilot library contains 43 of the pilot
game's 50 cards; the other seven continue to show placeholders.

The current implementation covers reproducible acquisition, filtered
extraction, normalized ingestion, biological-tree reconstruction, structural
validation, read-only topology queries, batch target-feasibility analysis, a
versioned per-target eligibility index, deterministic seeded relative
selection, validated immutable game assembly, the UI-independent guess,
reveal, and scoring engine, a local browser prototype, and an incremental local
media library. The next milestone is promotion into a compact, reviewed
gameplay bundle, so a clean clone can play with images without the ignored
intermediates.

The project is licensed under the [MIT License](LICENSE). Source datasets and
media retain their own licenses and attribution requirements.
