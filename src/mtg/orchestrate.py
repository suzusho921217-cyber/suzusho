"""agent-mtg: 5役職（analyst→researcher→marketer→critic→coordinator）を順番に呼び、
coordinatorの結論から安全な変更だけを自動反映し、結果をメールする。

金銭・ポリシーが絡む提案は絶対に自動反映しない（apply.py が構造的に許可しない）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.common.notify import send_alert_email
from src.mtg import roles
from src.mtg.apply import apply_all
from src.mtg.client import call_role
from src.mtg.context import STATE_DIR, gather_context

JST = timezone(timedelta(hours=9))
_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class MtgResult:
    date: str
    transcripts: dict[str, str] = field(default_factory=dict)
    coordinator_json: dict | None = None
    apply_results: list[str] = field(default_factory=list)
    error: str | None = None


def _extract_json(text: str) -> dict | None:
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def run() -> MtgResult:
    date = datetime.now(JST).date().isoformat()
    result = MtgResult(date=date)

    context = gather_context()

    analyst_out = call_role(roles.ANALYST_SYSTEM, context)
    result.transcripts["analyst"] = analyst_out

    researcher_out = call_role(roles.RESEARCHER_SYSTEM, context, with_web_search=True)
    result.transcripts["researcher"] = researcher_out

    marketer_input = (
        f"{context}\n\n## analystの分析\n{analyst_out}\n\n## researcherの調査\n{researcher_out}"
    )
    marketer_out = call_role(roles.MARKETER_SYSTEM, marketer_input)
    result.transcripts["marketer"] = marketer_out

    critic_input = f"## marketerの提案\n{marketer_out}\n\n## 参考データ\n{context}"
    critic_out = call_role(roles.CRITIC_SYSTEM, critic_input)
    result.transcripts["critic"] = critic_out

    coordinator_input = (
        f"## analyst\n{analyst_out}\n\n## researcher\n{researcher_out}\n\n"
        f"## marketer\n{marketer_out}\n\n## critic\n{critic_out}"
    )
    coordinator_out = call_role(roles.COORDINATOR_SYSTEM, coordinator_input, max_tokens=4000)
    result.transcripts["coordinator"] = coordinator_out

    parsed = _extract_json(coordinator_out)
    result.coordinator_json = parsed
    if parsed is None:
        result.error = "coordinatorの出力からJSONを抽出できなかった（自動反映はスキップ）"
        return result

    auto_apply = parsed.get("auto_apply") or []
    result.apply_results = apply_all(auto_apply)
    return result


def _write_log(result: MtgResult) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"mtg-{result.date}.json"
    path.write_text(
        json.dumps(
            {
                "date": result.date,
                "transcripts": result.transcripts,
                "coordinator_json": result.coordinator_json,
                "apply_results": result.apply_results,
                "error": result.error,
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _email_body(result: MtgResult) -> str:
    lines = [f"エージェントMTG {result.date}", ""]
    if result.error:
        lines.append(f"⚠ {result.error}")
        lines.append("")
    if result.coordinator_json:
        lines.append(f"■ 結論: {result.coordinator_json.get('headline', '(headline無し)')}")
        lines.append("")
    lines.append("■ 統括の報告")
    lines.append(result.transcripts.get("coordinator", "(無し)"))
    lines.append("")
    if result.apply_results:
        lines.append("■ 自動反映した内容")
        lines.extend(f"  - {r}" for r in result.apply_results)
        lines.append("")
    needs_approval = (result.coordinator_json or {}).get("needs_user_approval") or []
    if needs_approval:
        lines.append("■ 要ユーザー承認（自動実行していません）")
        for item in needs_approval:
            cost = item.get("estimated_cost_jpy_per_month", 0)
            lines.append(f"  - {item.get('description', '')}（月額目安: ¥{cost:,.0f}）")
        lines.append("")
    lines.append("--- 各役職の全文 ---")
    for role_name in ("analyst", "researcher", "marketer", "critic"):
        lines.append(f"\n## {role_name}\n{result.transcripts.get(role_name, '(無し)')}")
    return "\n".join(lines)


def run_and_report() -> MtgResult:
    result = run()
    log_path = _write_log(result)
    print(f"[agent-mtg] wrote {log_path}")
    subject = f"[AI動画自動投稿] エージェントMTG {result.date}"
    if result.coordinator_json:
        subject += f" - {result.coordinator_json.get('headline', '')[:40]}"
    sent = send_alert_email(subject, _email_body(result))
    print(f"[agent-mtg] メール送信: {'成功' if sent else '失敗/未設定'}")
    if result.apply_results:
        for r in result.apply_results:
            print(f"[agent-mtg] {r}")
    return result
