"""media.processor（§12）: ffmpeg 引数の組み立てと不在時の振る舞い。"""

import subprocess

import pytest

from src.common.models import Platform
from src.media.processor import (
    MediaError,
    MediaVariantSpec,
    ffmpeg_available,
    make_variant,
    normalize_cmd,
    normalize_master,
    variant_cmd,
)

_HAS_FFMPEG = ffmpeg_available()


def test_normalize_cmd_targets_9_16_and_loudnorm():
    cmd = normalize_cmd("file://in.mp4", "out.mp4")
    assert cmd[0] == "ffmpeg" and cmd[-1] == "out.mp4"
    assert "in.mp4" in cmd  # file:// が剥がれている
    vf = cmd[cmd.index("-vf") + 1]
    assert "1080:1920" in vf and "pad=1080:1920" in vf
    assert "loudnorm" in cmd[cmd.index("-af") + 1]


def test_variant_cmd_trims_to_duration():
    spec = MediaVariantSpec(platform=Platform.YOUTUBE, duration_sec=8)
    cmd = variant_cmd("master.mp4", spec, "yt.mp4")
    assert cmd[cmd.index("-t") + 1] == "8"
    assert cmd[-1] == "yt.mp4"


@pytest.mark.skipif(_HAS_FFMPEG, reason="ffmpeg があると別経路")
def test_raises_media_error_without_ffmpeg(tmp_path):
    with pytest.raises(MediaError):
        normalize_master("x.mp4", str(tmp_path / "o.mp4"))


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg 必須")
def test_normalize_and_variant_end_to_end(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=640x480:rate=15:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest", str(src)],
        capture_output=True, check=True,
    )
    master = normalize_master(str(src), str(tmp_path / "master.mp4"))
    out = make_variant(
        master, MediaVariantSpec(platform=Platform.TIKTOK, duration_sec=2),
        str(tmp_path / "tt.mp4"),
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", out],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert probe == "1080,1920"
