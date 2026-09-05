# ai_media_automation

AIショートメディア完全自動運用基盤（設計書 v1.0 の実装）。
企画 → AI動画生成 → ポリシー判定 → 媒体別加工 → 自動投稿 → 成績回収 → 勝ちパターン分析 → 翌日配分。

設計書: `docs/ai_media_automation_design_v1.docx`

## 現状

**Phase: 頭脳（Planner ↔ Learning ループ）完成。外部連携は未着手。**

実装済み（Planner 一式 ＋ Learning ＋ Policy プロンプト段階）:
- `planner.next_day_allocation` … 前日成績（勝ちタグ）から当日6枠のブランド別 exploit/explore 配分（§11）
- `planner.build_daily_plan` … 配分＋勝ちタグ → 具体的な `ContentPlan` 6本（§6）
- `planner.render_prompt` … `ContentPlan`＋キャラ定義 → 日本語の動画生成プロンプト（§12）
- `learning.compute_score` … 1投稿のスコア。欠損指標は重みを再正規化、revenue/roi/原価効率は母集団最大で 0〜1 化（§11）
- `learning.extract_winning_tags` … 企画タグ×媒体ごとに直近7日・30日のスコアを中央値で束ね重み付き平均。10キー形式の勝ちタグを出力（§2 §11）
- `learning.next_day_allocation` … planner 版の再エクスポート（配分本体は planner 側）
- `policy.check_prompt` … プロンプト段階のポリシー判定（§7 1段目）。`config/policy_rules/` の版付きルール
- `policy.is_policy_stale` … `policy_sync` の版差分検知を参照（§8）
- `policy_sync.check_feeds` / `cli policy-sync` … 媒体の RSS/Atom フィード監視。新着検知で該当媒体を stale・非0終了（§8 §20）
- `guardrails.check_budget` … §13 automatic_stop。各上限の95%到達で新規生成停止（STOP_NEW_GENERATION まで）
- `guardrails.check_kill_switch` … §14 トリガー表。異常シグナル → brand×platform HOLD / 新規生成STOP / 全停止
- `cli plan-daily` … config 読込 → 配分 → 企画 → プロンプト付与 → ポリシー事前判定 → `.state/plan-<date>.json` 出力
- `cli daily-learning` … 成績 JSON → スコア → 勝ちタグ → `.state/winning_tags.json`（plan-daily がこれを食う＝ループが閉じる）
- `cli kill-switch` … 予算/シグナル JSON → 停止判定 → `.state/guard.json`。1つでも非 ALLOW なら exit 3
- `generation.pipeline` + `cli generate` … `config/budget.yaml` の上限と `.state/spend.json`（実績の積み上げ・月/日で自動リセット）を突き合わせ、95%到達で以降を投入しない → provider.submit → `.state/jobs-<date>.json`。投入分の概算費を spend.json に加算
- `generation.pipeline` + `cli poll-generation` … 生成中ジョブを poll → `quality.inspect`（尺・9:16 の機械チェック）→ NG は再生成（最大2回）→ 超過で SKIP
- 動画生成 provider: `mock`（はりぼて）と `veo`（Google Gemini API / Veo 3.1）。`config/generation.yaml` の `provider:` か `VIDEO_PROVIDER` env で切替
- `publishers.pipeline` + `cli publish` … 冪等キー照会（§15）→ policy_sync stale 確認 → guardrails → `check_prompt` 再判定（REWRITE はキャプション上書き）→ AI 開示ラベル → 投稿。`PUBLISH_MODE=dryrun`（既定）は投稿せず決定的 ID を返す
- `sheets.client` … 管理DB の窓口（§10）。`LocalStore`（`.state/db/*.json`）が既定。投稿DB/パフォーマンスDB/アカウント日次DB の行⇔モデル変換。`publish` は投稿レコードをここに upsert（冪等キーも DB から復元）
- `metrics.collector` + `cli metrics` … 公開済み投稿を経過時間で 24h/72h/7d/latest に振り分け、`publisher.fetch_metrics` の生レスポンスを媒体差を吸収して `PerformanceSnapshot` に正規化 → DB に append → `.state/performance.json` を書く（daily-learning がそれを読む＝**成績→学習→翌日企画の輪が閉じた**）
- `media.processor` + `cli media` … マスターを 9:16（1080x1920）へ scale+pad、`loudnorm` で音量正規化、媒体別に尺トリム → `.state/media-<date>.json`。`publish` は派生があればそれを使う。**ffmpeg 不在ならスキップ**（`publish` は元動画にフォールバック）
- 字幕焼き込み / SE / BGM は `MediaVariantSpec` に受け口だけ（素材と設計詰めが必要）

- `publishers.youtube` … `videos.insert` で投稿。冪等性は post_key を非公開タグ
  （`pk:<post_key>`）として埋め込み `search.list(forMine=True)` で照会。AI開示は
  `status.containsSyntheticMedia`（要 `youtube.force-ssl` スコープ。無いとエラー無く
  無視される。説明欄への明記も二重対策として実施）。指標は `videos.list`（views/
  likes/comments、常時取得可）＋ YouTube Analytics API（engagedViews等、未収益化
  チャンネルでは失敗しうるので基本指標のみへ自動degrade）。
  **2026-09-05 Dog Momentsチャンネルで実キー投稿・確認済み**
  （https://www.youtube.com/shorts/mQmeSDUdXvw ）。Cat Momentsは審査待ちで未検証。
- `publishers.instagram` … Instagram Graph API（Instagram Login方式）。動画は
  「一般公開HTTPS URLから取得」方式(`video_url`)のため、GCSの非公開バケットへ
  一時アップロード→v4署名付きURL（既定1時間）で取得させる（`GCS_BUCKET_NAME`）。
  冪等性はキャプション末尾にゼロ幅スペースで挟んだ `pk:<post_key>` を埋め込み、
  `/media` 一覧から照会。AI開示はAPIに自己申告フィールドが無いためキャプション
  本文に明記。指標は `/insights`（likes/comments/shares/saved/reach/views。
  `plays` は廃止済みで `views` が正しいメトリック名 — 実API確認済み）。
  **2026-09-05 Dog Momentsで実キー投稿・確認済み**
  （https://www.instagram.com/reel/Dc5r71VgisH/ ）。Cat Momentsは未整備。

未実装スタブ: `policy.check_video`（動画段階）/ publishers の tiktok・x（youtube・instagramは実装済み）。
`metrics` が成績を集めて `daily-learning` / `kill-switch` の入力 JSON を作る所は未実装（今はサンプル JSON を手で与える）。
`quality.inspect` の「AI 破綻・不適切表現の検出」は vision LLM 待ち（`policy.check_video` と一緒に実装予定）。

- 動画生成プロバイダ: 未定。`mock` アダプタでE2Eを通す前提。
- 有効ブランド: cat / dog（adult は Phase 3 まで無効 = `config/brands.yaml`）。
- 有効媒体: youtube のみ（他は `config/platforms.yaml` で無効）。

## ディレクトリ

```
.github/workflows/   8本のワークフロー + _reusable.yml（§5）
src/
  cli.py             GitHub Actions からの単一エントリポイント
  common/            models.py（§6§10）/ config.py / guardrails.py（§13§14）
  planner/           企画生成・配分（§6§11）
  policy/            ポリシーエンジン（§7§8）
  generation/        base.py（Provider IF）/ providers/mock.py / quality.py（§12）
  media/             FFmpeg 媒体別加工（§12）
  publishers/        youtube/tiktok/instagram/x + base.py + registry.py（§9§15）
  metrics/           24h/72h/7d 回収（§10.2）
  learning/          スコア・勝ちタグ・翌日配分（§11）
  sheets/            管理DB 窓口（§10）
config/
  brands.yaml characters.yaml platforms.yaml scoring.yaml budget.yaml
  planning.yaml        explore 枠の企画候補プール（§6）
  policy_rules/       _common.yaml + <platform>.yaml（版付きポリシールール §8）
  policy_sync.yaml     監視フィード + 目視確認URL（§8 §20）
tests/
docs/                設計書 / platform_policies.md（4媒体ポリシー要約 §7 §8 §20）
```

## パイプライン（§4）

```
Scheduler → Planner → Policy Engine → Video Provider Adapter → Quality Gate
  → FFmpeg → [YouTube/TikTok/Instagram/X Adapter] → Google Sheets
  → Metrics Collector → Learning Engine → 翌日配分
```

## セットアップ

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest
python -m src.cli plan-daily --date 2026-08-31   # .state/plan-2026-08-31.json を出力
# 成績 JSON → 勝ちタグ（.state/winning_tags.json）:
python -m src.cli daily-learning --input .state/performance.json
# 勝ちタグがあると plan-daily は実績モード配分になる（自動で .state/winning_tags.json を読む）:
python -m src.cli plan-daily
# 予算消化・異常シグナルから停止判定（非 ALLOW で exit 3 = Actions 失敗通知）:
python -m src.cli kill-switch --input .state/guard_input.json
# 生成: plan の企画を投入 → 完了確認・品質判定（mock provider で E2E 可）:
python -m src.cli generate --date 2026-09-02
python -m src.cli poll-generation --date 2026-09-02
# 加工（ffmpeg 必須。無ければスキップ）:
python -m src.cli media --date 2026-09-02
# 投稿: 既定は dryrun（実際には投稿しない）。再実行しても二重投稿しない:
python -m src.cli publish --date 2026-09-02
# 成績回収 → .state/performance.json → 学習 → 翌日の企画（輪が閉じる）:
python -m src.cli metrics
python -m src.cli daily-learning
python -m src.cli plan-daily            # winning_tags があれば実績モード
```

mock/dryrun のまま
`plan-daily → generate → poll-generation → media → publish → metrics → daily-learning`
が一周する（外部の認証情報・アカウント不要。ffmpeg も無くて可。dryrun は決定的なダミー指標を返す）。

`daily-learning` の入力 JSON（`.state/performance.json`）は `metrics` が生成する:
`{"records": [{"post": {...企画タグ + generation_cost_jpy + published_at}, "snapshots": {"latest": {...}}}]}`。

## シークレット（§5）

APIキー・OAuth token・refresh token はコードにも Sheets にも保存しない。
GitHub Environments（`production`）の Secrets で管理し、`_reusable.yml` から env 注入。
必要なキー一覧は `config/platforms.yaml` の各 `secret_env` を参照。

## 原則（§8 §20）

- 審査・同意・表示要件を迂回する目的のブラウザRPAは使わない。公式APIのみ。
- API・ポリシー・収益化条件は実装時と運用中に公式情報を再確認する。
- ポリシー監視は違反回避の保護機構。差分検知したら旧ルールのまま投稿しない（HOLD）。

## 動画生成を本番（Veo）に切り替える

Veo は Google Gemini API 経由の **pay-as-you-go**（月額サブスク不要。`gemini-3.6-flash` と同じキー）。

1. `pip install google-genai`（`requirements.txt` に追加済み）
2. Google Cloud で Veo を使えるようにし、API キーを用意
3. 環境変数 `GEMINI_API_KEY`（無ければ `GOOGLE_API_KEY`）を設定。GitHub Actions は
   `production` Environment の Secret `GEMINI_API_KEY`
4. env `VIDEO_PROVIDER=veo`（推奨。`config/generation.yaml` の `provider:` は
   テストが `mock` 前提のため書き換えない。GitHub Actions では Environment 変数で注入）
5. `config/generation.yaml` の `veo.price_jpy_per_sec` を契約時の実単価に更新
   （Lite 1080p ≈ ¥12/s、画質不足なら `model: veo-3.1-fast-generate-preview` に変更）

Veo は 1本 **4/6/8 秒のみ**（企画の目標尺以上で最小を自動選択）。出力は音声付き・9:16 指定。

**実 API で確認済みの制約（Veo 3.1 Lite, 2026-09）:**
- `negative_prompt` 非対応 → `use_negative_prompt: false`（禁止節はプロンプト本文に残る）
- 1080p は 8 秒固定（6秒×1080p は 400）→ 既定は `720p`（¥8/s、6秒=¥48。柔軟＆安い）
- **extend（15秒化等）非対応** → 400 `"Video extension is not allowed for this model"`。
  `target_total_sec` は lite のままなら **0**（単発クリップ）固定。extend したいなら
  `model: veo-3.1-fast-generate-preview` 等に変更が必要（未検証・単価も上がる）。
  ※ `poll-generation` 側は extend 失敗を捕捉せず例外で落ちる（ジョブは RUNNING のまま残留）。
  config を直して再実行すれば base クリップ（生成済み・課金済み）はそのまま回収できる。
- 生成は 40〜60 秒。`.venv/bin/python -m pip install google-genai`（venv の `pip` 直叩きは壊れている）
- 少数テストは `python -m src.cli generate --limit 1`
- 2026-09-05: 実キーで plan-daily → generate → poll-generation を通し、720p/8秒/9:16の
  猫動画が実際に生成・DLされ目視確認済み（cat-01, ¥64, `.state/generation/`）。
  請求単価はCloud Consoleの実請求で別途確認要（現状 `price_jpy_per_sec: 8` は見積り一致のみ）。

## 次にやること（ユーザー側の準備が要る）

1. ✅ **動画生成API**: Veo に決定。実キーで smoke テスト完了(2026-09-05, plan-daily→generate→poll-generation→実動画DL確認)。
   残: Cloud Console の実請求で `price_jpy_per_sec` を最終確認。extend 非対応の例外処理は未修正(§次項参照)。
2. ✅ **Google Sheets 3DB**: 完了(2026-09-05)。スプレッドシート1本＋タブ3枚（投稿DB／
   パフォーマンスDB／アカウント日次DB）、見出しは日本語。専用サービスアカウント
   `sns-automation-sheets@gen-lang-client-0301308589.iam.gserviceaccount.com` を新規作成
   （鍵は `secrets/sheets-service-account.json`、gitignore済）。`sheets.SheetsStore` 実装済み、
   実シートでのread/write roundtrip確認済み。`.env` の `SHEETS_BACKEND=sheets` で有効化
   （未設定時は既定で `local`＝テストや初回セットアップはこれで良い）。
3. ✅ **GitHub private repo**: 完了(2026-09-05)。`suzusho921217-cyber/suzusho`。
   `production` Environment に Secrets/Variables 設定済み。CI（ruff+pytest）・
   `kill_switch`（毎時cron）とも動作確認済み。ガードレール発火時は Gmail アプリ
   パスワード経由でメール通知（`src/common/notify.py`）。
   ✅ **YouTube・Instagram**: Dog Momentsチャンネル/アカウントで実キー投稿・確認済み
   （2026-09-05）。Cat Momentsはチャンネル未作成（本人確認審査待ち）のため未整備。
   **残: TikTok・X の認証情報**。`publishers/{tiktok,x}.py` は未実装スタブのまま。
4. **Phase 0**: 4媒体のAPIで実際に取れる指標を検証（YouTube/Instagramは実キーで確認済み）。
   `metrics.collector._ALIASES` を実キーに。
5. `policy.check_video`（完成動画段階のポリシー判定）— vision LLM が要る。

`docs/economics.md` に初期の収支試算。`ffmpeg` はローカル加工の確認用に `brew install ffmpeg`。
