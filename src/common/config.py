"""config/*.yaml のローダ。

設計原則: シークレットは YAML に書かない。GitHub Secrets / Environments から
環境変数で受ける（§5）。ここは非機密の運用パラメータのみ扱う。
"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = _ROOT / "config"


@cache
def _load_dotenv() -> None:
    """リポジトリ直下の .env を（あれば）環境変数に流し込む。既存の環境変数は上書きしない。

    依存を増やさない簡易パーサ。`KEY=VALUE` の行のみ。#行・空行は無視。
    .env は .gitignore 済み（§5 シークレットはコミットしない）。
    """
    path = _ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@cache
def load(name: str) -> dict[str, Any]:
    """config/<name>.yaml を読む。例: load("brands")。"""
    path = CONFIG_DIR / f"{name}.yaml"
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def env(key: str, default: str | None = None, *, required: bool = False) -> str | None:
    """シークレット/環境依存値はここ経由で取得する。初回に .env を読み込む。"""
    _load_dotenv()
    val = os.environ.get(key, default)
    if required and not val:
        raise RuntimeError(f"required env var missing: {key}")
    return val
