"""Planner: 当日の企画生成と配分（§6 §11）。

plan_daily.yml が1日1回呼ぶ。前日までの成績（learning の出力）を受け取り、
1日6本の枠を Exploit 70% / Explore 30%、ブランド上限 60% の制約で割り当てる。

このモジュールの実装状況:
  - next_day_allocation … 実装済み（枠数の配分のみ。純粋関数）
  - build_daily_plan    … 実装済み（配分＋勝ちタグ → 具体的な ContentPlan 6件）
  - render_prompt       … 実装済み（ContentPlan＋キャラ定義 → 日本語プロンプト）
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.common.models import (
    Brand,
    BrandAllocation,
    ContentPlan,
    DailyAllocation,
    ExperimentFlag,
    Platform,
    PolicyRisk,
)


def _coerce_brand(value: object) -> Brand | None:
    if isinstance(value, Brand):
        return value
    try:
        return Brand(str(value).lower())
    except ValueError:
        return None


def _largest_remainder(weights: dict[Brand, float], total: int) -> dict[Brand, int]:
    """比例配分を整数化する（最大剰余法）。weights の合計は正であること。"""
    order = sorted(weights)  # 決定的なタイブレーク（ブランド名順）
    wsum = sum(weights.values())
    raw = {b: total * weights[b] / wsum for b in order}
    out = {b: math.floor(raw[b]) for b in order}
    remainder = total - sum(out.values())
    for b in sorted(order, key=lambda b: (raw[b] - math.floor(raw[b]), b), reverse=True)[:remainder]:
        out[b] += 1
    return out


def _spread_evenly(brands: list[Brand], total: int) -> dict[Brand, int]:
    """total を brands に均等割りする（余りはブランド名順で先頭へ）。"""
    order = sorted(brands)
    q, r = divmod(total, len(order))
    return {b: q + (1 if i < r else 0) for i, b in enumerate(order)}


def next_day_allocation(
    winning_tags: list[dict],
    config: dict,
    enabled_brands: list[Brand],
) -> DailyAllocation:
    """翌日のブランド別・exploit/explore 配分を返す（§11）。

    Args:
        winning_tags: learning.extract_winning_tags の出力。各要素は少なくとも
            ``{"brand": <str|Brand>, "score": <float>}`` を持つ。空ならブートストラップ。
        config: ``config/scoring.yaml`` の ``allocation`` セクション。
            使うキー: total_daily_slots / exploit_ratio / brand_max_ratio /
            brand_min_explore_posts。
        enabled_brands: ``config/brands.yaml`` で enabled=true のブランド。

    制約:
        - 合計は total_daily_slots 固定
        - 1 ブランドの total ≤ floor(total_daily_slots * brand_max_ratio)
        - 各有効ブランドに explore を最低 brand_min_explore_posts 確保
    """
    total = int(config["total_daily_slots"])
    exploit_ratio = float(config.get("exploit_ratio", 0.70))
    brand_cap = math.floor(total * float(config.get("brand_max_ratio", 0.60)))
    min_explore = int(config.get("brand_min_explore_posts", 1))

    brands = sorted(set(enabled_brands))
    warnings: list[str] = []

    if not brands:
        return DailyAllocation(total, "equal", [], ["有効ブランドが 0 件"])

    n = len(brands)
    if brand_cap * n < total:
        warnings.append(
            f"brand_max_ratio が厳しすぎ: cap({brand_cap})×{n}ブランド < 総枠({total})。"
            "上限を緩めて配分する。"
        )
        brand_cap = math.ceil(total / n)

    # --- ブートストラップ: 勝ちパターン未確立なら均等割り・全 explore ---
    if not winning_tags:
        per = _spread_evenly(brands, total)
        allocs = [
            BrandAllocation(brand=b, total=per[b], exploit=0, explore=per[b])
            for b in brands
        ]
        return DailyAllocation(total, "equal", allocs, warnings)

    # --- 実績モード ---
    # explore を先に確保（最低本数 × ブランド数を下回らせない）
    explore_total = total - round(total * exploit_ratio)
    explore_total = max(explore_total, n * min_explore)
    explore_total = min(explore_total, total)
    exploit_total = total - explore_total

    # ブランド別スコア合計
    scores: dict[Brand, float] = {b: 0.0 for b in brands}
    for wt in winning_tags:
        b = _coerce_brand(wt.get("brand"))
        if b in scores:
            scores[b] += max(0.0, float(wt.get("score", 0.0) or 0.0))

    if sum(scores.values()) <= 0.0:
        warnings.append("winning_tags のスコア合計が 0。exploit 枠を均等配分。")
        exploit = _spread_evenly(brands, exploit_total)
    else:
        exploit = _largest_remainder(scores, exploit_total)

    # explore: 最低本数 + 残りを均等
    extra = explore_total - n * min_explore
    explore = {b: min_explore for b in brands}
    for b, add in _spread_evenly(brands, max(0, extra)).items():
        explore[b] += add

    _enforce_cap(brands, exploit, explore, brand_cap, scores, warnings)

    allocs = [
        BrandAllocation(
            brand=b,
            total=exploit[b] + explore[b],
            exploit=exploit[b],
            explore=explore[b],
        )
        for b in brands
    ]
    return DailyAllocation(total, "performance", allocs, warnings)


def _enforce_cap(
    brands: list[Brand],
    exploit: dict[Brand, int],
    explore: dict[Brand, int],
    cap: int,
    scores: dict[Brand, float],
    warnings: list[str],
) -> None:
    """total(exploit+explore) が cap を超えるブランドの exploit を、
    余裕のある高スコアのブランドへ移す。explore（最低本数）は動かさない。"""
    for _ in range(sum(exploit.values()) + 1):
        over = [b for b in brands if exploit[b] + explore[b] > cap]
        if not over:
            return
        src = max(over, key=lambda b: (exploit[b] + explore[b] - cap, b))
        room = [b for b in brands if b != src and exploit[b] + explore[b] < cap]
        if not room:
            warnings.append(f"{src.value}: 上限超過分を移せる枠がない")
            return
        dst = max(room, key=lambda b: (scores.get(b, 0.0), b))
        exploit[src] -= 1
        exploit[dst] += 1


def _date_seed(date: str) -> int:
    """YYYY-MM-DD から決定的な回転オフセットを作る（日ごとに explore の選択を変える）。"""
    digits = "".join(ch for ch in date if ch.isdigit())
    return int(digits) if digits else 0


def _pick_in_range(rng: object, i: int) -> int:
    """[lo, hi]（または単一値）から i 番目を巡回して選ぶ。"""
    if isinstance(rng, (list, tuple)) and rng:
        lo, hi = int(rng[0]), int(rng[-1])
    else:
        lo = hi = int(rng)  # type: ignore[arg-type]
    span = hi - lo + 1
    return lo if span <= 1 else lo + (i % span)


def _brand_pool(planning_cfg: dict, brand: Brand) -> dict:
    pools = planning_cfg.get("brands", {})
    if brand.value not in pools:
        raise KeyError(f"config/planning.yaml に brand '{brand.value}' の企画プールがない")
    return pools[brand.value]


def _explore_combos(pool: dict, brand_winning: list[dict], seed: int) -> list[tuple[str, str]]:
    """(concept_tag, hook_type) の候補列。勝ちタグと重複せず、concept をラウンドロビンで混ぜる。

    連続する枠が同じ concept のバリエーションにならないよう、concept を回しながら並べる。
    同じ seed（＝同じ日）なら決定的。
    """
    concepts = sorted(pool["concept_tags"])
    hooks = sorted(pool["hook_types"])
    used = {(w.get("concept_tag"), w.get("hook_type")) for w in brand_winning}

    queues = {c: [(c, h) for h in hooks if (c, h) not in used] for c in concepts}
    if not any(queues.values()):  # 全部 winning に取られていたら重複を許して埋める
        queues = {c: [(c, h) for h in hooks] for c in concepts}

    off = seed % len(concepts)
    order = concepts[off:] + concepts[:off]
    out: list[tuple[str, str]] = []
    while any(queues[c] for c in order):
        for c in order:
            if queues[c]:
                out.append(queues[c].pop(0))
    return out


def _brand_characters(pool: dict) -> tuple[str, ...]:
    """ブランドの品種プール。`characters`（複数）優先、無ければ `character_id`（単数）。"""
    chars = pool.get("characters")
    if chars:
        return tuple(str(c) for c in chars)
    return (str(pool.get("character_id", f"{pool.get('policy_risk', 'X')}_char")),)


@dataclass(frozen=True)
class _PlanCtx:
    """1 ブランド分の企画生成に必要な確定値。"""
    date: str
    brand: Brand
    pool: dict
    risk: PolicyRisk
    characters: tuple[str, ...]
    prompt_version: str
    duration_range: object
    platforms: list[Platform]

    def _level(self, key: str, nonce: int) -> int:
        return _pick_in_range(self.pool[key], nonce)

    def pick_character(self, nonce: int) -> str:
        """explore 枠の品種を決定的に巡回選択する。"""
        return self.characters[nonce % len(self.characters)]


def _explore_plan(
    ctx: _PlanCtx, seq: int, combo: tuple[str, str], nonce: int, *,
    flag: ExperimentFlag, notes: str,
) -> ContentPlan:
    concept_tag, hook_type = combo
    return ContentPlan(
        plan_id=f"{ctx.date}-{ctx.brand.value}-{seq:02d}",
        date=ctx.date,
        brand=ctx.brand,
        concept_tag=concept_tag,
        hook_type=hook_type,
        character_id=ctx.pick_character(nonce),
        reality_level=ctx._level("reality_level", nonce),
        oddity_level=ctx._level("oddity_level", nonce),
        duration_target_sec=_pick_in_range(ctx.duration_range, nonce),
        experiment_flag=flag,
        policy_risk=ctx.risk,
        prompt_version=ctx.prompt_version,
        target_platforms=list(ctx.platforms),
        notes=notes,
    )


def _exploit_plan(
    ctx: _PlanCtx, seq: int, wt: dict, fallback_combo: tuple[str, str], nonce: int,
) -> ContentPlan:
    return ContentPlan(
        plan_id=f"{ctx.date}-{ctx.brand.value}-{seq:02d}",
        date=ctx.date,
        brand=ctx.brand,
        concept_tag=str(wt.get("concept_tag", fallback_combo[0])),
        hook_type=str(wt.get("hook_type", fallback_combo[1])),
        character_id=str(wt.get("character_id", ctx.pick_character(nonce))),
        reality_level=int(wt.get("reality_level", ctx._level("reality_level", nonce))),
        oddity_level=int(wt.get("oddity_level", ctx._level("oddity_level", nonce))),
        duration_target_sec=int(
            wt.get("duration_target_sec", _pick_in_range(ctx.duration_range, nonce))
        ),
        experiment_flag=ExperimentFlag.EXPLOIT,
        policy_risk=ctx.risk,
        prompt_version=str(wt.get("prompt_version", ctx.prompt_version)),
        target_platforms=list(ctx.platforms),
        notes=(
            f"exploit: winning_tag score={float(wt.get('score', 0.0) or 0.0):.3f}"
            f" ({wt.get('platform', '?')})"
        ),
    )


def build_daily_plan(
    date: str,
    allocation: DailyAllocation,
    winning_tags: list[dict],
    planning_cfg: dict,
    target_platforms: list[Platform],
) -> list[ContentPlan]:
    """当日の ContentPlan リスト（配分どおりの本数）を返す（§6 §11）。

    Args:
        date: 対象日 YYYY-MM-DD
        allocation: next_day_allocation が出したブランド別・exploit/explore の配分
        winning_tags: learning.extract_winning_tags の出力（10キー形式）。exploit 枠に使う
        planning_cfg: ``config/planning.yaml``（explore 枠の企画候補プール）
        target_platforms: 当日投稿対象の媒体（platforms.yaml ∩ brands.yaml を呼び出し側で解決）

    exploit 枠はブランドの winning_tags を score 降順で採用。不足分は explore と同じ
    生成にフォールバックし ``notes`` に記録する。explore 枠は未使用の
    (concept_tag, hook_type) 組み合わせを日付シードで巡回選択する。
    """
    defaults = planning_cfg.get("defaults", {})
    prompt_version_default = str(defaults.get("prompt_version", "v1"))
    duration_range = defaults.get("duration_target_sec", [6, 15])
    seed = _date_seed(date)

    plans: list[ContentPlan] = []
    seq = 0

    for ba in allocation.brands:
        brand = _coerce_brand(ba.brand) or ba.brand
        pool = _brand_pool(planning_cfg, brand)
        ctx = _PlanCtx(
            date=date,
            brand=brand,
            pool=pool,
            risk=PolicyRisk(str(pool.get("policy_risk", "LOW"))),
            characters=_brand_characters(pool),
            prompt_version=prompt_version_default,
            duration_range=duration_range,
            platforms=list(target_platforms),
        )

        brand_winning = sorted(
            (w for w in winning_tags if _coerce_brand(w.get("brand")) == brand),
            key=lambda w: float(w.get("score", 0.0) or 0.0),
            reverse=True,
        )
        combos = _explore_combos(pool, brand_winning, seed)
        cursor = 0  # explore 系の企画で使った combo 数

        # --- exploit 枠: winning_tags を score 降順で採用。不足分は新規企画で補填 ---
        for k in range(ba.exploit):
            seq += 1
            if k < len(brand_winning):
                plans.append(_exploit_plan(ctx, seq, brand_winning[k], combos[0], k))
            else:
                plans.append(_explore_plan(
                    ctx, seq, combos[cursor % len(combos)], cursor,
                    flag=ExperimentFlag.EXPLOIT,
                    notes="exploit: 勝ちタグ不足のため新規企画で補填",
                ))
                cursor += 1

        # --- explore 枠: 未使用の (concept_tag, hook_type) を巡回選択 ---
        for _ in range(ba.explore):
            seq += 1
            plans.append(_explore_plan(
                ctx, seq, combos[cursor % len(combos)], cursor,
                flag=ExperimentFlag.EXPLORE,
                notes="explore: 未使用の企画タグ×フック組み合わせ",
            ))
            cursor += 1

    return plans


# concept_tag -> (フリ, 展開, オチ) の3ビート。render_prompt がこの順で情景を組む。
_CONCEPT_BEATS = {
    "二段オチ": (
        "何か（段差を登る／箱に入る）に挑もうとする",
        "案の定うまくいかず、ずるっと失敗する",
        "と思いきや、勢い余って別の場所にすぽっと綺麗に収まる。本人は満足げ",
    ),
    "そっくりさん": (
        "テーブルに置かれた丸いパン（またはぬいぐるみ）の隣に来る",
        "同じ体勢で丸くなり、色も形もそっくりになる",
        "一瞬どっちがどっちか分からなくなり、片方だけ耳がぴくっと動く",
    ),
    "予想外のサイズ感": (
        "極端に小さい子が、自分の何倍もある物（巨大なクッション／特大の器）に近づく",
        "よじ登ろう・入ろうとしてスケール差に翻弄される",
        "結局その中に完全に埋もれて見えなくなり、耳だけ出ている",
    ),
    "気づいたら": (
        "自分の世界に没頭して何かしている",
        "ふとカメラの存在に気づき、動きをぴたっと止める",
        "何事もなかったかのように、わざとらしく毛づくろいを始める",
    ),
    "タイミングの奇跡": (
        "何気なく歩いている／座っている",
        "上から物が落ちてくる、扉が動く等のタイミングが訪れる",
        "偶然ジャストのタイミングで完璧に収まる／避ける。狙ったかのよう",
    ),
    "昼寝あけドラマ": (
        "気持ちよさそうに熟睡している",
        "はっと目を覚まし、寝ぼけたまま立とうとして壮大にバランスを崩す",
        "盛大に転んだあと、何事もなかった顔で同じ場所に丸まり二度寝",
    ),
    "人間くさい": (
        "人間みたいな所帯じみた状況にいる（机に向かう／ソファでぐったり）",
        "電卓を前足で叩く、深いため息をつく等、妙に人間くさい仕草をする",
        "力尽きて突っ伏す。まるで働きすぎた大人",
    ),
    "全力空回り": (
        "何かに向かって全力で走り出す",
        "足が空転して前に進まず、その場で高速に足踏み",
        "急にグリップが効いて明後日の方向に吹っ飛んでいく",
    ),
    "予想させる": (
        "何か（狭い所に入る／高い所に飛び移る）に挑もうと構える",
        "助走をつけて、まさに跳ぼうとする瞬間まで見せる",
        "結果は見せずにフッと切る。見た人が『どうなった!?』と気になる所で終わる",
    ),
    "あるある": (
        "猫飼いなら誰もが見たことのある行動を始める（箱を見たら入る／PC の前に鎮座／新聞紙の上でだけ寝る）",
        "その『あるある』をたっぷり、共感できる細かさで見せる",
        "最後にこっちを見て『何か文句ある?』という顔をする",
    ),
}

# 旧・単発概念（後方互換）
_CONCEPT_HINT = {
    "かわいい": "愛らしい仕草・表情をとにかく主役にする",
    "驚き": "予想外の動き・出来事で見る人を一瞬驚かせる",
    "POV": "視聴者目線（一人称視点）で、キャラと目が合う構図",
    "違和感": "一見ふつうだが、よく見ると1つだけ小さくおかしい点を仕込む",
    "fashion": "衣装・スタイリングを主役にした構図",
    "日常": "生活の何気ない1シーンを自然に切り取る",
}

_HOOK_HINT = {
    "いきなりドアップ": "開始0.5秒で顔面のドアップ、そこからスッと引く",
    "カメラ目線でにゃっ": "冒頭でカメラと目が合い、小さく一声鳴く",
    "カメラ目線でワンっ": "冒頭でカメラと目が合い、小さく一声吠える",
    "コテンと倒れる": "登場した直後にぱたっと横に倒れて力尽きる",
    "ぬっと登場": "画面の外からゆっくり顔だけ出てくる",
    "スローで転ぶ": "スローモーションでゆっくり転ぶ一部始終",
    "0.5秒異常": "冒頭0.5秒だけ映る一瞬の違和感",
    "視線誘導": "被写体の視線で、見せたいものへ視聴者の目を誘導する",
    "突然の動き": "数秒の静止から急に大きく動く",
}

_REALITY = {
    1: "強くデフォルメされたイラスト調",
    2: "アニメ・CG調",
    3: "リアル寄りのCG",
    4: "実写に近い（細部にわずかなCG感）",
    5: "実写と見分けがつかない",
}

_ODDITY = {
    1: "違和感なし。完全に自然",
    2: "ごくわずかな違和感を1つだけ",
    3: "はっきり気づく違和感を1つ",
    4: "複数の奇妙な点。非現実的",
    5: "明確にシュール・異常",
}


def _clamp(v: int, lo: int = 1, hi: int = 5) -> int:
    return max(lo, min(hi, v))


def render_prompt(
    plan: ContentPlan,
    character: dict,
    brand_banned: list[str] | None = None,
) -> str:
    """ContentPlan とキャラ定義から日本語の動画生成プロンプトを組み立てる（§12）。

    Args:
        plan: build_daily_plan が出した企画1本
        character: ``config/characters.yaml`` の該当キャラ dict
            （appearance / personality / voice / world / forbidden / display_name）
        brand_banned: ``config/brands.yaml`` の該当ブランド ``banned_expressions``

    戻り値はそのまま ``plan.prompt_text`` に入れられる文字列。媒体別の AI 開示表記や
    キャプションはここでは扱わない（policy / publisher 側）。
    """
    name = str(character.get("display_name") or f"{plan.brand.value}_character")
    reality = _REALITY[_clamp(plan.reality_level)]
    hook = _HOOK_HINT.get(plan.hook_type, plan.hook_type)
    species = {"cat": "子猫", "dog": "子犬"}.get(plan.brand.value, "動物")

    beats = _CONCEPT_BEATS.get(plan.concept_tag)
    if beats:
        scene = (
            f"フリ: {beats[0]}。 展開: {beats[1]}。 オチ: {beats[2]}。"
            f" この「オチ」が一番の見どころなので、そこに向けて間を作る"
        )
    else:
        scene = _CONCEPT_HINT.get(plan.concept_tag, plan.concept_tag)

    forbidden: list[str] = []
    _extras = ["画面内の文字・字幕・テロップ", "実在ブランドのロゴ", "ウォーターマーク",
               "人間の顔をはっきり映す"]
    for item in list(character.get("forbidden") or []) + list(brand_banned or []) + _extras:
        if item and item not in forbidden:
            forbidden.append(item)

    # Veo 向けに、箇条書きでなく流れる情景描写にする。
    body = (
        f"主役は「{name}」という{species}。{character.get('appearance', '')} "
        f"性格は{character.get('personality', '')} "
        f"舞台は{character.get('world', '')} "
        f"内容（{plan.concept_tag}）: {scene}。 "
        f"冒頭のつかみ（{plan.hook_type}）: {hook}。 "
        f"映像は{reality}。手持ち風にわずかに揺れるカメラで被写体に寄り、やわらかい自然光。"
        f"BGMなし、生活音と小さな鳴き声のみ。"
        f"縦型9:16、約{plan.duration_target_sec}秒、被写体ひとりに集中。"
    )
    body += f" 違和感の度合い: {_ODDITY[_clamp(plan.oddity_level)]}。"

    text = body + f"\n\n【禁止】{'、'.join(forbidden)}"
    if plan.notes:
        text += f"\n【備考】{plan.notes}"
    return text
