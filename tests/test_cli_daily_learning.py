"""cli daily-learning の配線テスト（成績 JSON → 勝ちタグ JSON）＋ planner へのループ結合。"""

import json
from datetime import datetime, timedelta, timezone

from src.cli import main

JST = timezone(timedelta(hours=9))


def _perf_file(tmp_path, n_posts=3, cr=0.8):
    recs = []
    for i in range(n_posts):
        published = (datetime.now(JST) - timedelta(days=i + 1)).isoformat()
        recs.append({
            "post": {
                "brand": "cat", "concept_tag": "驚き", "hook_type": "視線誘導",
                "character_id": "CAT_001", "reality_level": 4, "oddity_level": 2,
                "duration_target_sec": 10, "prompt_version": "v1", "platform": "youtube",
                "generation_cost_jpy": 120, "published_at": published,
            },
            "snapshots": {"7d": {
                "snapshot": "7d", "views": 5000, "shares": 200, "comments": 60,
                "completion_rate": cr, "followers_before": 1000, "followers_after": 1080,
            }},
        })
    p = tmp_path / "performance.json"
    p.write_text(json.dumps({"records": recs}, ensure_ascii=False), encoding="utf-8")
    return p


def test_daily_learning_bootstrap_when_no_input(tmp_path, capsys):
    out = tmp_path / "wt.json"
    rc = main(["daily-learning", "--input", str(tmp_path / "missing.json"), "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["records_in"] == 0
    assert data["winning_tags"] == []
    assert "ブートストラップ" in capsys.readouterr().out


def test_daily_learning_extracts_winning_tags(tmp_path):
    out = tmp_path / "wt.json"
    rc = main(["daily-learning", "--input", str(_perf_file(tmp_path)), "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["records_in"] == 3
    assert len(data["winning_tags"]) == 1
    w = data["winning_tags"][0]
    assert w["brand"] == "cat" and w["concept_tag"] == "驚き"
    assert 0.0 < w["score"] <= 1.0


def test_daily_learning_min_posts_gate(tmp_path):
    out = tmp_path / "wt.json"
    main(["daily-learning", "--input", str(_perf_file(tmp_path, n_posts=2)), "--out", str(out)])
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["winning_tags"] == []  # 2本 < min_posts_for_winner(3)


def test_learning_output_feeds_plan_daily(tmp_path):
    """daily-learning の出力を plan-daily がそのまま食える（ループが閉じる）。"""
    wt = tmp_path / "wt.json"
    main(["daily-learning", "--input", str(_perf_file(tmp_path)), "--out", str(wt)])

    plan_out = tmp_path / "plan.json"
    rc = main(["plan-daily", "--date", "2026-09-02", "--out", str(plan_out),
               "--winning-tags", str(wt)])
    assert rc == 0
    plan = json.loads(plan_out.read_text(encoding="utf-8"))
    assert plan["allocation"]["mode"] == "performance"
    assert plan["winning_tags_used"] == 1
    assert any(p["experiment_flag"] == "exploit" for p in plan["plans"])
