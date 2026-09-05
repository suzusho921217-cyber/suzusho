"""FFmpeg Media Processor（§4 §12）。

ウォーターマークなしのマスターから、媒体別の派生動画を作る。

このモジュールでできること:
- normalize_master … 9:16（1080x1920）へ scale+pad、音量正規化（loudnorm）
- make_variant     … 媒体別に尺を詰めて書き出し（冒頭 hook 秒は先頭から確保）

まだできないこと（素材ファイルと設計詰めが要る）:
- 字幕焼き込み / SE / BGM ミックス / CTA テロップ（spec に受け口だけ用意済み）

FFmpeg バイナリが要る。無い環境では `ffmpeg_available()` が False を返し、
呼び出し側（cli media）はスキップする。GitHub Actions では `_reusable.yml` が apt で入れる。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from src.common.models import Platform

_W, _H = 1080, 1920  # 9:16 縦型


class MediaError(RuntimeError):
    pass


@dataclass
class MediaVariantSpec:
    platform: Platform
    duration_sec: int
    caption_style: str = "none"
    hook_seconds: float = 1.5
    cta_text: str = ""


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _strip_scheme(path: str) -> str:
    return path.removeprefix("file://")


def normalize_cmd(src_path: str, out_path: str) -> list[str]:
    """9:16 化＋音量正規化の ffmpeg 引数（純粋関数。テスト・監査用）。"""
    vf = (
        f"scale={_W}:{_H}:force_original_aspect_ratio=decrease,"
        f"pad={_W}:{_H}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
    return [
        "ffmpeg", "-y", "-i", _strip_scheme(src_path),
        "-vf", vf,
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]


def variant_cmd(master_path: str, spec: MediaVariantSpec, out_path: str) -> list[str]:
    """媒体別派生の ffmpeg 引数（尺トリム。先頭 = hook を必ず含む）。"""
    return [
        "ffmpeg", "-y", "-i", _strip_scheme(master_path),
        "-t", str(int(spec.duration_sec)),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]


def _run(cmd: list[str]) -> None:
    if not ffmpeg_available():
        raise MediaError("ffmpeg が見つからない（brew install ffmpeg）")
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=True)
    except subprocess.CalledProcessError as e:
        raise MediaError(f"ffmpeg 失敗: {e.stderr[-500:] if e.stderr else e}") from e
    except (subprocess.SubprocessError, OSError) as e:
        raise MediaError(f"ffmpeg 実行エラー: {e}") from e


def normalize_master(src_path: str, out_path: str) -> str:
    """9:16・音量正規化したマスターを作り、out_path を返す。"""
    _run(normalize_cmd(src_path, out_path))
    return out_path


def make_variant(master_path: str, spec: MediaVariantSpec, out_path: str) -> str:
    """媒体別派生を書き出し、out_path を返す。"""
    _run(variant_cmd(master_path, spec, out_path))
    return out_path
