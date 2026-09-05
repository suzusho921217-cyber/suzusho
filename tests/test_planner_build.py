"""build_daily_plan（§6 §11 企画の具体化）のテスト。"""

import pytest

from src.common.config import load
from src.common.models import (
    Brand,
    BrandAllocation,
    DailyAllocation,
    ExperimentFlag,
    Platform,
)
from src.planner.planner import build_daily_plan, next_day_allocation

PLANNING = load("planning")
PLATFORMS = [Platform.YOUTUBE]

ALLOC_CFG = {
    "total_daily_slots": 6,
    "exploit_ratio": 0.70,
    "brand_max_ratio": 0.60,
    "brand_min_explore_posts": 1,
}


def _alloc(**brands) -> DailyAllocation:
    bs = [
        BrandAllocation(brand=Brand(b), total=ex + xp, exploit=ex, explore=xp)
        for b, (ex, xp) in brands.items()
    ]
    return DailyAllocation(sum(b.total for b in bs), "performance", bs)


def test_bootstrap_plan_all_explore_correct_count():
    alloc = next_day_allocation([], ALLOC_CFG, [Brand.CAT, Brand.DOG])
    plans = build_daily_plan("2026-08-31", alloc, [], PLANNING, PLATFORMS)
    assert len(plans) == 6
    assert all(p.experiment_flag is ExperimentFlag.EXPLORE for p in plans)
    assert {p.brand for p in plans} == {Brand.CAT, Brand.DOG}
    assert all(p.target_platforms == [Platform.YOUTUBE] for p in plans)
    assert all(p.date == "2026-08-31" for p in plans)


def test_plan_ids_unique_and_prefixed():
    alloc = _alloc(cat=(2, 1), dog=(1, 2))
    plans = build_daily_plan("2026-08-31", alloc, [], PLANNING, PLATFORMS)
    ids = [p.plan_id for p in plans]
    assert len(ids) == len(set(ids))
    assert all(p.plan_id.startswith("2026-08-31-") for p in plans)


def test_exploit_uses_winning_tag_fields():
    winning = [
        {
            "brand": "cat", "concept_tag": "驚き", "hook_type": "視線誘導",
            "character_id": "CAT_001", "reality_level": 5, "oddity_level": 2,
            "duration_target_sec": 9, "prompt_version": "v3",
            "platform": "youtube", "score": 0.83,
        },
    ]
    alloc = _alloc(cat=(1, 1))
    plans = build_daily_plan("2026-08-31", alloc, winning, PLANNING, PLATFORMS)
    exploit = [p for p in plans if p.experiment_flag is ExperimentFlag.EXPLOIT]
    assert len(exploit) == 1
    p = exploit[0]
    assert (p.concept_tag, p.hook_type, p.duration_target_sec) == ("驚き", "視線誘導", 9)
    assert p.prompt_version == "v3"
    assert "0.830" in p.notes


def test_exploit_shortfall_falls_back_to_new_concept():
    # exploit 2 本要求だが winning_tag は 1 件だけ
    winning = [{"brand": "cat", "concept_tag": "驚き", "hook_type": "視線誘導",
               "platform": "youtube", "score": 0.5}]
    alloc = _alloc(cat=(2, 1))
    plans = build_daily_plan("2026-08-31", alloc, winning, PLANNING, PLATFORMS)
    exploit = [p for p in plans if p.experiment_flag is ExperimentFlag.EXPLOIT]
    assert len(exploit) == 2
    assert any("補填" in p.notes for p in exploit)


def test_explore_avoids_winning_combos():
    winning = [{"brand": "cat", "concept_tag": "違和感", "hook_type": "0.5秒異常",
               "platform": "youtube", "score": 0.9}]
    alloc = _alloc(cat=(0, 3))
    plans = build_daily_plan("2026-08-31", alloc, winning, PLANNING, PLATFORMS)
    combos = {(p.concept_tag, p.hook_type) for p in plans}
    assert ("違和感", "0.5秒異常") not in combos


def test_deterministic_same_inputs_same_output():
    alloc = _alloc(cat=(2, 1), dog=(1, 2))
    a = build_daily_plan("2026-09-01", alloc, [], PLANNING, PLATFORMS)
    b = build_daily_plan("2026-09-01", alloc, [], PLANNING, PLATFORMS)
    assert [p.plan_id for p in a] == [p.plan_id for p in b]
    assert [(p.concept_tag, p.hook_type) for p in a] == [(p.concept_tag, p.hook_type) for p in b]


def test_different_date_shifts_explore_selection():
    alloc = _alloc(cat=(0, 3))
    d1 = build_daily_plan("2026-09-01", alloc, [], PLANNING, PLATFORMS)
    d2 = build_daily_plan("2026-09-02", alloc, [], PLANNING, PLATFORMS)
    assert [(p.concept_tag, p.hook_type) for p in d1] != [
        (p.concept_tag, p.hook_type) for p in d2
    ]


def test_levels_within_configured_range():
    alloc = next_day_allocation([], ALLOC_CFG, [Brand.CAT, Brand.DOG])
    plans = build_daily_plan("2026-08-31", alloc, [], PLANNING, PLATFORMS)
    for p in plans:
        assert 4 <= p.reality_level <= 5
        assert 1 <= p.oddity_level <= 2
        assert 6 <= p.duration_target_sec <= 15


def test_unknown_brand_pool_raises():
    alloc = DailyAllocation(1, "equal", [BrandAllocation(Brand.ADULT, 1, 0, 1)])
    # adult はプールにあるので通る; プール無しブランドを模す
    bad = {"defaults": {}, "brands": {}}
    with pytest.raises(KeyError):
        build_daily_plan("2026-08-31", alloc, [], bad, PLATFORMS)
