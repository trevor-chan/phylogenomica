"""Build a local visual-review page for downloaded Wikimedia working assets."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from phylogenomica.data.onezoom_download import sha256_file
from phylogenomica.data.wikimedia_download import (
    WIKIMEDIA_DOWNLOAD_MANIFEST_SCHEMA_VERSION,
    WikimediaDownloadError,
)
from phylogenomica.data.wikimedia_rights import (
    WIKIMEDIA_RIGHTS_MANIFEST_SCHEMA_VERSION,
    WIKIMEDIA_RIGHTS_POLICY_VERSION,
)

WIKIMEDIA_REVIEW_PAGE_VERSION = 2


class WikimediaReviewError(RuntimeError):
    """Raised when a review page cannot be built from verified working assets."""


def _safe_relative_path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise WikimediaReviewError("download record has no local path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise WikimediaReviewError(f"unsafe asset path: {value!r}")
    return path


def _read_download_manifest(path: Path) -> Mapping[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WikimediaReviewError(f"invalid download manifest: {path}") from error
    if not isinstance(manifest, Mapping):
        raise WikimediaReviewError("download manifest is not an object")
    if (
        manifest.get("schema_version")
        != WIKIMEDIA_DOWNLOAD_MANIFEST_SCHEMA_VERSION
    ):
        raise WikimediaReviewError(
            f"unsupported download manifest schema: {manifest.get('schema_version')!r}"
        )
    records = manifest.get("records")
    if not isinstance(records, Sequence) or isinstance(records, str):
        raise WikimediaReviewError("download manifest has no records array")
    return manifest


def _verified_records(
    manifest_path: Path, manifest: Mapping[str, Any]
) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    seen_species: set[int] = set()
    for raw_record in manifest["records"]:
        if not isinstance(raw_record, Mapping):
            raise WikimediaReviewError("download record is not an object")
        species_id = raw_record.get("species_id")
        if not isinstance(species_id, int) or species_id <= 0:
            raise WikimediaReviewError("download record has an invalid species ID")
        if species_id in seen_species:
            raise WikimediaReviewError(f"duplicate species ID: {species_id}")
        seen_species.add(species_id)
        relative_path = _safe_relative_path(raw_record.get("local_path"))
        asset_path = manifest_path.parent / relative_path
        if not asset_path.is_file():
            raise WikimediaReviewError(f"working asset does not exist: {asset_path}")
        expected_checksum = raw_record.get("sha256")
        if not isinstance(expected_checksum, str) or (
            sha256_file(asset_path) != expected_checksum
        ):
            raise WikimediaReviewError(
                f"working asset checksum differs for species {species_id}"
            )
        expected_bytes = raw_record.get("bytes")
        if (
            not isinstance(expected_bytes, int)
            or asset_path.stat().st_size != expected_bytes
        ):
            raise WikimediaReviewError(
                f"working asset size differs for species {species_id}"
            )
        verified.append(dict(raw_record))
    verified.sort(key=lambda record: int(record["species_id"]))
    return verified


def _read_rights_manifest(
    path: Path, *, download_manifest_sha256: str
) -> Mapping[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WikimediaReviewError(f"invalid rights manifest: {path}") from error
    if not isinstance(manifest, Mapping):
        raise WikimediaReviewError("rights manifest is not an object")
    if manifest.get("schema_version") != WIKIMEDIA_RIGHTS_MANIFEST_SCHEMA_VERSION:
        raise WikimediaReviewError(
            f"unsupported rights manifest schema: {manifest.get('schema_version')!r}"
        )
    if manifest.get("rights_policy_version") != WIKIMEDIA_RIGHTS_POLICY_VERSION:
        raise WikimediaReviewError(
            f"unsupported rights policy: {manifest.get('rights_policy_version')!r}"
        )
    source = manifest.get("source")
    if not isinstance(source, Mapping) or (
        source.get("download_manifest_sha256") != download_manifest_sha256
    ):
        raise WikimediaReviewError("rights manifest does not match download manifest")
    records = manifest.get("records")
    if not isinstance(records, Sequence) or isinstance(records, str):
        raise WikimediaReviewError("rights manifest has no records array")
    return manifest


def _attach_rights(
    records: list[dict[str, Any]], rights_manifest: Mapping[str, Any]
) -> list[dict[str, Any]]:
    by_species: dict[int, Mapping[str, Any]] = {}
    for rights in rights_manifest["records"]:
        if not isinstance(rights, Mapping):
            raise WikimediaReviewError("rights record is not an object")
        species_id = rights.get("species_id")
        if not isinstance(species_id, int) or species_id in by_species:
            raise WikimediaReviewError("rights manifest has invalid species IDs")
        by_species[species_id] = rights
    if set(by_species) != {int(record["species_id"]) for record in records}:
        raise WikimediaReviewError("rights and download species sets differ")
    result: list[dict[str, Any]] = []
    for record in records:
        species_id = int(record["species_id"])
        rights = by_species[species_id]
        if rights.get("sha256") != record.get("sha256"):
            raise WikimediaReviewError(
                f"rights record checksum differs for species {species_id}"
            )
        result.append({**record, "rights": dict(rights)})
    return result


def _embedded_json(payload: Mapping[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return (
        rendered.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def _review_html(payload: Mapping[str, Any]) -> str:
    data = _embedded_json(payload)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Phylogenomica Wikimedia review</title>
  <style>
    :root {{ color-scheme: light; font-family: Georgia, serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #f4f0e7; color: #26271f; }}
    header {{ position: sticky; top: 0; z-index: 2; padding: 1rem 1.25rem;
      background: #233b2d; color: white; box-shadow: 0 2px 8px #0004; }}
    h1 {{ margin: 0 0 .35rem; font-size: 1.35rem; }}
    header p {{ margin: .25rem 0; }}
    .tools {{ display: flex; gap: .65rem; flex-wrap: wrap; align-items: center;
      margin-top: .7rem; }}
    button, select, input {{ font: inherit; }}
    button {{ padding: .45rem .75rem; cursor: pointer; }}
    main {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 1rem; padding: 1rem; }}
    article {{ background: white; border: 2px solid #d1cab9; border-radius: .55rem;
      overflow: hidden; box-shadow: 0 2px 6px #0002; }}
    article[data-decision="accept"] {{ border-color: #348354; }}
    article[data-decision="conditional"] {{ border-color: #9b7229; }}
    article[data-decision="reject"] {{ border-color: #b44a45; }}
    article[data-decision="alternate"] {{ border-color: #b4872d; }}
    img {{ display: block; width: 100%; height: 220px; object-fit: contain;
      background: #e8e5dc; }}
    .body {{ padding: .75rem; }}
    h2 {{ margin: 0; font-size: 1.05rem; }}
    .scientific {{ margin: .2rem 0 .65rem; font-style: italic; }}
    dl {{ display: grid; grid-template-columns: max-content 1fr; gap: .25rem .5rem;
      margin: .5rem 0; font-size: .84rem; }}
    dt {{ font-weight: bold; }} dd {{ margin: 0; overflow-wrap: anywhere; }}
    .warning {{ color: #9a3f2f; font-weight: bold; }}
    details {{ margin-top: .55rem; font-size: .84rem; }}
    details ul {{ margin: .35rem 0 0; padding-left: 1.1rem; }}
    .decision {{ display: grid; gap: .4rem; margin-top: .7rem; }}
    .decision input, .decision select {{ width: 100%; padding: .35rem; }}
    a {{ color: #315c87; }}
  </style>
</head>
<body>
  <header>
    <h1>Wikimedia candidate review</h1>
    <p id="summary"></p>
    <p>Decisions stay in this browser until exported.
      Nothing here promotes an asset.</p>
    <div class="tools">
      <label>Show <select id="filter">
        <option value="all">all</option><option value="pending">pending</option>
        <option value="accept">accepted</option><option value="reject">rejected</option>
        <option value="conditional">conditional</option>
        <option value="alternate">needs alternate</option>
      </select></label>
      <button id="export" type="button">Export review JSON</button>
      <button id="clear" type="button">Clear saved decisions</button>
    </div>
  </header>
  <main id="cards"></main>
  <script id="review-data" type="application/json">{data}</script>
  <script>
    "use strict";
    const payload = JSON.parse(document.getElementById("review-data").textContent);
    const storageKey = `phylogenomica-review-${{payload.dataset_version}}-` +
      `${{payload.game_id}}-${{payload.download_manifest_sha256}}-` +
      `${{payload.rights_manifest_sha256}}`;
    let saved = JSON.parse(localStorage.getItem(storageKey) || "{{}}");
    const cards = document.getElementById("cards");
    const filter = document.getElementById("filter");
    const element = (tag, text, className) => {{
      const node = document.createElement(tag);
      if (text !== undefined && text !== null) node.textContent = String(text);
      if (className) node.className = className;
      return node;
    }};
    const setSummary = () => {{
      const counts = {{
        pending: 0, accept: 0, conditional: 0, reject: 0, alternate: 0
      }};
      payload.records.forEach(record => {{
        counts[(saved[record.species_id] || {{
          decision: record.rights.suggested_review_decision
        }}).decision]++;
      }});
      document.getElementById("summary").textContent =
        `${{payload.records.length}} candidates · ${{counts.accept}} accepted · ` +
        `${{counts.conditional}} conditional · ` +
        `${{counts.reject}} rejected · ${{counts.alternate}} need alternates · ` +
        `${{counts.pending}} pending`;
    }};
    const persist = () => {{
      localStorage.setItem(storageKey, JSON.stringify(saved));
      setSummary();
    }};
    const addPair = (list, term, value, className) => {{
      list.append(element("dt", term));
      list.append(element("dd", value ?? "—", className));
    }};
    payload.records.forEach(record => {{
      const article = element("article");
      article.dataset.speciesId = record.species_id;
      const current = saved[record.species_id] || {{
        decision: record.rights.suggested_review_decision, notes: ""
      }};
      article.dataset.decision = current.decision;
      const image = element("img");
      image.src = record.local_path; image.alt = record.scientific_name;
      article.append(image);
      const body = element("div", null, "body");
      body.append(element("h2", record.scientific_name));
      body.append(element("div", `OneZoom species ${{record.species_id}}`,
        "scientific"));
      const details = element("dl");
      addPair(details, "File", record.commons_title);
      addPair(details, "Dimensions", `${{record.width}} × ${{record.height}}`);
      details.append(element("dt", "Rights"));
      if (record.rights.rights_url) {{
        const license = element("a", record.rights.identifier);
        license.href = record.rights.rights_url;
        license.target = "_blank";
        license.rel = "noopener noreferrer";
        const value = element("dd");
        value.append(license);
        details.append(value);
      }} else {{
        details.append(element("dd", `${{record.rights.identifier}} (no URL)`,
          "warning"));
      }}
      addPair(details, "Policy", record.rights.promotion_status,
        record.rights.promotion_status === "ready" ? null : "warning");
      addPair(details, "Creator", record.creator || record.credit);
      body.append(details);
      const requirements = element("details");
      requirements.append(element("summary", "Rights requirements"));
      const list = element("ul");
      record.rights.requirements.forEach(requirement => {{
        const item = element("li", requirement);
        list.append(item);
      }});
      requirements.append(list);
      body.append(requirements);
      if (record.commons_page_url) {{
        const link = element("a", "Open Commons source");
        link.href = record.commons_page_url; link.target = "_blank";
        link.rel = "noopener noreferrer"; body.append(link);
      }}
      const controls = element("div", null, "decision");
      const select = element("select");
      [["pending", "Pending"], ["accept", "Accept"],
       ["conditional", "Accept conditionally"], ["reject", "Reject"],
       ["alternate", "Find alternate"]].forEach(([value, label]) => {{
        const option = element("option", label); option.value = value;
        option.selected = current.decision === value; select.append(option);
      }});
      const notes = element("input"); notes.type = "text";
      notes.placeholder = "Review notes"; notes.value = current.notes || "";
      const update = () => {{
        saved[record.species_id] = {{
          decision: select.value, notes: notes.value.trim()
        }};
        article.dataset.decision = select.value; persist(); applyFilter();
      }};
      select.addEventListener("change", update);
      notes.addEventListener("change", update);
      controls.append(select, notes);
      body.append(controls);
      article.append(body);
      cards.append(article);
    }});
    const applyFilter = () => {{
      cards.querySelectorAll("article").forEach(card => {{
        card.hidden = filter.value !== "all" && card.dataset.decision !== filter.value;
      }});
    }};
    filter.addEventListener("change", applyFilter);
    document.getElementById("clear").addEventListener("click", () => {{
      if (confirm("Clear every saved decision for this manifest?")) {{
        localStorage.removeItem(storageKey); location.reload();
      }}
    }});
    document.getElementById("export").addEventListener("click", () => {{
      const decisions = payload.records.map(record => ({{
        species_id: record.species_id,
        scientific_name: record.scientific_name,
        commons_title: record.commons_title,
        sha256: record.sha256,
        rights_identifier: record.rights.identifier,
        rights_policy_status: record.rights.promotion_status,
        decision: (saved[record.species_id] || {{
          decision: record.rights.suggested_review_decision
        }}).decision,
        notes: (saved[record.species_id] || {{notes: ""}}).notes || ""
      }}));
      const review = {{schema_version: 1,
        review_page_version: payload.review_page_version,
        dataset_version: payload.dataset_version, game_id: payload.game_id,
        download_manifest_sha256: payload.download_manifest_sha256,
        rights_policy_version: payload.rights_policy_version,
        rights_manifest_sha256: payload.rights_manifest_sha256,
        reviewed_at: new Date().toISOString(), decisions}};
      const blob = new Blob([JSON.stringify(review, null, 2) + "\\n"],
        {{type: "application/json"}});
      const link = element("a"); link.href = URL.createObjectURL(blob);
      link.download = `wikimedia-review-${{payload.game_id.slice(0, 12)}}.json`;
      link.click(); URL.revokeObjectURL(link.href);
    }});
    setSummary(); applyFilter();
  </script>
</body>
</html>
"""


def generate_wikimedia_review(
    manifest_path: Path,
    *,
    rights_manifest_path: Path | None = None,
    output: Path | None = None,
) -> tuple[Path, int]:
    """Verify working assets and write a self-contained local review page."""
    manifest = _read_download_manifest(manifest_path)
    records = _verified_records(manifest_path, manifest)
    download_checksum = sha256_file(manifest_path)
    rights_path = rights_manifest_path or manifest_path.with_name("rights.json")
    rights_manifest = _read_rights_manifest(
        rights_path, download_manifest_sha256=download_checksum
    )
    records = _attach_rights(records, rights_manifest)
    dataset_version = manifest.get("dataset_version")
    game_id = manifest.get("game_id")
    if not isinstance(dataset_version, str) or not isinstance(game_id, str):
        raise WikimediaReviewError("download manifest lacks dataset or game identity")
    payload = {
        "review_page_version": WIKIMEDIA_REVIEW_PAGE_VERSION,
        "dataset_version": dataset_version,
        "game_id": game_id,
        "download_manifest_sha256": download_checksum,
        "rights_policy_version": rights_manifest["rights_policy_version"],
        "rights_manifest_sha256": sha256_file(rights_path),
        "records": records,
    }
    output_path = output or manifest_path.with_name("review.html")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    try:
        temporary.write_text(_review_html(payload), encoding="utf-8")
        temporary.replace(output_path)
    except OSError as error:
        raise WikimediaReviewError(f"cannot write review page: {error}") from error
    finally:
        if temporary.exists():
            temporary.unlink()
    return output_path, len(records)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a local visual-review page for Wikimedia working assets."
    )
    parser.add_argument("manifest", type=Path, help="download manifest")
    parser.add_argument("--rights", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        output, count = generate_wikimedia_review(
            args.manifest,
            rights_manifest_path=args.rights,
            output=args.output,
        )
    except (WikimediaDownloadError, WikimediaReviewError) as error:
        raise SystemExit(str(error)) from error
    print(f"wrote {output} ({count} candidates)")


if __name__ == "__main__":
    main()
