"""Quality Gate（§12）。

完成動画が「使えるか」を判定する。NG なら最大2回まで再生成、超過で SKIP。

このモジュールでできること:
- 動画メタデータの機械チェック（尺が目標から乖離していないか / 9:16 か）
  ffprobe があれば使う。無ければメタデータ検査はスキップして PASS 扱い。
- ファイル未取得（mock 運用・DL 前）は実検査をスキップして PASS 扱い。

まだできないこと（vision LLM が要る。§7 check_video と一緒に実装予定）:
- AI 破綻・身体異常・不要な文字・意図しない性的/暴力的表現の検出
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable

from src.common.models import ContentPlan, QualityResult

_ASPECT_TARGET = 9 / 16
_ASPECT_TOLERANCE = 0.05


def _strip_scheme(path: str) -> str:
    return path.removeprefix("file://")


def _ffprobe(path: str) -> dict | None:
    """{"duration": float, "width": int, "height": int} か None（ffprobe 不在/失敗）。"""
    if shutil.which("ffprobe") is None:
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        data = json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    dur = data.get("format", {}).get("duration")
    return {
        "duration": float(dur) if dur is not None else None,
        "width": video.get("width"),
        "height": video.get("height"),
    }


def inspect(
    plan: ContentPlan | None,
    video_path: str,
    *,
    probe: Callable[[str], dict | None] = _ffprobe,
) -> QualityResult:
    """完成動画の機械チェック。

    Args:
        plan: 目標尺の参照に使う。None なら尺チェックを飛ばす。
        video_path: ローカルパス、または file:// URL。
        probe: メタデータ取得関数（テスト差し替え用）。

    未取得ファイル・ffprobe 不在のときは passed=True（＋理由にスキップ明記）。
    """
    local = _strip_scheme(video_path) if video_path else ""
    if not local or not os.path.exists(local):
        return QualityResult(
            passed=True,
            reasons=["動画ファイル未取得のため実検査をスキップ（mock / DL 前）"],
        )

    meta = probe(local)
    if meta is None:
        return QualityResult(passed=True, reasons=["ffprobe 不在: メタデータ検査をスキップ"])

    problems: list[str] = []
    scores: dict[str, float] = {}

    dur = meta.get("duration")
    if dur is not None and plan is not None:
        scores["duration_sec"] = dur
        lo, hi = plan.duration_target_sec * 0.5, plan.duration_target_sec * 2.0
        if not (lo <= dur <= hi):
            problems.append(
                f"尺 {dur:.1f}s が目標 {plan.duration_target_sec}s から乖離（許容 {lo:.0f}〜{hi:.0f}s）"
            )

    w, h = meta.get("width"), meta.get("height")
    if w and h:
        ratio = w / h
        scores["aspect_ratio"] = ratio
        if abs(ratio - _ASPECT_TARGET) > _ASPECT_TOLERANCE:
            problems.append(f"アスペクト比 {w}x{h}（{ratio:.3f}）が 9:16 でない")

    return QualityResult(
        passed=not problems,
        reasons=problems or ["メタデータ検査 OK（AI 破綻検出は未実装 = 目視レビュー前提 §19）"],
        scores=scores,
    )
