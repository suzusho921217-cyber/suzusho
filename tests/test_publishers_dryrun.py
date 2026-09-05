"""publishers.dryrun: 投稿しない・冪等・決定的 ID。"""

from src.common.models import Brand, Platform, PolicyDecision, Post, PostStatus
from src.publishers.base import PublishRequest
from src.publishers.dryrun import DryRunPublisher


def _post(**over):
    base = {
        "post_key": "p1:youtube", "master_video_id": "p1", "brand": Brand.CAT,
        "platform": Platform.YOUTUBE, "account_id": "cat-youtube", "concept_tag": "違和感",
        "hook_type": "0.5秒異常", "character_id": "CAT_001", "duration_sec": 10,
        "oddity_level": 2, "prompt_version": "v1", "generation_cost_jpy": 0.0,
        "policy_version": "v1", "policy_result": PolicyDecision.PASS,
        "status": PostStatus.PUBLISHING,
    }
    base.update(over)
    return Post(**base)


def _req(post):
    return PublishRequest(post=post, video_path="file://x.mp4", title="t", caption="c", tags=[])


def test_publish_returns_deterministic_id():
    pub = DryRunPublisher(Platform.YOUTUBE)
    r1 = pub.publish(_req(_post()))
    assert r1.ok and r1.platform_post_id.startswith("dryrun-youtube-")
    # 同じ冪等キーなら別インスタンスでも同じ ID
    r2 = DryRunPublisher(Platform.YOUTUBE).publish(_req(_post()))
    assert r2.platform_post_id == r1.platform_post_id


def test_publish_twice_is_idempotent():
    pub = DryRunPublisher(Platform.YOUTUBE)
    first = pub.publish(_req(_post()))
    second = pub.publish(_req(_post()))
    assert second.already_published is True
    assert second.platform_post_id == first.platform_post_id


def test_find_existing_after_publish():
    pub = DryRunPublisher(Platform.YOUTUBE)
    assert pub.find_existing(_post()) is None
    pub.publish(_req(_post()))
    assert pub.find_existing(_post()) is not None


def test_shared_ledger_persists_across_instances():
    ledger = {}
    DryRunPublisher(Platform.YOUTUBE, ledger=ledger).publish(_req(_post()))
    other = DryRunPublisher(Platform.YOUTUBE, ledger=ledger)
    assert other.find_existing(_post()) is not None


def test_fetch_metrics_is_deterministic_dummy():
    a = DryRunPublisher(Platform.X).fetch_metrics("dryrun-x-abc")
    b = DryRunPublisher(Platform.X).fetch_metrics("dryrun-x-abc")
    assert a == b and a["views"] > 0
    assert DryRunPublisher(Platform.X).fetch_metrics("other-id") != a
