import hashlib
import json
import threading
import urllib.error
import urllib.request
from dataclasses import replace

import pytest

from phylogenomica.data.cards import CardImage, MetadataSource, SpeciesCard
from phylogenomica.data.wikimedia_library import (
    WikimediaAsset,
    WikimediaLibrary,
    load_wikimedia_library,
)
from phylogenomica.data.wikimedia_rights import classify_rights
from phylogenomica.gameplay.engine import GameplayError, initial_state, replay
from phylogenomica.generation.feasibility import FeasibilityConfig
from phylogenomica.generation.game import (
    GAME_GENERATOR_VERSION,
    GAME_SCHEMA_VERSION,
    GameMember,
    GameTier,
    GeneratedGame,
    GeneratedStage,
)
from phylogenomica.prototype.media import BackgroundMediaDownloader
from phylogenomica.prototype.server import (
    PAGE_PATH,
    PrototypeSession,
    _load_media_library,
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
PNG_1_BY_1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00"
)


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
                clade_name=f"Clade {m.tier_index}",
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


def test_reserves_anonymous_stage_slots_before_any_species_is_placed() -> None:
    game = _game()

    opening = build_view(game, initial_state(game))

    assert [stage["stage_index"] for stage in opening["lineage"]] == [0]
    tiers = opening["lineage"][0]["tiers"]
    assert len(tiers) == 3
    assert [tier["slot_count"] for tier in tiers] == [1, 1, 1]
    assert all(tier["species"] == [] for tier in tiers)
    # The divergence age labels the empty branching event so the player can
    # read the clade's shape in time, but the clade name is the answer in
    # words and waits until its first species is placed.
    assert [tier["age_ma"] for tier in tiers] == [500.0, 490.0, 480.0]
    assert all(tier["populated"] is False for tier in tiers)
    assert all(tier["clade_name"] is None for tier in tiers)

    state, _ = replay(game, [DECOY_A])
    populated = build_view(game, state)["lineage"][0]["tiers"]
    assert len(populated) == len(tiers)
    assert populated[0]["species"][0]["species_id"] == DECOY_A
    assert populated[0]["species"][0]["slot_index"] == 0
    assert populated[1]["species"] == populated[2]["species"] == []


def test_withholds_tier_and_role_until_a_card_is_placed() -> None:
    game = _game()

    view = build_view(game, initial_state(game))

    for card in view["cards"]:
        assert card["state"] == "active"
        # Tier and role are the answer; they must not travel early.
        assert "tier_index" not in card
        assert "role" not in card
        assert card["english_name"] and card["scientific_name"]
        # Historical OneZoom URLs are never hotlinked at runtime.
        assert card["image_url"] is None
        assert card["rights"] is None
        assert card["license"] is None

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


def test_review_view_keeps_the_completed_stage_and_hides_the_next_one() -> None:
    game = _game()
    state, _ = replay(game, [UNLOCK])

    view = build_view(game, state, review_stage_index=0)

    assert view["reviewing_stage"] is True
    assert view["stage_index"] == 0
    assert view["stage_at_stake"] == 0
    assert [stage["stage_index"] for stage in view["lineage"]] == [0]
    assert {card["species_id"] for card in view["cards"]} == {
        DECOY_A,
        MULLIGAN_A,
        UNLOCK,
    }
    assert all(card["state"] in {"guessed", "revealed"} for card in view["cards"])
    assert not ({DECOY_B, MULLIGAN_B, TARGET} & {
        card["species_id"] for card in view["cards"]
    })


def test_guided_play_reveals_the_target_and_deals_no_mulligan() -> None:
    game = _game()

    view = build_view(game, initial_state(game, "guided"))

    assert view["difficulty"] == "guided"
    assert view["target"]["species_id"] == TARGET
    assert view["target"]["english_name"] == f"Common {TARGET}"
    assert view["target"]["selectable"] is False
    assert view["target"]["role"] == "target"
    # The mulligan is not dealt, so it is neither a card nor a tree slot.
    assert [card["species_id"] for card in view["cards"]] == [DECOY_A, UNLOCK]
    assert all(card["selectable"] for card in view["cards"])
    assert [tier["tier_index"] for tier in view["lineage"][0]["tiers"]] == [0, 2]
    # Two choices per stage rather than three, and five stages' worth of them.
    assert view["stage_at_stake"] == 2
    assert view["maximum"] == 4


def test_guided_ultimate_stage_deals_the_target_without_offering_it() -> None:
    game = _game()
    state, _ = replay(game, [UNLOCK], "guided")

    view = build_view(game, state)

    cards = {card["species_id"]: card for card in view["cards"]}
    assert set(cards) == {DECOY_B, MULLIGAN_B, TARGET}
    assert cards[TARGET]["selectable"] is False
    assert cards[TARGET]["state"] == "revealed"
    assert cards[TARGET]["role"] == "target"
    assert cards[MULLIGAN_B]["selectable"] is True

    # Naming the closest relative ends the game and closes the cladogram.
    final = build_view(game, replay(game, [UNLOCK, MULLIGAN_B], "guided")[0])
    assert final["completed"] is True
    assert final["score"] == final["maximum"] == 4
    assert [t["english_name"] for s in final["lineage"] for t in s["target"]] == [
        f"Common {TARGET}"
    ]


def test_expert_play_is_unchanged_by_the_guided_option() -> None:
    game = _game()

    view = build_view(game, initial_state(game))

    assert view["difficulty"] == "expert"
    assert view["target"] is None
    assert [card["species_id"] for card in view["cards"]] == [
        DECOY_A,
        MULLIGAN_A,
        UNLOCK,
    ]
    assert all(card["selectable"] for card in view["cards"])
    assert view["maximum"] == 6


def test_session_switches_difficulty_by_restarting_the_target() -> None:
    session = PrototypeSession.start(_game(), _next_game)
    session.guess(DECOY_A)

    session.set_difficulty("guided")

    assert session.difficulty == "guided"
    assert session.state == initial_state(session.game, "guided")
    assert session.game.game_id == "a" * 64  # the same target, restarted
    assert session.review_stage_index is None
    with pytest.raises(GameplayError, match="unknown difficulty"):
        session.set_difficulty("easy")

    # A new game keeps the difficulty the player chose.
    session.play_again()
    assert session.difficulty == "guided"
    assert session.state == initial_state(session.game, "guided")


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


def test_session_requires_continue_after_a_transition_stage() -> None:
    session = PrototypeSession.start(_game(), _next_game)

    outcome = session.guess(UNLOCK)

    assert outcome.stage_completed is True
    assert session.state.current_stage_index == 1
    assert session.review_stage_index == 0
    with pytest.raises(GameplayError, match="continue"):
        session.guess(DECOY_B)

    session.continue_stage()
    assert session.review_stage_index is None
    assert session.guess(DECOY_B).species_id == DECOY_B


def test_session_never_reviews_after_the_final_stage() -> None:
    session = PrototypeSession.start(_game(), _next_game)

    with pytest.raises(GameplayError, match="no completed stage"):
        session.continue_stage()

    session.guess(UNLOCK)
    session.continue_stage()
    outcome = session.guess(TARGET)

    assert outcome.game_completed is True
    assert session.review_stage_index is None
    assert build_view(game=session.game, state=session.state)["completed"] is True


def test_background_media_download_prioritizes_the_opening_stage(
    tmp_path,
) -> None:
    game = _game()
    resolved_ids = {
        member.species_id for stage in game.stages for member in stage.members
    }
    resolver_calls = []
    update_calls = []
    assets = {}

    def resolver(requested_game, **options):
        resolver_calls.append((requested_game, options))
        return tmp_path / "resolver.json", {
            "records": [
                {"species_id": species_id, "status": "resolved"}
                for species_id in sorted(resolved_ids)
            ]
        }

    def updater(_manifest, **options):
        selected = set(options["species_ids"])
        update_calls.append(selected)
        for species_id in selected:
            assets[species_id] = WikimediaAsset(
                species_id=species_id,
                path=tmp_path / f"{species_id}.png",
                mime_type="image/png",
                sha256="a" * 64,
                attribution_text="Example attribution",
                license_name="CC-BY-4.0",
                rights_url="https://creativecommons.org/licenses/by/4.0/",
                commons_page_url=f"https://commons.example/{species_id}",
            )
        return tmp_path / "library" / "manifest.json", {}

    def loader(manifest_path, **_options):
        return WikimediaLibrary(manifest_path, game.dataset_version, dict(assets))

    downloader = BackgroundMediaDownloader(
        normalized_database=tmp_path / "onezoom.sqlite3",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
        resolver=resolver,
        updater=updater,
        library_loader=loader,
    )
    try:
        downloader.request(game)
        assert downloader.wait(game.game_id) == "ready"

        assert len(resolver_calls) == 1
        assert set(resolver_calls[0][1]["species_ids"]) == resolved_ids
        assert update_calls == [
            {DECOY_A, MULLIGAN_A, UNLOCK},
            {DECOY_B, MULLIGAN_B, TARGET},
        ]
        status = downloader.player_status(game, 0)
        assert status["available_count"] == status["total_count"] == 3
        assert status["revision"] == 2
    finally:
        downloader.close()


@pytest.fixture
def media_library(tmp_path):
    root = tmp_path / "library"
    files = root / "files"
    files.mkdir(parents=True)
    records = []
    checksum = hashlib.sha256(PNG_1_BY_1).hexdigest()
    for species_id in range(1, 7):
        local_path = f"files/{species_id}.png"
        (root / local_path).write_bytes(PNG_1_BY_1)
        record = {
            "species_id": species_id,
            "commons_title": f"File:{species_id}.png",
            "commons_page_url": f"https://commons.example/{species_id}",
            "mime_type": "image/png",
            "width": 1,
            "height": 1,
            "bytes": len(PNG_1_BY_1),
            "sha256": checksum,
            "local_path": local_path,
            "creator": "Example creator",
            "license_name": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "transformation": "No local transformation.",
        }
        record["rights"] = classify_rights(record)
        records.append(record)
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_version": "test-proto-1",
                "records": records,
            }
        ),
        encoding="utf-8",
    )
    return load_wikimedia_library(
        manifest, expected_dataset_version="test-proto-1"
    )


@pytest.fixture
def server(media_library):
    httpd = serve(
        _game(), next_game=_next_game, media_library=media_library, port=0
    )
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
    assert b"fonts.googleapis.com" in page
    assert b"Solway" in page
    assert page == PAGE_PATH.read_bytes()
    # The tree is the board, so its container and controls must be present.
    for marker in (
        b'id="stage-tree"',
        b'id="cards"',
        b'id="fit"',
        b'id="follow"',
        b'id="media-status"',
    ):
        assert marker in page
    # The column adapter must preserve anonymous slots for vertical layout.
    assert b"slotCount: Number.isInteger(tier.slot_count)" in page
    assert b"length: tier.slotCount" in page
    assert b"setTimeout(refreshMedia, 1000)" in page

    status, body = _get(server, "/api/view")
    assert status == 200
    view = json.loads(body)
    assert view["stage_index"] == 0
    assert view["cards"][0]["image_url"] == f"/media/{DECOY_A}"
    assert view["cards"][0]["image_attribution"]["source_url"]

    status, image = _get(server, f"/media/{DECOY_A}")
    assert status == 200
    assert image == PNG_1_BY_1


def test_server_reports_download_progress_and_queues_play_again(
    media_library,
) -> None:
    class FakeDownloader:
        def __init__(self):
            self.library = media_library
            self.requests = []
            self.closed = False

        def request(self, game):
            self.requests.append(game.game_id)

        def player_status(self, _game, _stage_index, also_shown=()):
            return {
                "enabled": True,
                "state": "ready",
                "available_count": 3,
                "total_count": 3,
                "revision": 1,
                "failed": False,
            }

        def close(self):
            self.closed = True

    downloader = FakeDownloader()
    httpd = serve(
        _game(),
        next_game=_next_game,
        media_downloader=downloader,  # type: ignore[arg-type]
        port=0,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        _, body = _get(base, "/api/view")
        assert json.loads(body)["media_download"]["state"] == "ready"

        _, payload = _post(base, "/api/play-again")
        assert payload["view"]["media_download"]["available_count"] == 3
        assert downloader.requests == ["a" * 64, "b" * 64]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert downloader.closed is True


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
    assert payload["view"]["reviewing_stage"] is True
    assert payload["view"]["stage_index"] == 0

    status, payload = _post(server, "/api/continue")
    assert status == 200
    assert payload["view"]["reviewing_stage"] is False
    assert payload["view"]["stage_index"] == 1

    status, payload = _post(server, "/api/play-again")
    assert payload["view"]["stage_index"] == 0
    assert payload["view"]["score"] == 0


def test_switches_difficulty_over_http(server: str) -> None:
    status, payload = _post(server, "/api/difficulty", {"difficulty": "guided"})

    assert status == 200
    view = payload["view"]
    assert view["difficulty"] == "guided"
    assert view["target"]["species_id"] == TARGET
    assert [card["species_id"] for card in view["cards"]] == [DECOY_A, UNLOCK]

    status, payload = _post(server, "/api/guess", {"species_id": UNLOCK})
    assert payload["outcome"]["stage_completed"] is True
    _post(server, "/api/continue")
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(server, "/api/guess", {"species_id": TARGET})
    assert caught.value.code == 409
    assert "revealed target" in json.loads(caught.value.read())["error"]

    status, payload = _post(server, "/api/difficulty", {"difficulty": "expert"})
    assert payload["view"]["difficulty"] == "expert"
    assert payload["view"]["target"] is None
    assert payload["view"]["stage_index"] == 0


def test_rejects_bad_requests(server: str) -> None:
    for path, body, status in (
        ("/api/difficulty", {"difficulty": "easy"}, 409),
        ("/api/difficulty", {}, 400),
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
    download_args = parser.parse_args(
        ["--download-missing-images", "--media-transport", "curl"]
    )
    assert download_args.download_missing_images is True
    assert download_args.media_transport == "curl"
    with pytest.raises(SystemExit):
        parser.parse_args(["--target", "5", "--game", "game.json"])


def test_loads_an_explicit_dataset_matching_media_library(media_library) -> None:
    args = build_parser().parse_args(
        ["--media-library", str(media_library.manifest_path)]
    )

    loaded = _load_media_library(args, _game())

    assert loaded is not None
    assert loaded.asset(DECOY_A) is not None


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


def test_dates_an_empty_branching_event_without_naming_it() -> None:
    game = _game()
    state, _ = replay(game, [DECOY_A])

    tiers = build_view(game, state)["lineage"][0]["tiers"]

    assert [(t["age_ma"], t["clade_name"], t["populated"]) for t in tiers] == [
        (500.0, "Clade 0", True),
        (490.0, None, False),
        (480.0, None, False),
    ]


def test_reports_clade_names_on_placed_tiers() -> None:
    game = _game()
    state, _ = replay(game, [MULLIGAN_A, UNLOCK])

    tiers = build_view(game, state)["lineage"][0]["tiers"]

    assert [(t["tier_index"], t["clade_name"]) for t in tiers] == [
        (0, "Clade 0"),
        (1, "Clade 1"),
        (2, "Clade 2"),
    ]


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
