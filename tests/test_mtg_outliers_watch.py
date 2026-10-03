"""定点観測チャンネル: @ハンドルと UC…ID の新着を拾い、取得失敗のチャンネルは飛ばす。"""

from src.mtg import outliers


def test_watch_channel_video_ids(monkeypatch):
    calls = []

    def fake_get(path, key, **params):
        calls.append((path, params))
        if path == "channels":
            if params.get("forHandle") == "@broken":
                raise RuntimeError("boom")
            return {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}
        return {"items": [
            {"contentDetails": {"videoId": "new1", "videoPublishedAt": "2026-10-01T00:00:00Z"}},
            {"contentDetails": {"videoId": "old1", "videoPublishedAt": "2026-01-01T00:00:00Z"}},
        ]}

    monkeypatch.setattr(outliers, "_get", fake_get)
    ids = outliers._watch_channel_video_ids("k", ["@good", "@broken", "UCxyz"], "2026-07-01T00:00:00Z")
    assert ids == ["new1", "new1"]
    assert ("channels", {"part": "contentDetails", "forHandle": "@good"}) in calls
    assert ("channels", {"part": "contentDetails", "id": "UCxyz"}) in calls
