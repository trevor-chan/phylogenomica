# Data layout

The data tree separates local, reproducible build inputs from the compact
runtime bundle that ships with the repository.

| Directory | Contents | Tracked? |
|---|---|---:|
| `raw/` | Immutable upstream snapshots exactly as downloaded | No |
| `processed/` | Normalized and derived build intermediates | No |
| `cache/` | Disposable request and computation caches | No |
| `gameplay/` | Compact, fully processed runtime data | Yes |

See `docs/data_sources.md` for provenance and promotion requirements. Do not put
secrets or credentials anywhere under `data/`.
