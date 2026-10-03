"""テロップの試し焼き（投稿はしない）。直近の生成動画にテロップを付け、静止画を preview/ に書き出す。

本番に入れる前に見た目を確認するための道具。`.github/workflows/telop_preview.yml` から手動で使う。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src.common.models import Platform
from src.media import captions
from src.media.processor import MediaVariantSpec, make_variant, normalize_master

STATE = Path(__file__).resolve().parents[2] / ".state"
OUT = Path(__file__).resolve().parents[2] / "preview"


def main() -> int:
    jobs_files = sorted(STATE.glob("jobs-*.json"))
    if not jobs_files:
        print("jobs が無い")
        return 1
    payload = json.loads(jobs_files[-1].read_text(encoding="utf-8"))
    date = payload["date"]
    plans = {p["plan_id"]: p for p in json.loads((STATE / f"plan-{date}.json").read_text())["plans"]}
    OUT.mkdir(exist_ok=True)
    for j in payload["jobs"]:
        src = (j.get("local_path") or "").removeprefix("file://")
        p = plans.get(j["plan_id"])
        if not p or not Path(src).exists():
            continue
        master = normalize_master(src, str(OUT / f"{p['plan_id']}-master.mp4"))
        caps = captions.suggest(master, concept=p["concept_tag"], hook=p["hook_type"],
                                duration=p["duration_target_sec"]) or []
        print(p["plan_id"], json.dumps(caps, ensure_ascii=False))
        out = make_variant(master, MediaVariantSpec(Platform.YOUTUBE, p["duration_target_sec"],
                                                    captions=caps), str(OUT / f"{p['plan_id']}.mp4"))
        for i, c in enumerate(caps or [{"start": 1, "end": 2}]):
            t = (float(c["start"]) + float(c["end"])) / 2
            subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", out, "-frames:v", "1",
                            "-vf", "scale=540:-1", str(OUT / f"{p['plan_id']}-{i}.png")],
                           capture_output=True, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
