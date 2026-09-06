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
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))

from src.common.config import env
from src.common.models import (
    AccountDaily,
    Brand,
    DecisionLog,
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
    "revenue_jpy", "views_delta", "followers_delta",
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
           "collected_at": s.collected_at.isoformat(),
           # 媒体は投稿キー(<master_video_id>:<platform>)から導出する表示用の値。
           # PerformanceSnapshot 自体はplatformを持たない。
           "platform": s.post_key.rsplit(":", 1)[-1] if ":" in s.post_key else None}
    row.update({k: getattr(s, k) for k in _SNAPSHOT_NUMERIC})
    return row


def snapshot_from_row(d: dict) -> PerformanceSnapshot:
    return PerformanceSnapshot(
        post_key=d["post_key"], snapshot=d["snapshot"],
        collected_at=_parse_dt(d.get("collected_at")) or _EPOCH,
        **{k: d.get(k) for k in _SNAPSHOT_NUMERIC},
    )


def _fill_views_delta(snap: PerformanceSnapshot, old_views) -> None:
    """upsert_snapshot 用: 上書き前の再生数との差分を snap.views_delta に入れる。

    呼び出し側が明示的に views_delta を渡している場合は上書きしない。
    """
    if snap.views_delta is not None:
        return
    if snap.views is not None and old_views is not None:
        snap.views_delta = snap.views - int(old_views)


# 「概念自体が無い/API仕様上取得不可」なことを空欄（＝今回たまたま取れなかった）と
# 区別して人向けに示す印。値が None の対象媒体（None = 全媒体）なら "-" を書く。
# ★2026-09-05 実API確認: impressions は YouTube Analytics APIに存在しない識別子
# （Unknown identifier エラー）／Instagram REELSでは非対応。revenue_jpy はYouTubeが
# 未収益化（収益化すれば自動で実数値に置き換わる。このロジックはNoneの間だけ働く）、
# Instagramはオーガニック投稿の収益指標自体がAPIに存在しない。
_SNAPSHOT_NA_MARKERS: dict[str, set[str] | None] = {
    "engaged_views": {"instagram"},  # Instagramにはエンゲージ視聴数に相当する指標が無い
    "impressions": None,             # 両媒体ともAPIで取得不可
    "revenue_jpy": None,             # 両媒体とも取得不可(YouTubeは収益化すれば直る)
}


def _snapshot_row_with_na_markers(row: dict) -> dict:
    platform = row.get("post_key", "").rsplit(":", 1)[-1]
    for field, target_platforms in _SNAPSHOT_NA_MARKERS.items():
        if (target_platforms is None or platform in target_platforms) and row.get(field) is None:
            row[field] = "-"
    return row


def account_daily_to_row(a: AccountDaily) -> dict:
    return {
        "date": a.date, "brand": a.brand.value, "platform": a.platform.value,
        "account_id": a.account_id, "followers": a.followers, "status": a.status,
    }


def account_daily_from_row(d: dict) -> AccountDaily:
    return AccountDaily(
        date=d["date"], brand=Brand(d["brand"]), platform=Platform(d["platform"]),
        account_id=d["account_id"], followers=d.get("followers"),
        status=d.get("status", "ACTIVE"),
    )


def _account_key(a: AccountDaily) -> str:
    return f"{a.date}|{a.brand.value}|{a.platform.value}|{a.account_id}"


_DECISION_FIELDS = (
    "decision_id", "date", "account", "hypothesis", "data_used", "agent_opinions",
    "critic_objection", "decision", "changed_vars", "unchanged_vars", "expected_kpi",
    "success_criteria", "review_date", "confidence", "data_sufficient",
    "result", "result_reason", "actual_kpi", "reviewed_date",
)


def decision_to_row(d: DecisionLog) -> dict:
    return {f: getattr(d, f) for f in _DECISION_FIELDS}


def decision_from_row(r: dict) -> DecisionLog:
    return DecisionLog(
        decision_id=str(r.get("decision_id", "")),
        date=str(r.get("date", "")),
        account=str(r.get("account", "")),
        hypothesis=str(r.get("hypothesis", "") or ""),
        data_used=str(r.get("data_used", "") or ""),
        agent_opinions=str(r.get("agent_opinions", "") or ""),
        critic_objection=str(r.get("critic_objection", "") or ""),
        decision=str(r.get("decision", "") or ""),
        changed_vars=str(r.get("changed_vars", "") or ""),
        unchanged_vars=str(r.get("unchanged_vars", "") or ""),
        expected_kpi=str(r.get("expected_kpi", "") or ""),
        success_criteria=str(r.get("success_criteria", "") or ""),
        review_date=str(r.get("review_date", "") or ""),
        confidence=int(r.get("confidence") or 0),
        data_sufficient=bool(r.get("data_sufficient", True))
        if r.get("data_sufficient") not in (None, "") else True,
        result=str(r.get("result", "") or ""),
        result_reason=str(r.get("result_reason", "") or ""),
        actual_kpi=str(r.get("actual_kpi", "") or ""),
        reviewed_date=str(r.get("reviewed_date", "") or ""),
    )


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
    def upsert_snapshot(self, snap: PerformanceSnapshot, *, compute_delta: bool = True) -> None:
        """(post_key, snapshot) が一致する既存行があれば上書き、無ければ追加する。

        "latest" は毎回この投稿の現在値を1行で持ちたい（履歴を残さない）ため、
        単純追記の append_snapshot ではなく upsert を使う（§10.2）。

        compute_delta=True なら上書き前の再生数との差を snap.views_delta に入れる。
        呼び出し側が前日比を自前で計算する場合は False（= 上書き直前値との差を書かない）。
        """

    @abc.abstractmethod
    def list_snapshots(self, *, post_key: str | None = None) -> list[PerformanceSnapshot]: ...

    @abc.abstractmethod
    def upsert_account_daily(self, row: AccountDaily) -> None: ...

    @abc.abstractmethod
    def list_account_daily(self) -> list[AccountDaily]: ...

    @abc.abstractmethod
    def upsert_decision(self, row: DecisionLog) -> None:
        """意思決定ログを 1 行。decision_id 一致で上書き、無ければ追加。"""

    @abc.abstractmethod
    def list_decisions(self) -> list[DecisionLog]: ...


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

    def upsert_snapshot(self, snap: PerformanceSnapshot, *, compute_delta: bool = True) -> None:
        data = self._read("snapshots.json", [])
        for i, row in enumerate(data):
            if row.get("post_key") == snap.post_key and row.get("snapshot") == snap.snapshot:
                if compute_delta:
                    _fill_views_delta(snap, row.get("views"))
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

    # 意思決定ログ — decision_id の dict
    def upsert_decision(self, row: DecisionLog) -> None:
        data = self._read("decisions.json", {})
        data[row.decision_id] = decision_to_row(row)
        self._write("decisions.json", data)

    def list_decisions(self) -> list[DecisionLog]:
        return [decision_from_row(r) for r in self._read("decisions.json", {}).values()]


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
    "post_key": "投稿キー", "platform": "媒体", "snapshot": "計測時点", "collected_at": "取得日時",
    "views": "再生数", "engaged_views": "eng視聴数", "likes": "いいね数",
    "comments": "コメント数", "shares": "シェア数", "impressions": "IMP数",
    "avg_watch_sec": "平均視聴秒数", "completion_rate": "完視聴率",
    "followers_after": "Flwr数", "followers_delta": "Flwr数(前日比)",
    "revenue_jpy": "収益", "views_delta": "再生数(前日比)",
}
_SNAPSHOT_UNITS = {
    "views": "回", "engaged_views": "回", "likes": "回", "comments": "回", "shares": "回",
    "impressions": "回", "avg_watch_sec": "秒", "followers_after": "人",
    "followers_delta": "人", "revenue_jpy": "円", "views_delta": "回",
    "completion_rate": "%",
}
# 0〜1の割合をパーセント表示にするフィールド（シート上は ×100 した数値で保持する）
_PERCENT_FIELDS = {"completion_rate"}
# 単位付き数値のうち、小数の桁数を絞りたいフィールド（見やすさのため）
_ROUND_DIGITS: dict[str, int] = {
    "completion_rate": 1,
    "avg_watch_sec": 1,
    "generation_cost_jpy": 0, "revenue_jpy": 0,
}

_ACCOUNT_HEADERS_JA = {
    "date": "日付", "brand": "ブランド", "platform": "媒体", "account_id": "アカウントID",
    "followers": "フォロワー数", "status": "ステータス",
}
_ACCOUNT_NUMERIC = {"followers"}

# 意思決定ログ（agent-mtg 統括が毎日残す台帳）
_DECISION_HEADERS_JA = {
    "decision_id": "判断ID", "date": "日付", "account": "対象", "hypothesis": "仮説",
    "data_used": "使ったデータ", "agent_opinions": "各エージェントの意見",
    "critic_objection": "批判エージェントの反論", "decision": "最終決定",
    "changed_vars": "変える変数", "unchanged_vars": "変えない変数",
    "expected_kpi": "期待KPI", "success_criteria": "成功/失敗の基準",
    "review_date": "再評価日", "confidence": "確信度", "data_sufficient": "データ",
    "result": "結果", "result_reason": "結果の理由", "actual_kpi": "実績値",
    "reviewed_date": "再評価した日",
}
_DECISION_NUMERIC = {"confidence"}
_DECISION_UNITS = {"confidence": "%"}
_DECISION_VALUE_MAPS = {"data_sufficient": {True: "十分", False: "不足"}}
_ACCOUNT_UNITS = {"followers": "人"}

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
_SNAPSHOT_VALUE_MAPS = {"snapshot": _SNAPSHOT_LABEL_JA, "platform": _PLATFORM_JA}
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
    # ★タイムゾーンをJSTに正規化してから表示用の数字にする。ここを省くと、
    # 例えばJSTのdatetimeを渡した場合に時刻の数字だけそのままUTC扱いで
    # 読み戻されてしまい、実際の時刻から9時間ずれる（実際に発生したバグ）。
    # UTCではなくJSTに揃えるのは、シート上の表示を日本時間で読めるようにするため
    # （書き込み・読み戻しの両方でJSTに統一していれば、この変換自体はどちらでも
    # ズレは起きない。人が読む画面なのでJSTを選んでいる）。
    if dt.tzinfo is not None:
        dt = dt.astimezone(JST)
    return dt.strftime(_DT_DISPLAY_FMT)


def _parse_dt_ja(display_str: str) -> str:
    """"26/08/31 12:00" 等 -> ISO 8601 文字列（*_from_row の datetime.fromisoformat 用）。

    旧形式(ISO文字列がそのまま残っている行)にも後方互換で対応。
    """
    try:
        return datetime.strptime(display_str, _DT_DISPLAY_FMT).replace(tzinfo=JST).isoformat()
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
    _TAB_DECISION = "意思決定ログ"

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

    @staticmethod
    def _find_header_row(
        raw: list[list[str]], headers_ja: dict[str, str],
    ) -> int | None:
        """見出し行の実行番号(1始まり)を探す。常に1行目とは決め打たない。

        シート上部に集計欄などを挿入されても壊れないよう、headers_ja の最初の
        フィールド（例: 投稿キー）のラベルがどこかのセルに現れる最初の行を見出し行とみなす。
        列の並び替えには元々強い設計なので、行の中でどの位置にあっても検出できる。
        """
        anchor = next(iter(headers_ja.values()), None)
        if anchor is None:
            return None
        for i, row in enumerate(raw, start=1):
            if anchor in row:
                return i
        return None

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
        header_idx = self._find_header_row(raw, headers_ja)
        if header_idx is None:
            return [], []
        header_row = raw[header_idx - 1]
        ja_to_key = {v: k for k, v in headers_ja.items()}
        col_keys = [ja_to_key.get(h) for h in header_row]
        out = []
        for i, r in enumerate(raw[header_idx:], start=header_idx + 1):  # 見出し行の次から
            d: dict = {}
            for idx, key in enumerate(col_keys):
                if key is None:
                    continue
                val = r[idx] if idx < len(r) else ""
                if val in ("", "-"):
                    # "-" は「この媒体では概念自体が無い」ことを人向けに示す表示用の印。
                    # コード内部では空欄と同じ扱い（None）にする。
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
                    if key in _PERCENT_FIELDS and val is not None:
                        val = val / 100
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
            elif v is not None and key in units and isinstance(v, (int, float)):
                num = v * 100 if key in _PERCENT_FIELDS else v
                if key not in _PERCENT_FIELDS and isinstance(num, float) and num.is_integer():
                    num = int(num)
                if isinstance(num, float) and key in _ROUND_DIGITS:
                    digits = _ROUND_DIGITS[key]
                    num = round(num, digits)
                    if digits == 0:
                        num = int(num)
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
        raw = self._fetch_raw(tab)
        header_idx = self._find_header_row(raw, headers_ja)
        header_row = raw[header_idx - 1] if header_idx else list(headers_ja.values())
        values = self._values_for_header(header_row, headers_ja, row, value_maps, dt_keys, units)
        svc = self._svc().spreadsheets().values()
        if match_row_number is None:
            # OVERWRITE: 新規行を挿入せず既存の空セルに書くだけにする。
            # INSERT_ROWS だと挿入した行が直上（見出し行）の書式を引き継いでしまう。
            # range は見出し行から下に絞る（見出しより上に集計欄等があっても、
            # そちらを「表」として誤検出して追記されないようにするため）。
            append_range = f"{tab}!A{header_idx}:Z" if header_idx else tab
            svc.append(
                spreadsheetId=self.spreadsheet_id, range=append_range,
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
            _snapshot_row_with_na_markers(snapshot_to_row(snap)), match_row_number=None,
            value_maps=_SNAPSHOT_VALUE_MAPS, dt_keys=_SNAPSHOT_DT_KEYS, units=_SNAPSHOT_UNITS,
        )

    def upsert_snapshot(self, snap: PerformanceSnapshot, *, compute_delta: bool = True) -> None:
        _, rows = self._rows_with_index(
            self._TAB_SNAPSHOT, _SNAPSHOT_HEADERS_JA, set(_SNAPSHOT_NUMERIC),
            _SNAPSHOT_VALUE_MAPS, _SNAPSHOT_DT_KEYS, _SNAPSHOT_UNITS,
        )
        match = None
        for i, d in rows:
            if d.get("post_key") == snap.post_key and d.get("snapshot") == snap.snapshot:
                match = i
                if compute_delta:
                    _fill_views_delta(snap, d.get("views"))
                break
        self._upsert_row(
            self._TAB_SNAPSHOT, _SNAPSHOT_HEADERS_JA, set(_SNAPSHOT_NUMERIC),
            _snapshot_row_with_na_markers(snapshot_to_row(snap)), match_row_number=match,
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

    def upsert_decision(self, row: DecisionLog) -> None:
        _, rows = self._rows_with_index(
            self._TAB_DECISION, _DECISION_HEADERS_JA, _DECISION_NUMERIC,
            _DECISION_VALUE_MAPS, None, _DECISION_UNITS,
        )
        match = next(
            (i for i, d in rows if d.get("decision_id") == row.decision_id), None
        )
        self._upsert_row(
            self._TAB_DECISION, _DECISION_HEADERS_JA, _DECISION_NUMERIC,
            decision_to_row(row), match_row_number=match,
            value_maps=_DECISION_VALUE_MAPS, units=_DECISION_UNITS,
        )

    def list_decisions(self) -> list[DecisionLog]:
        _, rows = self._rows_with_index(
            self._TAB_DECISION, _DECISION_HEADERS_JA, _DECISION_NUMERIC,
            _DECISION_VALUE_MAPS, None, _DECISION_UNITS,
        )
        return [decision_from_row(d) for _, d in rows]


def get_store(*, backend: str | None = None, root: Path | str | None = None) -> Store:
    backend = (backend or env("SHEETS_BACKEND", "local") or "local").lower()
    if backend == "local":
        return LocalStore(root)
    if backend == "sheets":
        return SheetsStore()
    raise ValueError(f"未知の SHEETS_BACKEND: {backend!r}（local / sheets）")
