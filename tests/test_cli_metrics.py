"""cli metrics の配線テスト＋ 生成→投稿→回収→学習→企画 のフルループ。"""

import json
from datetime import datetime, timezone

import pytest

import src.cli as cli_module
from src.cli import main
from src.common.models import AccountDaily, Brand, Platform, PolicyDecision, Post, PostStatus
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
    assert len(perf["records"]) == 12  # 6企画 × youtube/instagram の2媒体
    rec = perf["records"][0]
    assert rec["post"]["reality_level"] is not None
    # published_at は今なので latest だけ回収される（24h 未経過）
    assert set(rec["snapshots"]) == {"latest"}
    assert rec["snapshots"]["latest"]["views"] > 0


def test_metrics_latest_is_upserted_not_duplicated(published):
    main(["metrics"])
    main(["metrics"])
    snaps = json.loads((published / "db" / "snapshots.json").read_text(encoding="utf-8"))
    # latest は履歴を残さず上書きされるので、2回実行しても 12投稿分のまま
    assert len(snaps) == 12


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


def test_metrics_tracks_followers_before_and_after(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_module, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cli_module, "get_publisher", lambda platform, brand=None: _FakeAccountPublisher())

    store = LocalStore(tmp_path / "db")
    store.upsert_account_daily(AccountDaily(
        date="2026-09-03", brand=Brand.DOG, platform=Platform.YOUTUBE,
        account_id="dog-youtube", followers=100,
    ))
    store.upsert_post(Post(
        post_key="p1:youtube", master_video_id="p1", brand=Brand.DOG,
        platform=Platform.YOUTUBE, account_id="dog-youtube", concept_tag="c",
        hook_type="h", character_id="DOG_001", duration_sec=8, oddity_level=1,
        prompt_version="v1", generation_cost_jpy=32.0, policy_version="v1",
        policy_result=PolicyDecision.PASS, status=PostStatus.PUBLISHED,
        published_at=datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc),
        platform_post_id="yt-1",
    ))

    rc = main(["metrics"])
    assert rc == 0

    snaps = store.list_snapshots(post_key="p1:youtube")
    assert len(snaps) == 1
    assert snaps[0].followers_before == 100
    assert snaps[0].followers_after == 120
    assert snaps[0].followers_delta == 20

    today_rows = [a for a in store.list_account_daily() if a.account_id == "dog-youtube"]
    todays = max(today_rows, key=lambda a: a.date)
    assert todays.followers == 120


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
