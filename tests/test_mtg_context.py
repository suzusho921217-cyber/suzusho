"""mtg の会議資料: 投稿実績が新しい分まで見えること・月初に前月の生成費を今月分と読まないこと。"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.common.models import Brand, Platform, PostStatus
from src.mtg import context

JST = timezone(timedelta(hours=9))


class _Store:
    def __init__(self, n):
        start = datetime(2026, 9, 5, 12, 0, tzinfo=JST)
        self.posts = [
            SimpleNamespace(
                status=PostStatus.PUBLISHED, platform_post_id=f"v{i}", post_key=f"k{i}",
                brand=Brand.CAT, platform=Platform.YOUTUBE, concept_tag="二段オチ",
                hook_type="いきなりドアップ", published_at=start + timedelta(days=i // 2),
            )
            for i in range(n)
        ]

    def list_posts(self):
        return self.posts

    def list_snapshots(self, *, post_key):
        return [SimpleNamespace(snapshot="latest", views=int(post_key[1:]), likes=None, comments=None,
                                shares=None, saved=None, avg_watch_sec=None, views_delta=None)]


def test_performance_summary_shows_latest_posts_even_when_many():
    out = context._performance_summary(_Store(120))
    summary = json.loads(out.split("### 直近")[0].split("\n", 1)[1])
    assert summary[0]["投稿数"] == 120
    assert summary[0]["最新の投稿"] == "2026-11-03"  # 古い順に切り捨てて最新が消える、を防ぐ
    recent = json.loads(out.rsplit("\n", 1)[1])
    assert len(recent) == 30 and recent[0]["published_at"].startswith("2026-11-03")
    assert len(out) < 9000


def test_spend_summary_ignores_previous_month(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "STATE_DIR", tmp_path)
    (tmp_path / "spend.json").write_text(json.dumps({"month_key": "2000-01", "month": 3616, "total": 3616}))
    out = json.loads(context._spend_summary(context.load("budget")))
    assert out["今月の生成費"] == 0
    assert out["残り(停止までに使える額)"] > 1134
