"""生成パイプラインの段取り（§12）。

投入（submit）と 完了確認＋品質判定＋再生成（advance）を分ける。
生成待ちで Runner を占有しないため（§5 generate.yml / poll_generation.yml）。

- submit_plans  … 企画ごとに概算原価で予算ゲート → provider.submit()
- advance_jobs  … RUNNING なジョブを poll → 完了なら品質判定 → NG は再投入（最大 max_attempts）
                   超過したら status=FAILED（＝以降の publish 対象から外れる）
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Optional

from src.common.guardrails import GuardAction, GuardVerdict
from src.common.models import (
    ContentPlan,
    GenerationJob,
    GenerationStatus,
    QualityResult,
)
from src.generation.base import VideoProvider
from src.generation.quality import inspect as default_inspect

# job -> ローカルに取得済み動画パス。既定は video_url をそのまま使う（mock 用）。
DownloadFn = Callable[[GenerationJob], str]
InspectFn = Callable[[Optional[ContentPlan], str], QualityResult]
BudgetGateFn = Callable[[float], GuardVerdict]  # 引数 = ここまでの確定概算コスト合計(円)

_TERMINAL = {GenerationStatus.SUCCEEDED, GenerationStatus.FAILED}


def submit_plans(
    plans: Sequence[ContentPlan],
    provider: VideoProvider,
    *,
    budget_gate: BudgetGateFn | None = None,
) -> tuple[list[GenerationJob], list[dict]]:
    """企画を生成投入する。

    budget_gate が ALLOW 以外を返した企画は投入せず skipped に回す（§13）。
    以降の企画も投入しない（予算は累積で見るため）。

    Returns:
        (投入できたジョブ, スキップ情報 dict のリスト)
    """
    jobs: list[GenerationJob] = []
    skipped: list[dict] = []
    confirmed_cost = 0.0
    stop = False

    for plan in plans:
        est = float(provider.estimate_cost_jpy(plan))
        if stop:
            skipped.append({"plan_id": plan.plan_id, "est_cost_jpy": est,
                            "reason": "予算ゲートにより以降の投入を停止"})
            continue
        if budget_gate is not None:
            verdict = budget_gate(confirmed_cost + est)
            if verdict.action != GuardAction.ALLOW:
                skipped.append({"plan_id": plan.plan_id, "est_cost_jpy": est,
                                "reason": verdict.reason})
                stop = True
                continue
        job = provider.submit(plan)
        job.attempt = job.attempt or 1
        jobs.append(job)
        confirmed_cost += est

    return jobs, skipped


def advance_jobs(
    jobs: Sequence[GenerationJob],
    provider: VideoProvider,
    plans_by_id: Mapping[str, ContentPlan],
    *,
    inspect: InspectFn = default_inspect,
    download: DownloadFn | None = None,
) -> list[GenerationJob]:
    """未完了ジョブを1歩進める。冪等: 何度呼んでも同じ結果に収束する。"""
    for job in jobs:
        if job.status in _TERMINAL:
            continue

        try:
            polled = provider.poll(job)
        except Exception as e:  # noqa: BLE001 - provider側の未捕捉例外でCLI全体を落とさない
            _retry_or_give_up(job, provider, plans_by_id, prefix=f"poll例外: {e}")
            continue
        job.status = polled.status
        job.video_url = polled.video_url or job.video_url
        job.local_path = polled.local_path or job.local_path
        job.cost_jpy = polled.cost_jpy or job.cost_jpy
        job.completed_at = polled.completed_at or job.completed_at
        job.error = polled.error or job.error

        if job.status == GenerationStatus.FAILED:
            _retry_or_give_up(job, provider, plans_by_id, prefix="生成失敗")
            continue
        if job.status != GenerationStatus.SUCCEEDED:
            continue  # まだ RUNNING / QUEUED

        # 完了 → 取得 → 品質判定（provider が既に local_path を埋めていればそれを尊重）
        if not job.local_path:
            job.local_path = download(job) if download else (job.video_url or "")
        plan = plans_by_id.get(job.plan_id)
        result = inspect(plan, job.local_path)
        if result.passed:
            job.error = None
        else:
            job.status = GenerationStatus.RUNNING  # 判定 NG は「未完了」に戻す
            _retry_or_give_up(
                job, provider, plans_by_id,
                prefix="品質NG: " + "; ".join(result.reasons),
            )

    return list(jobs)


def _retry_or_give_up(
    job: GenerationJob,
    provider: VideoProvider,
    plans_by_id: Mapping[str, ContentPlan],
    *,
    prefix: str,
) -> None:
    plan = plans_by_id.get(job.plan_id)
    if plan is not None and job.attempt < job.max_attempts:
        fresh = provider.submit(plan)
        job.external_job_id = fresh.external_job_id
        job.status = GenerationStatus.RUNNING
        job.attempt += 1
        job.video_url = None
        job.local_path = None
        job.completed_at = None
        job.error = f"{prefix} → 再生成 {job.attempt}/{job.max_attempts}"
    else:
        job.status = GenerationStatus.FAILED
        job.error = f"{prefix} → 再生成上限（{job.max_attempts}）超過につき SKIP"


def summarize(jobs: Sequence[GenerationJob]) -> dict[str, int]:
    out: dict[str, int] = {}
    for job in jobs:
        out[job.status.value] = out.get(job.status.value, 0) + 1
    return out
