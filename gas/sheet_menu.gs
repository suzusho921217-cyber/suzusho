/**
 * SNS/AI管理スプレッドシート用のメニュー。
 * GitHub Actions のワークフローを手動で起動して、シートを更新する。
 *
 * ■ セットアップ（1回だけ）
 *   1. このスプレッドシートを開く → 拡張機能 → Apps Script
 *   2. このファイルの中身を貼り付けて保存
 *   3. プロジェクトの設定（歯車）→ スクリプト プロパティ に以下を追加:
 *        GITHUB_TOKEN = <fine-grained PAT>
 *      PAT は https://github.com/settings/personal-access-tokens/new で作成:
 *        - Repository access: Only select repositories → suzusho921217-cyber/suzusho
 *        - Permissions: Actions = Read and write（それだけでOK）
 *   4. エディタ上部の関数選択で onOpen を選び ▶ 実行 → 権限を承認
 *   5. スプレッドシートを再読み込み → メニューバーに「🔄 更新」が出る
 *
 * ■ 使い方
 *   「🔄 更新」→「数字を更新（metrics＋学習）」で、再生数などを回収して各DBに反映。
 *   1〜2分で GitHub 側の処理が終わるので、そのあとシートをリロード。
 *   お金のかかる生成・投稿はこのメニューからは走らない。
 */

var REPO = 'suzusho921217-cyber/suzusho';
var API = 'https://api.github.com/repos/' + REPO + '/actions';

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('🔄 更新')
    .addItem('数字を更新（metrics＋学習）', 'refreshNumbers')
    .addSeparator()
    .addItem('今日の企画を作る（plan-daily）', 'runPlanDaily')
    .addItem('エージェントMTGを実行', 'runAgentMtg')
    .addSeparator()
    .addItem('直近の実行状況を見る', 'showRuns')
    .addToUi();
}

function refreshNumbers() { dispatch_('manual_refresh.yml', '数字の更新'); }
function runPlanDaily()   { dispatch_('plan_daily.yml', '今日の企画づくり'); }
function runAgentMtg()    { dispatch_('agent_mtg.yml', 'エージェントMTG'); }

function dispatch_(workflowFile, label) {
  var token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) {
    SpreadsheetApp.getUi().alert('GITHUB_TOKEN がスクリプトプロパティに未設定です。セットアップ手順を確認してください。');
    return;
  }
  var res = UrlFetchApp.fetch(API + '/workflows/' + workflowFile + '/dispatches', {
    method: 'post',
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ ref: 'main' }),
    muteHttpExceptions: true,
  });
  var code = res.getResponseCode();
  if (code === 204) {
    SpreadsheetApp.getActiveSpreadsheet().toast(
      label + 'を開始しました。1〜2分後にシートをリロードしてください。', '🔄 更新', 8);
  } else {
    SpreadsheetApp.getUi().alert('起動に失敗しました（HTTP ' + code + '）\n' + res.getContentText());
  }
}

function showRuns() {
  var token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  var res = UrlFetchApp.fetch(API + '/runs?per_page=8', {
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
    muteHttpExceptions: true,
  });
  var runs = JSON.parse(res.getContentText()).workflow_runs || [];
  var lines = runs.map(function (r) {
    var when = Utilities.formatDate(new Date(r.created_at), 'Asia/Tokyo', 'MM/dd HH:mm');
    return when + '  ' + r.name + '  ' + (r.conclusion || r.status);
  });
  SpreadsheetApp.getUi().alert('直近の実行:\n\n' + lines.join('\n'));
}
