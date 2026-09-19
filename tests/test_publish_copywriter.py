"""publishers.copywriter: キー無し/失敗時は None（従来テンプレートに戻る）。"""

from src.common.models import Brand, ContentPlan, ExperimentFlag, PolicyRisk
from src.publishers import copywriter


def _plan():
    return ContentPlan(
        plan_id="p", date="2026-09-19", brand=Brand.CAT, concept_tag="違和感", hook_type="0.5秒異常",
        character_id="CAT_001", reality_level=4, oddity_level=2, duration_target_sec=8,
        experiment_flag=ExperimentFlag.EXPLORE, policy_risk=PolicyRisk.LOW, prompt_version="v1",
    )


def test_returns_none_without_api_key(monkeypatch):
    monkeypatch.setattr(copywriter, "env", lambda *a, **k: None)
    assert copywriter.write_copy(_plan(), "youtube") is None


def test_returns_none_on_api_failure(monkeypatch):
    monkeypatch.setattr(copywriter, "env", lambda *a, **k: "key")
    from src.mtg import client

    def boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr(client, "call_role", boom)
    assert copywriter.write_copy(_plan(), "youtube") is None


def test_parses_json_reply(monkeypatch):
    monkeypatch.setattr(copywriter, "env", lambda *a, **k: "key")
    from src.mtg import client

    monkeypatch.setattr(
        client, "call_role",
        lambda *a, **k: 'はい {"title": "え、そこ入るの？🐱", "body": "ぴったりハマる子猫。どう思う？"}',
    )
    assert copywriter.write_copy(_plan(), "youtube") == ("え、そこ入るの？🐱", "ぴったりハマる子猫。どう思う？")
