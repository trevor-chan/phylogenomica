"""A local, dependency-free browser prototype over the gameplay engine.

The page is a renderer. Every guess is resolved by
:mod:`phylogenomica.gameplay.engine` on the server, and the browser draws the
returned transition without recomputing correctness.

The API is stage-scoped: it serves only the cards of the open stage, and it
reveals a card's tier and role only once that card has been placed. The
concealed target and the answer to the open stage therefore never cross the
wire early, even to a player reading the network traffic. Guided difficulty
reveals the target deliberately, and only the target: the answer to the open
stage stays concealed exactly as it is in expert play.
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from phylogenomica.data.onezoom_ingest import DATABASE_FILENAME
from phylogenomica.data.wikimedia import DEFAULT_CACHE_ROOT
from phylogenomica.data.wikimedia_library import (
    DEFAULT_LIBRARY_ROOT,
    WikimediaLibrary,
    WikimediaLibraryError,
    load_wikimedia_library,
)
from phylogenomica.gameplay.engine import (
    DEFAULT_DIFFICULTY,
    DIFFICULTIES,
    Difficulty,
    GameplayError,
    GameState,
    GuessOutcome,
    PlacedSpecies,
    apply_guess,
    best_achievable_score,
    dealt_members,
    initial_state,
    maximum_score,
    parse_difficulty,
    revealed_target,
    score,
    selectable_members,
    stage_at_stake,
)
from phylogenomica.generation.eligibility import (
    ELIGIBILITY_DATABASE_FILENAME,
    ELIGIBILITY_INDEX_VERSION,
    TargetEligibilityError,
    TargetEligibilityIndex,
)
from phylogenomica.generation.feasibility import FeasibilityConfig
from phylogenomica.generation.game import (
    GameGenerationError,
    GameMember,
    GeneratedGame,
    generate_game,
    load_game,
)
from phylogenomica.generation.selection import RelativeSelectionError
from phylogenomica.prototype.media import BackgroundMediaDownloader
from phylogenomica.tree.preprocess import (
    DEFAULT_NORMALIZED_DIR,
    TREE_DATABASE_FILENAME,
    TREE_SCHEMA_VERSION,
)

PAGE_PATH = Path(__file__).with_name("index.html")
MAX_REQUEST_BYTES = 4096


@dataclass
class PrototypeSession:
    """One single-player session with a source for subsequent games."""

    game: GeneratedGame
    state: GameState
    next_game: Callable[[GeneratedGame], GeneratedGame]
    difficulty: Difficulty = DEFAULT_DIFFICULTY
    review_stage_index: int | None = None

    @classmethod
    def start(
        cls,
        game: GeneratedGame,
        next_game: Callable[[GeneratedGame], GeneratedGame],
        difficulty: Difficulty = DEFAULT_DIFFICULTY,
    ) -> PrototypeSession:
        difficulty = parse_difficulty(difficulty)
        return cls(
            game=game,
            state=initial_state(game, difficulty),
            next_game=next_game,
            difficulty=difficulty,
        )

    def play_again(self) -> None:
        self.game = self.next_game(self.game)
        self.state = initial_state(self.game, self.difficulty)
        self.review_stage_index = None

    def set_difficulty(self, difficulty: Difficulty) -> None:
        """Restart the current target under another difficulty.

        A difficulty decides which cards a stage deals, so it cannot change
        under a position that was reached without them. Restarting the same
        target rather than generating a new one keeps the switch instant and
        lets a player retry a lineage they have just seen.
        """
        self.difficulty = parse_difficulty(difficulty)
        self.state = initial_state(self.game, self.difficulty)
        self.review_stage_index = None

    def continue_stage(self) -> None:
        if self.review_stage_index is None:
            raise GameplayError("there is no completed stage to continue")
        self.review_stage_index = None

    def guess(self, species_id: int) -> GuessOutcome:
        if self.review_stage_index is not None:
            raise GameplayError("continue to the next stage before guessing")
        self.state, outcome = apply_guess(self.game, self.state, species_id)
        if outcome.stage_completed and not outcome.game_completed:
            self.review_stage_index = outcome.stage_index
        return outcome


def _members_by_id(game: GeneratedGame) -> dict[int, GameMember]:
    return {
        member.species_id: member
        for stage in game.stages
        for member in stage.members
    }


def _card(
    member: GameMember,
    placed: PlacedSpecies | None,
    media_library: WikimediaLibrary | None,
    *,
    selectable: bool = True,
) -> dict[str, object]:
    """Render one card, withholding the answer until the card is placed.

    A dealt but unselectable card is guided play's revealed target: its role is
    what the player has been told, so it travels with the card rather than
    waiting for a placement that only the end of the game will produce.
    """
    card = member.card
    asset = (
        None if media_library is None else media_library.asset(member.species_id)
    )
    payload: dict[str, object] = {
        "species_id": member.species_id,
        "english_name": card.english_name,
        "scientific_name": card.scientific_name,
        "image_url": (
            None if asset is None else f"/media/{member.species_id}"
        ),
        "rights": None if asset is None else asset.attribution_text,
        "license": None if asset is None else asset.license_name,
    }
    if asset is not None:
        payload["image_attribution"] = {
            "text": asset.attribution_text,
            "source_url": asset.commons_page_url,
            "rights_url": asset.rights_url,
        }
    if placed is None and not selectable:
        payload["state"] = "revealed"
        payload["role"] = member.role
    elif placed is None:
        payload["state"] = "active"
    else:
        # Tier and role are the answer, so they travel only after placement.
        payload["state"] = placed.placement
        payload["tier_index"] = placed.tier_index
        payload["role"] = placed.role
    payload["selectable"] = selectable and payload["state"] == "active"
    return payload


def _lineage(
    game: GeneratedGame,
    state: GameState,
    final_visible_stage: int | None = None,
    difficulty: Difficulty = DEFAULT_DIFFICULTY,
) -> list[dict[str, object]]:
    """Render placed species into fixed anonymous slots, root to target.

    Slots reserve the geometry of the cards actually in play. A tier this
    difficulty does not deal is not a slot the player can ever fill, so it is
    left out of the tree rather than drawn as an unreachable blank.
    """
    members = _members_by_id(game)
    placements: dict[tuple[int, int | None], list[PlacedSpecies]] = {}
    for placed in state.placements:
        placements.setdefault((placed.stage_index, placed.tier_index), []).append(
            placed
        )

    if final_visible_stage is None:
        final_visible_stage = (
            len(game.stages) - 1 if state.completed else state.current_stage_index
        )
    lineage: list[dict[str, object]] = []
    for stage in game.stages[: final_visible_stage + 1]:
        dealt_ids = {
            member.species_id for member in dealt_members(stage, difficulty)
        }
        rendered_tiers: list[dict[str, object]] = []
        for tier in stage.tiers:
            dealt_slots = [
                species_id
                for species_id in tier.species_ids
                if species_id in dealt_ids
            ]
            if not dealt_slots:
                continue
            placed_at_tier = placements.get(
                (stage.stage_index, tier.tier_index), []
            )
            placed_by_id = {placed.species_id: placed for placed in placed_at_tier}
            rendered_species: list[dict[str, object]] = []
            for slot_index, species_id in enumerate(dealt_slots):
                placed = placed_by_id.get(species_id)
                if placed is None:
                    continue
                card = members[species_id].card
                rendered_species.append(
                    {
                        "species_id": species_id,
                        "english_name": card.english_name,
                        "scientific_name": card.scientific_name,
                        "role": placed.role,
                        "placement": placed.placement,
                        "slot_index": slot_index,
                    }
                )
            # Geometry and divergence age describe the branching event itself,
            # so both travel from stage opening: the age teaches the shape of
            # the clade without naming it. The clade name is the answer in
            # words and stays hidden until a species populates the event.
            populated = bool(rendered_species)
            rendered_tiers.append(
                {
                    "tier_index": tier.tier_index,
                    "slot_count": len(dealt_slots),
                    "age_ma": tier.age_ma,
                    "clade_name": tier.clade_name if populated else None,
                    "populated": populated,
                    "species": rendered_species,
                }
            )

        rendered_target: list[dict[str, object]] = []
        for placed in placements.get((stage.stage_index, None), []):
            card = members[placed.species_id].card
            rendered_target.append(
                {
                    "species_id": placed.species_id,
                    "english_name": card.english_name,
                    "scientific_name": card.scientific_name,
                    "role": placed.role,
                    "placement": placed.placement,
                }
            )
        lineage.append(
            {
                "stage_index": stage.stage_index,
                "tiers": rendered_tiers,
                "target": rendered_target,
            }
        )
    return lineage


def build_view(
    game: GeneratedGame,
    state: GameState,
    media_library: WikimediaLibrary | None = None,
    review_stage_index: int | None = None,
) -> dict[str, object]:
    """Render everything the page may know at this moment."""
    difficulty = state.difficulty
    placed_by_id = {placed.species_id: placed for placed in state.placements}
    visible_stage_index = (
        review_stage_index
        if review_stage_index is not None
        else state.current_stage_index
    )
    open_stage = (
        None
        if state.completed
        else game.stages[visible_stage_index]
    )
    if open_stage is None:
        cards: list[dict[str, object]] = []
    else:
        selectable_ids = {
            member.species_id
            for member in selectable_members(open_stage, difficulty)
        }
        cards = [
            _card(
                member,
                placed_by_id.get(member.species_id),
                media_library,
                selectable=member.species_id in selectable_ids,
            )
            for member in dealt_members(open_stage, difficulty)
        ]
    target = revealed_target(game, difficulty)
    return {
        "difficulty": difficulty,
        "target": (
            None
            if target is None
            else _card(target, None, media_library, selectable=False)
        ),
        "stage_index": visible_stage_index,
        "stage_count": len(game.stages),
        "is_ultimate": (
            open_stage is not None
            and open_stage.stage_index == len(game.stages) - 1
        ),
        "cards": cards,
        "score": score(game, state),
        "stage_at_stake": (
            0 if review_stage_index is not None else stage_at_stake(game, state)
        ),
        "best_achievable": best_achievable_score(game, state),
        "maximum": maximum_score(game, difficulty),
        "stage_scores": list(state.stage_scores),
        "completed": state.completed,
        "reviewing_stage": review_stage_index is not None,
        "lineage": _lineage(game, state, review_stage_index, difficulty),
    }


def _outcome_payload(outcome: GuessOutcome) -> dict[str, object]:
    return {
        "species_id": outcome.species_id,
        "role": outcome.role,
        "penalty": outcome.penalty,
        "bonus": outcome.bonus,
        "placed": [
            {"species_id": placed.species_id, "placement": placed.placement}
            for placed in outcome.placed
        ],
        "remaining": len(outcome.remaining_species_ids),
        "stage_completed": outcome.stage_completed,
        "stage_score": outcome.stage_score,
        "game_completed": outcome.game_completed,
    }


class _Handler(BaseHTTPRequestHandler):
    server_version = "phylogenomica-prototype"

    @property
    def _session(self) -> PrototypeSession:
        return self.server.session  # type: ignore[attr-defined]

    @property
    def _media_library(self) -> WikimediaLibrary | None:
        downloader = self._media_downloader
        if downloader is not None:
            return downloader.library
        return self.server.media_library  # type: ignore[attr-defined]

    @property
    def _media_downloader(self) -> BackgroundMediaDownloader | None:
        return self.server.media_downloader  # type: ignore[attr-defined]

    def _view(self) -> dict[str, object]:
        view = build_view(
            self._session.game,
            self._session.state,
            self._media_library,
            self._session.review_stage_index,
        )
        if self._media_downloader is not None:
            target = view.get("target")
            view["media_download"] = self._media_downloader.player_status(
                self._session.game,
                int(view["stage_index"]),
                also_shown=(
                    () if target is None else (int(target["species_id"]),)  # type: ignore[index]
                ),
            )
        return view

    def log_message(self, *_: object) -> None:  # pragma: no cover - quiet server
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: Mapping[str, object]) -> None:
        self._send(
            status,
            json.dumps(payload).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self._send(
                200, PAGE_PATH.read_bytes(), "text/html; charset=utf-8"
            )
        elif path == "/api/view":
            self._send_json(200, self._view())
        elif path.startswith("/media/"):
            try:
                species_id = int(path.removeprefix("/media/"))
            except ValueError:
                self._send_json(404, {"error": "not found"})
                return
            asset = (
                None
                if self._media_library is None
                else self._media_library.asset(species_id)
            )
            if asset is None:
                self._send_json(404, {"error": "not found"})
                return
            self._send(200, asset.path.read_bytes(), asset.mime_type)
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        if self.path == "/api/difficulty":
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_REQUEST_BYTES:
                self._send_json(413, {"error": "request too large"})
                return
            try:
                request = json.loads(self.rfile.read(length) or b"{}")
                difficulty = request["difficulty"]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "expected a difficulty"})
                return
            try:
                self._session.set_difficulty(difficulty)
            except GameplayError as error:
                self._send_json(409, {"error": str(error)})
                return
            self._send_json(200, {"view": self._view()})
            return
        if self.path == "/api/continue":
            try:
                self._session.continue_stage()
            except GameplayError as error:
                self._send_json(409, {"error": str(error)})
                return
            self._send_json(200, {"view": self._view()})
            return
        if self.path == "/api/play-again":
            try:
                self._session.play_again()
            except (
                GameGenerationError,
                RelativeSelectionError,
                TargetEligibilityError,
                ValueError,
            ) as error:
                self._send_json(409, {"error": str(error)})
                return
            if self._media_downloader is not None:
                self._media_downloader.request(self._session.game)
            self._send_json(
                200,
                {"view": self._view()},
            )
            return
        if self.path != "/api/guess":
            self._send_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "request too large"})
            return
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
            species_id = int(request["species_id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "expected a species_id"})
            return
        try:
            outcome = self._session.guess(species_id)
        except GameplayError as error:
            self._send_json(409, {"error": str(error)})
            return
        self._send_json(
            200,
            {
                "outcome": _outcome_payload(outcome),
                "view": self._view(),
            },
        )


class _PrototypeHTTPServer(ThreadingHTTPServer):
    """Prototype server that also owns its optional background worker."""

    media_downloader: BackgroundMediaDownloader | None = None

    def server_close(self) -> None:
        if self.media_downloader is not None:
            self.media_downloader.close()
        super().server_close()


def serve(
    game: GeneratedGame,
    *,
    next_game: Callable[[GeneratedGame], GeneratedGame],
    media_library: WikimediaLibrary | None = None,
    media_downloader: BackgroundMediaDownloader | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    difficulty: Difficulty = DEFAULT_DIFFICULTY,
) -> ThreadingHTTPServer:
    """Return a bound server for a sequence of games, ready to serve."""
    httpd = _PrototypeHTTPServer((host, port), _Handler)
    httpd.session = PrototypeSession.start(  # type: ignore[attr-defined]
        game, next_game, difficulty
    )
    httpd.media_library = media_library  # type: ignore[attr-defined]
    httpd.media_downloader = media_downloader
    if media_downloader is not None:
        media_downloader.request(game)
    return httpd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Play a phylogenomica game in a local browser prototype."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--game", type=Path, help="a serialized game to play")
    source.add_argument(
        "--target",
        type=int,
        help="generate for this target (default: choose an eligible target randomly)",
    )
    parser.add_argument(
        "--seed", type=int, default=0, help="relative-selection seed (default: 0)"
    )
    parser.add_argument(
        "--difficulty",
        choices=DIFFICULTIES,
        default=DEFAULT_DIFFICULTY,
        help=(
            "expert conceals the target and deals a mulligan; guided reveals "
            f"the target and deals no mulligan (default: {DEFAULT_DIFFICULTY}). "
            "Switchable in the page."
        ),
    )
    parser.add_argument("--normalized-dir", type=Path, default=DEFAULT_NORMALIZED_DIR)
    parser.add_argument(
        "--media-library",
        type=Path,
        help=(
            "local Wikimedia library manifest (default: auto-detect the current "
            "dataset under assets/processed/wikimedia-library)"
        ),
    )
    parser.add_argument(
        "--download-missing-images",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "resolve and download missing game images in the background "
            "(default: disabled)"
        ),
    )
    parser.add_argument(
        "--media-cache-root",
        type=Path,
        default=DEFAULT_CACHE_ROOT,
        help="ignored Wikimedia metadata cache used by background downloads",
    )
    parser.add_argument(
        "--media-transport",
        choices=("urllib", "curl"),
        default="urllib",
        help="verified HTTPS transport for background metadata and images",
    )
    parser.add_argument("--media-ca-file", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--open",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="open a browser window on start",
    )
    return parser


def _load_prototype_game(args: argparse.Namespace) -> GeneratedGame:
    if args.game:
        return load_game(args.game)
    normalized_dir = args.normalized_dir
    eligibility_database = (
        normalized_dir
        / f"target-eligibility-v{ELIGIBILITY_INDEX_VERSION}"
        / ELIGIBILITY_DATABASE_FILENAME
    )
    target_id = args.target
    if target_id is None:
        with TargetEligibilityIndex(eligibility_database) as eligibility:
            target_id = eligibility.random_eligible_target_id()
    return generate_game(
        target_id=target_id,
        seed=args.seed,
        normalized_database=normalized_dir / DATABASE_FILENAME,
        tree_database=normalized_dir
        / f"tree-v{TREE_SCHEMA_VERSION}"
        / TREE_DATABASE_FILENAME,
        eligibility_database=eligibility_database,
        config=FeasibilityConfig(require_rich_card_metadata=True),
    )


def _next_game_factory(
    args: argparse.Namespace,
) -> Callable[[GeneratedGame], GeneratedGame]:
    """Build the launch-mode-aware source used by the Play again action."""
    random_target = args.game is None and args.target is None
    normalized_dir = args.normalized_dir
    eligibility_database = (
        normalized_dir
        / f"target-eligibility-v{ELIGIBILITY_INDEX_VERSION}"
        / ELIGIBILITY_DATABASE_FILENAME
    )

    def next_game(current: GeneratedGame) -> GeneratedGame:
        target_id = current.target_id
        if random_target:
            with TargetEligibilityIndex(eligibility_database) as eligibility:
                target_id = eligibility.random_eligible_target_id()
        return generate_game(
            target_id=target_id,
            seed=current.seed + 1,
            normalized_database=normalized_dir / DATABASE_FILENAME,
            tree_database=normalized_dir
            / f"tree-v{TREE_SCHEMA_VERSION}"
            / TREE_DATABASE_FILENAME,
            eligibility_database=eligibility_database,
            config=current.configuration,
        )

    return next_game


def _startup_summary(
    game: GeneratedGame, difficulty: Difficulty = DEFAULT_DIFFICULTY
) -> str:
    """Describe the session without disclosing its concealed target."""
    return f"game {game.game_id[:12]} - seed {game.seed} - {difficulty}"


def _load_media_library(
    args: argparse.Namespace, game: GeneratedGame
) -> WikimediaLibrary | None:
    """Load an explicit library or auto-detect the current dataset's library."""
    manifest_path = args.media_library
    explicit = manifest_path is not None
    if manifest_path is None:
        manifest_path = DEFAULT_LIBRARY_ROOT / game.dataset_version / "manifest.json"
    if not manifest_path.exists():
        if explicit:
            raise WikimediaLibraryError(
                f"media library manifest does not exist: {manifest_path}"
            )
        return None
    return load_wikimedia_library(
        manifest_path, expected_dataset_version=game.dataset_version
    )


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        game = _load_prototype_game(args)
    except (
        GameGenerationError,
        RelativeSelectionError,
        TargetEligibilityError,
        ValueError,
    ) as error:
        raise SystemExit(str(error)) from error

    try:
        media_library = _load_media_library(args, game)
    except WikimediaLibraryError as error:
        raise SystemExit(str(error)) from error

    media_downloader = None
    if args.download_missing_images:
        library_root = (
            args.media_library.parent.parent
            if args.media_library is not None
            else DEFAULT_LIBRARY_ROOT
        )
        media_downloader = BackgroundMediaDownloader(
            normalized_database=args.normalized_dir / DATABASE_FILENAME,
            initial_library=media_library,
            cache_root=args.media_cache_root,
            library_root=library_root,
            ca_file=args.media_ca_file,
            transport=args.media_transport,
        )

    httpd = serve(
        game,
        next_game=_next_game_factory(args),
        media_library=media_library,
        media_downloader=media_downloader,
        host=args.host,
        port=args.port,
        difficulty=args.difficulty,
    )
    url = f"http://{args.host}:{args.port}/"
    # The terminal is part of the player-visible surface. Do not disclose the
    # concealed target here, especially when it was selected randomly.
    print(_startup_summary(game, args.difficulty))
    print(f"serving {url}  (ctrl-c to stop)")
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
