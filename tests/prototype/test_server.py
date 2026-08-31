import json
import threading
import urllib.error
import urllib.request
from dataclasses import replace

import pytest

from phylogenomica.data.cards import CardImage, MetadataSource, SpeciesCard
from phylogenomica.gameplay.engine import initial_state, replay
from phylogenomica.generation.feasibility import FeasibilityConfig
from phylogenomica.generation.game import (
    GAME_GENERATOR_VERSION,
    GAME_SCHEMA_VERSION,
    GameMember,
    GameTier,
    GeneratedGame,
    GeneratedStage,
)
from phylogenomica.prototype.server import (
    PAGE_PATH,
    PrototypeSession,
    _load_prototype_game,
    _next_game_factory,
    _startup_summary,
    build_parser,
    build_view,
    serve,
)

DECOY_A, MULLIGAN_A, UNLOCK = 1, 2, 3
DECOY_B, MULLIGAN_B, TARGET = 4, 5, 6
CONFIG = FeasibilityConfig(members_per_stage=3, stages_per_game=2)


def _member(species_id: int, role: str, tier_index: int | None) -> GameMember:
    return GameMember(
        species_id=species_id,
        role=role,  # type: ignore[arg-type]
        tier_index=tier_index,
        ancestor_node_id=None if tier_index is None else 1000 + tier_index,
        card=SpeciesCard(
            species_id=species_id,
            scientific_name=f"Genus species{species_id}",
            english_name=f"Common {species_id}",
            ott_id=100 + species_id,
            popularity_rank=species_id,
            vernacular_source=MetadataSource("vernacular_by_ott", species_id),
            image=CardImage(
                url=f"https://example.test/{species_id}.jpg",
                rights="Test author",
                license="CC BY 4.0",
                source_code=99,
                source_id=f"image-{species_id}",
                source=MetadataSource("images_by_ott", species_id),
            ),
        ),
    )


def _stage(stage_index: int, specs, *, target_id: int | None = None):
    members = tuple(_member(*spec) for spec in specs)
    relatives = [m for m in members if m.role != "target"]
    return GeneratedStage(
        stage_index=stage_index,
        start_node_id=relatives[0].ancestor_node_id,
        end_node_id=relatives[-1].ancestor_node_id,
        members=members,
        tiers=tuple(
            GameTier(
                tier_index=m.tier_index,
                ancestor_node_id=m.ancestor_node_id,
                role=m.role,  # type: ignore[arg-type]
                species_ids=(m.species_id,),
                age_ma=float(500 - m.tier_index * 10),
            )
            for m in relatives
        ),
        mulligan_species_ids=tuple(
            m.species_id for m in relatives if m.role == "mulligan"
        ),
        unlock_species_ids=tuple(
            m.species_id for m in relatives if m.role == "unlock"
        ),
        target_species_id=target_id,
    )


def _game() -> GeneratedGame:
    return GeneratedGame(
        schema_version=GAME_SCHEMA_VERSION,
        game_id="a" * 64,
        dataset_version="test-proto-1",
        generator_version=GAME_GENERATOR_VERSION,
        selector_version=1,
        eligibility_index_version=1,
        target_id=TARGET,
        seed=7,
        configuration=CONFIG,
        stages=(
            _stage(
                0,
                [
                    (DECOY_A, "decoy", 0),
                    (MULLIGAN_A, "mulligan", 1),
                    (UNLOCK, "unlock", 2),
                ],
            ),
            _stage(
                1,
                [
                    (DECOY_B, "decoy", 3),
                    (MULLIGAN_B, "mulligan", 4),
                    (TARGET, "target", None),
                ],
                target_id=TARGET,
            ),
        ),
    )


def _next_game(current: GeneratedGame) -> GeneratedGame:
    return replace(current, game_id="b" * 64, seed=current.seed + 1)


def test_serves_only_the_open_stage_and_hides_the_target() -> None:
    game = _game()

    view = build_view(game, initial_state(game))

    assert view["stage_index"] == 0
    assert view["stage_count"] == 2
    assert view["is_ultimate"] is False
    assert [card["species_id"] for card in view["cards"]] == [
        DECOY_A,
        MULLIGAN_A,
        UNLOCK,
    ]
    # The concealed target is not in a transition stage's payload at all.
    assert TARGET not in [card["species_id"] for card in view["cards"]]
    # Generation identity includes the target in a deterministic digest. None
    # of it belongs in the player view while the target is concealed.
    assert {"game_id", "dataset_version", "seed", "target_id"}.isdisjoint(view)


def test_withholds_tier_and_role_until_a_card_is_placed() -> None:
    game = _game()

    view = build_view(game, initial_state(game))

    for card in view["cards"]:
        assert card["state"] == "active"
        # Tier and role are the answer; they must not travel early.
        assert "tier_index" not in card
        assert "role" not in card
        assert card["english_name"] and card["scientific_name"]
        assert card["image_url"] and card["rights"] and card["license"]

    state, _ = replay(game, [DECOY_A])
    placed = {c["species_id"]: c for c in build_view(game, state)["cards"]}
    assert placed[DECOY_A]["state"] == "guessed"
    assert placed[DECOY_A]["tier_index"] == 0
    assert placed[DECOY_A]["role"] == "decoy"
    assert "role" not in placed[MULLIGAN_A]  # still active, still secret


def test_shows_the_target_as_a_normal_card_in_the_ultimate_stage() -> None:
    game = _game()
    state, _ = replay(game, [UNLOCK])

    view = build_view(game, state)

    assert view["is_ultimate"] is True
    target = next(c for c in view["cards"] if c["species_id"] == TARGET)
    assert target["state"] == "active"
    # Even here it is indistinguishable from a decoy until it is chosen.
    assert "role" not in target
    assert target["english_name"] == f"Common {TARGET}"


def test_reports_the_growing_cladogram() -> None:
    game = _game()
    state, _ = replay(game, [MULLIGAN_A, UNLOCK, TARGET])

    view = build_view(game, state)

    assert view["completed"] is True
    assert view["cards"] == []
    stages = view["lineage"]
    assert [stage["stage_index"] for stage in stages] == [0, 1]
    assert [tier["tier_index"] for tier in stages[0]["tiers"]] == [0, 1, 2]
    first = stages[0]["tiers"][0]["species"][0]
    assert first["english_name"] == f"Common {DECOY_A}"
    assert first["placement"] == "revealed"
    assert stages[0]["tiers"][1]["species"][0]["placement"] == "guessed"
    # The target hangs off the end of the backbone, not off a tier.
    assert [s["species_id"] for s in stages[1]["target"]] == [TARGET]


def test_reports_scores_alongside_the_board() -> None:
    game = _game()

    opening = build_view(game, initial_state(game))
    assert opening["score"] == 0
    assert opening["stage_at_stake"] == 3
    assert opening["best_achievable"] == opening["maximum"] == 6

    state, _ = replay(game, [DECOY_A, UNLOCK, TARGET])
    finished = build_view(game, state)
    assert finished["score"] == 5
    assert finished["stage_scores"] == [2, 3]
    assert finished["stage_at_stake"] == 0


def test_session_guesses_and_plays_another_seed() -> None:
    session = PrototypeSession.start(_game(), _next_game)

    outcome = session.guess(MULLIGAN_A)
    assert outcome.role == "mulligan"
    assert session.state.stage_bonus == 1

    session.play_again()
    assert session.game.seed == 8
    assert session.game.game_id == "b" * 64
    assert session.state == initial_state(session.game)
    assert session.state.placements == ()


@pytest.fixture
def server():
    httpd = serve(_game(), next_game=_next_game, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _get(base: str, path: str):
    with urllib.request.urlopen(base + path) as response:
        return response.status, response.read()


def _post(base: str, path: str, body: object = None):
    data = b"" if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return response.status, json.loads(response.read())


def test_serves_the_page_and_the_view(server: str) -> None:
    status, page = _get(server, "/")
    assert status == 200
    assert b"<title>Phylogenomica</title>" in page
    assert page == PAGE_PATH.read_bytes()
    # The tree is the board, so its container and controls must be present.
    for marker in (b'id="stage-tree"', b'id="cards"', b'id="fit"', b'id="follow"'):
        assert marker in page

    status, body = _get(server, "/api/view")
    assert status == 200
    assert json.loads(body)["stage_index"] == 0


def test_resolves_guesses_over_http(server: str) -> None:
    status, payload = _post(server, "/api/guess", {"species_id": DECOY_A})

    assert status == 200
    outcome = payload["outcome"]
    assert outcome["role"] == "decoy"
    assert outcome["penalty"] == 1
    assert outcome["remaining"] == 2
    assert outcome["stage_completed"] is False
    assert payload["view"]["stage_at_stake"] == 2

    status, payload = _post(server, "/api/guess", {"species_id": UNLOCK})
    assert payload["outcome"]["stage_completed"] is True
    assert payload["view"]["stage_index"] == 1

    status, payload = _post(server, "/api/play-again")
    assert payload["view"]["stage_index"] == 0
    assert payload["view"]["score"] == 0


def test_rejects_bad_requests(server: str) -> None:
    for path, body, status in (
        ("/api/guess", {"species_id": 999}, 409),
        ("/api/guess", {"species_id": TARGET}, 409),
        ("/api/guess", {}, 400),
        ("/api/guess", {"species_id": "abc"}, 400),
        ("/api/reset", {}, 404),
        ("/api/missing", {}, 404),
    ):
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post(server, path, body)
        assert caught.value.code == status
        assert "error" in json.loads(caught.value.read())

    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(server, "/api/missing")
    assert caught.value.code == 404


def test_command_line_allows_a_random_target_or_one_explicit_source() -> None:
    parser = build_parser()

    assert parser.parse_args(["--target", "5"]).target == 5
    random_args = parser.parse_args([])
    assert random_args.target is None and random_args.game is None
    with pytest.raises(SystemExit):
        parser.parse_args(["--target", "5", "--game", "game.json"])


def test_loads_a_uniformly_selected_eligible_target_when_unspecified(
    monkeypatch, tmp_path
) -> None:
    selected_target = 123
    opened = []

    class FakeEligibilityIndex:
        def __init__(self, path):
            opened.append(path)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def random_eligible_target_id(self):
            return selected_target

    generation_args = {}

    def fake_generate_game(**kwargs):
        generation_args.update(kwargs)
        return _game()

    monkeypatch.setattr(
        "phylogenomica.prototype.server.TargetEligibilityIndex",
        FakeEligibilityIndex,
    )
    monkeypatch.setattr(
        "phylogenomica.prototype.server.generate_game", fake_generate_game
    )
    args = build_parser().parse_args(
        ["--normalized-dir", str(tmp_path), "--seed", "19"]
    )

    game = _load_prototype_game(args)

    eligibility = (
        tmp_path / "target-eligibility-v1" / "target_eligibility.sqlite3"
    )
    assert opened == [eligibility]
    assert generation_args["target_id"] == selected_target
    assert generation_args["seed"] == 19
    assert generation_args["eligibility_database"] == eligibility
    assert game == _game()


def test_play_again_increments_seed_and_retains_an_explicit_target(
    monkeypatch, tmp_path
) -> None:
    generation_args = {}

    def fake_generate_game(**kwargs):
        generation_args.update(kwargs)
        return _next_game(_game())

    monkeypatch.setattr(
        "phylogenomica.prototype.server.generate_game", fake_generate_game
    )
    args = build_parser().parse_args(
        [
            "--target",
            str(TARGET),
            "--normalized-dir",
            str(tmp_path),
            "--seed",
            "7",
        ]
    )

    game = _next_game_factory(args)(_game())

    assert generation_args["target_id"] == TARGET
    assert generation_args["seed"] == 8
    assert game.seed == 8


def test_play_again_selects_a_target_in_random_target_mode(
    monkeypatch, tmp_path
) -> None:
    selected_target = 123
    generation_args = {}

    class FakeEligibilityIndex:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def random_eligible_target_id(self):
            return selected_target

    def fake_generate_game(**kwargs):
        generation_args.update(kwargs)
        return _next_game(_game())

    monkeypatch.setattr(
        "phylogenomica.prototype.server.TargetEligibilityIndex",
        FakeEligibilityIndex,
    )
    monkeypatch.setattr(
        "phylogenomica.prototype.server.generate_game", fake_generate_game
    )
    args = build_parser().parse_args(["--normalized-dir", str(tmp_path)])

    _next_game_factory(args)(_game())

    assert generation_args["target_id"] == selected_target
    assert generation_args["seed"] == 8


def test_startup_summary_does_not_disclose_the_target() -> None:
    summary = _startup_summary(_game())

    assert "target" not in summary
    assert "seed 7" in summary


def test_reports_divergence_ages_on_placed_tiers() -> None:
    game = _game()
    state, _ = replay(game, [MULLIGAN_A, UNLOCK])

    tiers = build_view(game, state)["lineage"][0]["tiers"]

    # The fixture ages fall by ten per tier, matching the game's own tiers.
    assert [(t["tier_index"], t["age_ma"]) for t in tiers] == [
        (0, 500.0),
        (1, 490.0),
        (2, 480.0),
    ]
    ages = [t["age_ma"] for t in tiers]
    assert ages == sorted(ages, reverse=True)


def test_reports_a_missing_divergence_age_as_null() -> None:
    game = _game()
    first = game.stages[0]
    ageless = replace(
        first, tiers=(replace(first.tiers[0], age_ma=None), *first.tiers[1:])
    )
    game = replace(game, stages=(ageless, *game.stages[1:]))
    state, _ = replay(game, [DECOY_A])

    tiers = build_view(game, state)["lineage"][0]["tiers"]
    assert tiers[0]["age_ma"] is None
