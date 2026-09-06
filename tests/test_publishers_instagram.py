"""publishers.instagram: 実アダプタ（モックしたGraph API / GCS経由）。"""

import json

import pytest
import requests

from src.common.models import Brand, Platform, PolicyDecision, Post, PostStatus
from src.publishers.base import PublishRequest
from src.publishers.instagram import InstagramPublisher, _idempotency_marker


def _post(**over):
    base = {
        "post_key": "p1:instagram", "master_video_id": "p1", "brand": Brand.DOG,
        "platform": Platform.INSTAGRAM, "account_id": "dog-instagram", "concept_tag": "違和感",
        "hook_type": "0.5秒異常", "character_id": "DOG_001", "duration_sec": 10,
        "oddity_level": 2, "prompt_version": "v1", "generation_cost_jpy": 0.0,
        "policy_version": "v1", "policy_result": PolicyDecision.PASS,
        "status": PostStatus.PUBLISHING,
    }
    base.update(over)
    return Post(**base)


def _req(post=None, *, video_path="file.mp4", **over):
    base = {"post": post or _post(), "video_path": video_path,
            "title": "t", "caption": "c", "tags": []}
    base.update(over)
    return PublishRequest(**base)


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("IG_ACCESS_TOKEN_DOG", "dog-token")
    monkeypatch.setenv("IG_BUSINESS_ACCOUNT_ID_DOG", "1234567890")
    monkeypatch.setenv("GCS_BUCKET_NAME", "fake-bucket")


def _pub(monkeypatch, *, get=None, post=None, upload=None):
    pub = InstagramPublisher(brand=Brand.DOG)
    if upload is not None:
        monkeypatch.setattr(pub, "_upload_to_gcs", upload)
    else:
        monkeypatch.setattr(pub, "_upload_to_gcs", lambda path: "https://signed.example.com/x.mp4")
    if get is not None:
        monkeypatch.setattr(requests, "get", get)
    if post is not None:
        monkeypatch.setattr(requests, "post", post)
    return pub


# --- publish -------------------------------------------------------------

def test_publish_creates_container_polls_then_publishes(monkeypatch):
    calls = {"post": [], "get": []}

    def fake_post(url, data, timeout):
        calls["post"].append((url, data))
        if url.endswith("/media"):
            return _FakeResponse({"id": "container-1"})
        if url.endswith("/media_publish"):
            return _FakeResponse({"id": "media-1"})
        raise AssertionError(url)

    statuses = iter(["IN_PROGRESS", "FINISHED"])

    def fake_get(url, params, timeout):
        calls["get"].append((url, params))
        return _FakeResponse({"status_code": next(statuses)})

    pub = _pub(monkeypatch, get=fake_get, post=fake_post)
    monkeypatch.setenv("INSTAGRAM_POLL_INTERVAL_SEC", "0")

    result = pub.publish(_req(ai_disclosure=True))

    assert result.ok is True
    assert result.platform_post_id == "media-1"
    media_call = next(c for c in calls["post"] if c[0].endswith("/media"))
    assert media_call[1]["video_url"] == "https://signed.example.com/x.mp4"
    assert "AI" in media_call[1]["caption"]
    # 冪等キーの目印は完全不可視（ゼロ幅文字の並び）。生の "pk:" テキストは入れない。
    cap = media_call[1]["caption"]
    assert "pk:" not in cap
    assert _idempotency_marker(_post()) in cap
    assert all(ord(c) in (0x200b, 0x200c) for c in _idempotency_marker(_post()))
    assert len(calls["get"]) == 2  # IN_PROGRESS -> FINISHED


def test_publish_returns_failed_result_when_processing_errors(monkeypatch):
    def fake_post(url, data, timeout):
        return _FakeResponse({"id": "container-1"})

    def fake_get(url, params, timeout):
        return _FakeResponse({"status_code": "ERROR"})

    pub = _pub(monkeypatch, get=fake_get, post=fake_post)
    monkeypatch.setenv("INSTAGRAM_POLL_INTERVAL_SEC", "0")

    result = pub.publish(_req())
    assert result.ok is False
    assert result.error


def test_publish_returns_failed_result_when_upload_fails(monkeypatch):
    def failing_upload(path):
        raise RuntimeError("bucket not found")

    pub = _pub(monkeypatch, upload=failing_upload)
    result = pub.publish(_req())
    assert result.ok is False
    assert "一時公開" in result.error


def test_publish_returns_failed_result_on_graph_error(monkeypatch):
    def fake_post(url, data, timeout):
        return _FakeResponse({"error": {"message": "Invalid OAuth access token"}}, status=400)

    pub = _pub(monkeypatch, post=fake_post)
    result = pub.publish(_req())
    assert result.ok is False
    assert "Invalid OAuth" in result.error


# --- find_existing ---------------------------------------------------------

def test_find_existing_matches_marker_in_caption(monkeypatch):
    marker = _idempotency_marker(_post())

    def fake_get(url, params, timeout):
        return _FakeResponse({"data": [
            {"id": "other", "caption": "no match"},
            {"id": "media-42", "caption": f"hello{marker}"},
        ]})

    pub = _pub(monkeypatch, get=fake_get)
    assert pub.find_existing(_post()) == "media-42"


def test_find_existing_returns_none_without_match(monkeypatch):
    def fake_get(url, params, timeout):
        return _FakeResponse({"data": []})

    pub = _pub(monkeypatch, get=fake_get)
    assert pub.find_existing(_post()) is None


def test_find_existing_returns_none_on_api_error(monkeypatch):
    def fake_get(url, params, timeout):
        raise requests.ConnectionError("network down")

    pub = _pub(monkeypatch, get=fake_get)
    assert pub.find_existing(_post()) is None


# --- fetch_metrics ---------------------------------------------------------

def test_fetch_metrics_parses_insights(monkeypatch):
    def fake_get(url, params, timeout):
        return _FakeResponse({"data": [
            {"name": "likes", "values": [{"value": 12}]},
            {"name": "reach", "values": [{"value": 300}]},
        ]})

    pub = _pub(monkeypatch, get=fake_get)
    metrics = pub.fetch_metrics("media-1")
    assert metrics == {"likes": 12, "reach": 300}


def test_fetch_metrics_converts_avg_watch_time_from_ms_to_sec(monkeypatch):
    def fake_get(url, params, timeout):
        return _FakeResponse({"data": [
            {"name": "ig_reels_avg_watch_time", "values": [{"value": 31775}]},
        ]})

    pub = _pub(monkeypatch, get=fake_get)
    metrics = pub.fetch_metrics("media-1")
    assert metrics == {"avg_watch_sec": 31.775}


def test_fetch_account_followers_returns_count(monkeypatch):
    def fake_get(url, params, timeout):
        assert params["fields"] == "followers_count"
        return _FakeResponse({"followers_count": 42})

    pub = _pub(monkeypatch, get=fake_get)
    assert pub.fetch_account_followers() == 42


def test_fetch_account_followers_returns_none_on_error(monkeypatch):
    def fake_get(url, params, timeout):
        raise requests.ConnectionError("boom")

    pub = _pub(monkeypatch, get=fake_get)
    assert pub.fetch_account_followers() is None


def test_fetch_metrics_returns_empty_on_error(monkeypatch):
    def fake_get(url, params, timeout):
        raise requests.ConnectionError("boom")

    pub = _pub(monkeypatch, get=fake_get)
    assert pub.fetch_metrics("media-1") == {}
