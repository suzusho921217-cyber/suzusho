"""Instagram Reels publisher（§9 §19）。

API: Instagram Reels Publishing + Insights。
注意: 実装開始時に Meta 公式ドキュメントでアカウント種別・権限・指標を再確認する。
Secrets(env): META_APP_ID / META_APP_SECRET / IG_ACCESS_TOKEN / IG_BUSINESS_ACCOUNT_ID
"""

from __future__ import annotations

from src.common.models import Platform, Post
from src.publishers.base import Publisher, PublishRequest, PublishResult


class InstagramPublisher(Publisher):
    platform = Platform.INSTAGRAM

    def publish(self, req: PublishRequest) -> PublishResult:
        raise NotImplementedError

    def find_existing(self, post: Post) -> str | None:
        raise NotImplementedError

    def fetch_metrics(self, platform_post_id: str) -> dict:
        raise NotImplementedError
