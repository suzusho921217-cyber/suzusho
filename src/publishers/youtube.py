"""YouTube Shorts publisher（§9 §20）。

API: YouTube Data API v3 `videos.insert` + YouTube Analytics API。
注意: 未監査APIプロジェクトのアップロードは private 制限があり得る。監査前提。
指標: views / engagedViews / averageViewDuration / shares / subscribersGained / estimatedRevenue
Secrets(env): YOUTUBE_OAUTH_CLIENT_ID / _SECRET / _REFRESH_TOKEN
"""

from __future__ import annotations

from src.common.models import Platform, Post
from src.publishers.base import Publisher, PublishRequest, PublishResult


class YouTubePublisher(Publisher):
    platform = Platform.YOUTUBE

    def publish(self, req: PublishRequest) -> PublishResult:
        raise NotImplementedError

    def find_existing(self, post: Post) -> str | None:
        raise NotImplementedError

    def fetch_metrics(self, platform_post_id: str) -> dict:
        raise NotImplementedError
