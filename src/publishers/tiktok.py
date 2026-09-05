"""TikTok publisher（§9 §20）。

API: Content Posting API (Direct Post) + Display API。
注意: video.publish scope の承認が必要。未監査クライアント投稿は private 制限。
      Direct Post は明示的同意等の UX 要件あり — 回避しない（§8）。
Secrets(env): TIKTOK_CLIENT_KEY / _CLIENT_SECRET / _ACCESS_TOKEN / _REFRESH_TOKEN
"""

from __future__ import annotations

from src.common.models import Platform, Post
from src.publishers.base import Publisher, PublishRequest, PublishResult


class TikTokPublisher(Publisher):
    platform = Platform.TIKTOK

    def publish(self, req: PublishRequest) -> PublishResult:
        raise NotImplementedError

    def find_existing(self, post: Post) -> str | None:
        raise NotImplementedError

    def fetch_metrics(self, platform_post_id: str) -> dict:
        raise NotImplementedError
