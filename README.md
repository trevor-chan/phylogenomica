# Phylogenomica

Phylogenomica is a phylogeny trivia and deduction game about reconstructing the
evolutionary path to an initially concealed species. In each stage, the player searches for
the closest selected relative. Every guess reveals part of one persistent
cladogram, and a game progressively narrows from broad branches of life toward
the target, which becomes a normal selectable card in the ultimate stage.

The project is currently in its data-pipeline and game-engine design phase. It
is not playable yet. The first implementation goal is to validate the game
against a real OneZoom snapshot, then build a deterministic, UI-independent
generator and gameplay engine.

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

The current implementation covers reproducible acquisition, filtered
extraction, normalized ingestion, biological-tree reconstruction, structural
validation, read-only topology queries, and batch target-feasibility analysis.
The next milestone is a versioned eligibility index with explicit reason
codes, followed by deterministic representative selection.

The project is licensed under the [MIT License](LICENSE). Source datasets and
media retain their own licenses and attribution requirements.
