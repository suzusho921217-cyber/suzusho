"""X publisher（§9 §20）。

API: X API v2 + Media Upload / POST /2/tweets。
注意: pay-per-use。費用上限を必ず設定する（§13）。
指標: impressions / video views / playback 25/50/75/100%
Secrets(env): X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET
"""

from __future__ import annotations

from src.common.models import Platform, Post
from src.publishers.base import Publisher, PublishRequest, PublishResult


class XPublisher(Publisher):
    platform = Platform.X

    def publish(self, req: PublishRequest) -> PublishResult:
        raise NotImplementedError

    def find_existing(self, post: Post) -> str | None:
        raise NotImplementedError

    def fetch_metrics(self, platform_post_id: str) -> dict:
        raise NotImplementedError
