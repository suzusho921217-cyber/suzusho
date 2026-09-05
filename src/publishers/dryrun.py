"""Dry-run publisher。

実際には何も投稿せず、投稿したことにして決定的な ID を返す。
本番アダプタ（youtube/tiktok/instagram/x）が揃うまで、パイプライン全体を
エンドツーエンドで動かすために使う。`PUBLISH_MODE=dryrun`（既定）で選ばれる。
"""

from __future__ import annotations

import hashlib
import random

from src.common.models import Platform, Post
from src.publishers.base import Publisher, PublishRequest, PublishResult


def _idempotency_key(post: Post) -> str:
    return f"{post.master_video_id}|{post.platform.value}|{post.account_id}"


class DryRunPublisher(Publisher):
    """投稿しない。冪等キーから決定的な platform_post_id を作る。"""

    def __init__(self, platform: Platform, ledger: dict[str, str] | None = None) -> None:
        self.platform = platform
        # key -> platform_post_id。CLI が渡す共有 dict を使えば .state に永続化できる。
        self._published: dict[str, str] = ledger if ledger is not None else {}

    def publish(self, req: PublishRequest) -> PublishResult:
        key = _idempotency_key(req.post)
        if key in self._published:
            return PublishResult(
                ok=True, platform_post_id=self._published[key], already_published=True
            )
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        post_id = f"dryrun-{self.platform.value}-{digest}"
        self._published[key] = post_id
        return PublishResult(ok=True, platform_post_id=post_id)

    def find_existing(self, post: Post) -> str | None:
        return self._published.get(_idempotency_key(post))

    def fetch_metrics(self, platform_post_id: str) -> dict:
        """dry-run は投稿IDから決定的なダミー指標を返す（パイプライン全体を通すため）。

        本番アダプタは公式 Analytics API の値をそのまま返す。
        """
        seed = int(hashlib.sha1(platform_post_id.encode("utf-8")).hexdigest()[:8], 16)
        rng = random.Random(seed)
        views = rng.randint(400, 20000)
        return {
            "views": views,
            "likes": int(views * rng.uniform(0.02, 0.09)),
            "comments": int(views * rng.uniform(0.004, 0.03)),
            "shares": int(views * rng.uniform(0.004, 0.05)),
            "impressions": int(views * rng.uniform(1.2, 3.0)),
            "average_view_duration_sec": round(rng.uniform(2.5, 13.0), 1),
            "subscribers_gained": int(views * rng.uniform(0.002, 0.02)),
            "estimated_revenue_jpy": round(views * rng.uniform(0.0, 0.06), 2),
        }
