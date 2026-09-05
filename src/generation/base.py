"""動画生成 Provider Adapter のインターフェース（§4 / §12）。

特定ベンダーに固定しない。新しい生成APIは VideoProvider を実装するだけで
差し替えられるようにする。生成待ちで Runner を占有しないため、
submit() と poll() を分離する（§5 poll_generation.yml）。
"""

from __future__ import annotations

import abc

from src.common.models import ContentPlan, GenerationJob


class VideoProvider(abc.ABC):
    """全生成プロバイダの共通契約。"""

    name: str = "base"

    @abc.abstractmethod
    def submit(self, plan: ContentPlan) -> GenerationJob:
        """生成ジョブを投入し、external_job_id を持った GenerationJob を返す。

        ブロッキングしないこと（完了待ちは poll で行う）。
        """

    @abc.abstractmethod
    def poll(self, job: GenerationJob) -> GenerationJob:
        """ジョブ状態を更新して返す。完了時は video_url / cost_jpy を埋める。"""

    @abc.abstractmethod
    def estimate_cost_jpy(self, plan: ContentPlan) -> float:
        """投入前の概算原価。予算ゲート（§13）で使う。"""


_REGISTRY: dict[str, type[VideoProvider]] = {}


def register(cls: type[VideoProvider]) -> type[VideoProvider]:
    _REGISTRY[cls.name] = cls
    return cls


def get_provider(name: str) -> VideoProvider:
    if name not in _REGISTRY:
        raise KeyError(f"unknown video provider: {name!r} (registered: {list(_REGISTRY)})")
    return _REGISTRY[name]()
