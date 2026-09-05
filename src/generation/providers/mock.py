"""モック生成プロバイダ。

実プロバイダ未定のあいだ、パイプライン全体（企画→生成→品質→投稿→回収→学習）を
エンドツーエンドで動かすために使う。実 API を選定したら同じ VideoProvider を
実装した本番アダプタに差し替える。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from src.common.models import ContentPlan, GenerationJob, GenerationStatus
from src.generation.base import VideoProvider, register


@register
class MockVideoProvider(VideoProvider):
    name = "mock"

    def submit(self, plan: ContentPlan) -> GenerationJob:
        return GenerationJob(
            job_id=str(uuid.uuid4()),
            plan_id=plan.plan_id,
            provider=self.name,
            external_job_id=f"mock-{uuid.uuid4().hex[:12]}",
            status=GenerationStatus.RUNNING,
            submitted_at=datetime.now(timezone.utc),
        )

    def poll(self, job: GenerationJob) -> GenerationJob:
        # モックは即完了扱い。実プロバイダでは外部APIに状態を問い合わせる。
        job.status = GenerationStatus.SUCCEEDED
        job.video_url = "file://sample/mock_master.mp4"
        job.cost_jpy = 0.0
        job.completed_at = datetime.now(timezone.utc)
        return job

    def estimate_cost_jpy(self, plan: ContentPlan) -> float:
        return 0.0
