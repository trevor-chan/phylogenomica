"""A local, dependency-free browser prototype over the gameplay engine.

The page is a renderer. Every guess is resolved by
:mod:`phylogenomica.gameplay.engine` on the server, and the browser draws the
returned transition without recomputing correctness.

The API is stage-scoped: it serves only the cards of the open stage, and it
reveals a card's tier and role only once that card has been placed. The
concealed target and the answer to the open stage therefore never cross the
wire early, even to a player reading the network traffic.
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from phylogenomica.data.onezoom_ingest import DATABASE_FILENAME
from phylogenomica.gameplay.engine import (
    GameplayError,
    GameState,
    GuessOutcome,
    PlacedSpecies,
    apply_guess,
    best_achievable_score,
    initial_state,
    maximum_score,
    score,
    stage_at_stake,
)
from phylogenomica.generation.eligibility import (
    ELIGIBILITY_DATABASE_FILENAME,
    ELIGIBILITY_INDEX_VERSION,
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
from phylogenomica.tree.preprocess import (
    DEFAULT_NORMALIZED_DIR,
    TREE_DATABASE_FILENAME,
    TREE_SCHEMA_VERSION,
)

PAGE_PATH = Path(__file__).with_name("index.html")
MAX_REQUEST_BYTES = 4096


@dataclass
class PrototypeSession:
    """One single-player session over an immutable game."""

    game: GeneratedGame
    state: GameState

    @classmethod
    def start(cls, game: GeneratedGame) -> PrototypeSession:
        return cls(game=game, state=initial_state(game))

    def reset(self) -> None:
        self.state = initial_state(self.game)

    def guess(self, species_id: int) -> GuessOutcome:
        self.state, outcome = apply_guess(self.game, self.state, species_id)
        return outcome


def _members_by_id(game: GeneratedGame) -> dict[int, GameMember]:
    return {
        member.species_id: member
        for stage in game.stages
        for member in stage.members
    }


def _card(member: GameMember, placed: PlacedSpecies | None) -> dict[str, object]:
    """Render one card, withholding the answer until the card is placed."""
    card = member.card
    payload: dict[str, object] = {
        "species_id": member.species_id,
        "english_name": card.english_name,
        "scientific_name": card.scientific_name,
        "image_url": card.image.url,
        "rights": card.image.rights,
        "license": card.image.license,
    }
    if placed is None:
        payload["state"] = "active"
    else:
        # Tier and role are the answer, so they travel only after placement.
        payload["state"] = placed.placement
        payload["tier_index"] = placed.tier_index
        payload["role"] = placed.role
    return payload


def _lineage(game: GeneratedGame, state: GameState) -> list[dict[str, object]]:
    """Group placed species into the growing cladogram, root to target."""
    members = _members_by_id(game)
    ages = {
        tier.ancestor_node_id: tier.age_ma
        for stage in game.stages
        for tier in stage.tiers
    }
    age_by_tier: dict[int, float | None] = {}
    stages: dict[int, dict[int | None, list[dict[str, object]]]] = {}
    for placed in state.placements:
        if placed.tier_index is not None:
            age_by_tier[placed.tier_index] = ages.get(placed.ancestor_node_id)
        tiers = stages.setdefault(placed.stage_index, {})
        tiers.setdefault(placed.tier_index, []).append(
            {
                "species_id": placed.species_id,
                "english_name": members[placed.species_id].card.english_name,
                "scientific_name": members[placed.species_id].card.scientific_name,
                "role": placed.role,
                "placement": placed.placement,
            }
        )
    return [
        {
            "stage_index": stage_index,
            "tiers": [
                {
                    "tier_index": tier_index,
                    "age_ma": age_by_tier.get(tier_index),
                    "species": tiers[tier_index],
                }
                for tier_index in sorted(
                    (t for t in tiers if t is not None),
                )
            ],
            "target": tiers.get(None, []),
        }
        for stage_index, tiers in sorted(stages.items())
    ]


def build_view(game: GeneratedGame, state: GameState) -> dict[str, object]:
    """Render everything the page may know at this moment."""
    placed_by_id = {placed.species_id: placed for placed in state.placements}
    open_stage = None if state.completed else game.stages[state.current_stage_index]
    cards = (
        []
        if open_stage is None
        else [
            _card(member, placed_by_id.get(member.species_id))
            for member in open_stage.members
        ]
    )
    return {
        "game_id": game.game_id,
        "dataset_version": game.dataset_version,
        "seed": game.seed,
        "stage_index": state.current_stage_index,
        "stage_count": len(game.stages),
        "is_ultimate": (
            open_stage is not None
            and open_stage.stage_index == len(game.stages) - 1
        ),
        "cards": cards,
        "score": score(game, state),
        "stage_at_stake": stage_at_stake(game, state),
        "best_achievable": best_achievable_score(game, state),
        "maximum": maximum_score(game),
        "stage_scores": list(state.stage_scores),
        "completed": state.completed,
        "lineage": _lineage(game, state),
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
        if self.path in ("/", "/index.html"):
            self._send(
                200, PAGE_PATH.read_bytes(), "text/html; charset=utf-8"
            )
        elif self.path == "/api/view":
            self._send_json(
                200, build_view(self._session.game, self._session.state)
            )
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        if self.path == "/api/reset":
            self._session.reset()
            self._send_json(
                200, {"view": build_view(self._session.game, self._session.state)}
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
                "view": build_view(self._session.game, self._session.state),
            },
        )


def serve(
    game: GeneratedGame, *, host: str = "127.0.0.1", port: int = 8000
) -> ThreadingHTTPServer:
    """Return a bound server for one game, ready to serve."""
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.session = PrototypeSession.start(game)  # type: ignore[attr-defined]
    return httpd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Play a phylogenomica game in a local browser prototype."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--game", type=Path, help="a serialized game to play")
    source.add_argument("--target", type=int, help="generate a game for this target")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--normalized-dir", type=Path, default=DEFAULT_NORMALIZED_DIR)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--open",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="open a browser window on start",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        if args.game:
            game = load_game(args.game)
        else:
            normalized_dir = args.normalized_dir
            game = generate_game(
                target_id=args.target,
                seed=args.seed,
                normalized_database=normalized_dir / DATABASE_FILENAME,
                tree_database=normalized_dir
                / f"tree-v{TREE_SCHEMA_VERSION}"
                / TREE_DATABASE_FILENAME,
                eligibility_database=normalized_dir
                / f"target-eligibility-v{ELIGIBILITY_INDEX_VERSION}"
                / ELIGIBILITY_DATABASE_FILENAME,
                config=FeasibilityConfig(require_rich_card_metadata=True),
            )
    except (GameGenerationError, RelativeSelectionError, ValueError) as error:
        raise SystemExit(str(error)) from error

    httpd = serve(game, host=args.host, port=args.port)
    url = f"http://{args.host}:{args.port}/"
    print(f"game {game.game_id[:12]} - target {game.target_id} - seed {game.seed}")
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
