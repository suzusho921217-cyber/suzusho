# policy_rules/

媒体別のポリシー「版」とチェック項目を YAML で保持する（§8）。
`src/policy/engine.py` が `_common.yaml` ＋ `<platform>.yaml` をマージして使う。
`policy_sync.yml`（未実装）が `config/platforms.yaml` の `policy_urls` を確認し、
差分を検知したら `.state/policy_sync.json` に記録 → `is_policy_stale()` が True を返し
自動投稿を止める。ルール変更を検知しても旧ルールのまま投稿を続けない。

## ファイル

- `_common.yaml` … 全媒体共通のチェック（各 `<platform>.yaml` にマージ）
- `youtube.yaml` … YouTube 固有。**現在有効**
- `tiktok.yaml` / `instagram.yaml` / `x.yaml` … 媒体が無効なので `checks: []` のスタブ

## check の書き方

```yaml
- id: <一意なID>
  applies_to: [prompt]        # prompt（生成前）/ video（投稿前）。今は prompt のみ実装
  when:                       # 複数キーは AND
    text_matches: "正規表現"  # concept_tag / hook_type / notes を re.search（IGNORECASE）
    max_oddity_level: 4       # plan.oddity_level がこの値を超えたら発火
    max_reality_level: 4      # plan.reality_level がこの値を超えたら発火
    brand_in: [adult]         # plan.brand が一致
    policy_risk_in: [HIGH]    # plan.policy_risk が一致
  decision: HOLD              # PASS / REWRITE / REGENERATE / SKIP_PLATFORM / HOLD
  message: "理由（reasons に入る）"
```

1企画が複数ルールにヒットしたら、最も重い decision を返す
（重い順: HOLD > SKIP_PLATFORM > REGENERATE > REWRITE > PASS）。
