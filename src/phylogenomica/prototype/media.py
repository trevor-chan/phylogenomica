"""Background orchestration for opt-in prototype media downloads."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from threading import Condition, Thread
from typing import Any, Literal

from phylogenomica.data.wikimedia import (
    DEFAULT_CACHE_ROOT,
    WikimediaResolutionError,
    _fetch_json,
    _fetch_json_curl,
    resolve_game_wikimedia,
)
from phylogenomica.data.wikimedia_download import (
    _fetch_binary,
    _fetch_binary_curl,
)
from phylogenomica.data.wikimedia_library import (
    DEFAULT_LIBRARY_ROOT,
    WikimediaLibrary,
    WikimediaLibraryError,
    load_wikimedia_library,
    update_wikimedia_library,
)
from phylogenomica.generation.game import GeneratedGame

MediaState = Literal["idle", "queued", "resolving", "downloading", "ready", "error"]
Resolver = Callable[..., tuple[Path, dict[str, Any]]]
Updater = Callable[..., tuple[Path, dict[str, Any]]]
LibraryLoader = Callable[..., WikimediaLibrary]


class BackgroundMediaDownloader:
    """Resolve and download the latest requested game's missing media.

    A single daemon worker serializes writes to the dataset library. Requests
    coalesce while work is in flight: after the current network operation, the
    newest game is processed next. Gameplay never waits for this worker.
    """

    def __init__(
        self,
        *,
        normalized_database: Path,
        initial_library: WikimediaLibrary | None = None,
        cache_root: Path = DEFAULT_CACHE_ROOT,
        library_root: Path = DEFAULT_LIBRARY_ROOT,
        ca_file: Path | None = None,
        transport: Literal["urllib", "curl"] = "urllib",
        resolver: Resolver = resolve_game_wikimedia,
        updater: Updater = update_wikimedia_library,
        library_loader: LibraryLoader = load_wikimedia_library,
    ) -> None:
        self.normalized_database = normalized_database
        self.cache_root = cache_root
        self.library_root = library_root
        self.ca_file = ca_file
        self.transport = transport
        self._resolver = resolver
        self._updater = updater
        self._library_loader = library_loader
        self._condition = Condition()
        self._library = initial_library
        self._pending: GeneratedGame | None = None
        self._requested_game_id: str | None = None
        self._state: MediaState = "idle"
        self._error: str | None = None
        self._revision = 0
        self._closed = False
        self._thread = Thread(
            target=self._run,
            name="phylogenomica-media",
            daemon=True,
        )
        self._thread.start()

    @property
    def library(self) -> WikimediaLibrary | None:
        with self._condition:
            return self._library

    def request(self, game: GeneratedGame) -> None:
        """Queue a game without blocking its session startup."""
        with self._condition:
            if self._closed:
                return
            self._pending = game
            self._requested_game_id = game.game_id
            self._state = "queued"
            self._error = None
            self._condition.notify_all()

    def close(self) -> None:
        """Stop accepting work; an in-flight network call remains daemonized."""
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify_all()
        self._thread.join(timeout=2)

    def wait(self, game_id: str, timeout: float = 5) -> MediaState:
        """Wait for one request to finish; intended for deterministic tests."""
        with self._condition:
            self._condition.wait_for(
                lambda: (
                    self._requested_game_id == game_id
                    and self._state in {"ready", "error"}
                ),
                timeout=timeout,
            )
            return self._state

    def player_status(
        self, game: GeneratedGame, current_stage_index: int
    ) -> dict[str, object]:
        """Return stage-scoped progress without exposing future species IDs."""
        with self._condition:
            library = self._library
            state: MediaState = (
                self._state
                if self._requested_game_id == game.game_id
                else "idle"
            )
            revision = self._revision
            failed = state == "error"
        if current_stage_index >= len(game.stages):
            stage_ids: tuple[int, ...] = ()
        else:
            stage_ids = tuple(
                member.species_id for member in game.stages[current_stage_index].members
            )
        available = sum(
            library is not None and library.asset(species_id) is not None
            for species_id in stage_ids
        )
        return {
            "enabled": True,
            "state": state,
            "available_count": available,
            "total_count": len(stage_ids),
            "revision": revision,
            "failed": failed,
        }

    def _set_state(
        self, game_id: str, state: MediaState, error: str | None = None
    ) -> None:
        with self._condition:
            if self._requested_game_id == game_id:
                self._state = state
                self._error = error
                self._condition.notify_all()

    def _publish(self, game_id: str, library: WikimediaLibrary) -> None:
        with self._condition:
            self._library = library
            self._revision += 1
            if self._requested_game_id == game_id:
                self._condition.notify_all()

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._closed or self._pending is not None
                )
                if self._closed:
                    return
                game = self._pending
                self._pending = None
            if game is None:  # pragma: no cover - condition invariant
                continue
            try:
                self._process(game)
            except (WikimediaResolutionError, WikimediaLibraryError, OSError) as error:
                self._set_state(game.game_id, "error", str(error))

    def _process(self, game: GeneratedGame) -> None:
        all_ids = tuple(
            dict.fromkeys(
                member.species_id
                for stage in game.stages
                for member in stage.members
            )
        )
        library = self.library
        missing_ids = tuple(
            species_id
            for species_id in all_ids
            if library is None or library.asset(species_id) is None
        )
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
            species_id
            for species_id in resolved_ids
            if isinstance(species_id, int)
        }
        if not resolved_ids:
            self._set_state(game.game_id, "ready")
            return

        first_stage_ids = {
            member.species_id for member in game.stages[0].members
        }
        priority_ids = resolved_ids & first_stage_ids
        remaining_ids = resolved_ids - priority_ids
        self._set_state(game.game_id, "downloading")
        for selected_ids in (priority_ids, remaining_ids):
            if not selected_ids:
                continue
            with self._condition:
                if self._closed:
                    return
            library_manifest_path, _ = self._updater(
                manifest_path,
                library_root=self.library_root,
                ca_file=self.ca_file,
                species_ids=selected_ids,
                fetch_binary=(
                    _fetch_binary_curl
                    if self.transport == "curl"
                    else _fetch_binary
                ),
            )
            updated = self._library_loader(
                library_manifest_path,
                expected_dataset_version=game.dataset_version,
            )
            self._publish(game.game_id, updated)
        self._set_state(game.game_id, "ready")
