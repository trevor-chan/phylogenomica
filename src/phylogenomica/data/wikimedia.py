"""Resolve game species to auditable Wikimedia Commons media metadata.

This module performs Phase 10's metadata-only pilot. It deliberately stops
before downloading media: raw API responses are cached as evidence, normalized
records retain explicit failure reasons, and every candidate remains subject to
manual image and license review before promotion into gameplay assets.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sqlite3
import ssl
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from phylogenomica.data.onezoom_download import build_ssl_context, sha256_file
from phylogenomica.data.onezoom_ingest import DATABASE_FILENAME
from phylogenomica.generation.game import (
    GameGenerationError,
    GeneratedGame,
    load_game,
)
from phylogenomica.tree.preprocess import DEFAULT_NORMALIZED_DIR

WIKIMEDIA_RESOLVER_VERSION = 2
WIKIMEDIA_MANIFEST_SCHEMA_VERSION = 1
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"
DEFAULT_CACHE_ROOT = Path("data/cache/wikimedia")
WIKIDATA_BATCH_SIZE = 50
# Commons documents extmetadata as expensive; keep these batches deliberately
# small even though the API permits more titles in one request.
COMMONS_BATCH_SIZE = 10
THUMBNAIL_WIDTH = 512
USER_AGENT = (
    "Phylogenomica/0.1 "
    "(https://github.com/trevor-chan/phylogenomica; metadata enrichment)"
)

STATUS_DEFINITIONS = {
    "resolved": "A Commons image and its required attribution fields were found.",
    "missing_wikidata_id": "The OneZoom leaf has no Wikidata identifier.",
    "invalid_wikidata_id": "The OneZoom Wikidata identifier is not positive.",
    "wikidata_entity_missing": "Wikidata reports that the requested entity is missing.",
    "missing_p18": "The Wikidata entity has no usable, non-deprecated P18 image.",
    "commons_page_missing": "The selected P18 filename has no Commons file page.",
    "missing_imageinfo": "The Commons file page returned no current image metadata.",
    "unsupported_media": "The selected Commons file is not an image media type.",
    "missing_image_url": "The Commons image has no usable download URL.",
    "incomplete_attribution": (
        "The Commons metadata lacks a license name or creator/credit."
    ),
}

JsonFetcher = Callable[[str, Mapping[str, str], ssl.SSLContext], Mapping[str, Any]]
Clock = Callable[[], datetime]


class WikimediaResolutionError(RuntimeError):
    """Raised when Wikimedia metadata cannot be resolved or audited safely."""


@dataclass(frozen=True)
class SpeciesSource:
    species_id: int
    scientific_name: str
    wikidata_id: int | None

    @property
    def qid(self) -> str | None:
        if self.wikidata_id is None or self.wikidata_id <= 0:
            return None
        return f"Q{self.wikidata_id}"


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(value: object) -> str | None:
    if value is None:
        return None
    parser = _PlainTextParser()
    parser.feed(str(value))
    rendered = " ".join("".join(parser.parts).split())
    return html.unescape(rendered) or None


def _plain_scalar(value: object) -> str | None:
    """Normalize a non-HTML API scalar without interpreting URL ampersands."""
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _absolute_http_url(value: object) -> str | None:
    """Return an absolute HTTP(S) URL without passing it through an HTML parser."""
    rendered = _plain_scalar(value)
    if rendered is None:
        return None
    parsed = urlsplit(rendered)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return None
    return rendered


def _now() -> datetime:
    return datetime.now(UTC)


def _batches(values: Sequence[str], size: int) -> list[tuple[str, ...]]:
    return [
        tuple(values[offset : offset + size])
        for offset in range(0, len(values), size)
    ]


def _canonical_request_url(endpoint: str, parameters: Mapping[str, str]) -> str:
    return f"{endpoint}?{urlencode(sorted(parameters.items()))}"


def _fetch_json(
    endpoint: str, parameters: Mapping[str, str], context: ssl.SSLContext
) -> Mapping[str, Any]:
    url = _canonical_request_url(endpoint, parameters)
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urlopen(request, timeout=60, context=context) as response:  # noqa: S310
            payload = json.load(response)
    except (OSError, json.JSONDecodeError) as error:
        raise WikimediaResolutionError(f"failed to fetch {url}: {error}") from error
    return _validated_api_payload(payload, url)


def _fetch_json_curl(
    endpoint: str, parameters: Mapping[str, str], _context: ssl.SSLContext
) -> Mapping[str, Any]:
    """Fetch JSON with system curl while retaining normal TLS verification."""
    url = _canonical_request_url(endpoint, parameters)
    command = [
        "curl",
        "-fsSL",
        "--connect-timeout",
        "15",
        "--max-time",
        "60",
        "--retry",
        "2",
        "--retry-all-errors",
        "--header",
        "Accept: application/json",
        "--user-agent",
        USER_AGENT,
        url,
    ]
    try:
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=75
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WikimediaResolutionError(
            f"failed to run curl for {url}: {error}"
        ) from error
    if completed.returncode:
        detail = completed.stderr.strip() or f"exit status {completed.returncode}"
        raise WikimediaResolutionError(f"curl failed for {url}: {detail}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise WikimediaResolutionError(
            f"curl returned invalid JSON for {url}: {error}"
        ) from error
    return _validated_api_payload(payload, url)


def _validated_api_payload(payload: object, url: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise WikimediaResolutionError(f"API response is not an object: {url}")
    if isinstance(payload.get("error"), Mapping):
        raise WikimediaResolutionError(
            f"API returned an error for {url}: {payload['error']!r}"
        )
    return payload


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _read_cached_response(
    path: Path, request_url: str
) -> tuple[Mapping[str, Any], str]:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WikimediaResolutionError(
            f"invalid cached API response: {path}"
        ) from error
    if not isinstance(envelope, Mapping) or envelope.get("request_url") != request_url:
        raise WikimediaResolutionError(f"cached API request does not match: {path}")
    response = envelope.get("response")
    retrieved_at = envelope.get("retrieved_at")
    if not isinstance(response, Mapping) or not isinstance(retrieved_at, str):
        raise WikimediaResolutionError(f"invalid cached API envelope: {path}")
    return response, retrieved_at


def _cached_request(
    *,
    endpoint: str,
    parameters: Mapping[str, str],
    source: str,
    cache_dir: Path,
    context: ssl.SSLContext,
    fetch_json: JsonFetcher,
    clock: Clock,
    refresh: bool,
) -> tuple[Mapping[str, Any], dict[str, object]]:
    request_url = _canonical_request_url(endpoint, parameters)
    request_key = hashlib.sha256(request_url.encode("utf-8")).hexdigest()
    path = cache_dir / "raw" / source / f"{request_key}.json"
    cache_hit = path.is_file() and not refresh
    if cache_hit:
        response, retrieved_at = _read_cached_response(path, request_url)
    else:
        response = fetch_json(endpoint, parameters, context)
        retrieved_at = clock().astimezone(UTC).isoformat()
        _atomic_json(
            path,
            {
                "schema_version": 1,
                "source": source,
                "request_url": request_url,
                "retrieved_at": retrieved_at,
                "response": response,
            },
        )
    return response, {
        "source": source,
        "path": path.relative_to(cache_dir).as_posix(),
        "request_url": request_url,
        "retrieved_at": retrieved_at,
        "sha256": sha256_file(path),
        "cache_hit": cache_hit,
    }


def _game_species_ids(game: GeneratedGame) -> tuple[int, ...]:
    return tuple(
        sorted(
            {
                member.species_id
                for stage in game.stages
                for member in stage.members
            }
        )
    )


def _load_species_sources(
    game: GeneratedGame, normalized_database: Path
) -> tuple[SpeciesSource, ...]:
    if not normalized_database.is_file():
        raise WikimediaResolutionError(
            f"normalized database does not exist: {normalized_database}"
        )
    connection = sqlite3.connect(f"file:{normalized_database}?mode=ro", uri=True)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()
        if version != (1,):
            raise WikimediaResolutionError(
                f"unsupported normalized database schema: {version!r}"
            )
        metadata = {
            str(key): str(value)
            for key, value in connection.execute(
                "SELECT key, value FROM dataset_metadata"
            )
        }
        if metadata.get("dataset_version") != game.dataset_version:
            raise WikimediaResolutionError(
                "game and normalized database dataset versions differ"
            )
        rows: dict[int, SpeciesSource] = {}
        ids = _game_species_ids(game)
        for offset in range(0, len(ids), 900):
            batch = ids[offset : offset + 900]
            placeholders = ",".join("?" for _ in batch)
            for species_id, scientific_name, wikidata_id in connection.execute(
                "SELECT leaf_id, scientific_name, wikidata_id FROM leaves "
                f"WHERE leaf_id IN ({placeholders})",
                batch,
            ):
                rows[int(species_id)] = SpeciesSource(
                    species_id=int(species_id),
                    scientific_name=(
                        "" if scientific_name is None else str(scientific_name)
                    ),
                    wikidata_id=(
                        None if wikidata_id is None else int(wikidata_id)
                    ),
                )
    except sqlite3.Error as error:
        raise WikimediaResolutionError(
            f"cannot read normalized Wikimedia identifiers: {error}"
        ) from error
    finally:
        connection.close()
    missing = sorted(set(ids) - rows.keys())
    if missing:
        raise WikimediaResolutionError(
            f"normalized database lacks game species IDs: {missing[:5]!r}"
        )
    return tuple(rows[species_id] for species_id in ids)


def _p18_candidates(entity: Mapping[str, Any]) -> tuple[str, ...]:
    claims = entity.get("claims")
    if not isinstance(claims, Mapping):
        return ()
    statements = claims.get("P18")
    if not isinstance(statements, Sequence) or isinstance(statements, str):
        return ()
    candidates: list[tuple[int, int, str]] = []
    ranks = {"preferred": 0, "normal": 1}
    for position, statement in enumerate(statements):
        if not isinstance(statement, Mapping):
            continue
        rank = str(statement.get("rank", "normal"))
        if rank not in ranks:
            continue
        mainsnak = statement.get("mainsnak")
        if not isinstance(mainsnak, Mapping) or mainsnak.get("snaktype") != "value":
            continue
        datavalue = mainsnak.get("datavalue")
        if not isinstance(datavalue, Mapping):
            continue
        value = datavalue.get("value")
        if isinstance(value, str) and value.strip():
            candidates.append((ranks[rank], position, value.strip()))
    seen: set[str] = set()
    result: list[str] = []
    for _, _, filename in sorted(candidates):
        key = filename.casefold()
        if key not in seen:
            seen.add(key)
            result.append(filename)
    return tuple(result)


def _title_key(title: object) -> str:
    rendered = str(title).replace("_", " ").strip()
    if not rendered.casefold().startswith("file:"):
        rendered = f"File:{rendered}"
    return rendered.casefold()


def _commons_pages(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, str]]:
    query = payload.get("query")
    if not isinstance(query, Mapping):
        return {}, {}
    raw_pages = query.get("pages", [])
    if isinstance(raw_pages, Mapping):
        pages = raw_pages.values()
    elif isinstance(raw_pages, Sequence) and not isinstance(raw_pages, str):
        pages = raw_pages
    else:
        pages = ()
    page_by_key = {
        _title_key(page.get("title")): page
        for page in pages
        if isinstance(page, Mapping) and page.get("title") is not None
    }
    aliases: dict[str, str] = {}
    for field in ("normalized", "redirects"):
        records = query.get(field, [])
        if isinstance(records, Sequence) and not isinstance(records, str):
            for record in records:
                if isinstance(record, Mapping) and "from" in record and "to" in record:
                    aliases[_title_key(record["from"])] = _title_key(record["to"])
    return page_by_key, aliases


def _follow_alias(title: str, aliases: Mapping[str, str]) -> str:
    current = _title_key(title)
    seen: set[str] = set()
    while current in aliases and current not in seen:
        seen.add(current)
        current = aliases[current]
    return current


def _metadata_value(metadata: Mapping[str, Any], field: str) -> object:
    record = metadata.get(field)
    return record.get("value") if isinstance(record, Mapping) else None


def _attribution_required(value: object) -> bool | None:
    rendered = _plain_text(value)
    if rendered is None:
        return None
    normalized = rendered.casefold()
    if normalized in {"true", "yes", "1", "required"}:
        return True
    if normalized in {"false", "no", "0", "not required"}:
        return False
    return None


def _commons_media(page: Mapping[str, Any]) -> tuple[str, dict[str, object] | None]:
    if "missing" in page:
        return "commons_page_missing", None
    imageinfo = page.get("imageinfo")
    if (
        not isinstance(imageinfo, Sequence)
        or isinstance(imageinfo, str)
        or not imageinfo
    ):
        return "missing_imageinfo", None
    info = imageinfo[0]
    if not isinstance(info, Mapping):
        return "missing_imageinfo", None
    mime_type = _plain_scalar(info.get("mime"))
    if mime_type is None or not mime_type.startswith("image/"):
        return "unsupported_media", None
    extmetadata = info.get("extmetadata")
    metadata = extmetadata if isinstance(extmetadata, Mapping) else {}
    creator = _plain_text(_metadata_value(metadata, "Artist"))
    credit = _plain_text(_metadata_value(metadata, "Credit"))
    license_name = _plain_text(_metadata_value(metadata, "LicenseShortName"))
    license_url = _absolute_http_url(_metadata_value(metadata, "LicenseUrl"))
    missing_attribution = []
    if creator is None and credit is None:
        missing_attribution.append("creator_or_credit")
    if license_name is None:
        missing_attribution.append("license_name")
    media: dict[str, object] = {
        "commons_title": str(page.get("title", "")),
        "commons_page_url": _absolute_http_url(
            info.get("descriptionurl") or info.get("descriptionshorturl")
        ),
        "original_url": _absolute_http_url(info.get("url")),
        "thumbnail_url": _absolute_http_url(info.get("thumburl")),
        "mime_type": mime_type,
        "bytes": info.get("size") if isinstance(info.get("size"), int) else None,
        "width": info.get("width") if isinstance(info.get("width"), int) else None,
        "height": info.get("height") if isinstance(info.get("height"), int) else None,
        "thumbnail_width": (
            info.get("thumbwidth") if isinstance(info.get("thumbwidth"), int) else None
        ),
        "thumbnail_height": (
            info.get("thumbheight")
            if isinstance(info.get("thumbheight"), int)
            else None
        ),
        "source_sha1": _plain_scalar(info.get("sha1")),
        "creator": creator,
        "credit": credit,
        "license_name": license_name,
        "license_url": license_url,
        "usage_terms": _plain_text(_metadata_value(metadata, "UsageTerms")),
        "attribution_required": _attribution_required(
            _metadata_value(metadata, "AttributionRequired")
        ),
        "missing_attribution_fields": missing_attribution,
    }
    if media["original_url"] is None and media["thumbnail_url"] is None:
        return "missing_image_url", media
    if missing_attribution:
        return "incomplete_attribution", media
    return "resolved", media


def _game_digest(game: GeneratedGame) -> str:
    rendered = json.dumps(
        game.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def resolve_game_wikimedia(
    game: GeneratedGame,
    *,
    normalized_database: Path = DEFAULT_NORMALIZED_DIR / DATABASE_FILENAME,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    ca_file: Path | None = None,
    refresh: bool = False,
    fetch_json: JsonFetcher = _fetch_json,
    clock: Clock = _now,
    reproduction_command: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Resolve one game's species and write a resumable metadata audit."""
    species = _load_species_sources(game, normalized_database)
    cache_dir = cache_root / game.dataset_version / game.game_id
    context = build_ssl_context(ca_file)
    raw_requests: list[dict[str, object]] = []

    qids = sorted({source.qid for source in species if source.qid is not None})
    entities: dict[str, Mapping[str, Any]] = {}
    wikidata_evidence: dict[str, str] = {}
    for batch in _batches(qids, WIKIDATA_BATCH_SIZE):
        payload, evidence = _cached_request(
            endpoint=WIKIDATA_API_URL,
            parameters={
                "action": "wbgetentities",
                "format": "json",
                "formatversion": "2",
                "ids": "|".join(batch),
                "maxlag": "5",
                "props": "claims",
            },
            source="wikidata",
            cache_dir=cache_dir,
            context=context,
            fetch_json=fetch_json,
            clock=clock,
            refresh=refresh,
        )
        raw_requests.append(evidence)
        raw_entities = payload.get("entities")
        if not isinstance(raw_entities, Mapping):
            raise WikimediaResolutionError("Wikidata response has no entities object")
        for qid in batch:
            entity = raw_entities.get(qid)
            if isinstance(entity, Mapping):
                entities[qid] = entity
            wikidata_evidence[qid] = str(evidence["sha256"])

    candidates_by_qid = {
        qid: _p18_candidates(entity) for qid, entity in entities.items()
    }
    filenames = sorted(
        {candidates[0] for candidates in candidates_by_qid.values() if candidates},
        key=str.casefold,
    )
    commons_by_filename: dict[str, Mapping[str, Any]] = {}
    commons_evidence: dict[str, str] = {}
    for batch in _batches(filenames, COMMONS_BATCH_SIZE):
        requested_titles = tuple(f"File:{filename}" for filename in batch)
        payload, evidence = _cached_request(
            endpoint=COMMONS_API_URL,
            parameters={
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "iiextmetadatafilter": (
                    "Artist|Credit|LicenseShortName|LicenseUrl|UsageTerms|"
                    "AttributionRequired"
                ),
                "iiprop": "url|mime|size|sha1|extmetadata",
                "iiurlwidth": str(THUMBNAIL_WIDTH),
                "maxlag": "5",
                "prop": "imageinfo",
                "redirects": "1",
                "titles": "|".join(requested_titles),
            },
            source="commons",
            cache_dir=cache_dir,
            context=context,
            fetch_json=fetch_json,
            clock=clock,
            refresh=refresh,
        )
        raw_requests.append(evidence)
        pages, aliases = _commons_pages(payload)
        for filename, title in zip(batch, requested_titles, strict=True):
            page = pages.get(_follow_alias(title, aliases))
            if page is not None:
                commons_by_filename[filename] = page
            commons_evidence[filename] = str(evidence["sha256"])

    records: list[dict[str, object]] = []
    for source in species:
        base: dict[str, object] = {
            "species_id": source.species_id,
            "scientific_name": source.scientific_name,
            "wikidata_id": source.wikidata_id,
            "wikidata_qid": source.qid,
            "p18_candidates": [],
            "selected_p18": None,
            "media": None,
            "evidence": {},
        }
        if source.wikidata_id is None:
            base["status"] = "missing_wikidata_id"
        elif source.wikidata_id <= 0:
            base["status"] = "invalid_wikidata_id"
        elif source.qid not in entities or "missing" in entities[source.qid]:
            base["status"] = "wikidata_entity_missing"
            base["evidence"] = {
                "wikidata_response_sha256": wikidata_evidence.get(source.qid)
            }
        else:
            candidates = candidates_by_qid.get(source.qid, ())
            base["p18_candidates"] = list(candidates)
            base["evidence"] = {
                "wikidata_response_sha256": wikidata_evidence[source.qid]
            }
            if not candidates:
                base["status"] = "missing_p18"
            else:
                filename = candidates[0]
                base["selected_p18"] = filename
                evidence = dict(base["evidence"])  # type: ignore[arg-type]
                evidence["commons_response_sha256"] = commons_evidence[filename]
                base["evidence"] = evidence
                page = commons_by_filename.get(filename)
                if page is None:
                    base["status"] = "commons_page_missing"
                else:
                    status, media = _commons_media(page)
                    base["status"] = status
                    base["media"] = media
        records.append(base)

    counts = Counter(str(record["status"]) for record in records)
    unknown_statuses = sorted(set(counts) - STATUS_DEFINITIONS.keys())
    if unknown_statuses:
        raise WikimediaResolutionError(
            f"unknown resolution statuses: {unknown_statuses}"
        )
    manifest: dict[str, Any] = {
        "schema_version": WIKIMEDIA_MANIFEST_SCHEMA_VERSION,
        "resolver_version": WIKIMEDIA_RESOLVER_VERSION,
        "generated_at": clock().astimezone(UTC).isoformat(),
        "dataset_version": game.dataset_version,
        "game_id": game.game_id,
        "game_sha256": _game_digest(game),
        "species_count": len(species),
        "source": {
            "normalized_database": str(normalized_database),
            "normalized_database_sha256": sha256_file(normalized_database),
            "wikidata_api": WIKIDATA_API_URL,
            "commons_api": COMMONS_API_URL,
        },
        "configuration": {
            "wikidata_batch_size": WIKIDATA_BATCH_SIZE,
            "commons_batch_size": COMMONS_BATCH_SIZE,
            "thumbnail_width": THUMBNAIL_WIDTH,
            "refresh": refresh,
        },
        "status_definitions": STATUS_DEFINITIONS,
        "status_counts": dict(sorted(counts.items())),
        "raw_requests": raw_requests,
        "records": records,
        "review_status": "required-before-download-or-promotion",
        "reproduction_command": reproduction_command,
    }
    manifest_path = cache_dir / "manifest.json"
    _atomic_json(manifest_path, manifest)
    return manifest_path, manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve one game's species to cached Wikimedia media metadata."
    )
    parser.add_argument("game", type=Path, help="validated generated game JSON")
    parser.add_argument(
        "--normalized-dir", type=Path, default=DEFAULT_NORMALIZED_DIR
    )
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument(
        "--transport",
        choices=("urllib", "curl"),
        default="urllib",
        help="verified HTTPS client (curl can use the macOS system trust store)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="replace matching cached API responses with current responses",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    command = (
        f"phylogenomica-resolve-wikimedia {args.game} "
        f"--normalized-dir {args.normalized_dir} --cache-root {args.cache_root}"
        f" --transport {args.transport}"
        + (" --refresh" if args.refresh else "")
    )
    try:
        game = load_game(args.game)
        manifest_path, manifest = resolve_game_wikimedia(
            game,
            normalized_database=args.normalized_dir / DATABASE_FILENAME,
            cache_root=args.cache_root,
            ca_file=args.ca_file,
            refresh=args.refresh,
            fetch_json=_fetch_json_curl if args.transport == "curl" else _fetch_json,
            reproduction_command=command,
        )
    except (GameGenerationError, WikimediaResolutionError) as error:
        raise SystemExit(str(error)) from error
    counts = ", ".join(
        f"{status}={count}" for status, count in manifest["status_counts"].items()
    )
    print(f"wrote {manifest_path} ({counts})")


if __name__ == "__main__":
    main()
