"""cli publish の配線テスト（plan → generate → poll → publish、dry-run）。"""

import json

import pytest

from src.cli import main
from src.common.config import load

_SLOTS = load("scoring")["allocation"]["total_daily_slots"]


@pytest.fixture
def generated(tmp_path, monkeypatch):
    monkeypatch.setattr("src.cli.STATE_DIR", tmp_path)
    out = tmp_path / "plan-2026-09-02.json"
    main(["plan-daily", "--date", "2026-09-02", "--out", str(out),
          "--winning-tags", str(tmp_path / "missing.json")])
    main(["generate", "--date", "2026-09-02"])
    main(["poll-generation", "--date", "2026-09-02"])
    return tmp_path


def _posts(root):
    return json.loads((root / "db" / "posts.json").read_text(encoding="utf-8"))


def test_publish_dryrun_publishes_all(generated):
    rc = main(["publish", "--date", "2026-09-02"])
    assert rc == 0
    pub = json.loads((generated / "publish-2026-09-02.json").read_text(encoding="utf-8"))
    assert pub["mode"] == "dryrun"
    assert {o["action"] for o in pub["outcomes"]} == {"PUBLISHED"}
    posts = _posts(generated)
    assert len(posts) == _SLOTS * 2  # 企画数 × youtube/instagram の2媒体
    assert all(p["status"] == "PUBLISHED" and p["platform_post_id"] for p in posts.values())


def test_publish_is_idempotent_second_run(generated):
    main(["publish", "--date", "2026-09-02"])
    main(["publish", "--date", "2026-09-02"])
    pub = json.loads((generated / "publish-2026-09-02.json").read_text(encoding="utf-8"))
    assert {o["action"] for o in pub["outcomes"]} == {"ALREADY_PUBLISHED"}
    assert len(_posts(generated)) == _SLOTS * 2  # 増えない
    # 2 回目は媒体 API を叩かず、管理DBの投稿済み記録だけで判定している
    assert all("管理DB" in "".join(o["reasons"]) for o in pub["outcomes"])


def test_publish_splits_generation_cost_across_platforms(tmp_path, monkeypatch):
    # 同じ動画を複数媒体に使い回す場合、生成費は媒体数で均等分割して記録される
    # （そのまま複製すると合計が実際の支出より水増しされるため）。
    monkeypatch.setattr("src.cli.STATE_DIR", tmp_path)
    plan = {
        "plan_id": "p1", "date": "2026-09-02", "brand": "dog",
        "concept_tag": "違和感", "hook_type": "0.5秒異常", "character_id": "DOG_001",
        "reality_level": 4, "oddity_level": 2, "duration_target_sec": 8,
        "experiment_flag": "explore", "policy_risk": "LOW", "prompt_version": "v1",
        "prompt_text": None, "target_platforms": ["youtube", "instagram"], "notes": "",
    }
    (tmp_path / "plan-2026-09-02.json").write_text(
        json.dumps({"date": "2026-09-02", "plans": [plan]}), encoding="utf-8"
    )
    (tmp_path / "jobs-2026-09-02.json").write_text(json.dumps({
        "date": "2026-09-02", "provider": "mock",
        "jobs": [{"job_id": "j1", "plan_id": "p1", "provider": "mock",
                  "status": "SUCCEEDED", "cost_jpy": 64.0, "local_path": "x.mp4"}],
        "skipped": [],
    }), encoding="utf-8")

    rc = main(["publish", "--date", "2026-09-02"])
    assert rc == 0
    posts = _posts(tmp_path)
    assert len(posts) == 2
    assert all(p["generation_cost_jpy"] == 32.0 for p in posts.values())


def test_publish_respects_guard_hold(generated):
    (generated / "guard.json").write_text(json.dumps({
        "targets": [{"brand": "cat", "platform": "youtube", "action": "HOLD",
                     "reasons": ["媒体警告が連続"]}],
    }), encoding="utf-8")
    main(["publish", "--date", "2026-09-02"])
    pub = json.loads((generated / "publish-2026-09-02.json").read_text(encoding="utf-8"))
    actions = {(o["plan_id"], o["action"]) for o in pub["outcomes"]}
    assert any(a == "HOLD_GUARD" for _, a in actions)
    # dog は止まらない
    assert any(o["action"] == "PUBLISHED" for o in pub["outcomes"])
