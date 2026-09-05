"""generation.providers.veo: 純粋ヘルパ ＋ モックした Gemini クライアントでの submit/poll。"""

from src.common.models import (
    Brand,
    ContentPlan,
    ExperimentFlag,
    GenerationJob,
    GenerationStatus,
    PolicyRisk,
)
from src.generation.base import get_provider
from src.generation.providers.veo import (
    VeoVideoProvider,
    pick_duration,
    split_prompt,
)


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


# --- 純粋ヘルパ --------------------------------------------------------

def test_pick_duration_snaps_to_allowed():
    assert pick_duration(6) == 6
    assert pick_duration(7) == 8
    assert pick_duration(10) == 8   # 目標 10 でも Veo 上限 8
    assert pick_duration(3) == 4
    assert pick_duration(5, [4, 6, 8]) == 6


def test_split_prompt_separates_negative():
    text = "かわいい猫が窓辺にいる。9:16。\n【禁止】実在人物, 露骨表現"
    pos, neg = split_prompt(text, _plan())
    assert "かわいい猫" in pos and "【禁止】" not in pos
    assert "実在人物" in neg


def test_split_prompt_without_marker_and_none():
    assert split_prompt("just a prompt", _plan()) == ("just a prompt", "")
    pos, neg = split_prompt(None, _plan())
    assert "違和感" in pos and neg == ""


def test_estimate_cost_uses_config_price():
    p = VeoVideoProvider()
    p.cfg = {"allowed_durations": [4, 6, 8], "price_jpy_per_sec": 12}
    assert p.estimate_cost_jpy(_plan(duration_target_sec=6)) == 72   # 6s * 12
    assert p.estimate_cost_jpy(_plan(duration_target_sec=15)) == 96  # 8s * 12


def test_registered_as_veo():
    assert get_provider("veo").name == "veo"


# --- モック Gemini クライアント ----------------------------------------

class _FakeConfig:
    def __init__(self, **kw):
        self.kw = kw


class _FakeTypes:
    GenerateVideosConfig = _FakeConfig
    GenerateVideosOperation = None


class _FakeOp:
    def __init__(self, *, name="operations/abc", done=False, error=None, videos=None):
        self.name = name
        self.done = done
        self.error = error
        self.response = type("R", (), {"generated_videos": videos or []})()


class _FakeModels:
    def __init__(self, op):
        self._op = op
        self.calls = []

    def generate_videos(self, *, model, prompt, config):
        self.calls.append({"model": model, "prompt": prompt, "config": config.kw})
        return self._op


class _FakeFiles:
    def __init__(self):
        self.downloaded = []

    def download(self, *, file):
        self.downloaded.append(file)


class _FakeOps:
    def __init__(self, op):
        self._op = op

    def get(self, *args, **kwargs):
        return self._op


class _FakeClient:
    def __init__(self, op):
        self.models = _FakeModels(op)
        self.files = _FakeFiles()
        self.operations = _FakeOps(op)


def _wire(monkeypatch, provider, op):
    client = _FakeClient(op)
    monkeypatch.setattr(provider, "_genai", lambda: (object(), _FakeTypes))
    monkeypatch.setattr(provider, "_client", lambda: client)
    return client


def test_submit_returns_running_job_with_operation_name(monkeypatch):
    p = VeoVideoProvider()
    p.cfg = {"model": "veo-3.1-lite-generate-preview", "aspect_ratio": "9:16",
             "resolution": "1080p", "allowed_durations": [4, 6, 8], "price_jpy_per_sec": 12}
    client = _wire(monkeypatch, p, _FakeOp(name="operations/xyz"))

    job = p.submit(_plan(duration_target_sec=8, prompt_text="猫\n【禁止】実在人物"))
    assert job.status is GenerationStatus.RUNNING
    assert job.external_job_id == "operations/xyz"
    assert job.cost_jpy == 96  # 8 * 12
    call = client.models.calls[0]
    assert call["config"]["duration_seconds"] == 8
    assert call["config"]["aspect_ratio"] == "9:16"
    # 既定（use_negative_prompt=false / Lite）: 禁止節はプロンプト本文に残す
    assert "negative_prompt" not in call["config"]
    assert "【禁止】実在人物" in call["prompt"]


def test_submit_uses_negative_prompt_when_enabled(monkeypatch):
    p = VeoVideoProvider()
    p.cfg = {"allowed_durations": [4, 6, 8], "use_negative_prompt": True}
    client = _wire(monkeypatch, p, _FakeOp())
    p.submit(_plan(prompt_text="猫\n【禁止】実在人物"))
    call = client.models.calls[0]
    assert call["config"]["negative_prompt"] == "実在人物"
    assert "【禁止】" not in call["prompt"]


def test_poll_running_when_not_done(monkeypatch):
    p = VeoVideoProvider()
    p.cfg = {}
    _wire(monkeypatch, p, _FakeOp(done=False))
    job = p.poll(GenerationJob("j", "p1", "veo", external_job_id="operations/abc",
                               status=GenerationStatus.RUNNING))
    assert job.status is GenerationStatus.RUNNING


def test_poll_failed_on_error(monkeypatch):
    p = VeoVideoProvider()
    p.cfg = {}
    _wire(monkeypatch, p, _FakeOp(done=True, error="quota exceeded"))
    job = p.poll(GenerationJob("j", "p1", "veo", external_job_id="operations/abc",
                               status=GenerationStatus.RUNNING))
    assert job.status is GenerationStatus.FAILED and "quota" in job.error


def test_poll_success_downloads_and_sets_paths(monkeypatch, tmp_path):
    class _Vid:
        def __init__(self, path):
            self._p = path
            self.video = self

        @property
        def uri(self):
            return "https://files/generated.mp4"

        def save(self, dest):
            with open(dest, "wb") as fh:
                fh.write(b"mp4")

    p = VeoVideoProvider()
    p.cfg = {"download_dir": str(tmp_path)}
    client = _wire(monkeypatch, p, _FakeOp(done=True, videos=[_Vid(tmp_path)]))
    job = p.poll(GenerationJob("j", "p1", "veo", external_job_id="operations/abc",
                               status=GenerationStatus.RUNNING))
    assert job.status is GenerationStatus.SUCCEEDED
    assert job.local_path == str(tmp_path / "p1.mp4")
    assert (tmp_path / "p1.mp4").read_bytes() == b"mp4"
    assert client.files.downloaded  # download 呼ばれた
