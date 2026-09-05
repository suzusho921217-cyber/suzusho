"""guardrails（§13 §14）: check_budget / check_kill_switch / combine。"""

from src.common.guardrails import (
    BudgetSpend,
    GuardAction,
    GuardVerdict,
    check_budget,
    check_kill_switch,
    combine,
)
from src.common.models import Brand, Platform

CFG = {
    "automatic_stop_ratio": 0.95,
    "daily_budget": 1500,
    "monthly_budget": 30000,
    "total_investment_cap": 100000,
    "brand_budget": {"cat": 12000, "dog": 12000},
}


# --- check_budget ---------------------------------------------------------

def test_budget_allows_when_under_threshold():
    v = check_budget(BudgetSpend(today=800, month=20000, total=50000), CFG)
    assert v.action is GuardAction.ALLOW
    assert not v.blocked


def test_budget_stops_new_generation_at_95_percent():
    v = check_budget(BudgetSpend(today=100, month=28500, total=0), CFG)
    assert v.action is GuardAction.STOP_NEW_GENERATION
    assert "月次予算" in v.reason


def test_budget_stops_when_over_100_percent():
    v = check_budget(BudgetSpend(today=1600, month=0, total=0), CFG)
    assert v.action is GuardAction.STOP_NEW_GENERATION
    assert "日次予算" in v.reason and "超過" in v.reason


def test_budget_checks_total_investment_cap():
    v = check_budget(BudgetSpend(total=99000), CFG)
    assert v.action is GuardAction.STOP_NEW_GENERATION
    assert "総投資枠" in v.reason


def test_budget_checks_brand_budget_when_brand_given():
    spend = BudgetSpend(month=10000, by_brand_month={"cat": 11800})
    assert check_budget(spend, CFG).action is GuardAction.ALLOW  # brand 未指定なら見ない
    v = check_budget(spend, CFG, brand=Brand.CAT)
    assert v.action is GuardAction.STOP_NEW_GENERATION
    assert "cat" in v.reason


def test_budget_skips_missing_limits():
    v = check_budget(BudgetSpend(today=9999, month=9999, total=9999), {"daily_budget": 1500})
    assert v.action is GuardAction.STOP_NEW_GENERATION  # daily だけ効く、他はスキップで落ちない


def test_budget_never_returns_hold_or_stop():
    v = check_budget(BudgetSpend(today=99999, month=99999, total=999999), CFG)
    assert v.action is GuardAction.STOP_NEW_GENERATION  # STOP でも HOLD でもない


# --- check_kill_switch ---------------------------------------------------

def test_kill_switch_allows_with_no_signals():
    v = check_kill_switch(Brand.CAT, Platform.YOUTUBE, {})
    assert v.action is GuardAction.ALLOW


def test_kill_switch_holds_on_repeated_platform_warnings():
    v = check_kill_switch(Brand.CAT, Platform.YOUTUBE, {"platform_warnings": 2})
    assert v.action is GuardAction.HOLD
    assert "警告" in v.reason


def test_kill_switch_holds_on_consecutive_publish_failures():
    v = check_kill_switch("dog", "tiktok", {"consecutive_publish_failures": 3})
    assert v.action is GuardAction.HOLD


def test_kill_switch_high_risk_policy_diff_holds():
    v = check_kill_switch(Brand.ADULT, Platform.X,
                          {"policy_diff": True, "brand_policy_risk": "HIGH"})
    assert v.action is GuardAction.HOLD
    assert "HIGH RISK" in v.reason


def test_kill_switch_low_risk_policy_diff_does_not_fire():
    v = check_kill_switch(Brand.CAT, Platform.YOUTUBE,
                          {"policy_diff": True, "brand_policy_risk": "LOW"})
    assert v.action is GuardAction.ALLOW


def test_kill_switch_budget_exceeded_stops_new_generation():
    v = check_kill_switch(Brand.CAT, Platform.YOUTUBE, {"budget_exceeded": True})
    assert v.action is GuardAction.STOP_NEW_GENERATION


def test_kill_switch_sheets_failure_stops_everything():
    v = check_kill_switch(Brand.CAT, Platform.YOUTUBE, {"cannot_record_post_id": True})
    assert v.action is GuardAction.STOP


def test_kill_switch_returns_most_severe_of_multiple():
    v = check_kill_switch(Brand.CAT, Platform.YOUTUBE,
                          {"platform_warnings": 5, "cannot_record_post_id": True})
    assert v.action is GuardAction.STOP
    assert len(v.triggers) == 2  # 両方の理由が残る


def test_kill_switch_threshold_override():
    sig = {"platform_warnings": 1}
    assert check_kill_switch(Brand.CAT, Platform.X, sig).action is GuardAction.ALLOW
    v = check_kill_switch(Brand.CAT, Platform.X, sig, thresholds={"platform_warnings": 1})
    assert v.action is GuardAction.HOLD


# --- combine -----------------------------------------------------------

def test_combine_all_allow_is_allow():
    assert combine([
        GuardVerdict(GuardAction.ALLOW, "a"), GuardVerdict(GuardAction.ALLOW, "b"),
    ]).action is GuardAction.ALLOW


def test_combine_picks_widest_action():
    v = combine([
        GuardVerdict(GuardAction.HOLD, "", ["h"]),
        GuardVerdict(GuardAction.STOP, "", ["s"]),
        GuardVerdict(GuardAction.ALLOW, "ok"),
    ])
    assert v.action is GuardAction.STOP
    assert v.triggers == ["h", "s"]
