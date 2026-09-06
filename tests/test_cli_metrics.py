"""cli metrics の配線テスト＋ 生成→投稿→回収→学習→企画 のフルループ。"""

import json
from datetime import datetime, timedelta, timezone

_JST = timezone(timedelta(hours=9))

import pytest

import src.cli as cli_module
from src.cli import main
from src.common.config import load

_SLOTS = load("scoring")["allocation"]["total_daily_slots"]
from src.common.models import Brand, Platform, PolicyDecision, Post, PostStatus
from src.publishers.base import Publisher, PublishResult
from src.sheets.client import LocalStore


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
    assert len(perf["records"]) == _SLOTS * 2  # 企画数 × youtube/instagram の2媒体
    rec = perf["records"][0]
    assert rec["post"]["reality_level"] is not None
    # published_at は今なので latest だけ回収される（24h 未経過）
    assert set(rec["snapshots"]) == {"latest"}
    assert rec["snapshots"]["latest"]["views"] > 0


def test_metrics_latest_is_upserted_not_duplicated(published):
    main(["metrics"])
    main(["metrics"])
    snaps = json.loads((published / "db" / "snapshots.json").read_text(encoding="utf-8"))
    # latest は履歴を残さず上書きされるので、2回実行しても投稿分のまま
    assert len(snaps) == _SLOTS * 2


class _FakeAccountPublisher(Publisher):
    platform = Platform.YOUTUBE

    def publish(self, req):
        return PublishResult(ok=True, platform_post_id="x")

    def find_existing(self, post):
        return None

    def fetch_metrics(self, platform_post_id):
        return {"views": 500}

    def fetch_account_followers(self):
        return 120


def test_metrics_daily_delta_is_vs_prev_day_not_prev_run(tmp_path, monkeypatch):
    """前日比: 1日に何回 metrics を回しても「前日の最終値」との差で出る（§10.2）。"""
    monkeypatch.setattr(cli_module, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cli_module, "get_publisher", lambda platform, brand=None: _FakeAccountPublisher())

    now = datetime.now(_JST)
    yday = (now - timedelta(days=1)).date().isoformat()
    store = LocalStore(tmp_path / "db")
    store.upsert_post(Post(
        post_key="p1:youtube", master_video_id="p1", brand=Brand.DOG,
        platform=Platform.YOUTUBE, account_id="dog-youtube", concept_tag="c",
        hook_type="h", character_id="DOG_001", duration_sec=8, oddity_level=1,
        prompt_version="v1", generation_cost_jpy=32.0, policy_version="v1",
        policy_result=PolicyDecision.PASS, status=PostStatus.PUBLISHED,
        published_at=now, platform_post_id="yt-1",
    ))
    # 昨日の最終計測: 再生 300 / フォロワー 100
    (tmp_path / "metrics_baseline.json").write_text(json.dumps({
        "p1:youtube": {"run_date": yday, "run_views": 300, "run_followers": 100},
    }), encoding="utf-8")

    # _FakeAccountPublisher は views=500 / followers=120 を返す
    assert main(["metrics"]) == 0
    assert main(["metrics"]) == 0  # 同じ日に2回目

    snaps = store.list_snapshots(post_key="p1:youtube")
    assert len(snaps) == 1
    assert snaps[0].views == 500
    assert snaps[0].views_delta == 200      # 500 - 300（前回実行比の 0 ではない）
    assert snaps[0].followers_after == 120
    assert snaps[0].followers_delta == 20   # 120 - 100

    today_rows = [a for a in store.list_account_daily() if a.account_id == "dog-youtube"]
    todays = max(today_rows, key=lambda a: a.date)
    assert todays.followers == 120


def test_metrics_aggregates_daily_account_stats(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_module, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cli_module, "get_publisher", lambda platform, brand=None: _FakeAccountPublisher())

    # cmd_metrics 側の「今日」判定はJST基準なので、テストの published_at もJSTで揃える
    # （UTCのままだとJST日付境界(0-9時)の実行だけ日付がズレて落ちる）。
    today = datetime.now(_JST)
    store = LocalStore(tmp_path / "db")
    for i in range(2):
        store.upsert_post(Post(
            post_key=f"p{i}:youtube", master_video_id=f"p{i}", brand=Brand.DOG,
            platform=Platform.YOUTUBE, account_id="dog-youtube", concept_tag="c",
            hook_type="h", character_id="DOG_001", duration_sec=8, oddity_level=1,
            prompt_version="v1", generation_cost_jpy=32.0, policy_version="v1",
            policy_result=PolicyDecision.PASS, status=PostStatus.PUBLISHED,
            published_at=today, platform_post_id=f"yt-{i}",
        ))
    (tmp_path / "guard.json").write_text(json.dumps({
        "targets": [{"brand": "dog", "platform": "youtube", "action": "HOLD",
                     "reasons": ["媒体警告が連続"]}],
    }), encoding="utf-8")

    rc = main(["metrics"])
    assert rc == 0

    rows = [a for a in store.list_account_daily() if a.account_id == "dog-youtube"]
    row = max(rows, key=lambda a: a.date)
    assert row.daily_posts == 2
    assert row.daily_views == 1000  # 500 views × 2投稿
    assert row.daily_api_cost_jpy == 64.0  # 32円 × 2投稿
    assert row.warnings == 1
    assert row.status == "HOLD"


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
