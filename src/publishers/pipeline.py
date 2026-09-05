"""投稿の段取り（§8 §9 §15）。

1媒体分の「投稿してよいか」を判定し、OK なら publisher に渡す。

判定順（どれかで止まったら投稿しない）:
  1. 冪等キーに既存投稿あり → ALREADY_PUBLISHED（二重送信しない §15）
  2. policy_sync が版差分を検知（is_policy_stale）→ HOLD_POLICY_STALE（旧ルールで投稿しない §8）
  3. guardrails が HOLD / STOP → HOLD_GUARD（§14）
  4. プロンプト段階ポリシー再判定（check_prompt）:
       SKIP_PLATFORM → その媒体に出さない / REGENERATE → 生成に戻す / HOLD → 人間確認
       REWRITE → caption・tags を上書きして続行
  5. AI 開示ラベルが要る媒体なら req.ai_disclosure = True
  6. publisher.publish()

完成動画段階のポリシー判定（check_video, §7 2段目）は未実装のためここでは呼ばない。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from src.common.guardrails import GuardAction, GuardVerdict
from src.common.models import ContentPlan, Platform, PolicyDecision
from src.policy.engine import (
    check_prompt,
    is_policy_stale,
    policy_version,
    requires_ai_disclosure,
)
from src.publishers.base import Publisher, PublishRequest

PUBLISHED = "PUBLISHED"
ALREADY_PUBLISHED = "ALREADY_PUBLISHED"
HOLD_POLICY_STALE = "HOLD_POLICY_STALE"
HOLD_GUARD = "HOLD_GUARD"
HOLD_POLICY = "HOLD_POLICY"
SKIP_PLATFORM = "SKIP_PLATFORM"
REGENERATE = "REGENERATE"
FAILED = "FAILED"

_DONE = {PUBLISHED, ALREADY_PUBLISHED}


@dataclass
class PublishOutcome:
    plan_id: str
    platform: Platform
    action: str
    platform_post_id: str | None = None
    policy_version: str = ""
    reasons: list[str] = field(default_factory=list)

    @property
    def published(self) -> bool:
        return self.action in _DONE


def decide_and_publish(
    plan: ContentPlan,
    platform: Platform,
    req: PublishRequest,
    *,
    publisher: Publisher,
    guard: GuardVerdict | None = None,
    policy_stale: Callable[[Platform], bool] = is_policy_stale,
    prompt_check=check_prompt,
    disclosure_required: Callable[[Platform], bool] = requires_ai_disclosure,
) -> PublishOutcome:
    pv = policy_version(platform)

    def out(action: str, *, post_id: str | None = None, reasons: list[str] | None = None):
        return PublishOutcome(plan.plan_id, platform, action, post_id, pv, reasons or [])

    existing = publisher.find_existing(req.post)
    if existing:
        return out(ALREADY_PUBLISHED, post_id=existing, reasons=["冪等キーに既存投稿あり（§15）"])

    if policy_stale(platform):
        return out(HOLD_POLICY_STALE,
                   reasons=["policy_sync が版差分を検知。旧ルールでは投稿しない（§8）"])

    if guard is not None and guard.action in (GuardAction.HOLD, GuardAction.STOP):
        return out(HOLD_GUARD, reasons=[guard.reason or guard.action.value])

    pr = prompt_check(plan, platform)
    if pr.decision is PolicyDecision.SKIP_PLATFORM:
        return out(SKIP_PLATFORM, reasons=pr.reasons)
    if pr.decision is PolicyDecision.REGENERATE:
        return out(REGENERATE, reasons=pr.reasons)
    if pr.decision is PolicyDecision.HOLD:
        return out(HOLD_POLICY, reasons=pr.reasons)

    applied: list[str] = []
    if pr.decision is PolicyDecision.REWRITE:
        if pr.caption_override:
            req.caption = pr.caption_override
        if pr.tags_override is not None:
            req.tags = list(pr.tags_override)
        applied.append("REWRITE 適用: " + "; ".join(pr.reasons))

    if disclosure_required(platform):
        req.ai_disclosure = True

    result = publisher.publish(req)
    if not result.ok:
        return out(FAILED, reasons=[*applied, result.error or "publish 失敗"])

    return out(
        ALREADY_PUBLISHED if result.already_published else PUBLISHED,
        post_id=result.platform_post_id,
        reasons=[*applied, *(["媒体側で既存扱い"] if result.already_published else [])],
    )
