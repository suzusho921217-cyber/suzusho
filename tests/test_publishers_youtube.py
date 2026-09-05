"""publishers.youtube: 実アダプタ（モックした googleapiclient 経由）。"""

from googleapiclient.errors import HttpError

from src.common.models import Brand, Platform, PolicyDecision, Post, PostStatus
from src.publishers.base import PublishRequest
from src.publishers.youtube import YouTubePublisher, _idempotency_tag


def _post(**over):
    base = {
        "post_key": "p1:youtube", "master_video_id": "p1", "brand": Brand.CAT,
        "platform": Platform.YOUTUBE, "account_id": "cat-youtube", "concept_tag": "違和感",
        "hook_type": "0.5秒異常", "character_id": "CAT_001", "duration_sec": 10,
        "oddity_level": 2, "prompt_version": "v1", "generation_cost_jpy": 0.0,
        "policy_version": "v1", "policy_result": PolicyDecision.PASS,
        "status": PostStatus.PUBLISHING,
    }
    base.update(over)
    return Post(**base)


def _req(post=None, *, video_path, **over):
    base = {"post": post or _post(), "video_path": video_path,
            "title": "t", "caption": "c", "tags": ["cat", "cute"]}
    base.update(over)
    return PublishRequest(**base)


class _FakeExecutable:
    def __init__(self, result=None, error=None):
        self._result, self._error = result, error

    def execute(self):
        if self._error:
            raise self._error
        return self._result


class _FakeVideosResource:
    def __init__(self, insert_result=None, insert_error=None, list_result=None):
        self.insert_calls = []
        self._insert_result, self._insert_error = insert_result, insert_error
        self._list_result = list_result or {"items": []}

    def insert(self, *, part, body, media_body):
        self.insert_calls.append({"part": part, "body": body, "media_body": media_body})
        return _FakeExecutable(self._insert_result, self._insert_error)

    def list(self, *, part, id):
        return _FakeExecutable(self._list_result)


class _FakeSearchResource:
    def __init__(self, result=None, error=None):
        self.calls = []
        self._result, self._error = result or {"items": []}, error

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeExecutable(self._result, self._error)


class _FakeYouTube:
    def __init__(self, videos=None, search=None):
        self._videos = videos or _FakeVideosResource()
        self._search = search or _FakeSearchResource()

    def videos(self):
        return self._videos

    def search(self):
        return self._search


class _FakeReportsResource:
    def __init__(self, result=None):
        self.calls = []
        self._result = result or {"columnHeaders": [], "rows": []}

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeExecutable(self._result)


class _FakeAnalytics:
    def __init__(self, result=None):
        self._reports = _FakeReportsResource(result)

    def reports(self):
        return self._reports


def _wire(monkeypatch, pub, *, youtube=None, analytics=None):
    if youtube is not None:
        monkeypatch.setattr(pub, "_youtube", lambda: youtube)
    if analytics is not None:
        monkeypatch.setattr(pub, "_analytics", lambda: analytics)


# --- publish -------------------------------------------------------------

def test_publish_uploads_with_idempotency_tag_and_disclosure(monkeypatch, tmp_path):
    video = tmp_path / "x.mp4"
    video.write_bytes(b"fake video bytes")
    videos = _FakeVideosResource(insert_result={"id": "yt-123"})
    yt = _FakeYouTube(videos=videos)
    pub = YouTubePublisher()
    _wire(monkeypatch, pub, youtube=yt)

    result = pub.publish(_req(video_path=str(video), ai_disclosure=True))

    assert result.ok is True
    assert result.platform_post_id == "yt-123"
    body = videos.insert_calls[0]["body"]
    assert _idempotency_tag(_post()) in body["snippet"]["tags"]
    assert body["status"]["containsSyntheticMedia"] is True


def test_publish_returns_failed_result_on_http_error(monkeypatch, tmp_path):
    video = tmp_path / "x.mp4"
    video.write_bytes(b"fake video bytes")
    err = HttpError(resp=type("R", (), {"status": 403, "reason": "Forbidden"})(), content=b"quota exceeded")
    videos = _FakeVideosResource(insert_error=err)
    pub = YouTubePublisher()
    _wire(monkeypatch, pub, youtube=_FakeYouTube(videos=videos))

    result = pub.publish(_req(video_path=str(video)))
    assert result.ok is False
    assert result.error


# --- find_existing ---------------------------------------------------------

def test_find_existing_returns_video_id_when_tag_matches(monkeypatch):
    search = _FakeSearchResource(result={"items": [{"id": {"videoId": "yt-999"}}]})
    pub = YouTubePublisher()
    _wire(monkeypatch, pub, youtube=_FakeYouTube(search=search))

    assert pub.find_existing(_post()) == "yt-999"
    assert search.calls[0]["q"] == _idempotency_tag(_post())
    assert search.calls[0]["forMine"] is True


def test_find_existing_returns_none_when_no_match(monkeypatch):
    pub = YouTubePublisher()
    _wire(monkeypatch, pub, youtube=_FakeYouTube())
    assert pub.find_existing(_post()) is None


def test_find_existing_returns_none_on_api_error(monkeypatch):
    err = HttpError(resp=type("R", (), {"status": 500, "reason": "Server Error"})(), content=b"boom")
    search = _FakeSearchResource(error=err)
    pub = YouTubePublisher()
    _wire(monkeypatch, pub, youtube=_FakeYouTube(search=search))
    assert pub.find_existing(_post()) is None


# --- fetch_metrics ---------------------------------------------------------

def test_fetch_metrics_merges_basic_stats_and_analytics(monkeypatch):
    videos = _FakeVideosResource(list_result={
        "items": [{"statistics": {"viewCount": "1000", "likeCount": "50", "commentCount": "3"}}],
    })
    analytics = _FakeAnalytics(result={
        "columnHeaders": [{"name": "engagedViews"}, {"name": "averageViewDuration"},
                          {"name": "shares"}, {"name": "subscribersGained"},
                          {"name": "estimatedRevenue"}],
        "rows": [[800, 6.5, 12, 4, 120.5]],
    })
    pub = YouTubePublisher()
    _wire(monkeypatch, pub, youtube=_FakeYouTube(videos=videos), analytics=analytics)

    metrics = pub.fetch_metrics("yt-123")
    assert metrics["views"] == 1000
    assert metrics["likes"] == 50
    assert metrics["comments"] == 3
    assert metrics["engagedViews"] == 800
    assert metrics["estimatedRevenue"] == 120.5


def test_fetch_metrics_degrades_to_basic_stats_when_analytics_fails(monkeypatch):
    videos = _FakeVideosResource(list_result={
        "items": [{"statistics": {"viewCount": "10"}}],
    })
    pub = YouTubePublisher()

    class _FailingReports:
        def query(self, **kwargs):
            return _FakeExecutable(
                error=HttpError(resp=type("R", (), {"status": 403, "reason": "Forbidden"})(), content=b"not monetized")
            )

    class _FailingAnalytics:
        def reports(self):
            return _FailingReports()

    _wire(monkeypatch, pub, youtube=_FakeYouTube(videos=videos), analytics=_FailingAnalytics())

    metrics = pub.fetch_metrics("yt-123")
    assert metrics == {"views": 10}
