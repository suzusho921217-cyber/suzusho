"""Claude API の使用量を `.state/llm_usage-<YYYY-MM>.json` に記録する（経理チェック用）。

Anthropic の残高は API で読めないため、呼び出しごとの `response.usage` を積み上げて
使用額を見積もる。記録の失敗で本処理（会議・投稿文生成）を止めない。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parents[2] / ".state"
JST = timezone(timedelta(hours=9))


def record_llm_usage(model: str, usage) -> None:
    try:
        now = datetime.now(JST)
        server = getattr(usage, "server_tool_use", None)
        row = {
            "at": now.isoformat(timespec="seconds"),
            "model": model,
            "input": int(getattr(usage, "input_tokens", 0) or 0),
            "output": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_read": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            "cache_write": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            "web_search": int(getattr(server, "web_search_requests", 0) or 0) if server else 0,
        }
        path = STATE_DIR / f"llm_usage-{now:%Y-%m}.json"
        rows = json.loads(path.read_text()) if path.exists() else []
        rows.append(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    except Exception as e:  # noqa: BLE001 - 記録の失敗で本処理を止めない
        print(f"[accounting] Claude 使用量の記録に失敗: {e}")
