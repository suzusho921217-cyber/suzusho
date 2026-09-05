"""YouTube Shorts publisher（§9 §20）。

API: YouTube Data API v3 `videos.insert`（投稿）+ YouTube Analytics API（指標）。
注意: 未監査APIプロジェクトのアップロードは private 制限があり得る（監査前提）。
指標: views / engagedViews / averageViewDuration / shares / subscribersGained / estimatedRevenue
Secrets(env): YOUTUBE_OAUTH_CLIENT_ID / _SECRET は共通（同じOAuthアプリ）。
リフレッシュトークンはブランド別チャンネルごとに別物（例: 猫チャンネルと犬チャンネル）
なので `YOUTUBE_OAUTH_REFRESH_TOKEN_<BRAND>`（例: `_CAT` / `_DOG`）を使う。
未設定なら `YOUTUBE_OAUTH_REFRESH_TOKEN`（単一アカウント運用向けの後方互換）にフォールバック。

冪等性（§15）: サーバ側に自前キーを持てないため、post_key を非公開タグ
（`pk:<post_key>`。視聴者には表示されない）として埋め込み、`search.list(forMine=True)`
で照会する。

AI開示（§7 / config/policy_rules/youtube.yaml の ai_disclosure_required）:
`status.containsSyntheticMedia` で「加工・合成されたコンテンツ」を宣言する
（公式ヘルプ: https://support.google.com/youtube/answer/14328491）。
"""

from __future__ import annotations

from src.common.config import env
from src.common.models import Brand, Platform, Post
from src.publishers.base import Publisher, PublishRequest, PublishResult

_TOKEN_URI = "https://oauth2.googleapis.com/token"
_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",  # videos.list / search.list に必要
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _idempotency_tag(post: Post) -> str:
    return f"pk:{post.post_key}"


class YouTubePublisher(Publisher):
    platform = Platform.YOUTUBE

    def __init__(self, brand: Brand | None = None) -> None:
        super().__init__(brand)
        self._youtube_client = None
        self._analytics_client = None

    def publish(self, req: PublishRequest) -> PublishResult:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        body = {
            "snippet": {
                "title": req.title[:100],  # YouTube 上限100文字
                "description": req.caption,
                "tags": [*req.tags, _idempotency_tag(req.post)],
                "categoryId": env("YOUTUBE_CATEGORY_ID", "15"),  # 既定: Pets & Animals
            },
            "status": {
                "privacyStatus": env("YOUTUBE_PRIVACY_STATUS", "public"),
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": bool(req.ai_disclosure),
            },
        }
        media = MediaFileUpload(req.video_path, chunksize=-1, resumable=True)
        try:
            resp = self._youtube().videos().insert(
                part="snippet,status", body=body, media_body=media,
            ).execute()
        except (HttpError, OSError) as e:
            return PublishResult(ok=False, error=str(e))
        return PublishResult(ok=True, platform_post_id=resp["id"])

    def find_existing(self, post: Post) -> str | None:
        from googleapiclient.errors import HttpError

        try:
            resp = self._youtube().search().list(
                part="id", forMine=True, type="video",
                q=_idempotency_tag(post), maxResults=1,
            ).execute()
        except HttpError:
            return None  # 照会に失敗しても投稿自体は続行させる（新規投稿を試みる）
        items = resp.get("items", [])
        return items[0]["id"]["videoId"] if items else None

    def fetch_metrics(self, platform_post_id: str) -> dict:
        from googleapiclient.errors import HttpError

        metrics: dict = {}
        try:
            resp = self._youtube().videos().list(
                part="statistics", id=platform_post_id,
            ).execute()
            items = resp.get("items", [])
            if items:
                stats = items[0].get("statistics", {})
                if "viewCount" in stats:
                    metrics["views"] = int(stats["viewCount"])
                if "likeCount" in stats:
                    metrics["likes"] = int(stats["likeCount"])
                if "commentCount" in stats:
                    metrics["comments"] = int(stats["commentCount"])
        except HttpError as e:
            print(f"[youtube] videos.list 失敗: {e}")

        try:
            metrics.update(self._fetch_analytics(platform_post_id))
        except HttpError as e:
            # 未監査/未収益化チャンネルでは engagedViews 等が取れないことがある。
            # 基本指標（上）だけで処理を続ける。
            print(f"[youtube] Analytics 取得失敗（基本指標のみで継続）: {e}")
        return metrics

    def _fetch_analytics(self, platform_post_id: str) -> dict:
        from datetime import datetime, timedelta, timezone

        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=365)  # 動画の公開日が分からないため広めに取る
        resp = self._analytics().reports().query(
            ids="channel==MINE",
            startDate=start.isoformat(),
            endDate=end.isoformat(),
            metrics="engagedViews,averageViewDuration,shares,subscribersGained,estimatedRevenue",
            filters=f"video=={platform_post_id}",
        ).execute()
        rows = resp.get("rows") or []
        if not rows:
            return {}
        headers = [h["name"] for h in resp.get("columnHeaders", [])]
        return dict(zip(headers, rows[0]))

    # --- 内部: 認証 ------------------------------------------------------

    def _refresh_token(self) -> str | None:
        if self.brand is not None:
            per_brand = env(f"YOUTUBE_OAUTH_REFRESH_TOKEN_{self.brand.value.upper()}")
            if per_brand:
                return per_brand
        return env("YOUTUBE_OAUTH_REFRESH_TOKEN")

    def _credentials(self):
        from google.oauth2.credentials import Credentials

        return Credentials(
            token=None,
            refresh_token=self._refresh_token(),
            token_uri=_TOKEN_URI,
            client_id=env("YOUTUBE_OAUTH_CLIENT_ID"),
            client_secret=env("YOUTUBE_OAUTH_CLIENT_SECRET"),
            scopes=_SCOPES,
        )

    def _youtube(self):
        if self._youtube_client is None:
            from googleapiclient.discovery import build

            self._youtube_client = build("youtube", "v3", credentials=self._credentials())
        return self._youtube_client

    def _analytics(self):
        if self._analytics_client is None:
            from googleapiclient.discovery import build

            self._analytics_client = build(
                "youtubeAnalytics", "v2", credentials=self._credentials(),
            )
        return self._analytics_client
