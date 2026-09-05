"""予算ゲートとキルスイッチ（§13 §14）。

各ワークフローは処理前にここを通す。純粋関数のみ（I/O は cli 側）。

- check_budget      … §13 automatic_stop。上限の一定割合を超えたら新規生成を止める
- check_kill_switch … §14 トリガー。異常シグナルから brand×platform / 全体の停止を判定

action の広さ:  STOP（全部）> STOP_NEW_GENERATION（生成のみ・投稿と分析は継続）
              > HOLD（対象 brand×platform のみ）> ALLOW
複数トリガーが立ったら最も広い action を返す。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from src.common.models import Brand, Platform


class GuardAction(str, Enum):
    ALLOW = "ALLOW"
    STOP_NEW_GENERATION = "STOP_NEW_GENERATION"  # 既存投稿/分析は継続
    HOLD = "HOLD"                                 # 対象 brand×platform を一時停止
    STOP = "STOP"                                 # 全処理を停止


# 広い（severe）ほど大きい
_SEVERITY = {
    GuardAction.ALLOW: 0,
    GuardAction.HOLD: 1,
    GuardAction.STOP_NEW_GENERATION: 2,
    GuardAction.STOP: 3,
}


@dataclass
class GuardVerdict:
    action: GuardAction
    reason: str
    triggers: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.action != GuardAction.ALLOW


def combine(verdicts: list[GuardVerdict]) -> GuardVerdict:
    """複数の判定を最も広い action にまとめる。ALLOW しか無ければ ALLOW。"""
    fired = [v for v in verdicts if v.action != GuardAction.ALLOW]
    if not fired:
        return GuardVerdict(GuardAction.ALLOW, "しきい値内", [])
    worst = max(_SEVERITY[v.action] for v in fired)
    action = next(a for a, s in _SEVERITY.items() if s == worst)
    triggers = [t for v in fired for t in (v.triggers or [v.reason])]
    return GuardVerdict(action, " / ".join(triggers), triggers)


# --- §13 予算 --------------------------------------------------------------

@dataclass
class BudgetSpend:
    """現時点の消化額（円）。metrics / provider_cost_log から供給する想定。"""
    today: float = 0.0
    month: float = 0.0
    total: float = 0.0                       # 検証期間（既定90日）の累計
    by_brand_month: Mapping[str, float] = field(default_factory=dict)


def check_budget(
    spend: BudgetSpend,
    cfg: Mapping[str, object],
    *,
    brand: Brand | str | None = None,
) -> GuardVerdict:
    """§13 automatic_stop: 各上限の automatic_stop_ratio（既定95%）到達で新規生成停止。

    Args:
        spend: 現時点の消化額
        cfg: ``config/budget.yaml``
        brand: 指定すると brand_budget も評価する

    既存投稿・分析は止めない（action は STOP_NEW_GENERATION まで）。
    上限そのものが未設定の項目はスキップする。
    """
    ratio = float(cfg.get("automatic_stop_ratio", 0.95))
    triggers: list[str] = []

    def _check(name: str, spent: float, limit: object) -> None:
        if limit is None:
            return
        limit_f = float(limit)
        if limit_f <= 0:
            return
        if spent >= limit_f:
            triggers.append(f"{name} 超過（{spent:.0f}/{limit_f:.0f}円）")
        elif spent >= limit_f * ratio:
            triggers.append(
                f"{name} が上限の{ratio * 100:.0f}%到達（{spent:.0f}/{limit_f:.0f}円）"
            )

    _check("総投資枠", spend.total, cfg.get("total_investment_cap"))
    _check("月次予算", spend.month, cfg.get("monthly_budget"))
    _check("日次予算", spend.today, cfg.get("daily_budget"))

    if brand is not None:
        b = brand.value if isinstance(brand, Brand) else str(brand)
        brand_limits = cfg.get("brand_budget") or {}
        _check(f"ブランド予算({b})", float(spend.by_brand_month.get(b, 0.0)),
               brand_limits.get(b))

    if triggers:
        return GuardVerdict(GuardAction.STOP_NEW_GENERATION, " / ".join(triggers), triggers)
    return GuardVerdict(GuardAction.ALLOW, "予算しきい値内", [])


# --- §14 キルスイッチ ----------------------------------------------------

_DEFAULT_THRESHOLDS = {
    "platform_warnings": 2,              # 媒体からの警告/削除が連続
    "consecutive_publish_failures": 3,   # 3回連続投稿失敗
    "consecutive_quality_failures": 3,   # 生成品質NG連続
}


def check_kill_switch(
    brand: Brand | str,
    platform: Platform | str,
    signals: Mapping[str, object],
    *,
    thresholds: Mapping[str, int] | None = None,
) -> GuardVerdict:
    """§14 トリガー表から action を判定する。

    signals（すべて任意。無いキーはそのトリガー無しとみなす）:
        platform_warnings: int              媒体からの警告/削除の連続数
        consecutive_publish_failures: int   連続投稿失敗数
        consecutive_quality_failures: int   連続の生成品質NG数
        policy_diff: bool                    ポリシー差分検知（policy_sync）
        brand_policy_risk: "LOW"|"MEDIUM"|"HIGH"   対象ブランドのリスク
        budget_exceeded: bool               予算超過（check_budget 由来）
        auth_expired: bool                  認証トークン期限切れ
        cannot_record_post_id: bool         Sheets/API 障害で投稿IDを記録できない
    """
    th = {**_DEFAULT_THRESHOLDS, **(thresholds or {})}
    b = brand.value if isinstance(brand, Brand) else str(brand)
    p = platform.value if isinstance(platform, Platform) else str(platform)
    verdicts: list[GuardVerdict] = []

    def _int(key: str) -> int:
        try:
            return int(signals.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    if _int("platform_warnings") >= th["platform_warnings"]:
        verdicts.append(GuardVerdict(
            GuardAction.HOLD, "",
            [f"{p} からの警告/削除が連続（{_int('platform_warnings')}回）→ {b}×{p} を HOLD"],
        ))

    if _int("consecutive_publish_failures") >= th["consecutive_publish_failures"]:
        verdicts.append(GuardVerdict(
            GuardAction.HOLD, "",
            [f"{p} への連続投稿失敗（{_int('consecutive_publish_failures')}回）→ {b}×{p} を HOLD"],
        ))

    if _int("consecutive_quality_failures") >= th["consecutive_quality_failures"]:
        verdicts.append(GuardVerdict(
            GuardAction.HOLD, "",
            [(f"生成品質NGが連続（{_int('consecutive_quality_failures')}回）"
              f"→ {b} の provider/テンプレートを HOLD")],
        ))

    risk = str(signals.get("brand_policy_risk", "")).upper()
    if bool(signals.get("policy_diff")) and risk == "HIGH":
        verdicts.append(GuardVerdict(
            GuardAction.HOLD, "",
            [f"ポリシー差分検知 ＋ HIGH RISK ブランド → {b} を HOLD（§7 §14）"],
        ))

    if bool(signals.get("auth_expired")):
        verdicts.append(GuardVerdict(
            GuardAction.HOLD, "",
            [f"{p} の認証トークン期限切れ → {b}×{p} の投稿を HOLD（要トークン更新・通知）"],
        ))

    if bool(signals.get("budget_exceeded")):
        verdicts.append(GuardVerdict(
            GuardAction.STOP_NEW_GENERATION, "",
            ["予算超過 → 新規生成を停止（既存投稿/分析は継続）"],
        ))

    if bool(signals.get("cannot_record_post_id")):
        verdicts.append(GuardVerdict(
            GuardAction.STOP, "",
            ["Sheets/API 障害で投稿IDを記録できない → 重複投稿防止のため全停止（§15）"],
        ))

    return combine(verdicts)
