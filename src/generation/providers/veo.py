"""Veo（Google Gemini API）動画生成アダプタ（§12）。

pay-as-you-go。月額サブスク不要。`gemini-3.6-flash` と同じ Gemini API キーを使う。
`VIDEO_PROVIDER=veo`（または config/generation.yaml の provider: veo）で有効。

必要:
  pip install google-genai
  環境変数 GEMINI_API_KEY（無ければ GOOGLE_API_KEY）

設計上の注意:
  - Veo は 1 本 4/6/8 秒のみ。企画の目標尺以上で最小のものを選ぶ。
  - 出力は常に音声付き・9:16 指定可。
  - submit は operation 名だけ返す（GenerationJob.external_job_id）。poll で完了確認＋DL。
  - google-genai の import はメソッド内（未インストールでもモジュール読み込みは通す）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.common.config import env, load
from src.common.models import ContentPlan, GenerationJob, GenerationStatus
from src.generation.base import VideoProvider, register

_UTC = timezone.utc


def _veo_cfg() -> dict:
    return (load("generation") or {}).get("veo", {}) or {}


def pick_duration(target_sec: int, allowed: list[int] | None = None) -> int:
    """目標尺以上で最小の許容尺。無ければ最大値（Veo は 4/6/8）。"""
    options = sorted(allowed or [4, 6, 8])
    for d in options:
        if d >= target_sec:
            return d
    return options[-1]


def split_prompt(prompt_text: str | None, plan: ContentPlan) -> tuple[str, str]:
    """render_prompt の出力を (肯定プロンプト, 禁止プロンプト) に分ける。"""
    if not prompt_text:
        base = f"{plan.concept_tag} / {plan.hook_type} / {plan.character_id}"
        return base, ""
    if "【禁止】" in prompt_text:
        positive, _, negative = prompt_text.partition("【禁止】")
        return positive.strip(), negative.strip()
    return prompt_text.strip(), ""


@register
class VeoVideoProvider(VideoProvider):
    name = "veo"

    def __init__(self) -> None:
        self.cfg = _veo_cfg()
        self._client_obj = None

    # --- VideoProvider ---------------------------------------------------

    def estimate_cost_jpy(self, plan: ContentPlan) -> float:
        dur = pick_duration(plan.duration_target_sec, self.cfg.get("allowed_durations"))
        return dur * float(self.cfg.get("price_jpy_per_sec", 12))

    def submit(self, plan: ContentPlan) -> GenerationJob:
        _, types = self._genai()
        client = self._client()
        dur = pick_duration(plan.duration_target_sec, self.cfg.get("allowed_durations"))
        positive, negative = split_prompt(plan.prompt_text, plan)

        gvc_kwargs = {
            "aspect_ratio": str(self.cfg.get("aspect_ratio", "9:16")),
            "resolution": str(self.cfg.get("resolution", "1080p")),
            "duration_seconds": int(dur),
        }
        if negative and self.cfg.get("use_negative_prompt", False):
            gvc_kwargs["negative_prompt"] = negative
        elif negative:
            # negative_prompt 非対応モデル（Lite 等）: 禁止事項もプロンプト本文に残す
            positive = plan.prompt_text or positive

        operation = client.models.generate_videos(
            model=str(self.cfg.get("model", "veo-3.1-lite-generate-preview")),
            prompt=positive,
            config=types.GenerateVideosConfig(**_filter_supported(types, gvc_kwargs)),
        )
        return GenerationJob(
            job_id=str(uuid.uuid4()),
            plan_id=plan.plan_id,
            provider=self.name,
            external_job_id=getattr(operation, "name", None) or str(operation),
            status=GenerationStatus.RUNNING,
            cost_jpy=dur * float(self.cfg.get("price_jpy_per_sec", 12)),
            submitted_at=datetime.now(_UTC),
        )

    def poll(self, job: GenerationJob) -> GenerationJob:
        client = self._client()
        op = self._get_operation(client, job.external_job_id)

        if not getattr(op, "done", False):
            job.status = GenerationStatus.RUNNING
            return job

        if getattr(op, "error", None):
            job.status = GenerationStatus.FAILED
            job.error = str(op.error)
            job.completed_at = datetime.now(_UTC)
            return job

        videos = list(getattr(op.response, "generated_videos", []) or [])
        if not videos:
            job.status = GenerationStatus.FAILED
            job.error = "Veo: response に generated_videos が無い"
            job.completed_at = datetime.now(_UTC)
            return job

        video = videos[0]

        # 15秒化: target_total_sec を満たすまで extend を繰り返す（同期・§12）。
        # base は 8 秒。extend は 1 回 +7 秒。target 0/未設定 なら単発クリップ。
        target = int(self.cfg.get("target_total_sec") or 0)
        extends = 0
        if target > 8:
            video, extends = self._extend_to(client, video, target)
            job.cost_jpy = job.cost_jpy + extends * 8 * float(self.cfg.get("price_jpy_per_sec", 8))

        out_dir = Path(self.cfg.get("download_dir", ".state/generation"))
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{job.plan_id}.mp4"
        self._download(client, video, out_path)

        job.status = GenerationStatus.SUCCEEDED
        job.video_url = getattr(getattr(video, "video", None), "uri", None) or str(out_path)
        job.local_path = str(out_path)
        job.error = f"extend x{extends}" if extends else None
        job.completed_at = datetime.now(_UTC)
        return job

    def _extend_to(self, client, video, target_sec: int):
        """base クリップを target_sec 以上になるまで継ぎ足す。(最終GeneratedVideo, extend回数) を返す。"""
        import time

        _, types = self._genai()
        total, rounds = 8, 0
        current = video
        cfg = _filter_supported(types, {
            "aspect_ratio": str(self.cfg.get("aspect_ratio", "9:16")),
            "resolution": str(self.cfg.get("resolution", "720p")),
        })
        interval = float(self.cfg.get("poll_interval_sec", 15))
        while total < target_sec and rounds < 6:
            rounds += 1
            op = client.models.generate_videos(
                model=str(self.cfg.get("model", "veo-3.1-lite-generate-preview")),
                video=current.video,
                prompt="同じ猫・同じ場所・同じ雰囲気のまま、シーンを自然に続ける。",
                config=types.GenerateVideosConfig(**cfg),
            )
            while not getattr(op, "done", False):
                time.sleep(interval)
                op = self._get_operation(client, getattr(op, "name", None))
            vids = list(getattr(op.response, "generated_videos", []) or [])
            if getattr(op, "error", None) or not vids:
                break
            current = vids[0]
            total += 7
        return current, rounds

    # --- 内部 ----------------------------------------------------------

    def _genai(self):
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:  # pragma: no cover - 環境依存
            raise RuntimeError(
                "google-genai が未インストール（pip install google-genai）"
            ) from e
        return genai, types

    def _client(self):
        if self._client_obj is None:
            genai, _ = self._genai()
            api_key = None
            for name in self.cfg.get("api_key_envs", ["GEMINI_API_KEY", "GOOGLE_API_KEY"]):
                api_key = env(name)
                if api_key:
                    break
            self._client_obj = genai.Client(api_key=api_key) if api_key else genai.Client()
        return self._client_obj

    def _get_operation(self, client, name: str):
        ops = client.operations
        try:
            return ops.get(name=name)
        except TypeError:
            pass
        _, types = self._genai()
        op_type = getattr(types, "GenerateVideosOperation", None)
        if op_type is not None:
            return ops.get(op_type(name=name))
        return ops.get(name)

    def _download(self, client, video, out_path: Path) -> None:
        file_obj = getattr(video, "video", video)
        client.files.download(file=file_obj)
        for saver in ("save", "write"):
            fn = getattr(file_obj, saver, None)
            if callable(fn):
                fn(str(out_path))
                return
        data = getattr(file_obj, "video_bytes", None) or getattr(file_obj, "data", None)
        if data:
            out_path.write_bytes(data)
            return
        raise RuntimeError("Veo: 生成動画の保存方法が SDK バージョンと合わない（要調整）")


def _filter_supported(types, kwargs: dict) -> dict:
    """GenerateVideosConfig が受け付けるキーだけ残す（SDK バージョン差の吸収）。"""
    cfg_cls = getattr(types, "GenerateVideosConfig", None)
    fields = getattr(cfg_cls, "model_fields", None)
    if not fields:
        return kwargs
    return {k: v for k, v in kwargs.items() if k in fields}
