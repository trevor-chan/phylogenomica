# Gameplay dataset

This tracked directory contains compact data needed at runtime. The curated
`rank_titles.json` catalog supplies endgame titles and a small taxon-to-clade
alias table; its provenance and checksum are recorded in `manifest.json`.

No phylogeny gameplay dataset has been built yet. When one is added, update
`manifest.json` with its version, provenance, license, checksums, schema,
generator version, and reproduction command. Keep large raw and intermediate
artifacts out of this directory.
