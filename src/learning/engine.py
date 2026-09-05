"""Learning Engine: スコア計算 → 勝ちタグ抽出 → 翌日配分（§11）。

daily_learning.yml が1日1回呼ぶ。planner ↔ learning のループを閉じる中核。

- 1投稿のスコア = 取得できた指標だけを重み付き合成（欠損指標は重みを再正規化）
- revenue / roi / 原価効率 はレートでないので、その回の母集団の最大値で 0〜1 化
- 勝ちタグ = 企画タグ×媒体の組ごとに、直近7日と30日のスコアを中央値で束ね、
  重み付き平均（§11: 直近7日＋30日を併用 / 1本の超バズで全配分を変えない）
- 直近30日で min_posts_for_winner 本に満たない組は「様子見」で勝ちタグにしない
- 翌日配分（next_day_allocation）は planner 側に実装済み。ここは窓口として再エクスポート

このモジュールの実装状況:
  - compute_score        … 実装済み（純粋関数）
  - extract_winning_tags … 実装済み（純粋関数）
  - next_day_allocation  … planner.next_day_allocation の再エクスポート
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from src.common.models import PerformanceSnapshot
from src.planner.planner import next_day_allocation  # 再エクスポート（§11 の配分本体は planner 側）

__all__ = [
    "WINNING_TAG_KEYS",
    "compute_norm",
    "compute_score",
    "extract_winning_tags",
    "next_day_allocation",
]

JST = timezone(timedelta(hours=9))

# 勝ちタグ1件の形。これ＋"score" の 10 キーで固定（planner.build_daily_plan が参照）。
WINNING_TAG_KEYS: tuple[str, ...] = (
    "brand",
    "concept_tag",
    "hook_type",
    "character_id",
    "reality_level",
    "oddity_level",
    "duration_target_sec",
    "prompt_version",
    "platform",
)

# 1投稿のスコアに使える指標（config の score_weights_* のキーと対応）
_RATE_METRICS = ("completion_rate", "share_rate", "follow_rate", "comment_rate")
_NORMALIZED_METRICS = ("revenue_jpy", "roi", "cost_efficiency")

# extract_winning_tags 入力の 1 レコード = (投稿の企画タグ等, {snapshot名: PerformanceSnapshot})
Record = tuple[Mapping[str, Any], Mapping[str, PerformanceSnapshot]]

_DEFAULT_SNAPSHOT_ORDER = ("7d", "72h", "24h", "latest")


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _follow_rate(snap: PerformanceSnapshot) -> float | None:
    if snap.views and snap.followers_before is not None and snap.followers_after is not None:
        return (snap.followers_after - snap.followers_before) / snap.views
    return None


def _roi(snap: PerformanceSnapshot, cost_jpy: float | None) -> float | None:
    if cost_jpy and cost_jpy > 0 and snap.revenue_jpy is not None:
        return (snap.revenue_jpy - cost_jpy) / cost_jpy
    return None


def _cost_efficiency(snap: PerformanceSnapshot, cost_jpy: float | None) -> float | None:
    """views / 原価。大きいほど「安く見られている」。母集団最大で正規化する前の生値。"""
    if cost_jpy and cost_jpy > 0 and snap.views:
        return snap.views / cost_jpy
    return None


def _raw_metric_values(
    snap: PerformanceSnapshot, *, cost_jpy: float | None
) -> dict[str, float]:
    """この snapshot から計算できる指標の生値（rate 系は 0〜1、normalized 系は未正規化）。"""
    out: dict[str, float] = {}

    if snap.completion_rate is not None:
        out["completion_rate"] = _clamp01(snap.completion_rate)
    sr = snap.rate(snap.shares)
    if sr is not None:
        out["share_rate"] = _clamp01(sr)
    cr = snap.rate(snap.comments)
    if cr is not None:
        out["comment_rate"] = _clamp01(cr)
    fr = _follow_rate(snap)
    if fr is not None:
        out["follow_rate"] = _clamp01(fr)

    if snap.revenue_jpy is not None:
        out["revenue_jpy"] = max(0.0, snap.revenue_jpy)
    roi = _roi(snap, cost_jpy)
    if roi is not None:
        out["roi"] = roi
    ce = _cost_efficiency(snap, cost_jpy)
    if ce is not None:
        out["cost_efficiency"] = ce

    return out


def compute_norm(
    records: Sequence[Record], *, snapshot_order: Sequence[str] = _DEFAULT_SNAPSHOT_ORDER
) -> dict[str, float]:
    """母集団（records 全体）での revenue_jpy / roi / cost_efficiency の最大値。

    compute_score にこの dict を渡すと、レートでない指標を「その回で一番良かった投稿＝1.0」
    として 0〜1 に丸められる。該当データが無い指標は 0.0（＝正規化不能なので scoring で除外）。
    """
    pools: dict[str, list[float]] = {k: [] for k in _NORMALIZED_METRICS}
    for post, snaps in records:
        snap = pick_snapshot(snaps, snapshot_order)
        if snap is None:
            continue
        cost = _as_float(post.get("generation_cost_jpy"))
        vals = _raw_metric_values(snap, cost_jpy=cost)
        for k in _NORMALIZED_METRICS:
            if k in vals:
                pools[k].append(max(0.0, vals[k]))
    return {k: (max(v) if v else 0.0) for k, v in pools.items()}


def compute_score(
    snap: PerformanceSnapshot,
    *,
    weights: Mapping[str, float],
    cost_jpy: float | None = None,
    norm: Mapping[str, float] | None = None,
) -> float:
    """1投稿のスコア（0〜1）。媒体で欠損した指標は重みを再正規化して除外する（§11）。

    Args:
        snap: 対象の PerformanceSnapshot
        weights: ``config/scoring.yaml`` の ``score_weights_pre_monetization`` か
            ``score_weights_post_monetization``（呼び出し側が収益化前/後で選ぶ）
        cost_jpy: その投稿の生成原価。roi / cost_efficiency に必要
        norm: ``compute_norm`` の出力。revenue_jpy / roi / cost_efficiency の
            正規化に使う。None または該当キーが 0 のときその指標は除外される
    """
    raw = _raw_metric_values(snap, cost_jpy=cost_jpy)

    usable: dict[str, float] = {}
    for key, value in raw.items():
        if key not in weights:
            continue
        if key in _NORMALIZED_METRICS:
            ref = (norm or {}).get(key, 0.0)
            if ref <= 0:
                continue  # 正規化できない → 除外（重みは下で再正規化される）
            usable[key] = _clamp01(max(0.0, value) / ref)
        else:
            usable[key] = value

    total_w = sum(weights[k] for k in usable)
    if total_w <= 0:
        return 0.0
    return sum(usable[k] * weights[k] for k in usable) / total_w


def pick_snapshot(
    snaps: Mapping[str, PerformanceSnapshot], order: Sequence[str] = _DEFAULT_SNAPSHOT_ORDER
) -> PerformanceSnapshot | None:
    """優先順に見て最初に存在する snapshot を返す。無ければ手元にある任意の 1 件。"""
    for name in order:
        if name in snaps and snaps[name] is not None:
            return snaps[name]
    for snap in snaps.values():
        if snap is not None:
            return snap
    return None


def extract_winning_tags(
    records: Sequence[Record],
    config: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> list[dict]:
    """企画タグ×媒体ごとに勝ちパターンを抽出する（§2 §11）。

    Args:
        records: ``(post, snapshots)`` のリスト。post は WINNING_TAG_KEYS ＋
            ``generation_cost_jpy`` ＋ ``published_at``（datetime か ISO 文字列）を持つ Mapping。
            snapshots は ``{"7d": PerformanceSnapshot, ...}``。
        config: ``config/scoring.yaml`` 全体（``score_weights_*`` と ``learning`` を読む）。
        now: 評価基準時刻（既定: 現在 JST）。テストの決定性のため注入可能。

    Returns:
        10 キー（WINNING_TAG_KEYS ＋ ``score``）の dict のリスト。score 降順。
    """
    now = now or datetime.now(JST)
    learning_cfg = config.get("learning", {}) or {}

    monetized = bool(learning_cfg.get("monetized", False))
    weights = config.get(
        "score_weights_post_monetization" if monetized else "score_weights_pre_monetization",
        {},
    )
    snapshot_order = tuple(learning_cfg.get("score_snapshot_order") or _DEFAULT_SNAPSHOT_ORDER)
    win_weights = _window_weights(learning_cfg.get("eval_window_weights"))
    min_posts = int(learning_cfg.get("min_posts_for_winner", 3))

    norm = compute_norm(records, snapshot_order=snapshot_order)

    # tagタプル -> [(score, age_days), ...]
    groups: dict[tuple, list[tuple[float, float]]] = {}
    for post, snaps in records:
        snap = pick_snapshot(snaps, snapshot_order)
        if snap is None:
            continue
        published = _coerce_dt(post.get("published_at"))
        if published is None:
            continue
        age_days = (now - published).total_seconds() / 86400.0
        if age_days < 0:
            age_days = 0.0
        score = compute_score(
            snap,
            weights=weights,
            cost_jpy=_as_float(post.get("generation_cost_jpy")),
            norm=norm,
        )
        key = tuple(_tag_value(k, post.get(k)) for k in WINNING_TAG_KEYS)
        groups.setdefault(key, []).append((score, age_days))

    winners: list[dict] = []
    for key, scored in groups.items():
        within_30 = [s for s, age in scored if age <= 30.0]
        if len(within_30) < min_posts:
            continue
        within_7 = [s for s, age in scored if age <= 7.0]

        parts: list[tuple[float, float]] = []
        if within_7:
            parts.append((statistics.median(within_7), win_weights["7"]))
        if within_30:
            parts.append((statistics.median(within_30), win_weights["30"]))
        wsum = sum(w for _, w in parts)
        if wsum <= 0:
            continue
        combined = sum(v * w for v, w in parts) / wsum

        winners.append({**dict(zip(WINNING_TAG_KEYS, key)), "score": round(combined, 6)})

    winners.sort(
        key=lambda w: (-w["score"], tuple(str(w[k]) for k in WINNING_TAG_KEYS))
    )
    return winners


# --- 小さいヘルパ --------------------------------------------------------------

def _window_weights(raw: Mapping[str, Any] | None) -> dict[str, float]:
    if not raw:
        return {"7": 0.6, "30": 0.4}
    return {str(k): float(v) for k, v in raw.items()}


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tag_value(key: str, value: Any) -> Any:
    """タグのキー値を正規化（brand/platform は str 化、level 系は int 化）。"""
    if value is None:
        return None
    if key in ("reality_level", "oddity_level", "duration_target_sec"):
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if hasattr(value, "value"):  # Enum
        return value.value
    return str(value)


def _coerce_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        text = value.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            try:
                dt = datetime.fromisoformat(text[:10])
            except ValueError:
                return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt
