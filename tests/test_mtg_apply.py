"""agent-mtg apply.py: 許可された3種類の自動反映のみ通ることを確認する。

金額・ポリシーが絡む変更が構造的に不可能であることが一番大事なテスト。
"""

import shutil

import pytest
from ruamel.yaml import YAML

from src.mtg import apply as apply_module
from src.mtg.apply import (
    ApplyError,
    apply_add_concept_tag,
    apply_add_hook_type,
    apply_all,
    apply_retire_concept_tag,
    apply_retire_hook_type,
    apply_set_allocation_ratio,
    apply_set_hashtags,
    apply_set_level_range,
)

_REAL_CONFIG_DIR = apply_module.CONFIG_DIR


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    d = tmp_path / "config"
    d.mkdir()
    shutil.copy(_REAL_CONFIG_DIR / "planning.yaml", d / "planning.yaml")
    shutil.copy(_REAL_CONFIG_DIR / "hashtags.yaml", d / "hashtags.yaml")
    shutil.copy(_REAL_CONFIG_DIR / "scoring.yaml", d / "scoring.yaml")
    monkeypatch.setattr(apply_module, "CONFIG_DIR", d)
    return d


def _load(path):
    return YAML().load(path.read_text(encoding="utf-8"))


def test_add_concept_tag_appends_and_is_idempotent(config_dir):
    msg = apply_add_concept_tag("cat", "テスト企画タグ")
    assert "[applied]" in msg
    data = _load(config_dir / "planning.yaml")
    assert "テスト企画タグ" in data["brands"]["cat"]["concept_tags"]

    # 2回目は重複追加しない
    msg2 = apply_add_concept_tag("cat", "テスト企画タグ")
    assert "[skip]" in msg2
    data2 = _load(config_dir / "planning.yaml")
    assert data2["brands"]["cat"]["concept_tags"].count("テスト企画タグ") == 1


def test_add_hook_type_appends(config_dir):
    apply_add_hook_type("dog", "テストフック")
    data = _load(config_dir / "planning.yaml")
    assert "テストフック" in data["brands"]["dog"]["hook_types"]


def test_add_concept_tag_rejects_unknown_brand(config_dir):
    with pytest.raises(ApplyError):
        apply_add_concept_tag("adult", "x")  # brands.yamlにはあるがmtg対象外の想定


def test_set_hashtags_replaces_pool_only(config_dir):
    before = _load(config_dir / "hashtags.yaml")
    always_before = list(before["cat"]["instagram"]["always"])
    per_video_before = before["cat"]["instagram"]["per_video"]

    apply_set_hashtags("cat", "instagram", ["#新タグ1", "#新タグ2"])

    after = _load(config_dir / "hashtags.yaml")
    assert after["cat"]["instagram"]["pool"] == ["#新タグ1", "#新タグ2"]
    assert after["cat"]["instagram"]["always"] == always_before  # alwaysは不変
    assert after["cat"]["instagram"]["per_video"] == per_video_before  # per_videoも不変


def test_set_hashtags_rejects_tags_without_hash_prefix(config_dir):
    with pytest.raises(ApplyError):
        apply_set_hashtags("cat", "instagram", ["タグ1（#無し）"])


def test_set_hashtags_rejects_unknown_platform(config_dir):
    with pytest.raises(ApplyError):
        apply_set_hashtags("cat", "facebook", ["#a"])


def test_retire_concept_tag_removes_but_keeps_minimum(config_dir):
    data = _load(config_dir / "planning.yaml")
    tags = list(data["brands"]["dog"]["concept_tags"])
    assert apply_retire_concept_tag("dog", tags[0]).startswith("[applied]")
    assert tags[0] not in _load(config_dir / "planning.yaml")["brands"]["dog"]["concept_tags"]
    # 最低数まで減ったら削らない
    d2 = _load(config_dir / "planning.yaml")
    while len(d2["brands"]["dog"]["concept_tags"]) > 3:
        apply_retire_concept_tag("dog", d2["brands"]["dog"]["concept_tags"][0])
        d2 = _load(config_dir / "planning.yaml")
    with pytest.raises(ApplyError):
        apply_retire_concept_tag("dog", d2["brands"]["dog"]["concept_tags"][0])


def test_retire_hook_type_missing_is_skip(config_dir):
    assert "[skip]" in apply_retire_hook_type("cat", "存在しないフック")


def test_set_level_range_validates(config_dir):
    assert apply_set_level_range("cat", "oddity", 2, 4).startswith("[applied]")
    assert _load(config_dir / "planning.yaml")["brands"]["cat"]["oddity_level"] == [2, 4]
    with pytest.raises(ApplyError):
        apply_set_level_range("cat", "reality", 3, 9)      # 5超え
    with pytest.raises(ApplyError):
        apply_set_level_range("cat", "reality", 5, 2)      # min>max
    with pytest.raises(ApplyError):
        apply_set_level_range("cat", "bogus", 1, 2)        # 未知の軸


def test_set_allocation_ratio_validates_sum_and_bounds(config_dir):
    assert apply_set_allocation_ratio(0.6, 0.4).startswith("[applied]")
    a = _load(config_dir / "scoring.yaml")["allocation"]
    assert a["exploit_ratio"] == 0.6 and a["explore_ratio"] == 0.4
    with pytest.raises(ApplyError):
        apply_set_allocation_ratio(0.6, 0.3)     # 合計1でない
    with pytest.raises(ApplyError):
        apply_set_allocation_ratio(0.95, 0.05)   # exploit 上限0.9超え


def test_apply_all_still_rejects_money_kinds(config_dir):
    results = apply_all([
        {"kind": "set_daily_slots", "count": 20},         # 本数=お金 → 許可外
        {"kind": "set_video_duration", "seconds": 30},    # 尺=お金 → 許可外
    ])
    assert all("[rejected]" in r for r in results)


def test_apply_all_rejects_unknown_kind(config_dir):
    results = apply_all([
        {"kind": "boost_ad_spend", "brand": "cat", "amount_jpy": 10000},  # 許可外
    ])
    assert len(results) == 1
    assert "[rejected]" in results[0]
    assert "未知のkind" in results[0]


def test_apply_all_continues_after_one_failure(config_dir):
    results = apply_all([
        {"kind": "add_concept_tag", "brand": "cat", "tag": "案A"},
        {"kind": "add_concept_tag", "brand": "unknown-brand", "tag": "案B"},
        {"kind": "add_hook_type", "brand": "dog", "hook": "案C"},
    ])
    assert "[applied]" in results[0]
    assert "[rejected]" in results[1]
    assert "[applied]" in results[2]
