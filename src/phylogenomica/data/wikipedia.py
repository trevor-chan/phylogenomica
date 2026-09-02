"""Resolve game species to auditable English Wikipedia lead-section text.

This module is the text counterpart of :mod:`phylogenomica.data.wikimedia`.
It reuses that module's Wikidata bridge: OneZoom supplies a Wikidata ID, the
entity's ``enwiki`` sitelink supplies an article title, and the article's lead
section supplies the player-facing description.

Article prose is licensed CC BY-SA 4.0 rather than under the per-file terms
that govern Commons media, so every record carries its own title, permanent
article URL, and revision ID. Presentation layers must show that attribution
alongside the text.
"""

from __future__ import annotations

import argparse
import hashlib
import time
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC
from pathlib import Path
from typing import Any

from phylogenomica.data.onezoom_download import build_ssl_context, sha256_file
from phylogenomica.data.onezoom_ingest import DATABASE_FILENAME
from phylogenomica.data.wikimedia import (
    WIKIDATA_API_URL,
    Clock,
    JsonFetcher,
    Sleeper,
    WikimediaResolutionError,
    _absolute_http_url,
    _atomic_json,
    _batches,
    _cached_request_with_backoff,
    _fetch_json,
    _fetch_json_curl,
    _game_digest,
    _game_species_ids,
    _load_species_sources,
    _now,
    _plain_scalar,
)
from phylogenomica.generation.game import (
    GameGenerationError,
    GeneratedGame,
    load_game,
)
from phylogenomica.tree.preprocess import DEFAULT_NORMALIZED_DIR

WIKIPEDIA_RESOLVER_VERSION = 1
WIKIPEDIA_MANIFEST_SCHEMA_VERSION = 1
WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_SITE = "enwiki"
DEFAULT_CACHE_ROOT = Path("data/cache/wikipedia")
SITELINK_BATCH_SIZE = 50
# TextExtracts caps an intro-only extraction at 20 pages per request.
EXTRACT_BATCH_SIZE = 20
# A lead section runs to several paragraphs. Cards and popups need a readable
# opening, not an article, and the library is read whole at startup.
MAX_EXTRACT_CHARACTERS = 1200
TEXT_LICENSE_NAME = "CC BY-SA 4.0"
TEXT_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
# The Wikipedia extract request carries maxlag; the Wikidata entity read does
# not. See the media resolver, which owns both that reasoning and the shared
# backoff these requests are made through.

STATUS_DEFINITIONS = {
    "resolved": "An English Wikipedia lead-section extract was found.",
    "missing_wikidata_id": "The OneZoom leaf has no Wikidata identifier.",
    "invalid_wikidata_id": "The OneZoom Wikidata identifier is not positive.",
    "wikidata_entity_missing": "Wikidata reports that the requested entity is missing.",
    "missing_sitelink": "The Wikidata entity has no English Wikipedia sitelink.",
    "wikipedia_page_missing": "The linked English Wikipedia article does not exist.",
    "missing_extract": "The article returned no usable lead-section extract.",
}


class WikipediaResolutionError(RuntimeError):
    """Raised when Wikipedia text cannot be resolved or audited safely."""


def _article_key(title: object) -> str:
    """Normalize an article title the way MediaWiki compares titles."""
    return str(title).replace("_", " ").strip().casefold()


def _alias_map(query: Mapping[str, Any]) -> dict[str, str]:
    """Collapse MediaWiki's normalization and redirect hops into one mapping."""
    aliases: dict[str, str] = {}
    for key in ("normalized", "redirects"):
        entries = query.get(key)
        if not isinstance(entries, Sequence) or isinstance(entries, str):
            continue
        for record in entries:
            if (
                isinstance(record, Mapping)
                and record.get("from") is not None
                and record.get("to") is not None
            ):
                aliases[_article_key(record["from"])] = _article_key(record["to"])
    return aliases


def _follow_alias(title: str, aliases: Mapping[str, str]) -> str:
    current = _article_key(title)
    # A normalization may feed a redirect, so walk the chain with a bound.
    for _ in range(len(aliases) + 1):
        following = aliases.get(current)
        if following is None or following == current:
            return current
        current = following
    return current


def _wikipedia_pages(
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
        _article_key(page.get("title")): page
        for page in pages
        if isinstance(page, Mapping) and page.get("title") is not None
    }
    return page_by_key, _alias_map(query)


def _enwiki_title(entity: Mapping[str, Any]) -> str | None:
    sitelinks = entity.get("sitelinks")
    if not isinstance(sitelinks, Mapping):
        return None
    sitelink = sitelinks.get(WIKIPEDIA_SITE)
    if not isinstance(sitelink, Mapping):
        return None
    return _plain_scalar(sitelink.get("title"))


def _trim_extract(value: object) -> tuple[str | None, bool]:
    """Return a whitespace-normalized lead extract and whether it was cut.

    Truncation prefers the last complete sentence inside the budget so a card
    never shows a description that stops mid-clause.
    """
    if value is None:
        return None, False
    rendered = " ".join(str(value).split())
    if not rendered:
        return None, False
    if len(rendered) <= MAX_EXTRACT_CHARACTERS:
        return rendered, False
    window = rendered[:MAX_EXTRACT_CHARACTERS]
    boundary = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if boundary > MAX_EXTRACT_CHARACTERS // 2:
        return window[: boundary + 1], True
    return window.rstrip() + "…", True


def _extract_record(page: Mapping[str, Any]) -> tuple[str, dict[str, object] | None]:
    if "missing" in page or "invalid" in page:
        return "wikipedia_page_missing", None
    extract, truncated = _trim_extract(page.get("extract"))
    if extract is None:
        return "missing_extract", None
    page_id = page.get("pageid")
    revision_id = page.get("lastrevid")
    title = _plain_scalar(page.get("title"))
    url = _absolute_http_url(page.get("canonicalurl")) or _absolute_http_url(
        page.get("fullurl")
    )
    return "resolved", {
        "title": title,
        "page_id": None if not isinstance(page_id, int) else page_id,
        "revision_id": None if not isinstance(revision_id, int) else revision_id,
        "url": url,
        "extract": extract,
        "extract_truncated": truncated,
        "license_name": TEXT_LICENSE_NAME,
        "license_url": TEXT_LICENSE_URL,
        "attribution_text": (
            f"“{title}”, English Wikipedia contributors, {TEXT_LICENSE_NAME}"
        ),
    }


def _request(
    *, sleep: Sleeper = time.sleep, **kwargs: Any
) -> tuple[Mapping[str, Any], dict[str, object]]:
    """Perform one cached API request in this module's error vocabulary."""
    try:
        return _cached_request_with_backoff(sleep=sleep, **kwargs)
    except WikimediaResolutionError as error:
        raise WikipediaResolutionError(str(error)) from error


def resolve_game_wikipedia(
    game: GeneratedGame,
    *,
    normalized_database: Path = DEFAULT_NORMALIZED_DIR / DATABASE_FILENAME,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    ca_file: Path | None = None,
    refresh: bool = False,
    species_ids: Collection[int] | None = None,
    fetch_json: JsonFetcher = _fetch_json,
    clock: Clock = _now,
    sleep: Sleeper = time.sleep,
    reproduction_command: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Resolve one game's species to cached Wikipedia text and write an audit."""
    try:
        species = _load_species_sources(game, normalized_database, species_ids)
    except WikimediaResolutionError as error:
        raise WikipediaResolutionError(str(error)) from error
    cache_dir = cache_root / game.dataset_version / game.game_id
    context = build_ssl_context(ca_file)
    raw_requests: list[dict[str, object]] = []

    qids = sorted({source.qid for source in species if source.qid is not None})
    entities: dict[str, Mapping[str, Any]] = {}
    sitelink_evidence: dict[str, str] = {}
    for batch in _batches(qids, SITELINK_BATCH_SIZE):
        payload, evidence = _request(
            sleep=sleep,
            endpoint=WIKIDATA_API_URL,
            parameters={
                "action": "wbgetentities",
                "format": "json",
                "formatversion": "2",
                "ids": "|".join(batch),
                "props": "sitelinks",
                "sitefilter": WIKIPEDIA_SITE,
            },
            source="wikidata-sitelinks",
            cache_dir=cache_dir,
            context=context,
            fetch_json=fetch_json,
            clock=clock,
            refresh=refresh,
        )
        raw_requests.append(evidence)
        raw_entities = payload.get("entities")
        if not isinstance(raw_entities, Mapping):
            raise WikipediaResolutionError("Wikidata response has no entities object")
        for qid in batch:
            entity = raw_entities.get(qid)
            if isinstance(entity, Mapping):
                entities[qid] = entity
            sitelink_evidence[qid] = str(evidence["sha256"])

    titles_by_qid = {
        qid: _enwiki_title(entity) for qid, entity in entities.items()
    }
    titles = sorted(
        {title for title in titles_by_qid.values() if title}, key=str.casefold
    )
    pages_by_title: dict[str, Mapping[str, Any]] = {}
    extract_evidence: dict[str, str] = {}
    for batch in _batches(titles, EXTRACT_BATCH_SIZE):
        payload, evidence = _request(
            sleep=sleep,
            endpoint=WIKIPEDIA_API_URL,
            parameters={
                "action": "query",
                "exintro": "1",
                "exlimit": str(EXTRACT_BATCH_SIZE),
                "explaintext": "1",
                "format": "json",
                "formatversion": "2",
                "inprop": "url",
                "maxlag": "5",
                "prop": "extracts|info",
                "redirects": "1",
                "titles": "|".join(batch),
            },
            source="wikipedia",
            cache_dir=cache_dir,
            context=context,
            fetch_json=fetch_json,
            clock=clock,
            refresh=refresh,
        )
        raw_requests.append(evidence)
        pages, aliases = _wikipedia_pages(payload)
        for title in batch:
            page = pages.get(_follow_alias(title, aliases))
            if page is not None:
                pages_by_title[title] = page
            extract_evidence[title] = str(evidence["sha256"])

    records: list[dict[str, object]] = []
    for source in species:
        base: dict[str, object] = {
            "species_id": source.species_id,
            "scientific_name": source.scientific_name,
            "wikidata_id": source.wikidata_id,
            "wikidata_qid": source.qid,
            "wikipedia_title": None,
            "text": None,
            "evidence": {},
        }
        if source.wikidata_id is None:
            base["status"] = "missing_wikidata_id"
        elif source.wikidata_id <= 0:
            base["status"] = "invalid_wikidata_id"
        elif source.qid not in entities or "missing" in entities[source.qid]:
            base["status"] = "wikidata_entity_missing"
            base["evidence"] = {
                "wikidata_response_sha256": sitelink_evidence.get(source.qid)
            }
        else:
            base["evidence"] = {
                "wikidata_response_sha256": sitelink_evidence[source.qid]
            }
            title = titles_by_qid.get(source.qid)
            if not title:
                base["status"] = "missing_sitelink"
            else:
                base["wikipedia_title"] = title
                evidence = dict(base["evidence"])  # type: ignore[arg-type]
                evidence["wikipedia_response_sha256"] = extract_evidence[title]
                base["evidence"] = evidence
                page = pages_by_title.get(title)
                if page is None:
                    base["status"] = "wikipedia_page_missing"
                else:
                    status, text = _extract_record(page)
                    base["status"] = status
                    base["text"] = text
        records.append(base)

    counts = Counter(str(record["status"]) for record in records)
    unknown_statuses = sorted(set(counts) - STATUS_DEFINITIONS.keys())
    if unknown_statuses:
        raise WikipediaResolutionError(
            f"unknown resolution statuses: {unknown_statuses}"
        )
    manifest: dict[str, Any] = {
        "schema_version": WIKIPEDIA_MANIFEST_SCHEMA_VERSION,
        "resolver_version": WIKIPEDIA_RESOLVER_VERSION,
        "generated_at": clock().astimezone(UTC).isoformat(),
        "dataset_version": game.dataset_version,
        "game_id": game.game_id,
        "game_sha256": _game_digest(game),
        "species_count": len(species),
        "game_species_count": len(_game_species_ids(game)),
        "source": {
            "normalized_database": str(normalized_database),
            "normalized_database_sha256": sha256_file(normalized_database),
            "wikidata_api": WIKIDATA_API_URL,
            "wikipedia_api": WIKIPEDIA_API_URL,
        },
        "configuration": {
            "sitelink_batch_size": SITELINK_BATCH_SIZE,
            "extract_batch_size": EXTRACT_BATCH_SIZE,
            "max_extract_characters": MAX_EXTRACT_CHARACTERS,
            "site": WIKIPEDIA_SITE,
            "refresh": refresh,
            "species_scope": "game" if species_ids is None else "subset",
        },
        "text_license": {
            "name": TEXT_LICENSE_NAME,
            "url": TEXT_LICENSE_URL,
        },
        "status_definitions": STATUS_DEFINITIONS,
        "status_counts": dict(sorted(counts.items())),
        "raw_requests": raw_requests,
        "records": records,
        "review_status": "attribution-required-before-promotion",
        "reproduction_command": reproduction_command,
    }
    if species_ids is None:
        manifest_name = "manifest.json"
    else:
        scope_digest = hashlib.sha256(
            ",".join(str(source.species_id) for source in species).encode("ascii")
        ).hexdigest()[:16]
        manifest_name = f"manifest-subset-{scope_digest}.json"
    manifest_path = cache_dir / manifest_name
    _atomic_json(manifest_path, manifest)
    return manifest_path, manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve one game's species to cached Wikipedia descriptions."
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
        f"phylogenomica-resolve-wikipedia {args.game} "
        f"--normalized-dir {args.normalized_dir} --cache-root {args.cache_root}"
        f" --transport {args.transport}"
        + (" --refresh" if args.refresh else "")
    )
    try:
        game = load_game(args.game)
        manifest_path, manifest = resolve_game_wikipedia(
            game,
            normalized_database=args.normalized_dir / DATABASE_FILENAME,
            cache_root=args.cache_root,
            ca_file=args.ca_file,
            refresh=args.refresh,
            fetch_json=_fetch_json_curl if args.transport == "curl" else _fetch_json,
            reproduction_command=command,
        )
    except (GameGenerationError, WikipediaResolutionError) as error:
        raise SystemExit(str(error)) from error
    counts = ", ".join(
        f"{status}={count}" for status, count in manifest["status_counts"].items()
    )
    print(f"wrote {manifest_path} ({counts})")


if __name__ == "__main__":
    main()
