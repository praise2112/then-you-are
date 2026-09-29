import random

from arena_evals.datagen.trainset import MAX_SHARE, UNCAPPED, cap_shares, half


def test_no_capped_game_ends_over_its_share_and_then_i_am_keeps_every_record():
    by_game = {UNCAPPED: ["t"] * 300, "big": ["b"] * 200} | {f"g{i}": ["x"] * 40 for i in range(30)}
    capped = cap_shares(by_game, random.Random(0))
    total = sum(len(v) for v in capped.values())
    assert len(capped[UNCAPPED]) == 300
    assert all(len(v) <= MAX_SHARE * total for g, v in capped.items() if g != UNCAPPED)
    assert len(capped["big"]) == int(MAX_SHARE * total)


def test_half_keeps_each_games_share():
    picked = half({"a": ["a"] * 10, "b": ["b"] * 4}, random.Random(0))
    assert sorted(picked) == ["a"] * 5 + ["b"] * 2
