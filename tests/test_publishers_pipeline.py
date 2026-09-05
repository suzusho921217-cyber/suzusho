"""publishers.pipeline.decide_and_publish: 投稿可否の判定順（§8 §15）。"""

from src.common.guardrails import GuardAction, GuardVerdict
from src.common.models import (
    Brand,
    ContentPlan,
    ExperimentFlag,
    Platform,
    PolicyDecision,
    PolicyResult,
    PolicyRisk,
    Post,
    PostStatus,
)
from src.publishers import pipeline
from src.publishers.base import PublishRequest, PublishResult
from src.publishers.pipeline import decide_and_publish


def _plan(**over):
    base = {
        "plan_id": "p1", "date": "2026-09-02", "brand": Brand.CAT, "concept_tag": "違和感",
        "hook_type": "0.5秒異常", "character_id": "CAT_001", "reality_level": 4,
        "oddity_level": 2, "duration_target_sec": 10,
        "experiment_flag": ExperimentFlag.EXPLORE, "policy_risk": PolicyRisk.LOW,
        "prompt_version": "v1",
    }
    base.update(over)
    return ContentPlan(**base)


def _req():
    post = Post(
        post_key="p1:youtube", master_video_id="p1", brand=Brand.CAT,
        platform=Platform.YOUTUBE, account_id="cat-youtube", concept_tag="違和感",
        hook_type="0.5秒異常", character_id="CAT_001", duration_sec=10, oddity_level=2,
        prompt_version="v1", generation_cost_jpy=0.0, policy_version="v1",
        policy_result=PolicyDecision.PASS, status=PostStatus.PUBLISHING,
    )
    return PublishRequest(post=post, video_path="file://x.mp4", title="t", caption="c", tags=["a"])


class FakePublisher:
    def __init__(self, *, existing=None, ok=True, error=None):
        self.existing = existing
        self.ok = ok
        self.error = error
        self.published_req = None

    def find_existing(self, post):
        return self.existing

    def publish(self, req):
        self.published_req = req
        return PublishResult(ok=self.ok, platform_post_id="pp-1" if self.ok else None,
                             error=self.error)

    def fetch_metrics(self, pid):
        return {}


def _pass(plan, platform):
    return PolicyResult(platform=platform, decision=PolicyDecision.PASS, policy_version="v1")


def test_publishes_and_sets_disclosure_when_required():
    pub = FakePublisher()
    req = _req()
    oc = decide_and_publish(
        _plan(), Platform.YOUTUBE, req, publisher=pub,
        policy_stale=lambda p: False, prompt_check=_pass,
        disclosure_required=lambda p: True,
    )
    assert oc.action == pipeline.PUBLISHED
    assert oc.platform_post_id == "pp-1"
    assert req.ai_disclosure is True


def test_already_published_short_circuits():
    oc = decide_and_publish(
        _plan(), Platform.YOUTUBE, _req(), publisher=FakePublisher(existing="old-id"),
        policy_stale=lambda p: False, prompt_check=_pass, disclosure_required=lambda p: False,
    )
    assert oc.action == pipeline.ALREADY_PUBLISHED
    assert oc.platform_post_id == "old-id"


def test_policy_stale_holds():
    oc = decide_and_publish(
        _plan(), Platform.YOUTUBE, _req(), publisher=FakePublisher(),
        policy_stale=lambda p: True, prompt_check=_pass, disclosure_required=lambda p: False,
    )
    assert oc.action == pipeline.HOLD_POLICY_STALE


def test_guard_hold_blocks_publish():
    oc = decide_and_publish(
        _plan(), Platform.YOUTUBE, _req(), publisher=FakePublisher(),
        guard=GuardVerdict(GuardAction.HOLD, "媒体警告連続"),
        policy_stale=lambda p: False, prompt_check=_pass, disclosure_required=lambda p: False,
    )
    assert oc.action == pipeline.HOLD_GUARD
    assert "媒体警告連続" in oc.reasons[0]


def test_guard_allow_does_not_block():
    oc = decide_and_publish(
        _plan(), Platform.YOUTUBE, _req(), publisher=FakePublisher(),
        guard=GuardVerdict(GuardAction.ALLOW, "ok"),
        policy_stale=lambda p: False, prompt_check=_pass, disclosure_required=lambda p: False,
    )
    assert oc.action == pipeline.PUBLISHED


def test_skip_platform_and_regenerate_and_hold():
    for decision, expected in [
        (PolicyDecision.SKIP_PLATFORM, pipeline.SKIP_PLATFORM),
        (PolicyDecision.REGENERATE, pipeline.REGENERATE),
        (PolicyDecision.HOLD, pipeline.HOLD_POLICY),
    ]:
        oc = decide_and_publish(
            _plan(), Platform.YOUTUBE, _req(), publisher=FakePublisher(),
            policy_stale=lambda p: False,
            prompt_check=lambda pl, pf, d=decision: PolicyResult(
                platform=pf, decision=d, policy_version="v1", reasons=["r"]),
            disclosure_required=lambda p: False,
        )
        assert oc.action == expected


def test_rewrite_applies_overrides_then_publishes():
    pub = FakePublisher()
    req = _req()
    oc = decide_and_publish(
        _plan(), Platform.YOUTUBE, req, publisher=pub,
        policy_stale=lambda p: False,
        prompt_check=lambda pl, pf: PolicyResult(
            platform=pf, decision=PolicyDecision.REWRITE, policy_version="v1",
            reasons=["タグ調整"], caption_override="安全な説明", tags_override=["safe"]),
        disclosure_required=lambda p: False,
    )
    assert oc.action == pipeline.PUBLISHED
    assert req.caption == "安全な説明" and req.tags == ["safe"]


def test_publish_failure_returns_failed():
    oc = decide_and_publish(
        _plan(), Platform.YOUTUBE, _req(),
        publisher=FakePublisher(ok=False, error="API 5xx"),
        policy_stale=lambda p: False, prompt_check=_pass, disclosure_required=lambda p: False,
    )
    assert oc.action == pipeline.FAILED
    assert "API 5xx" in oc.reasons[-1]
