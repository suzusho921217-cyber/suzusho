"""policy_sync（§8 媒体フィード監視）のテスト。"""

import json

import pytest

from src import cli
from src.policy.policy_sync import check_feeds, parse_feed

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>YouTube Data API - Revision History</title>
  <entry>
    <title>August 27, 2026</title>
    <id>tag:google.com,changelog:yt#2026-08-27</id>
    <updated>2026-08-27T00:00:00Z</updated>
    <link rel="alternate" href="https://developers.google.com/youtube/v3/revision_history"/>
    <content type="html">view counting change</content>
  </entry>
</feed>
"""

ATOM_WITH_NEW = ATOM.replace(
    "  <entry>",
    """  <entry>
    <title>August 30, 2026</title>
    <id>tag:google.com,changelog:yt#2026-08-30</id>
    <updated>2026-08-30T00:00:00Z</updated>
    <link rel="alternate" href="https://developers.google.com/youtube/v3/revision_history"/>
    <content type="html">new policy note</content>
  </entry>
  <entry>""",
    1,
)

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>X API changelog</title>
  <item>
    <title>Aug 13, 2026</title>
    <link>https://docs.x.com/changelog#aug-13-2026</link>
    <guid isPermaLink="false">a0de5295ade9a486</guid>
    <pubDate>Thu, 13 Aug 2026 21:00:20 GMT</pubDate>
  </item>
</channel></rss>
"""

FEEDS = [
    {"platform": "youtube", "name": "data-api", "url": "https://example.com/yt.xml"},
    {"platform": "x", "name": "api-changelog", "url": "https://example.com/x.xml"},
]


def _fetcher(mapping):
    def fetch(url):
        if url not in mapping:
            raise RuntimeError("404")
        return mapping[url]
    return fetch


def test_parse_atom():
    entries = parse_feed(ATOM)
    assert len(entries) == 1
    assert entries[0].entry_id == "tag:google.com,changelog:yt#2026-08-27"
    assert entries[0].title == "August 27, 2026"
    assert entries[0].link.endswith("revision_history")


def test_parse_rss():
    entries = parse_feed(RSS)
    assert len(entries) == 1
    assert entries[0].entry_id == "a0de5295ade9a486"
    assert entries[0].link == "https://docs.x.com/changelog#aug-13-2026"


def test_parse_unknown_format_raises():
    with pytest.raises(ValueError, match="未知のフィード形式"):
        parse_feed("<html><body>nope</body></html>")


def test_first_run_baselines_without_flagging():
    seen = {}
    report = check_feeds(
        FEEDS, seen, _fetcher({FEEDS[0]["url"]: ATOM, FEEDS[1]["url"]: RSS})
    )
    assert report.first_run is True
    assert report.has_changes is False
    assert seen[FEEDS[0]["url"]] == ["tag:google.com,changelog:yt#2026-08-27"]
    assert seen[FEEDS[1]["url"]] == ["a0de5295ade9a486"]


def test_second_run_no_change():
    seen = {}
    fetch = _fetcher({FEEDS[0]["url"]: ATOM, FEEDS[1]["url"]: RSS})
    check_feeds(FEEDS, seen, fetch)
    report = check_feeds(FEEDS, seen, fetch)
    assert report.first_run is False
    assert report.has_changes is False
    assert report.changed_platforms == set()


def test_new_entry_detected_and_platform_flagged():
    seen = {}
    check_feeds(FEEDS, seen, _fetcher({FEEDS[0]["url"]: ATOM, FEEDS[1]["url"]: RSS}))
    report = check_feeds(
        FEEDS, seen, _fetcher({FEEDS[0]["url"]: ATOM_WITH_NEW, FEEDS[1]["url"]: RSS})
    )
    assert report.has_changes is True
    assert report.changed_platforms == {"youtube"}
    yt = next(r for r in report.results if r.platform == "youtube")
    assert [e.title for e in yt.new_entries] == ["August 30, 2026"]


def test_fetch_error_recorded_not_raised():
    seen = {"x": []}  # 非空 → first_run=False
    report = check_feeds(FEEDS, seen, _fetcher({FEEDS[1]["url"]: RSS}))  # yt.xml 欠落
    assert report.has_errors is True
    bad = next(r for r in report.results if r.platform == "youtube")
    assert bad.ok is False
    assert "RuntimeError" in bad.error


def test_cli_policy_sync_first_run_then_change(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path)
    orig_load = cli.load
    monkeypatch.setattr(
        cli, "load", lambda name: {"feeds": FEEDS} if name == "policy_sync" else orig_load(name)
    )

    state = {"a": ATOM, "x": RSS}
    monkeypatch.setattr(cli, "_http_get", lambda url: state["a"] if "yt" in url else state["x"])

    assert cli.main(["policy-sync"]) == 0  # 初回ベースライン
    assert (tmp_path / "policy_sync_feeds.json").exists()

    state["a"] = ATOM_WITH_NEW
    assert cli.main(["policy-sync"]) == 2  # 新着 → 非0

    stale = json.loads((tmp_path / "policy_sync.json").read_text(encoding="utf-8"))
    assert stale["youtube"]["stale"] is True
    assert "新着" in capsys.readouterr().out
