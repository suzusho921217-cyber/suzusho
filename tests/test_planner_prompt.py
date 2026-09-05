"""render_prompt（§12 企画 → 日本語生成プロンプト）のテスト。"""

from src.common.models import (
    Brand,
    ContentPlan,
    ExperimentFlag,
    Platform,
    PolicyRisk,
)
from src.planner.planner import render_prompt

CHARACTER = {
    "display_name": "ミケ",
    "appearance": "三毛猫。右耳が少し折れている",
    "personality": "のんびり屋",
    "voice": "落ち着いた低め",
    "world": "昭和の日本家屋",
    "forbidden": ["実在ブランドのロゴ"],
}


def _plan(**over) -> ContentPlan:
    base = {
        "plan_id": "2026-08-31-cat-01", "date": "2026-08-31", "brand": Brand.CAT,
        "concept_tag": "違和感", "hook_type": "0.5秒異常", "character_id": "CAT_001",
        "reality_level": 5, "oddity_level": 2, "duration_target_sec": 10,
        "experiment_flag": ExperimentFlag.EXPLORE, "policy_risk": PolicyRisk.LOW,
        "prompt_version": "v1", "target_platforms": [Platform.YOUTUBE],
        "notes": "explore: test",
    }
    base.update(over)
    return ContentPlan(**base)


def test_prompt_includes_core_fields():
    text = render_prompt(_plan(), CHARACTER, ["実在の人物・団体の模倣"])
    assert "ミケ" in text
    assert "三毛猫" in text
    assert "違和感" in text
    assert "0.5秒異常" in text
    assert "10秒" in text
    assert "9:16" in text


def test_prompt_merges_forbidden_without_duplicates():
    text = render_prompt(_plan(), CHARACTER, ["実在ブランドのロゴ", "露骨な性的表現"])
    # character と brand で重複した "実在ブランドのロゴ" は1回だけ
    assert text.count("実在ブランドのロゴ") == 1
    assert "露骨な性的表現" in text
    assert "ウォーターマーク" in text


def test_prompt_level_descriptions_change_with_level():
    low = render_prompt(_plan(reality_level=1, oddity_level=1), CHARACTER)
    high = render_prompt(_plan(reality_level=5, oddity_level=5), CHARACTER)
    assert "イラスト調" in low
    assert "実写と見分けがつかない" in high
    assert low != high


def test_prompt_clamps_out_of_range_level():
    text = render_prompt(_plan(reality_level=9, oddity_level=0), CHARACTER)
    assert "実写と見分けがつかない" in text  # 9 -> 5
    assert "違和感なし" in text              # 0 -> 1


def test_prompt_handles_missing_character_fields():
    text = render_prompt(_plan(), {}, None)
    assert "cat_character" in text
    assert "【禁止】" in text  # 空フィールドでも壊れず組み上がる
