"""learning.engine（§11）: compute_score / compute_norm / extract_winning_tags。"""

from datetime import datetime, timedelta, timezone

import pytest

from src.common.models import PerformanceSnapshot
from src.learning import engine
from src.learning.engine import (
    WINNING_TAG_KEYS,
    compute_norm,
    compute_score,
    extract_winning_tags,
    pick_snapshot,
)

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 1, tzinfo=JST)

PRE_WEIGHTS = {
    "completion_rate": 0.30,
    "share_rate": 0.25,
    "follow_rate": 0.25,
    "comment_rate": 0.10,
    "cost_efficiency": 0.10,
}


def _snap(name="7d", **over):
    base = {
        "post_key": "k",
        "snapshot": name,
        "collected_at": NOW,
        "views": None,
        "shares": None,
        "comments": None,
        "completion_rate": None,
        "followers_before": None,
        "followers_after": None,
        "revenue_jpy": None,
    }
    base.update(over)
    return PerformanceSnapshot(**base)


# --- compute_score ----------------------------------------------------------

def test_compute_score_all_metrics_present():
    snap = _snap(
        views=1000, shares=50, comments=20, completion_rate=0.6,
        followers_before=100, followers_after=110,
    )
    score = compute_score(
        snap, weights=PRE_WEIGHTS, cost_jpy=100.0,
        norm={"cost_efficiency": 10.0},  # views/cost = 10 → 正規化後 1.0
    )
    # (0.6*.30 + .05*.25 + .01*.25 + .02*.10 + 1.0*.10) / 1.0
    assert score == pytest.approx(0.297)


def test_compute_score_renormalizes_when_metric_missing():
    """completion_rate 欠損 → 残り4指標の重みで再正規化される。"""
    snap = _snap(
        views=1000, shares=50, comments=20, completion_rate=None,
        followers_before=100, followers_after=110,
    )
    score = compute_score(
        snap, weights=PRE_WEIGHTS, cost_jpy=100.0, norm={"cost_efficiency": 10.0}
    )
    # (.05*.25 + .01*.25 + .02*.10 + 1.0*.10) / (.25+.25+.10+.10)
    assert score == pytest.approx(0.117 / 0.70)


def test_compute_score_drops_normalized_metric_without_norm():
    snap = _snap(views=1000, shares=50, comments=20, completion_rate=0.6,
                 followers_before=100, followers_after=110)
    score = compute_score(snap, weights=PRE_WEIGHTS, cost_jpy=100.0, norm=None)
    # cost_efficiency は norm 無しで除外 → 分母 0.90
    assert score == pytest.approx(0.197 / 0.90)


def test_compute_score_zero_when_no_usable_metric():
    assert compute_score(_snap(), weights=PRE_WEIGHTS) == 0.0


def test_compute_score_clamps_negative_follow_rate():
    snap = _snap(views=1000, followers_before=200, followers_after=100)
    score = compute_score(snap, weights={"follow_rate": 1.0})
    assert score == 0.0


# --- compute_norm ----------------------------------------------------------

def test_compute_norm_population_max():
    records = [
        ({"generation_cost_jpy": 100}, {"7d": _snap(views=500, revenue_jpy=300)}),
        ({"generation_cost_jpy": 200}, {"7d": _snap(views=4000, revenue_jpy=100)}),
    ]
    norm = compute_norm(records)
    assert norm["revenue_jpy"] == 300.0
    assert norm["cost_efficiency"] == pytest.approx(20.0)  # 4000/200
    # roi: (300-100)/100 = 2.0 vs (100-200)/200 = -0.5 → max(clamp>=0) = 2.0
    assert norm["roi"] == pytest.approx(2.0)


# --- pick_snapshot -------------------------------------------------------

def test_pick_snapshot_follows_order():
    snaps = {"24h": _snap("24h"), "7d": _snap("7d")}
    assert pick_snapshot(snaps).snapshot == "7d"
    assert pick_snapshot({"latest": _snap("latest")}).snapshot == "latest"
    assert pick_snapshot({}) is None


# --- extract_winning_tags ------------------------------------------------

TAG_A = {
    "brand": "cat", "concept_tag": "違和感", "hook_type": "0.5秒異常",
    "character_id": "CAT_001", "reality_level": 4, "oddity_level": 2,
    "duration_target_sec": 10, "prompt_version": "v1", "platform": "youtube",
}
TAG_B = {**TAG_A, "concept_tag": "かわいい", "hook_type": "視線誘導"}
TAG_C = {**TAG_A, "concept_tag": "驚き", "hook_type": "突然の動き"}

CONFIG = {
    "score_weights_pre_monetization": {"completion_rate": 1.0},
    "learning": {
        "monetized": False,
        "min_posts_for_winner": 3,
        "eval_window_weights": {"7": 0.6, "30": 0.4},
        "score_snapshot_order": ["7d", "latest"],
    },
}


def _rec(tag, days_ago, cr):
    post = {**tag, "generation_cost_jpy": 100,
            "published_at": (NOW - timedelta(days=days_ago)).isoformat()}
    return (post, {"7d": _snap(completion_rate=cr)})


def test_extract_winning_tags_median_and_window_blend():
    records = [
        _rec(TAG_A, 2, 0.8),
        _rec(TAG_A, 5, 0.6),
        _rec(TAG_A, 20, 0.4),
    ]
    winners = extract_winning_tags(records, CONFIG, now=NOW)
    assert len(winners) == 1
    w = winners[0]
    assert set(w) == set(WINNING_TAG_KEYS) | {"score"}
    # 7d median [0.8,0.6]=0.7, 30d median [0.8,0.6,0.4]=0.6 → 0.7*.6 + 0.6*.4
    assert w["score"] == pytest.approx(0.66)


def test_extract_winning_tags_min_posts_filters_sparse_group():
    records = [
        _rec(TAG_A, 2, 0.9), _rec(TAG_A, 3, 0.9), _rec(TAG_A, 4, 0.9),
        _rec(TAG_B, 2, 0.95), _rec(TAG_B, 3, 0.95),  # 2本のみ → 除外
    ]
    winners = extract_winning_tags(records, CONFIG, now=NOW)
    assert [w["concept_tag"] for w in winners] == ["違和感"]


def test_extract_winning_tags_sorted_by_score_desc():
    records = [
        _rec(TAG_A, 1, 0.5), _rec(TAG_A, 2, 0.5), _rec(TAG_A, 3, 0.5),
        _rec(TAG_C, 1, 0.9), _rec(TAG_C, 2, 0.9), _rec(TAG_C, 3, 0.9),
    ]
    winners = extract_winning_tags(records, CONFIG, now=NOW)
    assert [w["concept_tag"] for w in winners] == ["驚き", "違和感"]
    assert winners[0]["score"] > winners[1]["score"]


def test_extract_winning_tags_median_guards_single_viral():
    """3本が平凡＋1本だけ超バズ → median なので勝ちタグ化しない。"""
    records = [
        _rec(TAG_A, 1, 1.0),   # 超バズ
        _rec(TAG_A, 2, 0.2),
        _rec(TAG_A, 3, 0.2),
        _rec(TAG_A, 4, 0.2),
    ]
    winners = extract_winning_tags(records, CONFIG, now=NOW)
    # 7d median [1.0,0.2,0.2,0.2]=0.2, 30d 同じ → 0.2
    assert winners[0]["score"] == pytest.approx(0.2)


def test_extract_winning_tags_output_types_normalized():
    records = [_rec(TAG_A, 2, 0.8), _rec(TAG_A, 3, 0.7), _rec(TAG_A, 4, 0.6)]
    w = extract_winning_tags(records, CONFIG, now=NOW)[0]
    assert w["brand"] == "cat" and isinstance(w["brand"], str)
    assert w["reality_level"] == 4 and isinstance(w["reality_level"], int)


def test_next_day_allocation_is_planner_reexport():
    from src.planner.planner import next_day_allocation as planner_alloc

    assert engine.next_day_allocation is planner_alloc
