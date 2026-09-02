# Enrichment coverage audit

Audit date: 2026-09-02

## Question

Players reported that roughly half the species in a game showed no image and
no description. The intended standard is above 95% image coverage: for a
50-species game, two or three species without a picture is acceptable.

## Scope and reproduction

Three games were generated against dataset `onezoom-27400288` with randomly
selected eligible targets and seeds 1000–1002, giving 150 species. Each game
was resolved through both enrichment pipelines and then imported into a
throwaway library root, so no measurement reused a previously populated
library.

Aggregate resolver statuses were also recomputed over every manifest written
to `data/cache/wikimedia/` and `data/cache/wikipedia/` to date: 956 image
records over 933 distinct species, and 242 text records over 231 species.

## Finding 1: one unusable file discarded an entire batch

`update_wikimedia_library` converted any `WikimediaDownloadError` into a fatal
`WikimediaLibraryError`. Downloaded bytes are accumulated in memory and written
only after the loop completes, so a single failing record discarded every
already-downloaded file in the batch and the library manifest was never
written.

Measured directly on game `ab21da6618`, which contains one TIFF-sourced record:

| import path | species landed |
|---|---|
| one species per call | 38 of 39 |
| one batched call, as the background worker runs it | **0 of 39** |

The prototype's background worker downloads in two batches — opening stage
first, then the rest. A failure in the second batch therefore left stages 2–5
with no images at all while stage 1 looked healthy, which matches the reported
symptom exactly.

A per-species download failure is now counted and skipped like a blocked
rights record. It is recorded in `last_update.failed_species` with its reason
and printed by the CLI. Nothing is written for a failed species, so the next
update simply retries it.

## Finding 2: thumbnails were validated against the source file's format

The downloader requested the Commons thumbnail but required the *source* file's
media type to be JPEG or PNG. Commons renders thumbnails on demand, so a TIFF
or SVG original is served as JPEG or PNG. Verified against live Commons:

| source | thumbnail served | bytes |
|---|---|---|
| `image/tiff` | `image/jpeg` | JPEG |
| `image/gif` | `image/gif` | GIF |

A usable JPEG was therefore rejected because the original was a TIFF.
Validation now applies to what was actually downloaded: for a thumbnail the
response content type and the byte signature must agree and be displayable,
while an original download must still match the resolved media type. GIF was
added as a supported format because Commons serves GIF sources as GIF at every
size rather than transcoding them.

## Finding 3: the rights table enumerated licence ports one at a time

13 of 924 distinct resolved species (1.4%) were blocked as unrecognized. Nine
carried ordinary Creative Commons licences under a national port or an early
version:

```
3x 'CC BY 3.0 us'      1x 'CC BY-SA 3.0 de'     1x 'CC BY 3.0 au'
2x 'CC BY-SA 1.0'      1x 'CC BY 2.5 au'        1x 'CC BY-SA 2.0 fr'
4x 'GPL'
```

The policy table listed `CC BY-SA 2.0 de` as a one-off and nothing else. Ports
are the same licences with the same obligations, so they are now parsed rather
than enumerated. `GPL` remains blocked pending review; it is a different
licence, not a port.

## Coverage after the fixes

Measured over the same 150 species through the batched path:

| | before | after |
|---|---|---|
| images usable in game | 84.0% (0% for the aborted game) | **85.3%** |
| text usable in game | 82.7% | 82.7% |
| rights-blocked | 2 | **0** |
| resolved but not landed | 2 | **0** |

Every record the resolvers produce now reaches the game. The remaining loss is
entirely at resolution:

| images | | text | |
|---|---|---|---|
| `missing_p18` | 6.0% | `missing_sitelink` | 12.7% |
| `missing_wikidata_id` | 4.7% | `missing_wikidata_id` | 4.7% |
| `incomplete_attribution` | 4.0% | | |

## The remaining gap to 95%

The Wikidata bridge cannot close it: 10.7% of species have no Wikidata image
path at all. OneZoom's own `images` table can. Of the 74 species observed with
no Wikidata image path, OneZoom holds an image row for **74 of 74**, every one
with a licence string:

```
33x Marked as being in the public domain    11x CC-BY-SA 3.0
28x Released into the public domain         10x CC-BY 3.0, 7x CC-BY 2.0, 3x CC-BY 4.0
```

A OneZoom fallback would therefore close most of the image gap. It is not
implemented here: it introduces a second image source with its own rights
vocabulary — the licence strings carry encoding artefacts and differ in format
from Commons `extmetadata` — and that is a policy decision rather than a defect
fix. `incomplete_attribution` (4.0%) is a similar decision: the image exists
and is licensed, but Commons supplied no creator or credit, and the rights
policy requires attribution before working use.

Text cannot reach 95% by this route. 12.7% of species have a Wikidata item with
no English Wikipedia article, and substituting a genus or family article would
describe a different taxon.
