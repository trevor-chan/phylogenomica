import json

import pytest

from phylogenomica.prototype.ranks import (
    DEFAULT_RANK_TITLES_PATH,
    RankTitle,
    RankTitleCatalog,
    RankTitleError,
    load_rank_title_catalog,
    score_tier,
)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0, "needs_improvement"),
        (34, "needs_improvement"),
        (35, "good"),
        (39, "good"),
        (40, "excellent"),
        (44, "excellent"),
        (45, "perfect"),
    ],
)
def test_maps_default_scores_to_rank_tiers(score: int, expected: str) -> None:
    assert score_tier(score, 45) == expected


def test_maps_configurable_games_by_points_lost() -> None:
    assert score_tier(14, 14) == "perfect"
    assert score_tier(13, 14) == "excellent"
    assert score_tier(8, 14) == "good"
    assert score_tier(3, 14) == "needs_improvement"

    with pytest.raises(RankTitleError, match="maximum score"):
        score_tier(0, 0)
    with pytest.raises(RankTitleError, match="between zero"):
        score_tier(46, 45)


def test_loads_the_curated_catalog_and_alias_table() -> None:
    catalog = load_rank_title_catalog()

    assert len(catalog.titles) == 193
    assert catalog.aliases["Animalia"] == ("Animalia", "Metazoa")
    assert catalog.aliases["Plantae"] == (
        "Plantae",
        "Chloroplastida",
        "Viridiplantae",
    )


def test_selects_across_every_matching_clade_and_is_deterministic() -> None:
    catalog = RankTitleCatalog(
        schema_version=1,
        catalog_version=1,
        aliases={"Animalia": ("Animalia", "Metazoa")},
        titles=(
            RankTitle("Animal title", "perfect", ("Animalia",)),
            RankTitle("Mammal title", "perfect", ("Mammalia",)),
            RankTitle("Generic title", "perfect", ("generic",)),
        ),
    )
    arguments = {
        "score": 45,
        "maximum": 45,
        "target_clade_names": ("Eukaryota", "Metazoa", "Mammalia"),
    }

    selections = {
        catalog.attained_title(game_id=f"game-{number}", **arguments)
        for number in range(32)
    }
    first = catalog.attained_title(game_id="game-0", **arguments)
    repeated = catalog.attained_title(game_id="game-0", **arguments)

    assert first == repeated
    assert {selection.title for selection in selections} == {
        "Animal title",
        "Mammal title",
    }
    assert {selection.matched_taxon for selection in selections} == {
        "Animalia",
        "Mammalia",
    }


def test_uses_aliases_and_generic_fallbacks() -> None:
    catalog = load_rank_title_catalog()

    animal = catalog.attained_title(
        score=35,
        maximum=45,
        game_id="b" * 64,
        target_clade_names=("Eukaryota", "Metazoa"),
    )
    generic = catalog.attained_title(
        score=35,
        maximum=45,
        game_id="b" * 64,
        target_clade_names=("Unknown clade",),
    )

    assert animal.matched_taxon == "Animalia"
    assert generic.matched_taxon is None


def test_rejects_a_catalog_without_a_generic_fallback(tmp_path) -> None:
    payload = json.loads(DEFAULT_RANK_TITLES_PATH.read_text(encoding="utf-8"))
    payload["titles"] = {
        title: record
        for title, record in payload["titles"].items()
        if "generic" not in record["taxa"]
    }
    path = tmp_path / "rank_titles.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RankTitleError, match="generic fallback"):
        load_rank_title_catalog(path)
