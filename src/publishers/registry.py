"""Platform -> Publisher の対応表。

PUBLISH_MODE:
  - "dryrun"（既定）… DryRunPublisher。実際には投稿しない
  - "live"          … 各媒体の本番アダプタ（API 認証情報が必要）
"""

from __future__ import annotations

from src.common.config import env
from src.common.models import Platform
from src.publishers.base import Publisher
from src.publishers.dryrun import DryRunPublisher
from src.publishers.instagram import InstagramPublisher
from src.publishers.tiktok import TikTokPublisher
from src.publishers.x import XPublisher
from src.publishers.youtube import YouTubePublisher

_LIVE: dict[Platform, type[Publisher]] = {
    Platform.YOUTUBE: YouTubePublisher,
    Platform.TIKTOK: TikTokPublisher,
    Platform.INSTAGRAM: InstagramPublisher,
    Platform.X: XPublisher,
}


def get_publisher(platform: Platform, *, mode: str | None = None) -> Publisher:
    mode = (mode or env("PUBLISH_MODE", "dryrun") or "dryrun").lower()
    if mode == "dryrun":
        return DryRunPublisher(platform)
    return _LIVE[platform]()
