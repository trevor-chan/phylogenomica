# Media rights policy

## Purpose

This policy controls how downloaded media moves from ignored working storage
to a committed gameplay bundle. It is deliberately liberal for local prototype
use while preserving explicit evidence and obligations for redistribution.

The project is not currently intended for commercial distribution. That intent
does not waive attribution, ShareAlike, GFDL, or other conditions, and the
policy does not depend on a permanent promise that every future use will remain
noncommercial. None of these classifications is a warranty about copyright in
every jurisdiction.

Policy version 1 distinguishes three promotion states:

- **ready**: a reviewed standardized license or dedication has the metadata
  needed for promotion after visual approval;
- **conditional**: working use is allowed, but promotion still requires the
  listed evidence or license packaging; and
- **blocked**: the rights label is unknown or required source/attribution data
  is missing.

Every recognized `ready` or `conditional` record is allowed in ignored local
prototype assets. The policy never silently converts an unknown label into an
allowed use.

## Ready rights identifiers

The following Commons short labels normalize to canonical identifiers and
HTTPS rights URLs:

| Commons label | Identifier | Requirements |
|---|---|---|
| CC BY 2.0, 2.5, 3.0, or 4.0 | `CC-BY-*` | TASL attribution and change notice |
| CC BY-SA 2.0, 2.5, 3.0, or 4.0 | `CC-BY-SA-*` | TASL, change notice, ShareAlike adaptations |
| CC BY-SA 1.0, 2.0, 2.5, 3.0, or 4.0 `<port>` | `CC-BY-SA-*-<PORT>` | Same, under that national port |
| CC BY 1.0–4.0 `<port>` | `CC-BY-*-<PORT>` | Same, under that national port |
| CC0 | `CC0-1.0` | No legal attribution condition; retain provenance anyway |

Jurisdiction ports such as `CC BY 3.0 us` or `CC BY-SA 2.0 fr` are parsed
rather than enumerated. A port is the same license under a national adaptation
and carries the same obligations, so listing version-and-port pairs one at a
time only produced a table with gaps: a 2026-09-02 audit found nine species
blocked purely because their port was absent from it. A label that is not a
recognized Creative Commons form — `GPL`, for example — is still blocked and
still requires review.

TASL means title, author, source, and license. Each promoted asset must retain
its Commons filename, supplied creator or credit, Commons file page, exact
license identifier and canonical link, and a description of any transformation.
A simple format conversion or thumbnail rendition should still be disclosed.

ShareAlike applies to copyrightable adaptations of the image, not automatically
to the surrounding game, code, or collection. The asset manifest must keep the
image's license distinct from the project's own license.

## Article text

This policy governs media files. Wikipedia article prose is a separate work
under a separate licence and is not classified by the tables above.

Every lead-section extract is licensed CC BY-SA 4.0 by the English Wikipedia
contributors who wrote it. A description therefore may not travel without its
article title, canonical article URL, revision ID, and licence name, and the
presentation layer must show that credit wherever it shows the text: on a card,
in the hover detail, and in the endgame summary. The text credit is kept
distinct from the image credit even when both describe the same species,
because the two works have different authors and may have different licences.

ShareAlike applies to copyrightable adaptations of the article text, not to the
surrounding game or code. Truncating a lead to its first complete sentences is
a verbatim excerpt rather than an adaptation, but the extract is still stored
with its revision ID so the exact source text remains identifiable.

## Conditional rights identifiers

### Public-domain claims

`Public domain` becomes `Public-Domain-Claim`. Working use is allowed, but the
short label is not adequate promotion evidence. Record the exact Commons
public-domain tag, the basis such as author dedication, expired term, or United
States federal-government origin, and relevant source-country and United
States evidence. Preserve credit even where it is not legally required.

### GFDL 1.2

`GFDL 1.2` becomes `GFDL-1.2-only`. Working use is allowed. Promotion requires
creator attribution, the Commons source, an accessible unaltered copy or full
link to GFDL 1.2, the file-specific license notice, and GFDL-compatible
distribution of image derivatives.

### Copyrighted free use

`Copyrighted free use` becomes `Copyrighted-Free-Use`. Working use is allowed.
Promotion requires the complete Commons permission statement, a stable copy of
the cited original-source permission, and preserved creator and source data.

### No known copyright restrictions

`No restrictions` becomes `No-Known-Copyright-Restrictions`. Working use is
allowed. Promotion requires the item-specific institutional statement,
historical evidence, source and institutional credit, and an explicit note that
the statement is not a warranty of worldwide public-domain status.

## Attribution delivery

The runtime bundle should make attribution available from each image card or a
clearly associated credits view. A generated attribution record includes:

- image title or Commons filename;
- creator or required attribution party;
- Commons source URL;
- normalized rights identifier and canonical rights URL;
- local file checksum; and
- transformation statement.

Older Creative Commons versions may require a supplied title, so the project
retains it for every version. Public-domain and CC0 records receive the same
provenance treatment even where attribution is not a license condition.

## Reproducible workflow

The download manifest is evaluated into an ignored rights manifest:

```bash
phylogenomica-evaluate-wikimedia-rights \
  assets/processed/wikimedia/<dataset>/<game>/manifest.json
```

`rights.json` pins the download-manifest checksum, policy version, normalized
identifier, canonical URL, requirements, suggested review decision, and
generated attribution for every asset. The review page requires a matching
rights manifest. Policy-ready records default to `accept`; conditional records
default to `conditional`; blocked records remain pending.

For local gameplay across multiple generated games, the incremental working
library embeds the same policy classification and attribution beside each
species record:

```bash
phylogenomica-update-wikimedia-library \
  data/cache/wikimedia/<dataset>/<game>/manifest.json
```

Recognized `ready` and `conditional` records may enter this ignored library;
`blocked` records are not downloaded. This is a working-use decision only and
does not bypass per-file visual review or promotion requirements.

An exported review is still evidence, not automatic promotion authority. The
promotion builder must verify the download, rights, and review checksums before
copying any asset to `assets/gameplay/`.

## References

- [Wikimedia Commons reuse guidance](https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia/en)
- [Creative Commons reuse and TASL guidance](https://creativecommons.org/reusing-cc-licensed-content/)
- [Creative Commons FAQ on collections and adaptations](https://creativecommons.org/faq/)
- [Creative Commons public-domain tools](https://creativecommons.org/public-domain/)
- [GNU Free Documentation License 1.2](https://www.gnu.org/licenses/old-licenses/fdl-1.2.html)
- [Flickr Commons rights statement](https://www.flickr.com/commons/usage/)
