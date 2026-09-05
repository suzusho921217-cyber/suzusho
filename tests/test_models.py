from datetime import datetime, timezone

from src.common.models import (
    Brand,
    PerformanceSnapshot,
    Platform,
    PolicyDecision,
    Post,
    PostStatus,
)


def test_snapshot_rate_handles_missing_denominator():
    snap = PerformanceSnapshot(
        post_key="p1", snapshot="24h", collected_at=datetime.now(timezone.utc),
        views=None, likes=10,
    )
    assert snap.rate(snap.likes) is None


def test_snapshot_rate_basic():
    snap = PerformanceSnapshot(
        post_key="p1", snapshot="24h", collected_at=datetime.now(timezone.utc),
        views=1000, likes=50,
    )
    assert snap.rate(snap.likes) == 0.05


def test_post_idempotency_key_fields_present():
    post = Post(
        post_key="cat-youtube-acc1-0001",
        master_video_id="mv-0001",
        brand=Brand.CAT,
        platform=Platform.YOUTUBE,
        account_id="acc1",
        concept_tag="違和感",
        hook_type="0.5秒異常",
        character_id="CAT_001",
        duration_sec=10,
        oddity_level=2,
        prompt_version="v1",
        generation_cost_jpy=0.0,
        policy_version="unset",
        policy_result=PolicyDecision.PASS,
        status=PostStatus.PLANNED,
    )
    # §15 冪等キー
    assert (post.master_video_id, post.platform, post.account_id) == (
        "mv-0001", Platform.YOUTUBE, "acc1",
    )
