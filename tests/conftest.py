"""テスト全体の前提を固定する。

開発者ローカルの .env（例: VIDEO_PROVIDER=veo, SHEETS_BACKEND=sheets）が
テストに漏れないようにする。テストは常に mock provider・LocalStore・課金なしを前提とする。
"""

import pytest

from src.common import config as _config

_LOCAL_ONLY_ENV_KEYS = (
    "VIDEO_PROVIDER",
    "SHEETS_BACKEND",
    "SHEETS_SPREADSHEET_ID",
    "GOOGLE_SHEETS_CREDENTIALS_FILE",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    "GMAIL_SENDER_ADDRESS",
    "GMAIL_APP_PASSWORD",
    "ALERT_EMAIL_TO",
    "YOUTUBE_OAUTH_CLIENT_ID",
    "YOUTUBE_OAUTH_CLIENT_SECRET",
    "YOUTUBE_OAUTH_REFRESH_TOKEN",
    "YOUTUBE_OAUTH_REFRESH_TOKEN_CAT",
    "YOUTUBE_OAUTH_REFRESH_TOKEN_DOG",
    "META_APP_ID",
    "META_APP_SECRET",
    "IG_ACCESS_TOKEN",
    "IG_ACCESS_TOKEN_CAT",
    "IG_ACCESS_TOKEN_DOG",
    "IG_BUSINESS_ACCOUNT_ID",
    "IG_BUSINESS_ACCOUNT_ID_CAT",
    "IG_BUSINESS_ACCOUNT_ID_DOG",
    "GCS_BUCKET_NAME",
    "GOOGLE_CLOUD_PROJECT",
)


@pytest.fixture(autouse=True)
def _isolate_from_local_env(monkeypatch):
    # _load_dotenv は @cache で一度しか本体が走らない。ここで先に走らせてから
    # 剥がすことで、実行順に関わらず毎テストで確実に .env の値を消せる。
    _config._load_dotenv()
    for key in _LOCAL_ONLY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


# テストが前提にする1日の本数。本番の total_daily_slots は会議ボット(agent-mtg)が
# 自動で減らすことがあり（2026-10-20 に 2→1）、実 config を読むとテストが巻き込まれて落ちる。
TEST_DAILY_SLOTS = 2


@pytest.fixture(autouse=True)
def _pin_daily_slots(tmp_path_factory, monkeypatch):
    """実 config の写しで total_daily_slots だけ TEST_DAILY_SLOTS に固定して読ませる。"""
    import shutil

    import yaml

    d = tmp_path_factory.mktemp("config")
    shutil.copytree(_config.CONFIG_DIR, d, dirs_exist_ok=True)
    scoring = yaml.safe_load((d / "scoring.yaml").read_text(encoding="utf-8"))
    scoring["allocation"]["total_daily_slots"] = TEST_DAILY_SLOTS
    (d / "scoring.yaml").write_text(yaml.safe_dump(scoring, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(_config, "CONFIG_DIR", d)
    _config.load.cache_clear()  # load は @cache。固定前/他テストの読み込み結果を捨てる
    yield
    _config.load.cache_clear()


@pytest.fixture(autouse=True)
def _no_sheets_write_pacing(monkeypatch):
    """本番の Sheets 書き込み間隔（1分60回の上限対策）はテストでは待たない。"""
    from src.sheets import client as _sheets_client

    monkeypatch.setattr(_sheets_client, "_WRITE_MIN_INTERVAL_SEC", 0.0)
