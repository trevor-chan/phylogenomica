"""Shared orchestration for optional prototype enrichment workers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from threading import Condition, Thread
from typing import Generic, TypeVar

from phylogenomica.generation.game import GeneratedGame

LibraryT = TypeVar("LibraryT")
StateT = TypeVar("StateT", bound=str)


class LatestGameWorker(ABC, Generic[LibraryT, StateT]):
    """Serialize enrichment and coalesce pending work to the newest game.

    Images and descriptions have different resolver pipelines, but their
    concurrency contract is identical: gameplay never blocks, one worker owns
    library writes, and requests received during a fetch collapse to the most
    recent game. Subclasses implement only availability and processing.
    """

    def __init__(
        self,
        *,
        initial_library: LibraryT | None,
        thread_name: str,
        idle_state: StateT,
        queued_state: StateT,
        ready_state: StateT,
        error_state: StateT,
        handled_errors: tuple[type[Exception], ...],
    ) -> None:
        self._condition = Condition()
        self._library = initial_library
        self._pending: GeneratedGame | None = None
        self._requested_game_id: str | None = None
        self._idle_state = idle_state
        self._queued_state = queued_state
        self._ready_state = ready_state
        self._error_state = error_state
        self._handled_errors = handled_errors
        self._state = idle_state
        self._error: str | None = None
        self._revision = 0
        self._closed = False
        self._thread = Thread(target=self._run, name=thread_name, daemon=True)
        self._thread.start()

    @property
    def library(self) -> LibraryT | None:
        with self._condition:
            return self._library

    def request(self, game: GeneratedGame) -> None:
        """Queue a game without blocking its session startup."""
        with self._condition:
            if self._closed:
                return
            self._pending = game
            self._requested_game_id = game.game_id
            self._state = self._queued_state
            self._error = None
            self._condition.notify_all()

    def close(self) -> None:
        """Stop accepting work; an in-flight network call remains daemonized."""
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify_all()
        self._thread.join(timeout=2)

    def wait(self, game_id: str, timeout: float = 5) -> StateT:
        """Wait for one request to finish; intended for deterministic tests."""
        with self._condition:
            self._condition.wait_for(
                lambda: (
                    self._requested_game_id == game_id
                    and self._state in {self._ready_state, self._error_state}
                ),
                timeout=timeout,
            )
            return self._state

    def player_status(
        self,
        game: GeneratedGame,
        current_stage_index: int,
        also_shown: Sequence[int] = (),
    ) -> dict[str, object]:
        """Return stage-scoped progress without exposing future species IDs."""
        with self._condition:
            library = self._library
            state = (
                self._state
                if self._requested_game_id == game.game_id
                else self._idle_state
            )
            revision = self._revision
            failed = state == self._error_state
        if current_stage_index >= len(game.stages):
            members: tuple[int, ...] = ()
        else:
            members = tuple(
                member.species_id for member in game.stages[current_stage_index].members
            )
        stage_ids = tuple(dict.fromkeys((*members, *also_shown)))
        available = sum(
            library is not None and self._has_item(library, species_id)
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

    def _missing_ids(self, game: GeneratedGame) -> tuple[int, ...]:
        """Return unique game species absent from the current library."""
        species_ids = tuple(
            dict.fromkeys(
                member.species_id
                for stage in game.stages
                for member in stage.members
            )
        )
        library = self.library
        return tuple(
            species_id
            for species_id in species_ids
            if library is None or not self._has_item(library, species_id)
        )

    def _stop_requested(self) -> bool:
        """Return whether shutdown was requested between external operations."""
        with self._condition:
            return self._closed

    def _set_state(
        self, game_id: str, state: StateT, error: str | None = None
    ) -> None:
        with self._condition:
            if self._requested_game_id == game_id:
                self._state = state
                self._error = error
                self._condition.notify_all()

    def _publish(self, game_id: str, library: LibraryT) -> None:
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
            except self._handled_errors as error:
                self._set_state(game.game_id, self._error_state, str(error))

    @abstractmethod
    def _has_item(self, library: LibraryT, species_id: int) -> bool:
        """Return whether the library contains one species enrichment."""

    @abstractmethod
    def _process(self, game: GeneratedGame) -> None:
        """Resolve and publish one requested game."""
