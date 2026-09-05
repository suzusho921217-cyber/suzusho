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
)


@pytest.fixture(autouse=True)
def _isolate_from_local_env(monkeypatch):
    # _load_dotenv は @cache で一度しか本体が走らない。ここで先に走らせてから
    # 剥がすことで、実行順に関わらず毎テストで確実に .env の値を消せる。
    _config._load_dotenv()
    for key in _LOCAL_ONLY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
