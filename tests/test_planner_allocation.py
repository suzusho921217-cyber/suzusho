"""next_day_allocation（§11 配分ロジック）のテスト。"""

from src.common.models import Brand
from src.planner.planner import next_day_allocation

ALLOC_CFG = {
    "total_daily_slots": 6,
    "exploit_ratio": 0.70,
    "brand_max_ratio": 0.60,
    "brand_min_explore_posts": 1,
}


def _sum(alloc):
    return sum(b.total for b in alloc.brands)


def test_bootstrap_equal_split_all_explore():
    alloc = next_day_allocation([], ALLOC_CFG, [Brand.CAT, Brand.DOG])
    assert alloc.mode == "equal"
    assert _sum(alloc) == 6
    for b in alloc.brands:
        assert b.total == 3
        assert b.exploit == 0
        assert b.explore == 3


def test_bootstrap_three_brands():
    alloc = next_day_allocation([], ALLOC_CFG, [Brand.CAT, Brand.DOG, Brand.ADULT])
    assert sorted(b.total for b in alloc.brands) == [2, 2, 2]
    assert _sum(alloc) == 6


def test_bootstrap_remainder_goes_to_first_by_name():
    alloc = next_day_allocation([], {**ALLOC_CFG, "total_daily_slots": 5},
                                [Brand.CAT, Brand.DOG])
    per = {b.brand: b.total for b in alloc.brands}
    assert per[Brand.CAT] == 3  # "cat" < "dog"
    assert per[Brand.DOG] == 2


def test_performance_proportional_to_score_with_cap():
    winning = [
        {"brand": "cat", "score": 8.0},
        {"brand": "dog", "score": 2.0},
    ]
    alloc = next_day_allocation(winning, ALLOC_CFG, [Brand.CAT, Brand.DOG])
    assert alloc.mode == "performance"
    assert _sum(alloc) == 6
    per = {b.brand: b for b in alloc.brands}
    # brand_cap = floor(6 * 0.6) = 3 → 猫は上限で頭打ち
    assert per[Brand.CAT].total == 3
    assert per[Brand.DOG].total == 3
    # explore は各ブランド最低 1
    assert per[Brand.CAT].explore >= 1
    assert per[Brand.DOG].explore >= 1
    # 総 exploit は round(6*0.7)=4 相当（cap 調整後も本数は保存）
    assert per[Brand.CAT].exploit + per[Brand.DOG].exploit == 4


def test_performance_favours_higher_score_brand():
    winning = [
        {"brand": "cat", "score": 5.0},
        {"brand": "dog", "score": 1.0},
        {"brand": "adult", "score": 0.0},
    ]
    alloc = next_day_allocation(winning, ALLOC_CFG,
                               [Brand.CAT, Brand.DOG, Brand.ADULT])
    per = {b.brand: b for b in alloc.brands}
    assert _sum(alloc) == 6
    assert per[Brand.CAT].exploit >= per[Brand.DOG].exploit >= per[Brand.ADULT].exploit
    # スコア 0 のブランドでも最低探索は確保
    assert per[Brand.ADULT].explore >= 1


def test_performance_zero_scores_falls_back_to_even_exploit():
    winning = [{"brand": "cat", "score": 0.0}, {"brand": "dog", "score": 0.0}]
    alloc = next_day_allocation(winning, ALLOC_CFG, [Brand.CAT, Brand.DOG])
    assert _sum(alloc) == 6
    assert any("スコア合計が 0" in w for w in alloc.warnings)
    per = {b.brand: b for b in alloc.brands}
    assert per[Brand.CAT].exploit == per[Brand.DOG].exploit


def test_unknown_brand_in_winning_tags_is_ignored():
    winning = [{"brand": "hamster", "score": 9.0}, {"brand": "cat", "score": 1.0}]
    alloc = next_day_allocation(winning, ALLOC_CFG, [Brand.CAT, Brand.DOG])
    assert _sum(alloc) == 6


def test_no_enabled_brands():
    alloc = next_day_allocation([], ALLOC_CFG, [])
    assert alloc.brands == []
    assert alloc.warnings
