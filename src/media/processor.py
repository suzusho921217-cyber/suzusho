"""FFmpeg Media Processor（§4 §12）。

ウォーターマークなしのマスターから、媒体別の派生動画を作る。

このモジュールでできること:
- normalize_master … 9:16（1080x1920）へ scale+pad、音量正規化（loudnorm）
- make_variant     … 媒体別に尺を詰めて書き出し（冒頭 hook 秒は先頭から確保）

- テロップ焼き込み … spec.captions（[{start, end, text}]）を白太字＋黒ふちで画面上部に重ねる
  （文言は `media.captions` が完成動画を見て作る。日本語フォントが無い環境では焼き込まない）

まだできないこと:
- SE / BGM ミックス / CTA テロップ（BGM は著作権の都合で当面なし。Veo の生成音を使う）

FFmpeg バイナリが要る。無い環境では `ffmpeg_available()` が False を返し、
呼び出し側（cli media）はスキップする。GitHub Actions では `_reusable.yml` が apt で入れる。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

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
    captions: list[dict] = field(default_factory=list)  # [{start, end, text}]


# 日本語の太字フォント（見つかった最初のもの）。GitHub Actions は apt の fonts-noto-cjk。
_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
)
_CAPTION_FONTSIZE = 84
_CAPTION_WRAP = 10      # 1行の最大文字数（84px × 10字 ≒ 840px。画面幅 1080 に収まる）
_CAPTION_Y = "h*0.14"   # 画面上部（下部は媒体の UI と被る）


def caption_font() -> str | None:
    return next((f for f in _FONT_CANDIDATES if Path(f).exists()), None)


def _wrap(text: str, width: int = _CAPTION_WRAP) -> str:
    return "\n".join(text[i:i + width] for i in range(0, len(text), width))


def caption_filter(captions: list[dict], font: str, workdir: str) -> str:
    """テロップを焼き込む drawtext フィルタ列。1行ずつ中央揃えで描く。

    文字列のエスケープ事故を避けるため、各行は textfile で渡す。
    """
    parts = []
    line_h = _CAPTION_FONTSIZE + 18
    for i, c in enumerate(captions):
        lines = c.get("lines") or _wrap(c["text"]).split("\n")
        for k, line in enumerate(lines):
            tf = Path(workdir) / f"cap{i}_{k}.txt"
            tf.write_text(line, encoding="utf-8")
            parts.append(
                f"drawtext=fontfile='{font}':textfile='{tf}':fontsize={_CAPTION_FONTSIZE}"
                f":fontcolor=white:borderw=8:bordercolor=black"
                f":x=(w-text_w)/2:y={_CAPTION_Y}+{k * line_h}"
                f":enable='between(t,{float(c['start'])},{float(c['end'])})'"
            )
    return ",".join(parts)


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


def variant_cmd(master_path: str, spec: MediaVariantSpec, out_path: str,
                *, vf: str | None = None) -> list[str]:
    """媒体別派生の ffmpeg 引数（尺トリム。先頭 = hook を必ず含む。vf があればテロップ等を重ねる）。"""
    return [
        "ffmpeg", "-y", "-i", _strip_scheme(master_path),
        "-t", str(int(spec.duration_sec)),
        *(["-vf", vf] if vf else []),
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
    """媒体別派生を書き出し、out_path を返す。テロップがあれば焼き込む（フォントが無ければ付けない）。"""
    font = caption_font() if spec.captions else None
    if spec.captions and not font:
        print("[media] 日本語フォントが見つからないためテロップ無しで書き出す")
    with tempfile.TemporaryDirectory() as tmp:
        vf = caption_filter(spec.captions, font, tmp) if font else None
        _run(variant_cmd(master_path, spec, out_path, vf=vf))
    return out_path
