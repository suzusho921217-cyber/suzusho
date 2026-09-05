"""cli media の配線テスト（ffmpeg 不在時のスキップと manifest 出力）。"""

import json

import pytest

from src.cli import main
from src.media.processor import ffmpeg_available


@pytest.fixture
def generated(tmp_path, monkeypatch):
    monkeypatch.setattr("src.cli.STATE_DIR", tmp_path)
    out = tmp_path / "plan-2026-09-02.json"
    main(["plan-daily", "--date", "2026-09-02", "--out", str(out),
          "--winning-tags", str(tmp_path / "missing.json")])
    main(["generate", "--date", "2026-09-02"])
    main(["poll-generation", "--date", "2026-09-02"])
    return tmp_path


def test_media_writes_manifest_and_publish_reads_it(generated):
    rc = main(["media", "--date", "2026-09-02"])
    assert rc == 0
    manifest = json.loads((generated / "media-2026-09-02.json").read_text(encoding="utf-8"))
    assert manifest["date"] == "2026-09-02"
    # mock の master は実ファイルが無いので変換されない（ffmpeg の有無に関わらず）
    assert manifest["variants"] == {}

    # publish は manifest があっても元動画にフォールバックして通る
    assert main(["publish", "--date", "2026-09-02"]) == 0


@pytest.mark.skipif(ffmpeg_available(), reason="ffmpeg があるとスキップ文言が変わる")
def test_media_reports_ffmpeg_missing(generated, capsys):
    main(["media", "--date", "2026-09-02"])
    assert "ffmpeg が無い" in capsys.readouterr().out


def test_media_without_jobs_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr("src.cli.STATE_DIR", tmp_path)
    assert main(["media", "--date", "2099-01-01"]) == 0
