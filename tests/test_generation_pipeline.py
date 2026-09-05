"""generation.pipeline（§12）: submit_plans / advance_jobs / summarize。"""

import itertools

from src.common.guardrails import BudgetSpend, check_budget
from src.common.models import (
    Brand,
    ContentPlan,
    ExperimentFlag,
    GenerationJob,
    GenerationStatus,
    PolicyRisk,
    QualityResult,
)
from src.generation.base import VideoProvider
from src.generation.pipeline import advance_jobs, submit_plans, summarize

_ids = itertools.count(1)


def _plan(pid=None, **over):
    base = {
        "plan_id": pid or f"p{next(_ids)}", "date": "2026-09-02", "brand": Brand.CAT,
        "concept_tag": "違和感", "hook_type": "0.5秒異常", "character_id": "CAT_001",
        "reality_level": 4, "oddity_level": 2, "duration_target_sec": 10,
        "experiment_flag": ExperimentFlag.EXPLORE, "policy_risk": PolicyRisk.LOW,
        "prompt_version": "v1",
    }
    base.update(over)
    return ContentPlan(**base)


class FakeProvider(VideoProvider):
    name = "fake"

    def __init__(self, *, cost=100.0, poll_result=GenerationStatus.SUCCEEDED, poll_raises=None):
        self.cost = cost
        self.poll_result = poll_result
        self.poll_raises = poll_raises
        self.submits = 0

    def submit(self, plan):
        self.submits += 1
        return GenerationJob(
            job_id=f"job-{self.submits}", plan_id=plan.plan_id, provider=self.name,
            external_job_id=f"ext-{self.submits}", status=GenerationStatus.RUNNING,
        )

    def poll(self, job):
        if self.poll_raises is not None:
            raise self.poll_raises
        job.status = self.poll_result
        if self.poll_result == GenerationStatus.SUCCEEDED:
            job.video_url = "file://none/master.mp4"
            job.cost_jpy = self.cost
        elif self.poll_result == GenerationStatus.FAILED:
            job.error = "provider error"
        return job

    def estimate_cost_jpy(self, plan):
        return self.cost


_PASS = lambda plan, path: QualityResult(True, ["ok"])
_FAIL = lambda plan, path: QualityResult(False, ["尺が乖離"])


# --- submit_plans -------------------------------------------------------

def test_submit_plans_no_gate_submits_all():
    plans = [_plan(), _plan(), _plan()]
    jobs, skipped = submit_plans(plans, FakeProvider())
    assert len(jobs) == 3 and skipped == []


def test_submit_plans_budget_gate_stops_and_skips_rest():
    cfg = {"automatic_stop_ratio": 0.95, "monthly_budget": 1000}
    # 各 500 円。1本目 OK（500）、2本目で 1000 到達 → STOP。3本目も投入しない
    plans = [_plan(), _plan(), _plan()]
    gate = lambda add: check_budget(BudgetSpend(month=add), cfg)
    jobs, skipped = submit_plans(plans, FakeProvider(cost=500), budget_gate=gate)
    assert len(jobs) == 1
    assert len(skipped) == 2
    assert "以降の投入を停止" in skipped[1]["reason"]


# --- advance_jobs ------------------------------------------------------

def test_advance_success_with_passing_quality():
    p = _plan("pA")
    prov = FakeProvider()
    jobs, _ = submit_plans([p], prov)
    advance_jobs(jobs, prov, {"pA": p}, inspect=_PASS)
    assert jobs[0].status is GenerationStatus.SUCCEEDED
    assert jobs[0].local_path == "file://none/master.mp4"
    assert jobs[0].error is None


def test_advance_quality_fail_triggers_retry():
    p = _plan("pB")
    prov = FakeProvider()
    jobs, _ = submit_plans([p], prov)
    advance_jobs(jobs, prov, {"pB": p}, inspect=_FAIL)
    assert jobs[0].status is GenerationStatus.RUNNING
    assert jobs[0].attempt == 2
    assert prov.submits == 2  # 再投入された
    assert "再生成 2/3" in jobs[0].error


def test_advance_quality_fail_exhausts_retries_then_skip():
    p = _plan("pC")
    prov = FakeProvider()
    jobs, _ = submit_plans([p], prov)
    for _ in range(3):
        advance_jobs(jobs, prov, {"pC": p}, inspect=_FAIL)
    assert jobs[0].status is GenerationStatus.FAILED
    assert jobs[0].attempt == 3
    assert "上限" in jobs[0].error and "SKIP" in jobs[0].error


def test_advance_provider_failure_retries_then_fails():
    p = _plan("pD")
    prov = FakeProvider(poll_result=GenerationStatus.FAILED)
    jobs, _ = submit_plans([p], prov)
    for _ in range(3):
        advance_jobs(jobs, prov, {"pD": p}, inspect=_PASS)
    assert jobs[0].status is GenerationStatus.FAILED


def test_advance_poll_exception_retries_then_fails_without_crashing():
    p = _plan("pF")
    prov = FakeProvider(poll_raises=RuntimeError("Video extension is not allowed for this model"))
    jobs, _ = submit_plans([p], prov)
    for _ in range(3):
        advance_jobs(jobs, prov, {"pF": p}, inspect=_PASS)
    assert jobs[0].status is GenerationStatus.FAILED
    assert prov.submits == 3  # 初回投入 + 例外での再投入2回
    assert "poll例外" in jobs[0].error and "上限" in jobs[0].error


def test_advance_is_idempotent_on_terminal_jobs():
    p = _plan("pE")
    prov = FakeProvider()
    jobs, _ = submit_plans([p], prov)
    advance_jobs(jobs, prov, {"pE": p}, inspect=_PASS)
    submits_after_first = prov.submits
    advance_jobs(jobs, prov, {"pE": p}, inspect=_PASS)
    assert jobs[0].status is GenerationStatus.SUCCEEDED
    assert prov.submits == submits_after_first  # もう触らない


def test_summarize_counts_by_status():
    jobs = [
        GenerationJob("1", "p", "fake", status=GenerationStatus.SUCCEEDED),
        GenerationJob("2", "p", "fake", status=GenerationStatus.SUCCEEDED),
        GenerationJob("3", "p", "fake", status=GenerationStatus.FAILED),
    ]
    assert summarize(jobs) == {"SUCCEEDED": 2, "FAILED": 1}
