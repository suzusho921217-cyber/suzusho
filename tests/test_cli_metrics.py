"""cli metrics の配線テスト＋ 生成→投稿→回収→学習→企画 のフルループ。"""

import json

import pytest

from src.cli import main


@pytest.fixture
def published(tmp_path, monkeypatch):
    monkeypatch.setattr("src.cli.STATE_DIR", tmp_path)
    out = tmp_path / "plan-2026-09-02.json"
    main(["plan-daily", "--date", "2026-09-02", "--out", str(out),
          "--winning-tags", str(tmp_path / "missing.json")])
    main(["generate", "--date", "2026-09-02"])
    main(["poll-generation", "--date", "2026-09-02"])
    main(["publish", "--date", "2026-09-02"])
    return tmp_path


def test_metrics_collects_and_writes_performance(published):
    rc = main(["metrics"])
    assert rc == 0
    perf = json.loads((published / "performance.json").read_text(encoding="utf-8"))
    assert len(perf["records"]) == 6
    rec = perf["records"][0]
    assert rec["post"]["reality_level"] is not None
    # published_at は今なので latest だけ回収される（24h 未経過）
    assert set(rec["snapshots"]) == {"latest"}
    assert rec["snapshots"]["latest"]["views"] > 0


def test_metrics_is_incremental(published):
    main(["metrics"])
    main(["metrics"])
    snaps = json.loads((published / "db" / "snapshots.json").read_text(encoding="utf-8"))
    # latest は毎回追記されるので 2 回 × 6 投稿 = 12
    assert len(snaps) == 12


def test_full_loop_metrics_to_performance_mode_plan(published):
    main(["metrics"])
    main(["daily-learning"])  # 既定入力 = .state/performance.json
    wt = json.loads((published / "winning_tags.json").read_text(encoding="utf-8"))
    # dummy 指標なので勝ちタグが出るか 0 件か（min_posts 次第）は問わない。形だけ確認
    assert "winning_tags" in wt

    out = published / "plan-next.json"
    rc = main(["plan-daily", "--date", "2026-09-03", "--out", str(out),
               "--winning-tags", str(published / "winning_tags.json")])
    assert rc == 0
