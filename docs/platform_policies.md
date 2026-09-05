# 媒体ポリシー参照（§7 §8 §20）

各媒体の公式ポリシーのうち、本システムに関係する部分の要約。
**ポリシーは変わる。実装時と運用中に必ず公式で再確認すること（§20）。**

- 最終確認: 2026-08-30
- 対象: AI生成のショート動画（猫/犬 = 非性的、adult = 非露骨・成人固定キャラ）
- `config/policy_rules/<platform>.yaml` の `version` はこのドキュメントの確認日と揃える

凡例: ✅=可 / ⚠️=条件付き・ラベル必須 / ❌=不可

---

## 横断まとめ

| 項目 | YouTube | TikTok | Instagram | X |
|---|---|---|---|---|
| 実写級AI生成の開示ラベル | ✅必須 | ✅必須 | ✅必須 | ⚠️必須（地域により宣言義務） |
| 明らかに非現実（アニメ調・幻想的） | 開示不要 | 開示不要 | 開示不要 | 開示不要 |
| 露骨な性的コンテンツ | ❌ | ❌ | ❌ | ⚠️（同意制作・ラベル必須・目立つ場所不可） |
| 性的示唆（挑発的な服装・ダンス等） | ⚠️年齢制限 | ❌（おすすめ除外〜削除） | ⚠️年齢制限/配信制限 | ⚠️ラベル |
| アニメ/AIの性的表現 | ❌（実写と同基準） | ❌ | ❌ | ⚠️ラベル |
| 実在人物のディープフェイク | ❌ | ❌ | ❌ | ❌（誤認を招くもの） |
| 大量生成・テンプレ量産 | ❌収益化不可 | ⚠️オリジナリティ要求 | ⚠️ | ― |

→ **adult ブランドが投稿しうるのは実質 X のみ**（同意制作・非露骨・センシティブラベル必須・プロフィール画像/ヘッダー等の目立つ場所は不可）。YouTube / TikTok / Instagram は非露骨でも「性的示唆」判定リスクが高く、当面 `SKIP_PLATFORM`。

---

## YouTube

### ヌード・性的コンテンツ
- 「性的満足を目的とした露骨なコンテンツ」は不可。ポルノは削除・チャンネル停止もあり得る。
- 「実写・演出・イラスト・アニメを含む」全形式が同基準 → **AI生成・アニメの性的表現も実写と同じく不可**。
- 焦点が胸/尻/性器、性的興奮を意図したポーズ等は削除または年齢制限。
- 例外: 教育・ドキュメンタリー・科学・芸術目的で、かつ扇情的でない場合のみ。
- 出典: https://support.google.com/youtube/answer/2802002

### AI生成物の開示（Altered or synthetic content disclosure）
- 「視聴者が実在の人物・場所・シーン・出来事と容易に取り違えうる」リアルな合成/改変コンテンツは**開示必須**。
- 開示不要: 明らかに非現実（アニメ、幻想的な演出、グリーンスクリーン等）、美的のみの軽微な編集、台本/サムネ/字幕へのAI利用。
- 方法: アップロード時に YouTube Studio の属性で「AI利用」を「はい」に。
- 未開示を繰り返すと、ラベルの手動付与・コンテンツ削除・**YPP（収益化）停止**の可能性。2026年1月から本格執行。
- 出典: https://support.google.com/youtube/answer/14328491

### 収益化（inauthentic content / 2025-07-15 更新）
- 「大量生成・反復的・テンプレ的でバリエーションが乏しい」コンテンツは収益化不可。
- 名指しの例: 「作者独自の視点・洞察を加えず、汎用テンプレで量産された印象を与える **AI生成コンテンツ**」「ナレーションのない画像スライドショー」。
- チャンネル全体に適用。違反動画が多いとチャンネル丸ごと収益化剥奪。
- → **企画タグ/フック/尺のバリエーションと、各動画の独自性が収益化の生命線**（§11 の explore 枠と勝ちパターン分散が効く）。
- 出典: https://support.google.com/youtube/answer/1311392

### その他
- 未監査APIプロジェクトからのアップロードは private 制限があり得る（§9）。監査前提。
- 実在の人物・チャンネルのなりすまし禁止。

---

## TikTok

### 性的コンテンツ・成熟テーマ
- 露骨な性的コンテンツは禁止。
- **実在の人物が性的に示唆的なポーズ**を取る内容は概ね禁止。挑発的な服装、性的に示唆的なダンス、アダルト話題中心の会話も対象。
- ディープフェイクポルノ・実在個人の性的合成物は芸術的枠組みでも恒久禁止。
- 出典: https://www.tiktok.com/community-guidelines/en/ （Sensitive and mature themes / Integrity and authenticity）

### AI生成コンテンツ（AIGC）ラベル
- 「リアルな人物・シーン」を示す画像・音声・動画は**ラベル必須**（禁止ではなく開示）。対象:
  - 実在人物の画像/声/発言をAIで改変した動画
  - 実世界のシーンをAIで改変した動画/画像
  - 実在/架空の人物・場所・出来事を丸ごとAI生成した動画/画像
- 方法: 自前のキャプション/ステッカー/ウォーターマーク、または TikTok の AIGC ラベルトグル。
- C2PA Content Credentials で自動検出。未ラベルは削除・アカウントストライクの可能性。
- 出典: https://www.tiktok.com/creator-academy/en/article/ai-generated-content-label , https://newsroom.tiktok.com/new-labels-for-disclosing-ai-generated-content

### API（§9）
- Content Posting API / Direct Post。`video.publish` スコープ承認が必要。
- 未監査クライアントからの投稿は private 制限。Direct Post は明示的同意等の UX 要件あり。

---

## Instagram / Meta

### 成人のヌード・性的行為（Adult Nudity and Sexual Activity）
- 不可: 性器/肛門/尻のクローズアップ/女性の乳首（授乳・抗議等を除く）の**フォトリアル/デジタル画像**、性交・オーラル・自慰の露骨な描写、勃起・体液、獣姦/近親相姦等のフェチ、性行為の長時間音声。
- 18+制限（警告ラベル付きで許容）: 不透明物で隠した準ヌード、股間/尻/胸が焦点の画像、**フィクションの性的描写・性行為のシミュレーション**、舌を見せるキス等。
- 例外: 授乳、乳房切除の傷跡、ヌードを描いた現実世界のアート（制限あり）、医療/教育。
- → 「フォトリアル」「フィクションの性的描写も18+制限」= **AI生成の非露骨adultでも制限対象になりやすい**。
- 出典: https://transparency.meta.com/policies/community-standards/adult-nudity-sexual-activity/

### AI開示（"AI Info" ラベル）
- フォトリアルな動画・リアルな音声をデジタル生成/改変した organic コンテンツは**自己開示が必要**。未開示はペナルティの可能性。
- C2PA 等の業界標準＋不可視ウォーターマーク＋メタデータで検出。自己申告と業界シグナルの両方でラベル付与。
- 出典: https://about.fb.com/news/2024/04/metas-approach-to-labeling-ai-generated-content-and-manipulated-media/ , https://transparency.meta.com/governance/tracking-impact/labeling-ai-content

### 収益化
- 日本では Reels の直接収益化プログラムは対象外の想定（§ 収支メモ）。実装時に最新の Content Monetization ポリシーを確認。

---

## X

### アダルトコンテンツ
- **同意のもと制作・配布された**アダルトヌード/性的行為は、**適切にラベル付けし、目立たせなければ共有可**。
- ライブ動画・プロフィール画像・ヘッダー・リストバナー・コミュニティカバー等の**目立つ場所には不可**。
- ラベル対象: 露骨な性行為（膣/オーラル/アナル、性玩具、あらゆる挿入）、示唆的行為（着衣の性的シミュレーション、勃起等の性的興奮状態）。
- センシティブ設定: 安全設定で「投稿するメディアをセンシティブとしてマーク」を有効化。未マークは手動でマーク、繰り返すとアカウント単位でセンシティブ扱い。
- 出典: https://help.x.com/en/rules-and-policies/adult-content , https://help.x.com/en/rules-and-policies/media-settings

### 合成・改変メディア（Synthetic and manipulated media）
- 「人を欺く/混乱させ危害につながりうる」合成・改変・文脈外メディアは禁止。
- ラベルまたは削除の条件: 著しく欺瞞的に改変/捏造 ＋ 欺瞞的な文脈で共有 ＋ 公共の混乱・公共安全・重大な危害の恐れ。
- 高セベリティ（重大な危害リスク）は削除必須。
- 地域差: インドの IT Rules Amendment 2026 で、合成生成情報（SGI）は投稿前に宣言義務。
- 出典: https://help.x.com/en/rules-and-policies/manipulated-media

### 非同意ヌード / なりすまし
- 非同意の親密画像は禁止。実在人物のなりすまし禁止（パロディは明示要件あり）。
- 出典: https://help.x.com/en/rules-and-policies/intimate-media

### API（§9 §13）
- X API v2 + Media Upload。pay-per-use のため費用上限を必ず設定。

---

## 本システムへの落とし込み

`config/policy_rules/` に反映済みのもの:

| ルール | 実装 |
|---|---|
| HIGH RISK 企画（adult 既定）は投稿前に人間確認 | `_common.yaml: high_risk_needs_review` → HOLD |
| 実在人物の模倣 | `_common.yaml: no_real_person_mimicry` → HOLD |
| 実在ブランド/ロゴ | `_common.yaml: real_brand_or_logo` → REWRITE |
| 暴力・流血 | `_common.yaml: violence_or_gore` → REGENERATE |
| adult ブランド → YouTube 不可 | `youtube.yaml: yt_adult_hold` → SKIP_PLATFORM |
| adult ブランド → TikTok 不可 | `tiktok.yaml: tt_adult_block` → SKIP_PLATFORM |
| adult ブランド → Instagram 不可 | `instagram.yaml: ig_adult_block` → SKIP_PLATFORM |
| 違和感レベル過大（YouTube） | `youtube.yaml: yt_oddity_cap` → REGENERATE |

**まだコードで担保していない（publish / check_video 段階でやる）**:
- 実写級AI生成の **AI開示ラベル付与**（全媒体必須）。各 `<platform>.yaml` の `ai_disclosure_required: true` を publish が消費する想定。
- X のセンシティブメディアラベル付与、プロフィール系メディアへの不使用。
- 完成動画の性的/暴力的表現の検出（vision 判定。`check_video` 未実装）。
- 収益化の「量産・テンプレ」回避は運用モニタリング（§11 のバリエーション設計で低減）。
