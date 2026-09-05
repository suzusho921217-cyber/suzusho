"""generation.quality（§12）: inspect の機械チェック。"""

from src.common.models import (
    Brand,
    ContentPlan,
    ExperimentFlag,
    PolicyRisk,
)
from src.generation.quality import inspect


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


def test_inspect_missing_file_skips_and_passes():
    r = inspect(_plan(), "file://nowhere/x.mp4")
    assert r.passed is True
    assert "未取得" in r.reasons[0]


def test_inspect_ffprobe_absent_skips_and_passes(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"not really a video")
    r = inspect(_plan(), str(f), probe=lambda p: None)
    assert r.passed is True
    assert "ffprobe" in r.reasons[0]


def test_inspect_flags_wrong_aspect_ratio(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    r = inspect(_plan(), str(f), probe=lambda p: {"duration": 10.0, "width": 1920, "height": 1080})
    assert r.passed is False
    assert any("9:16" in x for x in r.reasons)


def test_inspect_flags_duration_far_from_target(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    r = inspect(_plan(duration_target_sec=10), str(f),
                probe=lambda p: {"duration": 45.0, "width": 1080, "height": 1920})
    assert r.passed is False
    assert any("尺" in x for x in r.reasons)


def test_inspect_passes_good_metadata(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    r = inspect(_plan(duration_target_sec=10), str(f),
                probe=lambda p: {"duration": 11.0, "width": 1080, "height": 1920})
    assert r.passed is True
    assert r.scores["aspect_ratio"] == 1080 / 1920
