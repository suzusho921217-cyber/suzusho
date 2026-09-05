"""媒体別 Publisher Adapter のインターフェース（§4 / §9 / §15）。

重要（§8）: 公式APIが要求するユーザー同意・UI・監査・アプリ審査を回避する目的で
ブラウザRPAを使わない。各アダプタは公式APIのみを叩く。

冪等性（§15）: (master_video_id, platform, account_id) を一意キーとし二重送信しない。
タイムアウト時は「失敗」と決めつけず、投稿状態を照会してから retry する。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

from src.common.models import Brand, Platform, Post


@dataclass
class PublishRequest:
    post: Post
    video_path: str                 # 媒体別に加工済みの派生動画
    title: str
    caption: str
    tags: list[str]
    ai_disclosure: bool = False      # 実写級AI生成の開示ラベル（§7 / policy_rules）


@dataclass
class PublishResult:
    ok: bool
    platform_post_id: str | None = None
    error: str | None = None
    already_published: bool = False  # 冪等照会でヒットした場合


class Publisher(abc.ABC):
    """媒体アダプタの基底。

    brand: ブランド別に別アカウント/チャンネルを使う媒体（YouTube等）向け。
    認証情報の選択に使う（例: `YOUTUBE_OAUTH_REFRESH_TOKEN_<BRAND>`）。
    """

    platform: Platform

    def __init__(self, brand: Brand | None = None) -> None:
        self.brand = brand

    @abc.abstractmethod
    def publish(self, req: PublishRequest) -> PublishResult:
        """公式APIで動画を投稿する。"""

    @abc.abstractmethod
    def find_existing(self, post: Post) -> str | None:
        """冪等キーに対応する既存投稿IDを照会する。無ければ None。"""

    @abc.abstractmethod
    def fetch_metrics(self, platform_post_id: str) -> dict:
        """指標を取得する。取得できない項目は含めない（呼び出し側で再正規化）。"""
