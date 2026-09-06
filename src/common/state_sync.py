"""ワークフロー間で `.state/` を引き継ぐための共有ストア（GCS）。

GitHub Actions のワークフローはランごとに `git clone` からやり直すため `.state/` が空に
なる。`plan_daily` が作った `plan-<date>.json` を `generate` が読めない、といった連鎖で
パイプライン全体が空回りする。各ワークフローを独立スケジュールのまま回すために、
`.state/` の実体を GCS バケットに置き、コマンドの前後で pull / push する。

有効化: env ``STATE_SYNC`` が真（``1`` / ``true`` / ``yes``）のときだけ動く。
ローカル実行・pytest では未設定なので no-op（GCS に触れない）。

  STATE_SYNC          … "1" で有効（GitHub Actions 側で設定）
  STATE_SYNC_BUCKET   … バケット名（未設定なら ``GCS_BUCKET_NAME`` を流用）
  STATE_SYNC_PREFIX   … オブジェクトの接頭辞。既定 "state"

認証は `publishers/instagram.py` と同じサービスアカウント
（``GOOGLE_SERVICE_ACCOUNT_JSON`` の JSON 文字列、または
``GOOGLE_SHEETS_CREDENTIALS_FILE`` のパス）。

使い方（`src/cli.py`）::

    state_sync.pull()          # コマンド実行前: リモート → .state/
    rc = dispatch(args)
    state_sync.push()          # 実行後: pull 時点から変わった/増えたファイルだけ上げる

同時実行対策: no-op で終わったワークフローは何も push しない（変更ゼロ）。同一ファイルを
二つのワークフローが同じ瞬間に書く競合は、各ワークフロー側の ``concurrency`` グループで
直列化して避ける。削除は伝播しない（安全側）。
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from pathlib import Path

from src.common.config import env

STATE_DIR = Path(__file__).resolve().parents[2] / ".state"

# pull 時点の {リモート相対パス: md5(base64)}。push はこれと突き合わせて差分だけ上げる。
_pulled_md5: dict[str, str] = {}
_bucket_cache = None


def _enabled() -> bool:
    return (env("STATE_SYNC", "") or "").strip().lower() in ("1", "true", "yes", "on")


def _bucket_name() -> str | None:
    return env("STATE_SYNC_BUCKET") or env("GCS_BUCKET_NAME")


def _prefix() -> str:
    return (env("STATE_SYNC_PREFIX", "state") or "state").strip("/")


def _bucket():
    global _bucket_cache
    if _bucket_cache is not None:
        return _bucket_cache

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
            "state_sync: GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SHEETS_CREDENTIALS_FILE が未設定"
        )

    name = _bucket_name()
    if not name:
        raise RuntimeError("state_sync: STATE_SYNC_BUCKET / GCS_BUCKET_NAME が未設定")

    client = storage.Client(credentials=creds, project=env("GOOGLE_CLOUD_PROJECT"))
    _bucket_cache = client.bucket(name)
    return _bucket_cache


def _md5_b64(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode("ascii")


def _iter_local_files() -> Iterator[tuple[Path, str]]:
    if not STATE_DIR.exists():
        return
    for p in sorted(STATE_DIR.rglob("*")):
        if p.is_file():
            yield p, p.relative_to(STATE_DIR).as_posix()


def pull() -> int:
    """リモートの `state/` 以下を `.state/` に展開する。展開したファイル数を返す。"""
    _pulled_md5.clear()
    if not _enabled():
        return 0

    prefix = _prefix()
    bucket = _bucket()
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    n = 0
    for blob in bucket.list_blobs(prefix=f"{prefix}/"):
        rel = blob.name[len(prefix) + 1 :]
        if not rel or rel.endswith("/"):
            continue
        dest = STATE_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(dest)
        _pulled_md5[rel] = blob.md5_hash or _md5_b64(dest)
        n += 1

    print(f"[state-sync] pull: {n} ファイルを取得（gs://{_bucket_name()}/{prefix}/）")
    return n


def push() -> int:
    """`.state/` のうち pull 時点から変わった/増えたファイルだけリモートへ上げる。上げた数を返す。"""
    if not _enabled():
        return 0

    prefix = _prefix()
    bucket = _bucket()

    uploaded = 0
    for abs_path, rel in _iter_local_files():
        if _pulled_md5.get(rel) == _md5_b64(abs_path):
            continue  # pull 時点から変化なし
        bucket.blob(f"{prefix}/{rel}").upload_from_filename(abs_path)
        uploaded += 1

    print(f"[state-sync] push: {uploaded} ファイルを更新（gs://{_bucket_name()}/{prefix}/）")
    return uploaded
