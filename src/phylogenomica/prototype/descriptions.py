"""Background orchestration for opt-in prototype description downloads.

This is the text counterpart of :mod:`phylogenomica.prototype.media`. Missing
descriptions are resolved for a whole game at once: unlike images, text is
small enough that there is nothing to gain from prioritizing the open stage.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from phylogenomica.data.wikimedia import _fetch_json, _fetch_json_curl
from phylogenomica.data.wikipedia import (
    DEFAULT_CACHE_ROOT,
    WikipediaResolutionError,
    resolve_game_wikipedia,
)
from phylogenomica.data.wikipedia_library import (
    DEFAULT_LIBRARY_ROOT,
    WikipediaLibrary,
    WikipediaLibraryError,
    load_wikipedia_library,
    update_wikipedia_library,
)
from phylogenomica.generation.game import GeneratedGame
from phylogenomica.prototype.background import LatestGameWorker

DescriptionState = Literal["idle", "queued", "resolving", "ready", "error"]
Resolver = Callable[..., tuple[Path, dict[str, Any]]]
Updater = Callable[..., tuple[Path, dict[str, Any]]]
LibraryLoader = Callable[..., WikipediaLibrary]


class BackgroundDescriptionDownloader(
    LatestGameWorker[WikipediaLibrary, DescriptionState]
):
    """Resolve the latest requested game's missing descriptions.

    A single daemon worker serializes writes to the dataset library. Requests
    coalesce while work is in flight: after the current network operation, the
    newest game is processed next. Gameplay never waits for this worker.
    """

    def __init__(
        self,
        *,
        normalized_database: Path,
        initial_library: WikipediaLibrary | None = None,
        cache_root: Path = DEFAULT_CACHE_ROOT,
        library_root: Path = DEFAULT_LIBRARY_ROOT,
        ca_file: Path | None = None,
        transport: Literal["urllib", "curl"] = "urllib",
        resolver: Resolver = resolve_game_wikipedia,
        updater: Updater = update_wikipedia_library,
        library_loader: LibraryLoader = load_wikipedia_library,
    ) -> None:
        self.normalized_database = normalized_database
        self.cache_root = cache_root
        self.library_root = library_root
        self.ca_file = ca_file
        self.transport = transport
        self._resolver = resolver
        self._updater = updater
        self._library_loader = library_loader
        super().__init__(
            initial_library=initial_library,
            thread_name="phylogenomica-descriptions",
            idle_state="idle",
            queued_state="queued",
            ready_state="ready",
            error_state="error",
            handled_errors=(
                WikipediaResolutionError,
                WikipediaLibraryError,
                OSError,
            ),
        )

    def _has_item(self, library: WikipediaLibrary, species_id: int) -> bool:
        return library.description(species_id) is not None

    def _process(self, game: GeneratedGame) -> None:
        missing_ids = self._missing_ids(game)
        if not missing_ids:
            self._set_state(game.game_id, "ready")
            return

        self._set_state(game.game_id, "resolving")
        manifest_path, resolver_manifest = self._resolver(
            game,
            normalized_database=self.normalized_database,
            cache_root=self.cache_root,
            ca_file=self.ca_file,
            species_ids=missing_ids,
            fetch_json=(
                _fetch_json_curl if self.transport == "curl" else _fetch_json
            ),
        )
        resolved_ids = {
            record.get("species_id")
            for record in resolver_manifest.get("records", [])
            if isinstance(record, Mapping) and record.get("status") == "resolved"
        }
        resolved_ids = {
            species_id for species_id in resolved_ids if isinstance(species_id, int)
        }
        if not resolved_ids:
            self._set_state(game.game_id, "ready")
            return
        if self._stop_requested():
            return
        library_manifest_path, _ = self._updater(
            manifest_path,
            library_root=self.library_root,
            species_ids=resolved_ids,
        )
        updated = self._library_loader(
            library_manifest_path,
            expected_dataset_version=game.dataset_version,
        )
        self._publish(game.game_id, updated)
        self._set_state(game.game_id, "ready")
