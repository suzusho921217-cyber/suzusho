"""テロップ: 出力の整形・焼き込みフィルタ・キー無しなら何もしない・失敗はキャッシュしない。"""

import json
from types import SimpleNamespace

from src.media import captions as cap
from src.media import processor


def test_clean_limits_count_length_and_time():
    raw = [
        {"start": 0, "end": 2, "text": "今日はお買い物にゃ！"},
        {"start": 2.5, "end": 99, "text": "あ" * 40},
        {"start": 5, "end": 5.3, "text": "短すぎ"},
        {"start": 6, "end": 8, "text": "1個だけね"},
        {"start": 7, "end": 8, "text": "4枚目"},
        {"oops": 1},
    ]
    out = cap.clean(raw, 8)
    assert [c["text"] for c in out] == ["今日はお買い物にゃ！", "あ" * cap.MAX_CHARS, "1個だけね"]
    assert out[1]["end"] == 8
    assert out[1]["lines"] == ["あ" * cap.MAX_LINE] * 2
    ok = cap.clean([{"start": 0, "end": 2, "lines": ["カメラ目線で", "ご挨拶にゃ♪"]}], 8)
    assert ok[0]["lines"] == ["カメラ目線で", "ご挨拶にゃ♪"]


def test_caption_filter_uses_textfiles(tmp_path):
    vf = processor.caption_filter(
        [{"start": 0, "end": 2, "text": "リストにないおやつ入れた…", "lines": ["リストにない", "おやつ入れた…"]}],
        "/f.ttc", str(tmp_path))
    assert "drawtext=fontfile='/f.ttc'" in vf and "between(t,0.0,2.0)" in vf
    assert vf.count("drawtext=") == 2 and "x=(w-text_w)/2" in vf  # 1行ずつ中央揃え
    assert (tmp_path / "cap0_1.txt").read_text() == "おやつ入れた…"


def test_variant_cmd_adds_vf_only_when_given():
    spec = processor.MediaVariantSpec(platform=processor.Platform.YOUTUBE, duration_sec=8)
    assert "-vf" not in processor.variant_cmd("m.mp4", spec, "o.mp4")
    assert "-vf" in processor.variant_cmd("m.mp4", spec, "o.mp4", vf="drawtext=x")


def test_no_key_means_no_captions_and_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cap, "STATE_DIR", tmp_path)
    monkeypatch.delenv("GEMINI_ANALYSIS_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "paid-key-must-not-be-used")
    assert cap.for_plan("2026-10-04", "p1", "v.mp4", concept="c", hook="h", duration=8) == []
    assert not (tmp_path / "captions-2026-10-04.json").exists()


def test_suggest_with_fake_client_deletes_upload():
    deleted = []
    client = SimpleNamespace(
        files=SimpleNamespace(upload=lambda file: SimpleNamespace(name="f1", state="ACTIVE"),
                              get=lambda name: None, delete=lambda name: deleted.append(name)),
        models=SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(
            text=json.dumps([{"start": 0, "end": 2, "text": "見て！"}]))),
    )
    caps = cap.suggest("v.mp4", concept="c", hook="h", duration=8, client=client)
    assert caps == [{"start": 0.0, "end": 2.0, "text": "見て！", "lines": ["見て！"]}]
    assert deleted == ["f1"]


def test_for_plan_caches_success_but_not_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(cap, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cap, "suggest", lambda *a, **k: None)  # 失敗
    assert cap.for_plan("2026-10-04", "p1", "v.mp4", concept="c", hook="h", duration=8) == []
    assert not (tmp_path / "captions-2026-10-04.json").exists()
    monkeypatch.setattr(cap, "suggest", lambda *a, **k: [{"start": 0, "end": 2, "text": "見て！"}])
    cap.for_plan("2026-10-04", "p1", "v.mp4", concept="c", hook="h", duration=8)
    monkeypatch.setattr(cap, "suggest", lambda *a, **k: 1 / 0)  # 2回目は呼ばれない
    assert cap.for_plan("2026-10-04", "p1", "v.mp4", concept="c", hook="h", duration=8)[0]["text"] == "見て！"
