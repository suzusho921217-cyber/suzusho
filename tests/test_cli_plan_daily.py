"""cli plan-daily の配線テスト（config → 配分 → 企画 → JSON 出力）。"""

import json

from src.cli import main
from src.common.config import load

_SLOTS = load("scoring")["allocation"]["total_daily_slots"]


def test_plan_daily_bootstrap_writes_file(tmp_path, capsys):
    out = tmp_path / "plan.json"
    rc = main(["plan-daily", "--date", "2026-08-31", "--out", str(out),
               "--winning-tags", str(tmp_path / "missing.json")])
    assert rc == 0

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["date"] == "2026-08-31"
    assert data["allocation"]["mode"] == "equal"
    assert len(data["plans"]) == _SLOTS
    assert {p["brand"] for p in data["plans"]} == {"cat", "dog"}
    assert all(p["experiment_flag"] == "explore" for p in data["plans"])
    assert all(p["target_platforms"] == ["youtube", "instagram"] for p in data["plans"])

    assert "mode=equal" in capsys.readouterr().out


def test_plan_daily_performance_mode_with_winning_tags(tmp_path):
    wt = tmp_path / "wt.json"
    wt.write_text(json.dumps([
        {"brand": "cat", "concept_tag": "驚き", "hook_type": "視線誘導",
         "platform": "youtube", "score": 0.8},
        {"brand": "cat", "concept_tag": "かわいい", "hook_type": "突然の動き",
         "platform": "youtube", "score": 0.5},
    ]), encoding="utf-8")
    out = tmp_path / "plan.json"

    rc = main(["plan-daily", "--date", "2026-09-01", "--out", str(out),
               "--winning-tags", str(wt)])
    assert rc == 0

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["allocation"]["mode"] == "performance"
    assert data["winning_tags_used"] == 2
    flags = [p["experiment_flag"] for p in data["plans"]]
    assert "exploit" in flags and "explore" in flags
    plan_ids = [p["plan_id"] for p in data["plans"]]
    assert len(plan_ids) == len(set(plan_ids)) == _SLOTS
