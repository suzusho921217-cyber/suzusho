"""cli generate / poll-generation の配線テスト（plan → jobs → 完了）。"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.cli import main
from src.common.config import load

_JST = timezone(timedelta(hours=9))
_SLOTS = load("scoring")["allocation"]["total_daily_slots"]  # 実 config の1日本数


@pytest.fixture
def plan_file(tmp_path):
    out = tmp_path / "plan-2026-09-02.json"
    main(["plan-daily", "--date", "2026-09-02", "--out", str(out),
          "--winning-tags", str(tmp_path / "missing.json")])
    return out


def test_generate_submits_all_plans_with_mock(plan_file, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("src.cli.STATE_DIR", tmp_path)
    rc = main(["generate", "--date", "2026-09-02"])
    assert rc == 0
    jobs = json.loads((tmp_path / "jobs-2026-09-02.json").read_text(encoding="utf-8"))
    assert jobs["provider"] == "mock"
    assert len(jobs["jobs"]) == _SLOTS
    assert jobs["skipped"] == []
    assert f"投入={_SLOTS}" in capsys.readouterr().out


def test_poll_generation_completes_mock_jobs(plan_file, tmp_path, monkeypatch):
    monkeypatch.setattr("src.cli.STATE_DIR", tmp_path)
    main(["generate", "--date", "2026-09-02"])
    rc = main(["poll-generation", "--date", "2026-09-02"])
    assert rc == 0
    jobs = json.loads((tmp_path / "jobs-2026-09-02.json").read_text(encoding="utf-8"))
    assert {j["status"] for j in jobs["jobs"]} == {"SUCCEEDED"}
    assert all(j["local_path"] for j in jobs["jobs"])


def test_generate_respects_budget_gate(plan_file, tmp_path, monkeypatch):
    monkeypatch.setattr("src.cli.STATE_DIR", tmp_path)
    now = datetime.now(_JST)
    # 今月の実績が既に月次上限（¥15,000）に達している状態を作る
    (tmp_path / "spend.json").write_text(json.dumps({
        "month_key": now.strftime("%Y-%m"), "month": 15000.0, "total": 15000.0,
        "day_key": now.date().isoformat(), "today": 0.0, "by_brand": {},
    }), encoding="utf-8")
    main(["generate", "--date", "2026-09-02"])
    jobs = json.loads((tmp_path / "jobs-2026-09-02.json").read_text(encoding="utf-8"))
    assert len(jobs["skipped"]) == _SLOTS
    assert jobs["jobs"] == []


def test_generate_tracks_spend_across_runs(plan_file, tmp_path, monkeypatch):
    """mock は原価0なので spend は増えないが、ファイルは作られ月キーが入る。"""
    monkeypatch.setattr("src.cli.STATE_DIR", tmp_path)
    main(["generate", "--date", "2026-09-02"])
    spend = json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))
    assert spend["month"] == 0.0  # mock
    assert "month_key" in spend


def test_generate_is_idempotent_across_runs(plan_file, tmp_path, monkeypatch, capsys):
    # generate は 1 日に複数回走る。2 回目は投入済みプランを再投入しない（二重生成防止）。
    monkeypatch.setattr("src.cli.STATE_DIR", tmp_path)
    main(["generate", "--date", "2026-09-02"])
    rc = main(["generate", "--date", "2026-09-02"])
    assert rc == 0
    jobs = json.loads((tmp_path / "jobs-2026-09-02.json").read_text(encoding="utf-8"))
    assert len(jobs["jobs"]) == _SLOTS  # 12 にならない
    assert "スキップ" in capsys.readouterr().out


def test_generate_without_plan_file_skips(tmp_path, monkeypatch, capsys):
    # plan がまだ無い（plan-daily 未実行）ときは失敗ではなくスキップ（exit 0）。
    # ワークフローが独立スケジュールで走るため、順番前後を失敗通知にしない。
    monkeypatch.setattr("src.cli.STATE_DIR", tmp_path)
    rc = main(["generate", "--date", "2099-01-01"])
    assert rc == 0
    assert "スキップ" in capsys.readouterr().out
