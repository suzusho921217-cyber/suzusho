"""管理DB の窓口（§10）。

3つのDB: 投稿DB(§10.1) / パフォーマンスDB(§10.2) / アカウント日次DB(§10.3)。
リポジトリ唯一の永続化窓口。ここを通しておけば後で保存先を差し替えられる。

バックエンド（env ``SHEETS_BACKEND``）:
  - ``local``（既定）… ``.state/db/*.json``。認証情報・アカウント不要。CI とローカルはこれ。
  - ``sheets``        … Google Sheets（未実装。スプレッドシート3つ作成後に対応）。

冪等・障害対応（§15）:
  - upsert_post は post_key で更新/挿入
  - Sheets 書込が失敗したら呼び出し側が状態ファイル/Artifact へ退避
"""

from __future__ import annotations

import abc
import json
from datetime import datetime, timezone
from pathlib import Path

from src.common.config import env
from src.common.models import (
    AccountDaily,
    Brand,
    PerformanceSnapshot,
    Platform,
    PolicyDecision,
    Post,
    PostStatus,
)

_STATE_DIR = Path(__file__).resolve().parents[2] / ".state"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_SNAPSHOT_NUMERIC = (
    "views", "engaged_views", "likes", "comments", "shares", "impressions",
    "avg_watch_sec", "completion_rate", "followers_before", "followers_after",
    "revenue_jpy",
)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# --- 行 <-> モデル -----------------------------------------------------------

def post_to_row(p: Post) -> dict:
    return {
        "post_key": p.post_key, "master_video_id": p.master_video_id,
        "brand": p.brand.value, "platform": p.platform.value, "account_id": p.account_id,
        "concept_tag": p.concept_tag, "hook_type": p.hook_type,
        "character_id": p.character_id, "duration_sec": p.duration_sec,
        "oddity_level": p.oddity_level, "reality_level": p.reality_level,
        "prompt_version": p.prompt_version,
        "generation_cost_jpy": p.generation_cost_jpy, "policy_version": p.policy_version,
        "policy_result": p.policy_result.value, "status": p.status.value,
        "published_at": p.published_at.isoformat() if p.published_at else None,
        "platform_post_id": p.platform_post_id,
    }


def post_from_row(d: dict) -> Post:
    return Post(
        post_key=d["post_key"], master_video_id=d["master_video_id"],
        brand=Brand(d["brand"]), platform=Platform(d["platform"]),
        account_id=d["account_id"], concept_tag=d["concept_tag"], hook_type=d["hook_type"],
        character_id=d["character_id"], duration_sec=int(d["duration_sec"]),
        oddity_level=int(d["oddity_level"]),
        reality_level=d["reality_level"] if d.get("reality_level") is not None else None,
        prompt_version=d["prompt_version"],
        generation_cost_jpy=float(d["generation_cost_jpy"]),
        policy_version=d["policy_version"],
        policy_result=PolicyDecision(d["policy_result"]),
        status=PostStatus(d["status"]),
        published_at=_parse_dt(d.get("published_at")),
        platform_post_id=d.get("platform_post_id"),
    )


def snapshot_to_row(s: PerformanceSnapshot) -> dict:
    row = {"post_key": s.post_key, "snapshot": s.snapshot,
           "collected_at": s.collected_at.isoformat()}
    row.update({k: getattr(s, k) for k in _SNAPSHOT_NUMERIC})
    return row


def snapshot_from_row(d: dict) -> PerformanceSnapshot:
    return PerformanceSnapshot(
        post_key=d["post_key"], snapshot=d["snapshot"],
        collected_at=_parse_dt(d.get("collected_at")) or _EPOCH,
        **{k: d.get(k) for k in _SNAPSHOT_NUMERIC},
    )


def account_daily_to_row(a: AccountDaily) -> dict:
    return {
        "date": a.date, "brand": a.brand.value, "platform": a.platform.value,
        "account_id": a.account_id, "followers": a.followers,
        "daily_views": a.daily_views, "daily_posts": a.daily_posts,
        "daily_revenue_jpy": a.daily_revenue_jpy, "daily_api_cost_jpy": a.daily_api_cost_jpy,
        "warnings": a.warnings, "status": a.status,
    }


def account_daily_from_row(d: dict) -> AccountDaily:
    return AccountDaily(
        date=d["date"], brand=Brand(d["brand"]), platform=Platform(d["platform"]),
        account_id=d["account_id"], followers=d.get("followers"),
        daily_views=d.get("daily_views"), daily_posts=int(d.get("daily_posts", 0)),
        daily_revenue_jpy=float(d.get("daily_revenue_jpy", 0.0)),
        daily_api_cost_jpy=float(d.get("daily_api_cost_jpy", 0.0)),
        warnings=int(d.get("warnings", 0)), status=d.get("status", "ACTIVE"),
    )


def _account_key(a: AccountDaily) -> str:
    return f"{a.date}|{a.brand.value}|{a.platform.value}|{a.account_id}"


# --- インターフェース ------------------------------------------------------

class Store(abc.ABC):
    @abc.abstractmethod
    def upsert_post(self, post: Post) -> None: ...

    @abc.abstractmethod
    def get_post(self, post_key: str) -> Post | None: ...

    @abc.abstractmethod
    def list_posts(self, *, since: str | None = None) -> list[Post]: ...

    @abc.abstractmethod
    def append_snapshot(self, snap: PerformanceSnapshot) -> None: ...

    @abc.abstractmethod
    def upsert_snapshot(self, snap: PerformanceSnapshot) -> None:
        """(post_key, snapshot) が一致する既存行があれば上書き、無ければ追加する。

        "latest" は毎回この投稿の現在値を1行で持ちたい（履歴を残さない）ため、
        単純追記の append_snapshot ではなく upsert を使う（§10.2）。
        """

    @abc.abstractmethod
    def list_snapshots(self, *, post_key: str | None = None) -> list[PerformanceSnapshot]: ...

    @abc.abstractmethod
    def upsert_account_daily(self, row: AccountDaily) -> None: ...

    @abc.abstractmethod
    def list_account_daily(self) -> list[AccountDaily]: ...


class LocalStore(Store):
    """``.state/db/*.json`` に保存する。1ファイル = 1DB。"""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else _STATE_DIR / "db"

    def _read(self, name: str, default):
        p = self.root / name
        if not p.exists():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default

    def _write(self, name: str, data) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    # 投稿DB (§10.1) — post_key の dict
    def upsert_post(self, post: Post) -> None:
        data = self._read("posts.json", {})
        data[post.post_key] = post_to_row(post)
        self._write("posts.json", data)

    def get_post(self, post_key: str) -> Post | None:
        row = self._read("posts.json", {}).get(post_key)
        return post_from_row(row) if row else None

    def list_posts(self, *, since: str | None = None) -> list[Post]:
        posts = [post_from_row(r) for r in self._read("posts.json", {}).values()]
        if since:
            posts = [
                p for p in posts
                if p.published_at and p.published_at.isoformat() >= since
            ]
        return sorted(posts, key=lambda p: p.post_key)

    # パフォーマンスDB (§10.2) — 追記リスト
    def append_snapshot(self, snap: PerformanceSnapshot) -> None:
        data = self._read("snapshots.json", [])
        data.append(snapshot_to_row(snap))
        self._write("snapshots.json", data)

    def upsert_snapshot(self, snap: PerformanceSnapshot) -> None:
        data = self._read("snapshots.json", [])
        for i, row in enumerate(data):
            if row.get("post_key") == snap.post_key and row.get("snapshot") == snap.snapshot:
                data[i] = snapshot_to_row(snap)
                self._write("snapshots.json", data)
                return
        data.append(snapshot_to_row(snap))
        self._write("snapshots.json", data)

    def list_snapshots(self, *, post_key: str | None = None) -> list[PerformanceSnapshot]:
        snaps = [snapshot_from_row(r) for r in self._read("snapshots.json", [])]
        if post_key:
            snaps = [s for s in snaps if s.post_key == post_key]
        return snaps

    # アカウント日次DB (§10.3) — (date, brand, platform, account) の dict
    def upsert_account_daily(self, row: AccountDaily) -> None:
        data = self._read("account_daily.json", {})
        data[_account_key(row)] = account_daily_to_row(row)
        self._write("account_daily.json", data)

    def list_account_daily(self) -> list[AccountDaily]:
        return [account_daily_from_row(r) for r in self._read("account_daily.json", {}).values()]


def _smart_num(v: str) -> int | float | str:
    """Sheets のセル文字列を int/float に。数値でなければそのまま返す。"""
    try:
        f = float(v.replace(",", "") if isinstance(v, str) else v)
    except (TypeError, ValueError):
        return v
    return int(f) if f.is_integer() else f


_POST_HEADERS_JA = {
    "post_key": "投稿キー", "master_video_id": "動画ID", "brand": "ブランド",
    "platform": "媒体", "account_id": "アカウントID", "concept_tag": "企画タグ",
    "hook_type": "フック種別", "character_id": "キャラクターID", "duration_sec": "尺",
    "oddity_level": "違和感レベル", "reality_level": "リアリティレベル",
    "prompt_version": "プロンプト版", "generation_cost_jpy": "生成費",
    "policy_version": "ポリシー版", "policy_result": "ポリシー判定", "status": "ステータス",
    "published_at": "投稿日時", "platform_post_id": "媒体側投稿ID",
}
_POST_NUMERIC = {"duration_sec", "oddity_level", "reality_level", "generation_cost_jpy"}
_POST_UNITS = {"duration_sec": "秒", "generation_cost_jpy": "円"}

_SNAPSHOT_HEADERS_JA = {
    "post_key": "投稿キー", "snapshot": "計測時点", "collected_at": "取得日時",
    "views": "再生数", "engaged_views": "エンゲージ視聴数", "likes": "いいね数",
    "comments": "コメント数", "shares": "シェア数", "impressions": "インプレッション数",
    "avg_watch_sec": "平均視聴秒数", "completion_rate": "完視聴率",
    "followers_before": "フォロワー数(投稿前)", "followers_after": "フォロワー数(投稿後)",
    "revenue_jpy": "収益",
}
_SNAPSHOT_UNITS = {
    "views": "回", "engaged_views": "回", "likes": "回", "comments": "回", "shares": "回",
    "impressions": "回", "avg_watch_sec": "秒", "followers_before": "人",
    "followers_after": "人", "revenue_jpy": "円",
}

_ACCOUNT_HEADERS_JA = {
    "date": "日付", "brand": "ブランド", "platform": "媒体", "account_id": "アカウントID",
    "followers": "フォロワー数", "daily_views": "当日再生数", "daily_posts": "当日投稿数",
    "daily_revenue_jpy": "当日収益", "daily_api_cost_jpy": "当日API費用",
    "warnings": "警告件数", "status": "ステータス",
}
_ACCOUNT_NUMERIC = {"followers", "daily_views", "daily_posts", "daily_revenue_jpy",
                     "daily_api_cost_jpy", "warnings"}
_ACCOUNT_UNITS = {
    "followers": "人", "daily_views": "回", "daily_posts": "件",
    "daily_revenue_jpy": "円", "daily_api_cost_jpy": "円", "warnings": "件",
}

# --- Enum等の値も日本語表示にする変換表（コード内部は英語の Enum.value のまま） ---

_BRAND_JA = {"cat": "猫", "dog": "犬", "adult": "大人向け"}
_PLATFORM_JA = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram", "x": "X"}
_POLICY_DECISION_JA = {
    "PASS": "合格", "REWRITE": "書き換え", "REGENERATE": "再生成",
    "SKIP_PLATFORM": "対象媒体スキップ", "HOLD": "保留",
}
_POST_STATUS_JA = {
    "PLANNED": "企画済み", "GENERATING": "生成中", "GENERATED": "生成済み",
    "QUALITY_FAILED": "品質NG", "POLICY_HOLD": "ポリシー保留", "PUBLISHING": "投稿中",
    "PUBLISHED": "投稿済み", "FAILED": "失敗", "SKIPPED": "スキップ",
}
_ACCOUNT_STATUS_JA = {"ACTIVE": "稼働中", "HOLD": "保留", "STOP": "停止"}
_SNAPSHOT_LABEL_JA = {"24h": "24時間後", "72h": "72時間後", "7d": "7日後", "latest": "最新"}

_POST_VALUE_MAPS = {
    "brand": _BRAND_JA, "platform": _PLATFORM_JA,
    "policy_result": _POLICY_DECISION_JA, "status": _POST_STATUS_JA,
}
_SNAPSHOT_VALUE_MAPS = {"snapshot": _SNAPSHOT_LABEL_JA}
_ACCOUNT_VALUE_MAPS = {"brand": _BRAND_JA, "platform": _PLATFORM_JA, "status": _ACCOUNT_STATUS_JA}

# --- 日時表示: シート上は "26/08/31 12:00"、コード内部は ISO 8601 のまま ------

_DT_DISPLAY_FMT = "%y/%m/%d %H:%M"
_POST_DT_KEYS = {"published_at"}
_SNAPSHOT_DT_KEYS = {"collected_at"}


def _format_dt_ja(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
    except (TypeError, ValueError):
        return iso_str
    # ★タイムゾーンをUTCに正規化してから表示用の数字にする。ここを省くと、
    # 例えばJSTのdatetimeを渡した場合に時刻の数字だけそのままUTC扱いで
    # 読み戻されてしまい、実際の時刻から9時間ずれる（実際に発生したバグ）。
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime(_DT_DISPLAY_FMT)


def _parse_dt_ja(display_str: str) -> str:
    """"26/08/31 12:00" 等 -> ISO 8601 文字列（*_from_row の datetime.fromisoformat 用）。

    旧形式(ISO文字列がそのまま残っている行)にも後方互換で対応。
    """
    try:
        return datetime.strptime(display_str, _DT_DISPLAY_FMT).replace(tzinfo=timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        datetime.fromisoformat(display_str)  # 妥当性チェックのみ
        return display_str
    except (TypeError, ValueError):
        return display_str


class SheetsStore(Store):
    """Google Sheets バックエンド。3タブ（投稿DB／パフォーマンスDB／アカウント日次DB）。

    1行目は人間向けの日本語見出し。列位置はその都度見出し行から解決するので、
    シート上で列を並び替えても壊れない。brand/platform/status 等の値も日本語表示にする
    （コード内部では引き続き Enum.value の英語文字列として扱い、シートI/Oの境界だけで
    _*_VALUE_MAPS を介して相互変換する）。

    認証: サービスアカウントの JSON 鍵。ローカルはファイルパス（env
    ``GOOGLE_SHEETS_CREDENTIALS_FILE``）、GitHub Actions は Secret の JSON 文字列そのもの
    （env ``GOOGLE_SERVICE_ACCOUNT_JSON``。ファイルとして置けないため）。両方あれば
    JSON 文字列を優先する。対象シート: env ``SHEETS_SPREADSHEET_ID``。
    """

    _TAB_POST = "投稿DB"
    _TAB_SNAPSHOT = "パフォーマンスDB"
    _TAB_ACCOUNT = "アカウント日次DB"

    def __init__(
        self, spreadsheet_id: str | None = None,
        credentials_file: str | None = None,
        credentials_json: str | None = None,
    ) -> None:
        self.spreadsheet_id = spreadsheet_id or env("SHEETS_SPREADSHEET_ID")
        self.credentials_file = credentials_file or env("GOOGLE_SHEETS_CREDENTIALS_FILE")
        self.credentials_json = credentials_json or env("GOOGLE_SERVICE_ACCOUNT_JSON")
        self._service = None

    # --- 内部: Sheets API ---------------------------------------------------

    def _svc(self):
        if self._service is None:
            import json as _json

            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            if not self.spreadsheet_id:
                raise RuntimeError("SHEETS_SPREADSHEET_ID が未設定")
            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            if self.credentials_json:
                info = _json.loads(self.credentials_json)
                creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
            elif self.credentials_file:
                creds = service_account.Credentials.from_service_account_file(
                    self.credentials_file, scopes=scopes,
                )
            else:
                raise RuntimeError(
                    "GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SHEETS_CREDENTIALS_FILE がどちらも未設定"
                )
            self._service = build("sheets", "v4", credentials=creds)
        return self._service

    def _fetch_raw(self, tab: str) -> list[list[str]]:
        res = self._svc().spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id, range=tab,
        ).execute()
        return res.get("values", [])

    def _rows_with_index(
        self, tab: str, headers_ja: dict[str, str], numeric_keys: set[str],
        value_maps: dict[str, dict[str, str]] | None = None,
        dt_keys: set[str] | None = None,
        units: dict[str, str] | None = None,
    ) -> tuple[list[str], list[tuple[int, dict]]]:
        """(見出し行の生セル, [(シート上の実行番号1始まり, 英語キーdict), ...])。

        value_maps は {フィールド名: {英語値: 日本語表示}}。読み込み時は日本語表示→
        英語値に逆変換してから dict に入れる（コード内部は常に英語の Enum.value）。
        dt_keys は "26/08/31 12:00" 表示 -> ISO 8601 文字列に戻すフィールド名の集合。
        units は {フィールド名: "回"等}。セル側に付いた単位を数値変換の前に取り除く。
        """
        value_maps = value_maps or {}
        dt_keys = dt_keys or set()
        units = units or {}
        reverse_maps = {k: {v: ek for ek, v in m.items()} for k, m in value_maps.items()}
        raw = self._fetch_raw(tab)
        if not raw:
            return [], []
        header_row = raw[0]
        ja_to_key = {v: k for k, v in headers_ja.items()}
        col_keys = [ja_to_key.get(h) for h in header_row]
        out = []
        for i, r in enumerate(raw[1:], start=2):  # 1行目=見出しなのでデータは2行目から
            d: dict = {}
            for idx, key in enumerate(col_keys):
                if key is None:
                    continue
                val = r[idx] if idx < len(r) else ""
                if val == "":
                    val = None
                elif key in dt_keys:
                    val = _parse_dt_ja(val)
                elif key in reverse_maps:
                    val = reverse_maps[key].get(val, val)
                elif key in numeric_keys:
                    unit = units.get(key)
                    if unit and val.endswith(unit):
                        val = val[: -len(unit)]
                    val = _smart_num(val)
                d[key] = val
            out.append((i, d))
        return header_row, out

    def _values_for_header(
        self, header_row: list[str], headers_ja: dict[str, str], row: dict,
        value_maps: dict[str, dict[str, str]] | None = None,
        dt_keys: set[str] | None = None,
        units: dict[str, str] | None = None,
    ) -> list:
        value_maps = value_maps or {}
        dt_keys = dt_keys or set()
        units = units or {}
        ja_to_key = {v: k for k, v in headers_ja.items()}
        values = []
        for h in header_row:
            key = ja_to_key.get(h)
            v = row.get(key) if key else None
            if v is not None and key in dt_keys:
                v = _format_dt_ja(v)
            elif v is not None and key in value_maps:
                v = value_maps[key].get(v, v)
            elif v is not None and key in units:
                num = int(v) if isinstance(v, float) and v.is_integer() else v
                v = f"{num:,}{units[key]}"
            values.append("" if v is None else v)
        return values

    def _upsert_row(
        self, tab: str, headers_ja: dict[str, str], numeric_keys: set[str],
        row: dict, *, match_row_number: int | None,
        value_maps: dict[str, dict[str, str]] | None = None,
        dt_keys: set[str] | None = None,
        units: dict[str, str] | None = None,
    ) -> None:
        header_row, _ = self._rows_with_index(tab, headers_ja, numeric_keys, value_maps, dt_keys, units)
        if not header_row:
            header_row = list(headers_ja.values())
        values = self._values_for_header(header_row, headers_ja, row, value_maps, dt_keys, units)
        svc = self._svc().spreadsheets().values()
        if match_row_number is None:
            # OVERWRITE: 新規行を挿入せず既存の空セルに書くだけにする。
            # INSERT_ROWS だと挿入した行が直上（見出し行）の書式を引き継いでしまう。
            svc.append(
                spreadsheetId=self.spreadsheet_id, range=tab,
                valueInputOption="RAW", insertDataOption="OVERWRITE",
                body={"values": [values]},
            ).execute()
        else:
            svc.update(
                spreadsheetId=self.spreadsheet_id, range=f"{tab}!A{match_row_number}",
                valueInputOption="RAW", body={"values": [values]},
            ).execute()

    # --- 投稿DB --------------------------------------------------------

    def upsert_post(self, post: Post) -> None:
        _, rows = self._rows_with_index(
            self._TAB_POST, _POST_HEADERS_JA, _POST_NUMERIC,
            _POST_VALUE_MAPS, _POST_DT_KEYS, _POST_UNITS,
        )
        match = next((i for i, d in rows if d.get("post_key") == post.post_key), None)
        self._upsert_row(
            self._TAB_POST, _POST_HEADERS_JA, _POST_NUMERIC,
            post_to_row(post), match_row_number=match,
            value_maps=_POST_VALUE_MAPS, dt_keys=_POST_DT_KEYS, units=_POST_UNITS,
        )

    def get_post(self, post_key: str) -> Post | None:
        _, rows = self._rows_with_index(
            self._TAB_POST, _POST_HEADERS_JA, _POST_NUMERIC,
            _POST_VALUE_MAPS, _POST_DT_KEYS, _POST_UNITS,
        )
        for _, d in rows:
            if d.get("post_key") == post_key:
                return post_from_row(d)
        return None

    def list_posts(self, *, since: str | None = None) -> list[Post]:
        _, rows = self._rows_with_index(
            self._TAB_POST, _POST_HEADERS_JA, _POST_NUMERIC,
            _POST_VALUE_MAPS, _POST_DT_KEYS, _POST_UNITS,
        )
        posts = [post_from_row(d) for _, d in rows]
        if since:
            posts = [p for p in posts if p.published_at and p.published_at.isoformat() >= since]
        return sorted(posts, key=lambda p: p.post_key)

    # --- パフォーマンスDB ------------------------------------------------

    def append_snapshot(self, snap: PerformanceSnapshot) -> None:
        self._upsert_row(
            self._TAB_SNAPSHOT, _SNAPSHOT_HEADERS_JA, set(_SNAPSHOT_NUMERIC),
            snapshot_to_row(snap), match_row_number=None,
            value_maps=_SNAPSHOT_VALUE_MAPS, dt_keys=_SNAPSHOT_DT_KEYS, units=_SNAPSHOT_UNITS,
        )

    def upsert_snapshot(self, snap: PerformanceSnapshot) -> None:
        _, rows = self._rows_with_index(
            self._TAB_SNAPSHOT, _SNAPSHOT_HEADERS_JA, set(_SNAPSHOT_NUMERIC),
            _SNAPSHOT_VALUE_MAPS, _SNAPSHOT_DT_KEYS, _SNAPSHOT_UNITS,
        )
        match = next(
            (i for i, d in rows if d.get("post_key") == snap.post_key and d.get("snapshot") == snap.snapshot),
            None,
        )
        self._upsert_row(
            self._TAB_SNAPSHOT, _SNAPSHOT_HEADERS_JA, set(_SNAPSHOT_NUMERIC),
            snapshot_to_row(snap), match_row_number=match,
            value_maps=_SNAPSHOT_VALUE_MAPS, dt_keys=_SNAPSHOT_DT_KEYS, units=_SNAPSHOT_UNITS,
        )

    def list_snapshots(self, *, post_key: str | None = None) -> list[PerformanceSnapshot]:
        _, rows = self._rows_with_index(
            self._TAB_SNAPSHOT, _SNAPSHOT_HEADERS_JA, set(_SNAPSHOT_NUMERIC),
            _SNAPSHOT_VALUE_MAPS, _SNAPSHOT_DT_KEYS, _SNAPSHOT_UNITS,
        )
        snaps = [snapshot_from_row(d) for _, d in rows]
        if post_key:
            snaps = [s for s in snaps if s.post_key == post_key]
        return snaps

    # --- アカウント日次DB ------------------------------------------------

    def upsert_account_daily(self, row: AccountDaily) -> None:
        key = _account_key(row)
        _, rows = self._rows_with_index(
            self._TAB_ACCOUNT, _ACCOUNT_HEADERS_JA, _ACCOUNT_NUMERIC,
            _ACCOUNT_VALUE_MAPS, None, _ACCOUNT_UNITS,
        )
        match = None
        for i, d in rows:
            existing_key = f"{d.get('date')}|{d.get('brand')}|{d.get('platform')}|{d.get('account_id')}"
            if existing_key == key:
                match = i
                break
        self._upsert_row(
            self._TAB_ACCOUNT, _ACCOUNT_HEADERS_JA, _ACCOUNT_NUMERIC,
            account_daily_to_row(row), match_row_number=match,
            value_maps=_ACCOUNT_VALUE_MAPS, units=_ACCOUNT_UNITS,
        )

    def list_account_daily(self) -> list[AccountDaily]:
        _, rows = self._rows_with_index(
            self._TAB_ACCOUNT, _ACCOUNT_HEADERS_JA, _ACCOUNT_NUMERIC,
            _ACCOUNT_VALUE_MAPS, None, _ACCOUNT_UNITS,
        )
        return [account_daily_from_row(d) for _, d in rows]


def get_store(*, backend: str | None = None, root: Path | str | None = None) -> Store:
    backend = (backend or env("SHEETS_BACKEND", "local") or "local").lower()
    if backend == "local":
        return LocalStore(root)
    if backend == "sheets":
        return SheetsStore()
    raise ValueError(f"未知の SHEETS_BACKEND: {backend!r}（local / sheets）")
