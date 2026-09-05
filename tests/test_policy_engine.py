"""ポリシーエンジン（§7 §8）: check_prompt / load_policy / is_policy_stale。"""

import json

import pytest

from src.common.models import (
    Brand,
    ContentPlan,
    ExperimentFlag,
    Platform,
    PolicyDecision,
    PolicyRisk,
)
from src.policy import engine
from src.policy.engine import (
    _condition_hits,
    check_prompt,
    is_policy_stale,
    load_policy,
    policy_version,
    requires_ai_disclosure,
)


def _plan(**over) -> ContentPlan:
    base = {
        "plan_id": "d-cat-01", "date": "2026-08-31", "brand": Brand.CAT,
        "concept_tag": "かわいい", "hook_type": "視線誘導", "character_id": "CAT_001",
        "reality_level": 5, "oddity_level": 1, "duration_target_sec": 10,
        "experiment_flag": ExperimentFlag.EXPLORE, "policy_risk": PolicyRisk.LOW,
        "prompt_version": "v1", "target_platforms": [Platform.YOUTUBE], "notes": "",
    }
    base.update(over)
    return ContentPlan(**base)


def test_load_policy_merges_common_and_platform():
    pol = load_policy(Platform.YOUTUBE)
    ids = {c["id"] for c in pol["checks"]}
    assert "no_real_person_mimicry" in ids      # _common 由来
    assert "yt_oddity_cap" in ids               # youtube 由来
    assert policy_version(Platform.YOUTUBE) == "2026-08-30"


def test_clean_plan_passes():
    res = check_prompt(_plan(), Platform.YOUTUBE)
    assert res.decision is PolicyDecision.PASS
    assert res.reasons == []
    assert res.policy_version == "2026-08-30"


def test_real_person_mimicry_holds():
    res = check_prompt(_plan(notes="実在の有名人そっくりに寄せる"), Platform.YOUTUBE)
    assert res.decision is PolicyDecision.HOLD
    assert any("模倣" in r for r in res.reasons)


def test_oddity_cap_regenerates():
    res = check_prompt(_plan(oddity_level=5), Platform.YOUTUBE)
    assert res.decision is PolicyDecision.REGENERATE


def test_adult_brand_high_risk_holds_and_lists_all_reasons():
    res = check_prompt(
        _plan(brand=Brand.ADULT, character_id="ADULT_001", policy_risk=PolicyRisk.HIGH),
        Platform.YOUTUBE,
    )
    # HIGH RISK(HOLD) と adult→SKIP_PLATFORM の両方が発火、重い方(HOLD)を返す
    assert res.decision is PolicyDecision.HOLD
    assert len(res.reasons) >= 2


def test_adult_low_risk_skips_platform_only():
    res = check_prompt(
        _plan(brand=Brand.ADULT, character_id="ADULT_001", policy_risk=PolicyRisk.LOW),
        Platform.YOUTUBE,
    )
    assert res.decision is PolicyDecision.SKIP_PLATFORM


def test_adult_blocked_on_tiktok_and_instagram():
    for platform in (Platform.TIKTOK, Platform.INSTAGRAM):
        res = check_prompt(
            _plan(brand=Brand.ADULT, character_id="ADULT_001", policy_risk=PolicyRisk.LOW),
            platform,
        )
        assert res.decision is PolicyDecision.SKIP_PLATFORM


def test_adult_not_prompt_blocked_on_x():
    # X は adult 可（video 段階でセンシティブラベル要求）。prompt 段階では素通り
    res = check_prompt(
        _plan(brand=Brand.ADULT, character_id="ADULT_001", policy_risk=PolicyRisk.LOW),
        Platform.X,
    )
    assert res.decision is PolicyDecision.PASS


def test_requires_ai_disclosure_all_platforms():
    for platform in Platform:
        assert requires_ai_disclosure(platform) is True


def test_condition_hits_rejects_unknown_type():
    with pytest.raises(ValueError, match="未知の条件タイプ"):
        _condition_hits({"nonsense_key": 1}, _plan())


def test_is_policy_stale_no_file_is_false(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    assert is_policy_stale(Platform.YOUTUBE) is False


def test_is_policy_stale_flag_true(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    (tmp_path / "policy_sync.json").write_text(
        json.dumps({"youtube": {"version": "2026-08-30", "stale": True}}), encoding="utf-8"
    )
    assert is_policy_stale(Platform.YOUTUBE) is True


def test_is_policy_stale_version_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    (tmp_path / "policy_sync.json").write_text(
        json.dumps({"youtube": {"version": "2099-01-01", "stale": False}}), encoding="utf-8"
    )
    assert is_policy_stale(Platform.YOUTUBE) is True


def test_is_policy_stale_matching_version_false(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "STATE_DIR", tmp_path)
    (tmp_path / "policy_sync.json").write_text(
        json.dumps({"youtube": {"version": "2026-08-30", "stale": False}}), encoding="utf-8"
    )
    assert is_policy_stale(Platform.YOUTUBE) is False
