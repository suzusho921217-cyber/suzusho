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
    apply_set_hashtags,
)

_REAL_CONFIG_DIR = apply_module.CONFIG_DIR


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    d = tmp_path / "config"
    d.mkdir()
    shutil.copy(_REAL_CONFIG_DIR / "planning.yaml", d / "planning.yaml")
    shutil.copy(_REAL_CONFIG_DIR / "hashtags.yaml", d / "hashtags.yaml")
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
