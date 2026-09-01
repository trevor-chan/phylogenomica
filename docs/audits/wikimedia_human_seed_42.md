# Wikimedia human/seed-42 metadata pilot

Audit date: 2026-09-01

## Scope and reproduction

The pilot regenerated the documented `Homo sapiens` target game under game
schema and generator version 3, then ran Wikimedia resolver version 1 against
all 50 unique game species. After the URL defect described below was fixed,
resolver version 2 re-normalized the same six cached response envelopes:

```bash
phylogenomica-generate-game 887269 \
  --seed 42 \
  --normalized-dir data/processed/onezoom/27400288 \
  --output data/processed/onezoom/27400288/games/human-seed-42.json

python -m phylogenomica.data.wikimedia \
  data/processed/onezoom/27400288/games/human-seed-42.json \
  --normalized-dir data/processed/onezoom/27400288 \
  --transport curl
```

The installed Conda environment predates the resolver console-script entry
point, so the equivalent module invocation was used. The generated game ID is
`81815b85942415a794ba256bbcf9d2c255941d2bc8f27c4b5c06944e42f4f482`.
The initial resolver run made one batched Wikidata request and five batched
Commons requests. All six were live cache misses and completed successfully.
The version-2 rebuild reported six cache hits and made no new API requests.

The current ignored normalized manifest is 92,750 bytes with SHA-256
`776a5b8de712a31b7c46c8b1d9b3c211f4f84e74fb8ba68a6ad2f18d56c86899`.
Its raw response envelopes preserve canonical request URLs, retrieval times,
and checksums beneath the same ignored cache directory.

## Resolution results

| Status | Species | Share |
|---|---:|---:|
| Resolved metadata | 43 | 86% |
| Incomplete creator or credit | 3 | 6% |
| Wikidata item has no `P18` | 2 | 4% |
| OneZoom leaf has no Wikidata ID | 2 | 4% |
| **Total** | **50** | **100%** |

The three incomplete-attribution candidates are `Lepidosiren paradoxa`,
`Tenrec ecaudatus`, and `Hydrurga leptonyx`. Each has an explicit CC BY-SA
license but neither creator nor credit in the returned Commons extended
metadata, so none is promotion-ready.

`Paspalum repens` and `Epizeuxis aemula` have valid Wikidata entities but no
usable `P18`. `Brucella abortus NCTC 8038` and `Escherichia coli 99.1753` have
no OneZoom Wikidata ID. These four require an ID-based EOL or GBIF fallback,
manual curation, or exclusion; the resolver did not use fuzzy name matching.

Thirty-three species have one `P18` candidate, nine have two, and four have
three. The selected 46 Commons filenames are all unique. The pilot currently
queries metadata only for the first ranked candidate, so the 13 multi-candidate
species still offer room for image-quality or attribution-aware selection.

## Media and license profile

All 46 selected Commons pages returned image metadata and a nominal 512-pixel
thumbnail: 45 JPEG files and one PNG. Nine originals are below 640 pixels wide
or 480 pixels high, but none has an aspect ratio more extreme than 3:1.

| License label | Count |
|---|---:|
| CC BY-SA 3.0 | 13 |
| CC BY-SA 4.0 | 8 |
| Public domain | 7 |
| CC BY 2.0 | 3 |
| CC BY 4.0 | 3 |
| CC BY-SA 2.5 | 3 |
| CC BY-SA 2.0 | 2 |
| CC BY 2.5 | 1 |
| CC BY 3.0 | 1 |
| CC BY-SA 2.0 de | 1 |
| CC0 | 1 |
| Copyrighted free use | 1 |
| GFDL 1.2 | 1 |
| No restrictions | 1 |

Eight records have no explicit `LicenseUrl`: the seven public-domain records
and the `Copyrighted free use` record. Before promotion, the project needs an
explicit license-label policy and a stable source or license URL for every
asset. The three incomplete-attribution records remain invalid regardless of
their otherwise acceptable license labels.

Filenames also identify candidates requiring human visual review. Examples
include a two-species image for `Oscarella lobularis`, a generic solitary-
ascidian image for `Herdmania momus`, a historical aquarium illustration for
`Balanophyllia regia`, synonym-labelled images for several taxa, and a human
portrait for `Homo sapiens`. These are not automatic failures, but demonstrate
that a valid `P18` and complete license fields do not establish gameplay
suitability.

## URL-normalization finding and fix

All 46 raw Commons `imageinfo` records contain both `url` and `thumburl`. In
resolver version 1, all 46 normalized media records nevertheless contained
`original_url: null` and `thumbnail_url: null`.

Wikimedia currently appends ampersand-delimited `utm_*` query parameters to
these URLs. The resolver passes every string, including URLs, through its HTML
plain-text parser. Python's `HTMLParser` buffers these ampersand sequences as
unfinished character references and emits no text, so `_plain_text` returns
`None`. Existing fixture URLs contain no query string and did not expose the
defect.

Resolver version 2 now uses an absolute HTTP(S) URL normalizer for URL fields
and retains the HTML parser only for human-readable extended metadata. A
query-string regression test covers original, thumbnail, and Commons page
URLs. Re-normalizing from cached evidence restored all 46 URL pairs.

## Validated download test

Downloader version 1 accepts only records with resolver status `resolved` and
writes to ignored `assets/processed/wikimedia/` storage. It enforces a byte
limit and requires the resolver MIME type, HTTP content type, and decoded PNG
or JPEG signature to agree. It records actual dimensions, SHA-256, source and
final URLs, attribution, license, retrieval time, and transformation status in
a working-copy manifest.

A live `curl` test downloaded the first three resolved records in deterministic
species-ID order:

| Species | Dimensions | Bytes | SHA-256 prefix |
|---|---:|---:|---|
| `Agrostis canina` | 400 × 600 | 209,923 | `9a359a10684d` |
| `Hemitrichia serpula` | 960 × 674 | 259,791 | `8937b1b66c27` |
| `Tuckermanopsis americana` | 750 × 543 | 94,254 | `f208889fb0e4` |

All three were valid JPEGs with distinct checksums and were visually plausible
images of the requested grass, slime mold, and lichen. Their actual dimensions
differ from the nominal API thumbnail dimensions, confirming that validation
must use downloaded bytes rather than assuming `iiurlwidth`. The files and
download manifest remained ignored working artifacts; this test did not
constitute final image or license approval.

The same downloader was then run without a limit. All 43 fully attributed
records downloaded successfully: 42 JPEGs and one PNG totaling 8,107,196
bytes, with no duplicate SHA-256 values. Downloaded widths range from 331 to
960 pixels and heights from 288 to 1,443 pixels; nine images are below either
640 pixels wide or 480 pixels high. Eight records—the seven public-domain
labels and one `Copyrighted free use` label—still lack an explicit license URL.
These are review flags, not silent exclusions.

The full ignored download manifest is 94,929 bytes with SHA-256
`7b61fb6eaa57b5be08ddbe8ccade62d636335a58ae990018b514561f6aacb00e`.

A generated `review.html` beside the ignored download manifest verifies each
file's checksum and byte count before rendering it. It presents the image,
taxon, dimensions, creator, Commons source, and license, then records one of
`pending`, `accept`, `conditional`, `reject`, or `alternate` plus reviewer
notes. Decisions are browser-local until exported as JSON; the export pins both
the download and rights manifests and does not promote files automatically.

The maintainer subsequently reviewed the full page and judged the candidate
images suitable as a set. This is recorded as qualitative pilot evidence, not
as a per-file review manifest; the latter still needs to be exported and pinned
before promotion.

## Rights-policy result

Rights policy version 1 evaluated the full download manifest as 33 `ready`, 10
`conditional`, and zero `blocked`. The ready set comprises eight CC BY records,
24 CC BY-SA records, and one CC0 dedication. The conditional set comprises
seven public-domain short labels plus one record each under GFDL 1.2,
copyrighted free use, and no known copyright restrictions.

The policy reflects the project's liberal, currently noncommercial prototype
posture without treating that posture as a waiver of license terms. All 43
recognized records are allowed in ignored working assets. Conditional records
may also be retained as candidate gameplay media, but they cannot be called
promotion-ready until their per-record evidence or license-packaging
requirements are captured. The versioned `rights.json` pins the download
manifest and supplies the review page's default decisions: 33 `accept` and 10
`conditional`.

## Incremental working-library result

Library builder version 1 imported all 43 validated files into the ignored,
dataset-level `onezoom-27400288` working library without a network request. The
library retained the same 8,107,196 image bytes and rights distribution (33
`ready`, 10 `conditional`). A second identical update validated and reused all
43 records: `downloaded=0`, `imported=0`, `reused=43`, and `blocked=0`. Its
manifest SHA-256 at that point was
`3e7af79450c496bbd048dabaeec9fd2a3f543f0bbaab6391a73ed26a587e0cdf`.

The prototype now auto-detects this dataset library, serves images from local
`/media/<species-id>` routes, and includes attribution only with the open
stage's card payload. The pilot game has local images for 43 of its 50 unique
cards; seven retain the placeholder. This integration is working-use evidence,
not promotion into tracked runtime assets.

## Decision and next action

The 92% candidate-media rate supports Wikimedia Commons as the primary image
source, but the current 86% fully attributed rate and several questionable
choices do not yet justify automated asset promotion.

Next:

1. capture exact public-domain and nonstandard permission evidence for the 10
   conditional records;
2. export and validate a visual-review decision manifest;
3. implement a checksum-pinned promotion and attribution build;
4. measure the reviewed runtime media bundle; and
5. test EOL and GBIF ID fallbacks for the four records with no `P18` path.

Only reviewed assets should move from ignored working storage to
`assets/gameplay/`, with complete per-file provenance and build metadata.
