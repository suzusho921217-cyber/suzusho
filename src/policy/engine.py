"""ポリシーエンジン（§8）。

固定ルールだけに依存せず、媒体別のルール「版」を保持する（config/policy_rules/）。
ポリシー変更検知時は旧ルールのまま投稿しない（HOLD / is_policy_stale）。
生成プロンプト段階と完成動画段階の二重チェック（§7）。

実装状況:
    load_policy / policy_version … 実装済み（_common + <platform> のマージ）
    check_prompt                 … 実装済み（プロンプト段階。§7 1段目）
    is_policy_stale              … 実装済み（.state/policy_sync.json を参照）
    check_video                  … 未実装（完成動画段階。§7 2段目。次ステップ）
"""

from __future__ import annotations

import json
import re
from functools import cache

from src.common.config import CONFIG_DIR, load
from src.common.models import ContentPlan, Platform, PolicyDecision, PolicyResult

STATE_DIR = CONFIG_DIR.parent / ".state"

# 重い順。check_prompt が複数ルールにヒットしたとき、この中で最も重い決定を返す。
_SEVERITY = [
    PolicyDecision.HOLD,           # 人間確認が必要。自動投稿停止
    PolicyDecision.SKIP_PLATFORM,  # その媒体には出さない
    PolicyDecision.REGENERATE,     # 映像から作り直し
    PolicyDecision.REWRITE,        # キャプション/タグ修正で再判定
    PolicyDecision.PASS,
]

@cache
def load_policy(platform: Platform) -> dict:
    """_common.yaml と <platform>.yaml をマージした「適用中ルール」を返す。"""
    common = load("policy_rules/_common")
    specific = load(f"policy_rules/{platform.value}")
    checks = list(common.get("checks") or []) + list(specific.get("checks") or [])
    return {
        "version": str(specific.get("version") or common.get("version") or "unset"),
        "common_version": str(common.get("version") or "unset"),
        "checks": checks,
    }


def policy_version(platform: Platform) -> str:
    return load_policy(platform)["version"]


def requires_ai_disclosure(platform: Platform) -> bool:
    """その媒体が実写級AI生成の開示ラベルを要求するか（publish 段階で消費）。

    実データは config/policy_rules/<platform>.yaml の ``ai_disclosure_required``。
    詳細は docs/platform_policies.md。
    """
    specific = load(f"policy_rules/{platform.value}")
    return bool(specific.get("ai_disclosure_required", False))


def _text_blob(plan: ContentPlan) -> str:
    """プロンプト段階の検査対象テキスト。

    render_prompt の出力（prompt_text）は【禁止】節を含み誤検知の元になるため使わず、
    planner が選んだ意味フィールドだけを見る。
    """
    return " / ".join(x for x in (plan.concept_tag, plan.hook_type, plan.notes) if x)


def _one_condition(key: str, val: object, plan: ContentPlan) -> bool:
    """条件1つが成立するか。"""
    if key == "text_matches":
        return re.search(str(val), _text_blob(plan), re.IGNORECASE) is not None
    if key == "max_oddity_level":
        return plan.oddity_level > int(val)  # type: ignore[arg-type]
    if key == "max_reality_level":
        return plan.reality_level > int(val)  # type: ignore[arg-type]
    if key == "brand_in":
        return plan.brand.value in {str(b).lower() for b in val}  # type: ignore[union-attr]
    if key == "policy_risk_in":
        return plan.policy_risk.value in {str(r).upper() for r in val}  # type: ignore[union-attr]
    raise ValueError(f"policy_rules: 未知の条件タイプ {key!r}")


def _condition_hits(when: dict, plan: ContentPlan) -> bool:
    """when の全キーが成立したら True（複数キーは AND）。"""
    return all(_one_condition(key, val, plan) for key, val in when.items())


def check_prompt(plan: ContentPlan, platform: Platform) -> PolicyResult:
    """生成前チェック（プロンプト段階）。§7 二重チェックの1段目。

    applies_to に "prompt" を含むルールを全評価し、ヒットした決定のうち最も重いものを返す。
    ヒットが無ければ PASS。
    """
    policy = load_policy(platform)
    triggered: list[tuple[PolicyDecision, str]] = []
    for chk in policy["checks"]:
        if "prompt" not in (chk.get("applies_to") or []):
            continue
        if _condition_hits(chk.get("when") or {}, plan):
            triggered.append((
                PolicyDecision(str(chk["decision"])),
                f"[{chk.get('id', '?')}] {chk.get('message', '')}".strip(),
            ))

    if not triggered:
        return PolicyResult(
            platform=platform,
            decision=PolicyDecision.PASS,
            policy_version=policy["version"],
            reasons=[],
        )

    worst = min((d for d, _ in triggered), key=_SEVERITY.index)
    return PolicyResult(
        platform=platform,
        decision=worst,
        policy_version=policy["version"],
        reasons=[m for _, m in triggered],
    )


def check_video(plan: ContentPlan, platform: Platform, video_path: str) -> PolicyResult:
    """投稿前チェック（完成動画段階）。§7 二重チェックの2段目。（次ステップで実装）"""
    raise NotImplementedError


def is_policy_stale(platform: Platform) -> bool:
    """policy_sync が版差分を検知済みなら True。True なら自動投稿を止める（§8 §14）。

    .state/policy_sync.json が無い（＝未同期）場合は False を返し、先に進む。
    policy_sync 実装後は、記録された版と現行ルール版の食い違いも stale とみなす。
    """
    p = STATE_DIR / "policy_sync.json"
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    entry = (data or {}).get(platform.value)
    if not isinstance(entry, dict):
        return False
    if entry.get("stale"):
        return True
    recorded = entry.get("version")
    return recorded is not None and str(recorded) != policy_version(platform)


def _default_hold(platform: Platform, reason: str) -> PolicyResult:
    return PolicyResult(
        platform=platform,
        decision=PolicyDecision.HOLD,
        policy_version=policy_version(platform),
        reasons=[reason],
    )
