"""strategy-review: 現在地の集計・週次所見の更新・JSON抽出の配線を確認する。

実際の Claude API 呼び出しはしない（call_role をモック）。
"""

import json
from datetime import datetime, timedelta, timezone

from src.common.models import (
    AccountDaily,
    Brand,
    PerformanceSnapshot,
    Platform,
    PolicyDecision,
    Post,
    PostStatus,
)
from src.sheets.client import LocalStore
from src.strategy import review

JST = timezone(timedelta(hours=9))

_ROADMAP_TEMPLATE = """# 収益化ロードマップ

本文いろいろ

<!-- WEEKLY:START -->
## 週次所見

_まだ初回レビューが走っていません。_
<!-- WEEKLY:END -->
"""


def _post(key="p1:youtube", **over):
    base = {
        "post_key": key, "master_video_id": "p1", "brand": Brand.CAT,
        "platform": Platform.YOUTUBE, "account_id": "cat-youtube", "concept_tag": "違和感",
        "hook_type": "0.5秒異常", "character_id": "CAT_001", "duration_sec": 10,
        "oddity_level": 2, "prompt_version": "v1", "generation_cost_jpy": 120.0,
        "policy_version": "yt-2026-08", "policy_result": PolicyDecision.PASS,
        "status": PostStatus.PUBLISHED,
        "published_at": datetime(2026, 9, 1, 12, 0, tzinfo=JST),
        "platform_post_id": "yt-abc",
    }
    base.update(over)
    return Post(**base)


# ---------------------------------------------------------------- 現在地の集計

def test_standing_aggregates_views_posts_and_followers(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_post(_post("p1:youtube", platform_post_id="yt-1"))
    store.upsert_post(_post("p2:youtube", platform_post_id="yt-2", brand=Brand.DOG))
    store.upsert_snapshot(PerformanceSnapshot(
        post_key="p1:youtube", snapshot="latest",
        collected_at=datetime(2026, 9, 3, tzinfo=JST), views=1000,
    ))
    store.upsert_snapshot(PerformanceSnapshot(
        post_key="p2:youtube", snapshot="latest",
        collected_at=datetime(2026, 9, 3, tzinfo=JST), views=500,
    ))
    store.upsert_account_daily(AccountDaily(
        date="2026-09-05", brand=Brand.CAT, platform=Platform.YOUTUBE,
        account_id="cat-youtube", followers=120,
    ))

    got = review._standing_by_platform(store)

    assert got["youtube"]["posts"] == 2
    assert got["youtube"]["cumulative_views"] == 1500
    assert got["youtube"]["brands"] == ["cat", "dog"]
    assert got["youtube"]["followers"] == {"cat": 120}
    # 未投稿の媒体も明示される
    assert got["tiktok"]["posts"] == 0
    assert got["instagram"]["posts"] == 0


def test_standing_ignores_unpublished_posts(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_post(_post("p1:youtube", status=PostStatus.PUBLISHING, platform_post_id=None))
    got = review._standing_by_platform(store)
    assert got["youtube"]["posts"] == 0


def test_standing_picks_latest_follower_count(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_post(_post("p1:instagram", platform=Platform.INSTAGRAM,
                            account_id="cat-ig", platform_post_id="ig-1"))
    store.upsert_account_daily(AccountDaily(
        date="2026-09-01", brand=Brand.CAT, platform=Platform.INSTAGRAM,
        account_id="cat-ig", followers=80,
    ))
    store.upsert_account_daily(AccountDaily(
        date="2026-09-06", brand=Brand.CAT, platform=Platform.INSTAGRAM,
        account_id="cat-ig", followers=95,
    ))
    got = review._standing_by_platform(store)
    assert got["instagram"]["followers"] == {"cat": 95}


# ---------------------------------------------------------------- 週次所見の更新

def test_update_roadmap_replaces_marker_block(tmp_path, monkeypatch):
    path = tmp_path / "monetization_roadmap.md"
    path.write_text(_ROADMAP_TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(review, "ROADMAP_PATH", path)

    changed = review._update_roadmap("YouTube は登録者120人。R&D段階。", "2026-09-07")

    assert changed is True
    text = path.read_text(encoding="utf-8")
    assert "YouTube は登録者120人" in text
    assert "2026-09-07 更新" in text
    # マーカーは残る（次回also更新できる）
    assert "<!-- WEEKLY:START -->" in text and "<!-- WEEKLY:END -->" in text
    assert "初回レビューが走っていません" not in text
    # マーカー外の本文は消えない
    assert "本文いろいろ" in text


def test_update_roadmap_noop_on_empty_note(tmp_path, monkeypatch):
    path = tmp_path / "monetization_roadmap.md"
    path.write_text(_ROADMAP_TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(review, "ROADMAP_PATH", path)
    assert review._update_roadmap("   ", "2026-09-07") is False
    assert path.read_text(encoding="utf-8") == _ROADMAP_TEMPLATE


def test_update_roadmap_noop_when_marker_absent(tmp_path, monkeypatch):
    path = tmp_path / "monetization_roadmap.md"
    path.write_text("# マーカーが無いファイル\n", encoding="utf-8")
    monkeypatch.setattr(review, "ROADMAP_PATH", path)
    assert review._update_roadmap("何か", "2026-09-07") is False


# ---------------------------------------------------------------- run() の配線

def _fenced(payload: dict) -> str:
    return f"所見の文章\n\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```"


def test_run_extracts_json_and_updates_roadmap(tmp_path, monkeypatch):
    path = tmp_path / "monetization_roadmap.md"
    path.write_text(_ROADMAP_TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(review, "ROADMAP_PATH", path)
    monkeypatch.setattr(review, "gather_strategy_context", lambda: "CONTEXT")

    payload = {
        "headline": "まだR&D。YouTubeに寄せる。",
        "platforms": {"youtube": {"standing": "登録120人"}},
        "weekly_note": "YouTube 登録者120人。本収益化まで遠い。",
        "needs_user_approval": [],
    }

    def fake_call_role(system, content, *, with_web_search=False, **kw):
        assert system is review.roles.STRATEGIST_SYSTEM
        assert with_web_search is True
        return _fenced(payload)

    monkeypatch.setattr(review, "call_role", fake_call_role)

    result = review.run()

    assert result.error is None
    assert result.parsed["headline"] == "まだR&D。YouTubeに寄せる。"
    assert result.roadmap_updated is True
    assert "登録者120人" in path.read_text(encoding="utf-8")


def test_run_sets_error_when_no_json(tmp_path, monkeypatch):
    path = tmp_path / "monetization_roadmap.md"
    path.write_text(_ROADMAP_TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(review, "ROADMAP_PATH", path)
    monkeypatch.setattr(review, "gather_strategy_context", lambda: "CONTEXT")
    monkeypatch.setattr(review, "call_role", lambda *a, **k: "JSONのない文章")

    result = review.run()

    assert result.error is not None
    assert result.roadmap_updated is False
    assert path.read_text(encoding="utf-8") == _ROADMAP_TEMPLATE
