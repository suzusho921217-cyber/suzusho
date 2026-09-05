"""metrics.collector（§10.2）: due_snapshots / collect_snapshot。"""

from datetime import datetime, timedelta, timezone

from src.common.models import Brand, Platform, PolicyDecision, Post, PostStatus
from src.metrics.collector import collect_snapshot, due_snapshots

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=JST)


def _post(published_offset_days=5.0, **over):
    base = {
        "post_key": "p1:youtube", "master_video_id": "p1", "brand": Brand.CAT,
        "platform": Platform.YOUTUBE, "account_id": "cat-youtube", "concept_tag": "違和感",
        "hook_type": "0.5秒異常", "character_id": "CAT_001", "duration_sec": 10,
        "oddity_level": 2, "reality_level": 4, "prompt_version": "v1",
        "generation_cost_jpy": 100.0, "policy_version": "v1",
        "policy_result": PolicyDecision.PASS, "status": PostStatus.PUBLISHED,
        "published_at": NOW - timedelta(days=published_offset_days),
        "platform_post_id": "yt-1",
    }
    base.update(over)
    return Post(**base)


# --- due_snapshots -----------------------------------------------------

def test_due_none_before_24h():
    assert due_snapshots(_post(published_offset_days=0.5), NOW) == ["latest"]


def test_due_includes_windows_passed():
    assert set(due_snapshots(_post(published_offset_days=4), NOW)) == {"24h", "72h", "latest"}
    assert set(due_snapshots(_post(published_offset_days=8), NOW)) == {"24h", "72h", "7d", "latest"}


def test_due_excludes_already_collected():
    due = due_snapshots(_post(published_offset_days=8), NOW, existing_labels={"24h", "72h"})
    assert set(due) == {"7d", "latest"}


def test_due_latest_stops_after_30d():
    assert due_snapshots(_post(published_offset_days=40), NOW,
                         existing_labels={"24h", "72h", "7d"}) == []


def test_due_empty_when_not_published():
    assert due_snapshots(_post(published_at=None), NOW) == []


# --- collect_snapshot -------------------------------------------------

def test_collect_normalizes_platform_keys():
    raw = {"video_views": 5000, "favorites": 200, "replies": 30, "reposts": 40,
           "impression_count": 12000, "average_view_duration_sec": 6.0}
    s = collect_snapshot(_post(), "24h", raw, now=NOW)
    assert s.views == 5000 and s.likes == 200 and s.comments == 30 and s.shares == 40
    assert s.impressions == 12000
    assert s.completion_rate == 0.6          # 6.0s / 10s
    assert s.snapshot == "24h" and s.post_key == "p1:youtube"


def test_collect_completion_from_playback_100():
    raw = {"views": 1000, "playback_100": 250}
    assert collect_snapshot(_post(), "72h", raw, now=NOW).completion_rate == 0.25


def test_collect_completion_explicit_wins_and_clamps():
    raw = {"views": 1000, "completion_rate": 1.4, "playback_100": 10}
    assert collect_snapshot(_post(), "72h", raw, now=NOW).completion_rate == 1.0


def test_collect_follow_rate_from_gained_without_baseline():
    raw = {"views": 1000, "subscribers_gained": 12}
    s = collect_snapshot(_post(), "24h", raw, now=NOW)
    assert s.followers_before == 0 and s.followers_after == 12  # 転換率 = 12/1000


def test_collect_follow_rate_with_baseline():
    raw = {"views": 1000, "followers_gained": 12}
    s = collect_snapshot(_post(), "24h", raw, followers_before=500, now=NOW)
    assert s.followers_before == 500 and s.followers_after == 512


def test_collect_missing_metrics_stay_none():
    s = collect_snapshot(_post(), "latest", {}, now=NOW)
    assert s.views is None and s.completion_rate is None and s.revenue_jpy is None
    assert s.followers_after is None


def test_collect_revenue_alias():
    s = collect_snapshot(_post(), "7d", {"estimatedRevenue": 123.45}, now=NOW)
    assert s.revenue_jpy == 123.45
