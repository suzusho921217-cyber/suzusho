"""コアデータモデル。

設計書 v1.0 の §6(企画モデル) / §10(Sheets DB) / §8(ポリシー) に対応。
ここは「構造」を固定する層。実処理は各サブモジュールに置く。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# --- 列挙 -------------------------------------------------------------------

class Brand(str, Enum):
    CAT = "cat"
    DOG = "dog"
    ADULT = "adult"


class Platform(str, Enum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    X = "x"


class ExperimentFlag(str, Enum):
    EXPLOIT = "exploit"
    EXPLORE = "explore"


class PolicyRisk(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class PolicyDecision(str, Enum):
    """§8 ポリシーエンジンの判定結果。"""
    PASS = "PASS"
    REWRITE = "REWRITE"
    REGENERATE = "REGENERATE"
    SKIP_PLATFORM = "SKIP_PLATFORM"
    HOLD = "HOLD"


class PostStatus(str, Enum):
    PLANNED = "PLANNED"
    GENERATING = "GENERATING"
    GENERATED = "GENERATED"
    QUALITY_FAILED = "QUALITY_FAILED"
    POLICY_HOLD = "POLICY_HOLD"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class GenerationStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


# --- 企画・生成 ------------------------------------------------------------

@dataclass
class ContentPlan:
    """§6 コンテンツ企画モデル。1 マスター動画 = 1 ContentPlan。"""
    plan_id: str
    date: str                       # YYYY-MM-DD（配分対象日）
    brand: Brand
    concept_tag: str                # 違和感 / かわいい / 驚き / POV / fashion ...
    hook_type: str                  # 0.5秒異常 / 視線誘導 / 突然の動き ...
    character_id: str               # CAT_001 / DOG_001 / ADULT_001
    reality_level: int              # 1-5
    oddity_level: int               # 1-5
    duration_target_sec: int        # 6-15（実績で媒体別に最適化）
    experiment_flag: ExperimentFlag
    policy_risk: PolicyRisk
    prompt_version: str
    prompt_text: str | None = None
    target_platforms: list[Platform] = field(default_factory=list)
    notes: str = ""


@dataclass
class GenerationJob:
    """§12 動画生成ジョブ。poll_generation ワークフローで完了確認する。"""
    job_id: str
    plan_id: str
    provider: str
    external_job_id: str | None = None
    status: GenerationStatus = GenerationStatus.QUEUED
    attempt: int = 1
    max_attempts: int = 3           # 初期値: リトライ2回まで
    video_url: str | None = None
    local_path: str | None = None
    cost_jpy: float = 0.0
    error: str | None = None
    submitted_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class QualityResult:
    """§12 Quality Gate の判定。"""
    passed: bool
    reasons: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)


@dataclass
class PolicyResult:
    """§8 媒体別ポリシー判定の結果。"""
    platform: Platform
    decision: PolicyDecision
    policy_version: str
    reasons: list[str] = field(default_factory=list)
    caption_override: str | None = None
    tags_override: list[str] | None = None


# --- 投稿・成績（§10 Sheets DB に対応）------------------------------------

@dataclass
class Post:
    """§10.1 投稿DB: 1 投稿 × 1 媒体 = 1 行。

    冪等キー: (master_video_id, platform, account_id) — §15。
    """
    post_key: str
    master_video_id: str
    brand: Brand
    platform: Platform
    account_id: str
    concept_tag: str
    hook_type: str
    character_id: str
    duration_sec: int
    oddity_level: int
    prompt_version: str
    generation_cost_jpy: float
    policy_version: str
    policy_result: PolicyDecision
    status: PostStatus
    reality_level: int | None = None    # §6 企画モデル由来（勝ちタグの粒度に使う §11）
    published_at: datetime | None = None
    platform_post_id: str | None = None


@dataclass
class PerformanceSnapshot:
    """§10.2 パフォーマンスDB。snapshot は 24h / 72h / 7d / latest。

    媒体で取得できない指標は None のままにし、スコア計算側で重みを
    再正規化する（§11）。
    """
    post_key: str
    snapshot: str
    collected_at: datetime
    views: int | None = None
    engaged_views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    impressions: int | None = None
    avg_watch_sec: float | None = None
    completion_rate: float | None = None
    followers_before: int | None = None
    followers_after: int | None = None
    revenue_jpy: float | None = None

    # 派生指標（分母が 0/None なら None）
    def rate(self, numerator: int | None) -> float | None:
        if not self.views or numerator is None:
            return None
        return numerator / self.views


@dataclass
class AccountDaily:
    """§10.3 アカウント日次DB。"""
    date: str
    brand: Brand
    platform: Platform
    account_id: str
    followers: int | None = None
    daily_views: int | None = None
    daily_posts: int = 0
    daily_revenue_jpy: float = 0.0
    daily_api_cost_jpy: float = 0.0
    warnings: int = 0
    status: str = "ACTIVE"          # ACTIVE / HOLD / STOP


# --- 配分（§11 学習・自動配分）------------------------------------------

@dataclass
class BrandAllocation:
    """1 ブランドに割り当てる当日枠の内訳。"""
    brand: Brand
    total: int
    exploit: int
    explore: int


@dataclass
class DailyAllocation:
    """§11 当日 6 枠のブランド別・exploit/explore 配分。

    mode:
      - "equal"        … ブートストラップ（勝ちパターン未確立）。均等割り・全 explore。
      - "performance"  … 実績連動。exploit をブランド別スコアに比例配分。
    """
    total_slots: int
    mode: str
    brands: list[BrandAllocation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def by_brand(self, brand: Brand) -> BrandAllocation | None:
        for b in self.brands:
            if b.brand == brand:
                return b
        return None
