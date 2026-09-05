"""Metrics Collector: 24h / 72h / 7d / latest の指標回収（§4 §10.2）。

metrics.yml が定期実行。各投稿について「今回どの snapshot を取るべきか」を決め、
Publisher.fetch_metrics() の生レスポンスを PerformanceSnapshot に正規化する。
取得できない指標は None のまま（学習側で重みを再正規化）。

- due_snapshots     … published_at からの経過時間で回収対象ラベルを返す（純粋関数）
- collect_snapshot  … 生の指標 dict + Post → PerformanceSnapshot（純粋関数）
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from src.common.models import PerformanceSnapshot, Post

JST = timezone(timedelta(hours=9))

# 経過時間の窓（§10.2）。latest は毎回更新する rolling スナップショット。
_WINDOWS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "72h": timedelta(hours=72),
    "7d": timedelta(days=7),
}
_LATEST_UNTIL = timedelta(days=30)   # これ以降は latest も更新しない

# 生の API キー -> PerformanceSnapshot のフィールド（媒体差を吸収）
_ALIASES: dict[str, tuple[str, ...]] = {
    "views": ("views", "video_views", "view_count", "plays"),
    "engaged_views": ("engaged_views", "engagedViews"),
    "likes": ("likes", "like_count", "favorites"),
    "comments": ("comments", "comment_count", "replies"),
    "shares": ("shares", "share_count", "reposts", "retweets"),
    "impressions": ("impressions", "impression_count"),
    "avg_watch_sec": ("avg_watch_sec", "average_view_duration_sec", "averageViewDuration"),
    "revenue_jpy": ("revenue_jpy", "estimated_revenue_jpy", "estimatedRevenue"),
}
_INT_FIELDS = {"views", "engaged_views", "likes", "comments", "shares", "impressions"}


def due_snapshots(
    post: Post,
    now: datetime | None = None,
    *,
    existing_labels: Iterable[str] = (),
) -> list[str]:
    """この投稿について今回回収すべき snapshot ラベル。

    24h/72h/7d は「経過時間を超えていて、まだ取っていない」ものだけ。
    latest は公開後 30 日までは毎回対象（rolling）。
    """
    now = now or datetime.now(JST)
    if post.published_at is None:
        return []
    published = post.published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=JST)
    age = now - published
    if age < timedelta(0):
        return []

    have = set(existing_labels)
    due = [label for label, delta in _WINDOWS.items() if age >= delta and label not in have]
    if age <= _LATEST_UNTIL:
        due.append("latest")
    return due


def _pick(raw: Mapping[str, Any], field: str) -> Any:
    for key in _ALIASES[field]:
        if key in raw and raw[key] is not None:
            return raw[key]
    return None


def _num(value: Any, *, as_int: bool) -> Any:
    if value is None:
        return None
    try:
        return round(float(value)) if as_int else float(value)
    except (TypeError, ValueError):
        return None


def collect_snapshot(
    post: Post,
    label: str,
    raw: Mapping[str, Any],
    *,
    followers_before: int | None = None,
    now: datetime | None = None,
) -> PerformanceSnapshot:
    """生の指標 dict を PerformanceSnapshot に正規化する。

    Args:
        post: 対象投稿（completion_rate を尺から推定するのに duration_sec を使う）
        label: "24h" / "72h" / "7d" / "latest"
        raw: Publisher.fetch_metrics() の戻り。媒体ごとにキーが違う（_ALIASES で吸収）
        followers_before: 分かれば渡す。followers_gained と足して followers_after を出す
    """
    now = now or datetime.now(JST)
    fields = {f: _num(_pick(raw, f), as_int=f in _INT_FIELDS) for f in _ALIASES}

    # フォロー転換: gained が取れれば before と足す（before 不明なら両方 None のまま）
    gained = _num(
        raw.get("followers_gained")
        if raw.get("followers_gained") is not None
        else raw.get("subscribers_gained")
        if raw.get("subscribers_gained") is not None
        else raw.get("subscribersGained"),
        as_int=True,
    )
    # gained が分かれば before(不明なら 0 とみなす) と足して after を出す。
    # 学習側が見るのは (after-before)/views = フォロー転換率（§2）なので分子は as-is。
    followers_before_out = followers_before
    followers_after = None
    if gained is not None:
        base = followers_before if followers_before is not None else 0
        followers_before_out = base
        followers_after = base + gained

    # 完視聴率: 明示値 > 100%再生数/再生数 > 平均視聴秒/尺
    completion = _num(raw.get("completion_rate"), as_int=False)
    if completion is None:
        p100 = _num(raw.get("playback_100") or raw.get("playback_100_count"), as_int=True)
        if p100 is not None and fields["views"]:
            completion = p100 / fields["views"]
    if completion is None and fields["avg_watch_sec"] and post.duration_sec:
        completion = fields["avg_watch_sec"] / post.duration_sec
    if completion is not None:
        completion = max(0.0, min(1.0, completion))

    return PerformanceSnapshot(
        post_key=post.post_key,
        snapshot=label,
        collected_at=now,
        views=fields["views"],
        engaged_views=fields["engaged_views"],
        likes=fields["likes"],
        comments=fields["comments"],
        shares=fields["shares"],
        impressions=fields["impressions"],
        avg_watch_sec=fields["avg_watch_sec"],
        completion_rate=completion,
        followers_before=followers_before_out,
        followers_after=followers_after,
        revenue_jpy=fields["revenue_jpy"],
    )
