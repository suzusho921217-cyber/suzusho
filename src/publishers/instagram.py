"""Instagram Reels publisher（§9 §19）。

API: Instagram Graph API（Instagram Login方式）の /media + /media_publish。
Secrets(env):
  META_APP_ID / META_APP_SECRET … Meta Developerアプリ（現状は手動発行のトークンのみ使用）
  IG_ACCESS_TOKEN_<BRAND> / IG_BUSINESS_ACCOUNT_ID_<BRAND> … ブランド別（例: _CAT / _DOG）
  未設定時は IG_ACCESS_TOKEN / IG_BUSINESS_ACCOUNT_ID にフォールバック（単一アカウント向け）

動画公開の制約: Instagram APIは動画ファイルを直接アップロードできず、
「一般公開されたHTTPS URL」から取得する方式（video_url）。ローカル生成物は
Google Cloud Storageの非公開バケットへ一時アップロードし、期限付き署名URL
（v4, 既定1時間）経由で取得させる（バケット自体は非公開のまま §8 最小権限）。
Secrets(env): GCS_BUCKET_NAME / GOOGLE_CLOUD_PROJECT。認証は sheets と同じ
サービスアカウント（GOOGLE_SHEETS_CREDENTIALS_FILE / GOOGLE_SERVICE_ACCOUNT_JSON）。

冪等性（§15）: Instagramには非公開タグ相当の入れ物が無いため、キャプション末尾に
ゼロ幅スペースで挟んだ post_key を埋め込み（視聴者には見えない）、自分の最近の
投稿一覧（/media）を検索して照会する。

AI開示: Instagram Graph API に自己申告フィールドが無いため、YouTube同様
キャプション本文に明記する。
"""

from __future__ import annotations

import time
import uuid

import requests

from src.common.config import env
from src.common.models import Brand, Platform, Post
from src.publishers.base import Publisher, PublishRequest, PublishResult

_GRAPH = "https://graph.instagram.com/v21.0"
_AI_DISCLOSURE_NOTE = "※この動画にはAIで生成・加工された合成コンテンツが含まれます。"
_ZWS = "\u200b"  # ゼロ幅スペース。視聴者には見えないがテキストとしては存在する


def _idempotency_marker(post: Post) -> str:
    return f"{_ZWS}{_ZWS}pk:{post.post_key}{_ZWS}"


class InstagramPublisher(Publisher):
    platform = Platform.INSTAGRAM

    def __init__(self, brand: Brand | None = None) -> None:
        super().__init__(brand)
        self._storage_client = None

    # --- Publisher ---------------------------------------------------------

    def publish(self, req: PublishRequest) -> PublishResult:
        try:
            video_url = self._upload_to_gcs(req.video_path)
        except Exception as e:  # noqa: BLE001 - GCS/ファイルI/O由来の例外を汎用に失敗扱いする
            return PublishResult(ok=False, error=f"動画の一時公開に失敗: {e}")

        caption = req.caption
        if req.ai_disclosure:
            caption += f"\n\n{_AI_DISCLOSURE_NOTE}"
        caption += _idempotency_marker(req.post)

        try:
            container_id = self._create_container(video_url, caption)
            self._wait_until_finished(container_id)
            media_id = self._publish_container(container_id)
        except (requests.RequestException, _GraphAPIError) as e:
            return PublishResult(ok=False, error=str(e))
        return PublishResult(ok=True, platform_post_id=media_id)

    def find_existing(self, post: Post) -> str | None:
        marker = f"pk:{post.post_key}"
        try:
            resp = self._get(
                f"{self._ig_user_id()}/media",
                params={"fields": "id,caption", "limit": 50},
            )
        except (requests.RequestException, _GraphAPIError):
            return None  # 照会に失敗しても投稿自体は続行させる（新規投稿を試みる）
        for item in resp.get("data", []):
            if marker in (item.get("caption") or ""):
                return item["id"]
        return None

    def fetch_metrics(self, platform_post_id: str) -> dict:
        metrics: dict = {}
        try:
            # ★2026-09-05 実API確認: "plays" は廃止済みで "views" が正。
            resp = self._get(
                f"{platform_post_id}/insights",
                params={"metric": "likes,comments,shares,saved,reach,views"},
            )
            for row in resp.get("data", []):
                name = row.get("name")
                values = row.get("values") or []
                if name and values:
                    metrics[name] = values[0].get("value")
        except (requests.RequestException, _GraphAPIError) as e:
            print(f"[instagram] insights取得失敗: {e}")
        return metrics

    # --- 内部: Instagram Graph API ------------------------------------------

    def _access_token(self) -> str | None:
        if self.brand is not None:
            per_brand = env(f"IG_ACCESS_TOKEN_{self.brand.value.upper()}")
            if per_brand:
                return per_brand
        return env("IG_ACCESS_TOKEN")

    def _ig_user_id(self) -> str:
        if self.brand is not None:
            per_brand = env(f"IG_BUSINESS_ACCOUNT_ID_{self.brand.value.upper()}")
            if per_brand:
                return per_brand
        return env("IG_BUSINESS_ACCOUNT_ID") or ""

    def _get(self, path: str, *, params: dict) -> dict:
        params = {**params, "access_token": self._access_token()}
        resp = requests.get(f"{_GRAPH}/{path}", params=params, timeout=30)
        return _raise_for_graph_error(resp)

    def _post(self, path: str, *, data: dict) -> dict:
        data = {**data, "access_token": self._access_token()}
        resp = requests.post(f"{_GRAPH}/{path}", data=data, timeout=30)
        return _raise_for_graph_error(resp)

    def _create_container(self, video_url: str, caption: str) -> str:
        resp = self._post(
            f"{self._ig_user_id()}/media",
            data={"media_type": "REELS", "video_url": video_url, "caption": caption},
        )
        return resp["id"]

    def _wait_until_finished(self, container_id: str, *, timeout_sec: int = 300) -> None:
        interval = float(env("INSTAGRAM_POLL_INTERVAL_SEC", "10") or 10)
        deadline = time.monotonic() + timeout_sec
        while True:
            resp = self._get(container_id, params={"fields": "status_code"})
            status = resp.get("status_code")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise _GraphAPIError(f"動画処理に失敗（container={container_id}）")
            if time.monotonic() > deadline:
                raise _GraphAPIError(f"動画処理がタイムアウト（container={container_id}）")
            time.sleep(interval)

    def _publish_container(self, container_id: str) -> str:
        resp = self._post(
            f"{self._ig_user_id()}/media_publish", data={"creation_id": container_id},
        )
        return resp["id"]

    # --- 内部: GCS 経由の一時公開URL -----------------------------------------

    def _storage(self):
        if self._storage_client is None:
            from google.cloud import storage
            from google.oauth2 import service_account

            creds_json = env("GOOGLE_SERVICE_ACCOUNT_JSON")
            creds_file = env("GOOGLE_SHEETS_CREDENTIALS_FILE")
            if creds_json:
                import json as _json

                creds = service_account.Credentials.from_service_account_info(_json.loads(creds_json))
            elif creds_file:
                creds = service_account.Credentials.from_service_account_file(creds_file)
            else:
                raise RuntimeError(
                    "GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SHEETS_CREDENTIALS_FILE がどちらも未設定"
                )
            self._storage_client = storage.Client(
                credentials=creds, project=env("GOOGLE_CLOUD_PROJECT"),
            )
        return self._storage_client

    def _upload_to_gcs(self, local_path: str) -> str:
        from datetime import timedelta

        bucket_name = env("GCS_BUCKET_NAME")
        if not bucket_name:
            raise RuntimeError("GCS_BUCKET_NAME が未設定")
        bucket = self._storage().bucket(bucket_name)
        name = f"{uuid.uuid4().hex}.mp4"
        blob = bucket.blob(name)
        blob.upload_from_filename(local_path, content_type="video/mp4")
        expire_min = int(env("GCS_SIGNED_URL_EXPIRE_MIN", "60") or 60)
        return blob.generate_signed_url(
            version="v4", expiration=timedelta(minutes=expire_min), method="GET",
        )


class _GraphAPIError(RuntimeError):
    pass


def _raise_for_graph_error(resp: requests.Response) -> dict:
    data = resp.json() if resp.content else {}
    if "error" in data:
        raise _GraphAPIError(data["error"].get("message", str(data["error"])))
    resp.raise_for_status()
    return data
