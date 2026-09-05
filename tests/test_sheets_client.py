"""sheets.client: LocalStore の行⇔モデル往復と 3DB の読み書き（§10）。"""

from datetime import datetime, timedelta, timezone

import pytest

from src.common.models import (
    AccountDaily,
    Brand,
    PerformanceSnapshot,
    Platform,
    PolicyDecision,
    Post,
    PostStatus,
)
from src.sheets.client import (
    _ACCOUNT_HEADERS_JA as ACCOUNT_HEADERS_JA,
)
from src.sheets.client import (
    _POST_HEADERS_JA as POST_HEADERS_JA,
)
from src.sheets.client import (
    _SNAPSHOT_HEADERS_JA as SNAPSHOT_HEADERS_JA,
)
from src.sheets.client import (
    LocalStore,
    SheetsStore,
    get_store,
)

JST = timezone(timedelta(hours=9))


def _post(key="p1:youtube", **over):
    base = {
        "post_key": key, "master_video_id": "p1", "brand": Brand.CAT,
        "platform": Platform.YOUTUBE, "account_id": "cat-youtube", "concept_tag": "違和感",
        "hook_type": "0.5秒異常", "character_id": "CAT_001", "duration_sec": 10,
        "oddity_level": 2, "prompt_version": "v1", "generation_cost_jpy": 120.0,
        "policy_version": "yt-2026-08", "policy_result": PolicyDecision.PASS,
        "status": PostStatus.PUBLISHED,
        "published_at": datetime(2026, 9, 1, 12, 0, tzinfo=JST),
        "platform_post_id": "yt-abc",
    }
    base.update(over)
    return Post(**base)


def test_post_roundtrip(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_post(_post())
    got = store.get_post("p1:youtube")
    assert got == _post()  # dataclass 等価: 全フィールド一致


def test_upsert_post_replaces_by_key(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_post(_post(status=PostStatus.PUBLISHING, platform_post_id=None))
    store.upsert_post(_post(status=PostStatus.PUBLISHED, platform_post_id="yt-abc"))
    assert len(store.list_posts()) == 1
    assert store.get_post("p1:youtube").status is PostStatus.PUBLISHED


def test_list_posts_since_filter(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_post(_post("old", published_at=datetime(2026, 8, 1, tzinfo=JST)))
    store.upsert_post(_post("new", published_at=datetime(2026, 9, 1, tzinfo=JST)))
    keys = [p.post_key for p in store.list_posts(since="2026-08-15")]
    assert keys == ["new"]


def test_get_missing_post_returns_none(tmp_path):
    assert LocalStore(tmp_path).get_post("nope") is None


def test_snapshot_append_and_list(tmp_path):
    store = LocalStore(tmp_path)
    s1 = PerformanceSnapshot(post_key="p1:youtube", snapshot="24h",
                             collected_at=datetime(2026, 9, 2, tzinfo=JST), views=1000, shares=30)
    s2 = PerformanceSnapshot(post_key="p2:youtube", snapshot="24h",
                             collected_at=datetime(2026, 9, 2, tzinfo=JST), views=None)
    store.append_snapshot(s1)
    store.append_snapshot(s2)
    assert len(store.list_snapshots()) == 2
    only = store.list_snapshots(post_key="p1:youtube")
    assert len(only) == 1 and only[0].views == 1000 and only[0].shares == 30


def test_upsert_snapshot_replaces_same_post_and_label(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_snapshot(PerformanceSnapshot(
        post_key="p1:youtube", snapshot="latest",
        collected_at=datetime(2026, 9, 2, tzinfo=JST), views=10,
    ))
    store.upsert_snapshot(PerformanceSnapshot(
        post_key="p1:youtube", snapshot="latest",
        collected_at=datetime(2026, 9, 3, tzinfo=JST), views=20,
    ))
    snaps = store.list_snapshots(post_key="p1:youtube")
    assert len(snaps) == 1 and snaps[0].views == 20  # 上書きされ、履歴は残らない


def test_upsert_snapshot_computes_views_delta(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_snapshot(PerformanceSnapshot(
        post_key="p1:youtube", snapshot="latest",
        collected_at=datetime(2026, 9, 2, tzinfo=JST), views=100,
    ))
    store.upsert_snapshot(PerformanceSnapshot(
        post_key="p1:youtube", snapshot="latest",
        collected_at=datetime(2026, 9, 3, tzinfo=JST), views=140,
    ))
    snap = store.list_snapshots(post_key="p1:youtube")[0]
    assert snap.views == 140 and snap.views_delta == 40


def test_upsert_snapshot_leaves_views_delta_none_on_first_record(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_snapshot(PerformanceSnapshot(
        post_key="p1:youtube", snapshot="latest",
        collected_at=datetime(2026, 9, 2, tzinfo=JST), views=100,
    ))
    snap = store.list_snapshots(post_key="p1:youtube")[0]
    assert snap.views_delta is None  # 比較対象の前回値が無い


def test_upsert_snapshot_keeps_other_labels_separate(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_snapshot(PerformanceSnapshot(
        post_key="p1:youtube", snapshot="24h",
        collected_at=datetime(2026, 9, 2, tzinfo=JST), views=10,
    ))
    store.upsert_snapshot(PerformanceSnapshot(
        post_key="p1:youtube", snapshot="latest",
        collected_at=datetime(2026, 9, 2, tzinfo=JST), views=20,
    ))
    assert len(store.list_snapshots(post_key="p1:youtube")) == 2


def test_account_daily_upsert_by_composite_key(tmp_path):
    store = LocalStore(tmp_path)
    row = AccountDaily(date="2026-09-02", brand=Brand.CAT, platform=Platform.YOUTUBE,
                       account_id="cat-youtube", followers=100, daily_views=5000)
    store.upsert_account_daily(row)
    store.upsert_account_daily(AccountDaily(date="2026-09-02", brand=Brand.CAT,
                                            platform=Platform.YOUTUBE, account_id="cat-youtube",
                                            followers=120))
    rows = store.list_account_daily()
    assert len(rows) == 1 and rows[0].followers == 120


def test_get_store_backend_selection(tmp_path):
    assert isinstance(get_store(backend="local", root=tmp_path), LocalStore)
    with pytest.raises(ValueError):
        get_store(backend="postgres")


def test_get_store_sheets_backend_needs_config():
    store = get_store(backend="sheets")
    assert isinstance(store, SheetsStore)
    with pytest.raises(RuntimeError):
        store.list_posts()  # SHEETS_SPREADSHEET_ID / 鍵ファイル未設定


# --- SheetsStore（Fake Sheets API）--------------------------------------

class _FakeRequest:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return self._data


class _FakeValues:
    """tabs: dict[タブ名, list[list[str]]]（0行目が見出し）を直接いじる簡易フェイク。"""

    def __init__(self, tabs):
        self.tabs = tabs

    def get(self, spreadsheetId, range):
        tab = range.split("!")[0]
        return _FakeRequest({"values": [list(r) for r in self.tabs.get(tab, [])]})

    def update(self, spreadsheetId, range, valueInputOption, body):
        tab, cell = range.split("!")
        row_num = int(cell[1:])  # "A5" -> 5 (1始まり)
        rows = self.tabs.setdefault(tab, [])
        while len(rows) < row_num:
            rows.append([])
        rows[row_num - 1] = list(body["values"][0])
        return _FakeRequest({})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        tab = range.split("!")[0]
        self.tabs.setdefault(tab, []).append(list(body["values"][0]))
        return _FakeRequest({})


class _FakeSpreadsheets:
    def __init__(self, tabs):
        self._values = _FakeValues(tabs)

    def values(self):
        return self._values


class FakeSheetsService:
    def __init__(self, tabs):
        self.tabs = tabs

    def spreadsheets(self):
        return _FakeSpreadsheets(self.tabs)


def _fake_store(tabs=None):
    store = SheetsStore(spreadsheet_id="fake", credentials_file="fake.json")
    store._service = FakeSheetsService(tabs if tabs is not None else {})
    return store


_POST_HEADER_JA = list(POST_HEADERS_JA.values())
_SNAPSHOT_HEADER_JA = list(SNAPSHOT_HEADERS_JA.values())
_ACCOUNT_HEADER_JA = list(ACCOUNT_HEADERS_JA.values())


def test_sheets_header_row_found_even_with_summary_block_above():
    # ユーザーがシート上部に集計欄を挿入しても、見出し行(投稿キー列を含む行)を
    # 動的に探して壊れないことを確認する。
    tabs = {"投稿DB": [
        ["ブランド", "媒体", "集計"],
        ["猫", "YouTube", "1"],
        [],
        _POST_HEADER_JA,
    ]}
    store = _fake_store(tabs)
    store.upsert_post(_post())
    got = store.get_post("p1:youtube")
    assert got == _post()
    # 集計欄(1〜3行目)はそのまま残っている
    assert tabs["投稿DB"][0] == ["ブランド", "媒体", "集計"]


def test_sheets_upsert_post_appends_when_missing():
    tabs = {"投稿DB": [_POST_HEADER_JA]}
    store = _fake_store(tabs)
    store.upsert_post(_post())
    assert len(tabs["投稿DB"]) == 2
    got = store.get_post("p1:youtube")
    assert got == _post()


def test_sheets_cell_values_are_japanese():
    tabs = {"投稿DB": [_POST_HEADER_JA]}
    store = _fake_store(tabs)
    store.upsert_post(_post())
    written = dict(zip(_POST_HEADER_JA, tabs["投稿DB"][1]))
    assert written["ブランド"] == "猫"
    assert written["媒体"] == "YouTube"
    assert written["ポリシー判定"] == "合格"
    assert written["ステータス"] == "投稿済み"
    # 読み込み側もコード内部では英語の Enum に戻る
    assert store.get_post("p1:youtube") == _post()


def test_sheets_units_are_embedded_in_the_data_not_the_header():
    tabs = {"投稿DB": [_POST_HEADER_JA]}
    store = _fake_store(tabs)
    store.upsert_post(_post(duration_sec=10, generation_cost_jpy=120.0))
    written = dict(zip(_POST_HEADER_JA, tabs["投稿DB"][1]))
    assert written["尺"] == "10秒"
    assert written["生成費"] == "120円"
    # ヘッダー自体には単位を付けない
    assert "尺(秒)" not in _POST_HEADER_JA and "尺" in _POST_HEADER_JA
    # 読み込み側は単位を剥がして数値に戻る
    got = store.get_post("p1:youtube")
    assert got.duration_sec == 10 and got.generation_cost_jpy == 120.0


def test_sheets_generation_cost_rounded_to_whole_yen():
    tabs = {"投稿DB": [_POST_HEADER_JA]}
    store = _fake_store(tabs)
    store.upsert_post(_post(generation_cost_jpy=237.11))
    written = dict(zip(_POST_HEADER_JA, tabs["投稿DB"][1]))
    assert written["生成費"] == "237円"


def test_sheets_snapshot_units_roundtrip():
    tabs = {"パフォーマンスDB": [_SNAPSHOT_HEADER_JA]}
    store = _fake_store(tabs)
    store.append_snapshot(PerformanceSnapshot(
        post_key="p1:youtube", snapshot="24h",
        collected_at=datetime(2026, 9, 2, tzinfo=JST), views=1000, shares=30,
    ))
    written = dict(zip(_SNAPSHOT_HEADER_JA, tabs["パフォーマンスDB"][1]))
    assert written["再生数"] == "1,000回"  # 桁区切りカンマ
    assert written["シェア数"] == "30回"
    snap = store.list_snapshots()[0]
    assert snap.views == 1000 and snap.shares == 30


def test_sheets_datetime_displayed_as_short_ja_format():
    tabs = {"投稿DB": [_POST_HEADER_JA]}
    store = _fake_store(tabs)
    store.upsert_post(_post(published_at=datetime(2026, 8, 31, 12, 0, tzinfo=JST)))
    written = dict(zip(_POST_HEADER_JA, tabs["投稿DB"][1]))
    assert written["投稿日時"] == "26/08/31 12:00"
    # 読み込み側は元の datetime に戻る
    assert store.get_post("p1:youtube").published_at == datetime(2026, 8, 31, 12, 0, tzinfo=JST)


def test_sheets_datetime_normalizes_non_jst_timezone_to_jst():
    # 実際に発生したバグ: JST等UTC以外のtzで保存すると、時刻の数字がそのまま
    # 別タイムゾーン扱いで読み戻され、実時刻からズレていた。表示はJSTに統一する
    # （シートを読むのは日本のユーザーなので）。
    tabs = {"投稿DB": [_POST_HEADER_JA]}
    store = _fake_store(tabs)
    utc = timezone.utc
    store.upsert_post(_post(published_at=datetime(2026, 8, 31, 3, 0, tzinfo=utc)))
    written = dict(zip(_POST_HEADER_JA, tabs["投稿DB"][1]))
    assert written["投稿日時"] == "26/08/31 12:00"  # 03:00 UTC = 12:00 JST
    got = store.get_post("p1:youtube").published_at
    assert got == datetime(2026, 8, 31, 3, 0, tzinfo=utc)  # 同じ瞬間として一致


def test_sheets_upsert_post_updates_existing_row_in_place():
    tabs = {"投稿DB": [_POST_HEADER_JA]}
    store = _fake_store(tabs)
    store.upsert_post(_post(status=PostStatus.PUBLISHING, platform_post_id=None))
    store.upsert_post(_post(status=PostStatus.PUBLISHED, platform_post_id="yt-abc"))
    assert len(tabs["投稿DB"]) == 2  # 追記されず上書き
    assert store.get_post("p1:youtube").status is PostStatus.PUBLISHED


def test_sheets_reads_are_robust_to_column_reorder():
    shuffled = list(reversed(_POST_HEADER_JA))
    tabs = {"投稿DB": [shuffled]}
    store = _fake_store(tabs)
    store.upsert_post(_post())
    assert store.get_post("p1:youtube") == _post()


def test_sheets_snapshot_append_and_filter():
    tabs = {"パフォーマンスDB": [_SNAPSHOT_HEADER_JA]}
    store = _fake_store(tabs)
    store.append_snapshot(PerformanceSnapshot(
        post_key="p1:youtube", snapshot="24h",
        collected_at=datetime(2026, 9, 2, tzinfo=JST), views=1000, shares=30,
    ))
    store.append_snapshot(PerformanceSnapshot(
        post_key="p2:youtube", snapshot="24h",
        collected_at=datetime(2026, 9, 2, tzinfo=JST), views=None,
    ))
    assert len(store.list_snapshots()) == 2
    only = store.list_snapshots(post_key="p1:youtube")
    assert len(only) == 1 and only[0].views == 1000 and only[0].shares == 30


def test_sheets_upsert_snapshot_replaces_same_post_and_label():
    tabs = {"パフォーマンスDB": [_SNAPSHOT_HEADER_JA]}
    store = _fake_store(tabs)
    store.upsert_snapshot(PerformanceSnapshot(
        post_key="p1:youtube", snapshot="latest",
        collected_at=datetime(2026, 9, 2, tzinfo=JST), views=10,
    ))
    store.upsert_snapshot(PerformanceSnapshot(
        post_key="p1:youtube", snapshot="latest",
        collected_at=datetime(2026, 9, 3, tzinfo=JST), views=20,
    ))
    assert len(tabs["パフォーマンスDB"]) == 2  # 見出し + データ1行のみ（追記されない）
    snaps = store.list_snapshots(post_key="p1:youtube")
    assert len(snaps) == 1 and snaps[0].views == 20
    assert snaps[0].views_delta == 10  # 20 - 10


def test_sheets_avg_watch_sec_rounded_to_one_decimal():
    tabs = {"パフォーマンスDB": [_SNAPSHOT_HEADER_JA]}
    store = _fake_store(tabs)
    store.upsert_snapshot(PerformanceSnapshot(
        post_key="p1:instagram", snapshot="latest",
        collected_at=datetime(2026, 9, 2, tzinfo=JST), views=10, avg_watch_sec=14.841,
    ))
    written = dict(zip(_SNAPSHOT_HEADER_JA, tabs["パフォーマンスDB"][1]))
    assert written["平均視聴秒数"] == "14.8秒"


def test_sheets_instagram_engaged_views_shown_as_na_marker():
    tabs = {"パフォーマンスDB": [_SNAPSHOT_HEADER_JA]}
    store = _fake_store(tabs)
    store.upsert_snapshot(PerformanceSnapshot(
        post_key="p1:instagram", snapshot="latest",
        collected_at=datetime(2026, 9, 2, tzinfo=JST), views=10, engaged_views=None,
    ))
    written = dict(zip(_SNAPSHOT_HEADER_JA, tabs["パフォーマンスDB"][1]))
    assert written["eng視聴数"] == "-"  # Instagramには概念自体が無いことを明示
    # 読み込み側ではコード内部は None に戻る(文字列"-"のまま出てこない)
    assert store.list_snapshots(post_key="p1:instagram")[0].engaged_views is None


def test_sheets_youtube_engaged_views_stays_blank_when_missing():
    tabs = {"パフォーマンスDB": [_SNAPSHOT_HEADER_JA]}
    store = _fake_store(tabs)
    store.upsert_snapshot(PerformanceSnapshot(
        post_key="p1:youtube", snapshot="latest",
        collected_at=datetime(2026, 9, 2, tzinfo=JST), views=10, engaged_views=None,
    ))
    written = dict(zip(_SNAPSHOT_HEADER_JA, tabs["パフォーマンスDB"][1]))
    # YouTubeでは「今回たまたま取れなかった」可能性があるので "-" にはしない(空欄のまま)
    assert written["eng視聴数"] == ""


def test_sheets_impressions_and_revenue_shown_as_na_for_both_platforms():
    tabs = {"パフォーマンスDB": [_SNAPSHOT_HEADER_JA]}
    store = _fake_store(tabs)
    for platform in ("instagram", "youtube"):
        store.upsert_snapshot(PerformanceSnapshot(
            post_key=f"p1:{platform}", snapshot="latest",
            collected_at=datetime(2026, 9, 2, tzinfo=JST),
            views=10, impressions=None, revenue_jpy=None,
        ))
    for platform in ("instagram", "youtube"):
        written = dict(zip(_SNAPSHOT_HEADER_JA, next(
            row for row in tabs["パフォーマンスDB"][1:]
            if row[0] == f"p1:{platform}"
        )))
        assert written["IMP数"] == "-"
        assert written["収益"] == "-"


def test_sheets_revenue_switches_to_real_value_once_available():
    tabs = {"パフォーマンスDB": [_SNAPSHOT_HEADER_JA]}
    store = _fake_store(tabs)
    store.upsert_snapshot(PerformanceSnapshot(
        post_key="p1:youtube", snapshot="latest",
        collected_at=datetime(2026, 9, 2, tzinfo=JST), views=10, revenue_jpy=500.0,
    ))
    written = dict(zip(_SNAPSHOT_HEADER_JA, tabs["パフォーマンスDB"][1]))
    assert written["収益"] == "500円"  # None でない限り "-" にはならない


def test_sheets_account_daily_upsert_by_composite_key():
    tabs = {"アカウント日次DB": [_ACCOUNT_HEADER_JA]}
    store = _fake_store(tabs)
    store.upsert_account_daily(AccountDaily(
        date="2026-09-02", brand=Brand.CAT, platform=Platform.YOUTUBE,
        account_id="cat-youtube", followers=100, daily_views=5000,
    ))
    store.upsert_account_daily(AccountDaily(
        date="2026-09-02", brand=Brand.CAT, platform=Platform.YOUTUBE,
        account_id="cat-youtube", followers=120,
    ))
    rows = store.list_account_daily()
    assert len(rows) == 1 and rows[0].followers == 120
