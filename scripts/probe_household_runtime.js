// Runtime probe for /household/index.html (家計簿タブ)
//
// household/index.html は2つのモードを持つ:
//   - embedded=1   : ホストから postMessage で認証情報を受け取り API を呼ぶ
//   - standalone   : APIを呼ばず当月フィクスチャで描画 (ブラウザでの目視確認用)
//
// このプローブは standalone モード (?embedded 無し) で読み込み、以下を検証する:
//   1. カレンダーに日付セルが42個描画され、支出のある日に金額が出る
//   2. 選択日の支出明細リストが描画される (カテゴリバッジ付き)
//   3. カテゴリ別サマリーの横棒が描画される (複数カテゴリ)
//   4. 「＋ 支出を追加」でフォームが開く
//   5. 月合計が ¥xx,xxx 形式で表示される
//   6. standalone モードでは API (fetch) を一切呼ばない
//   7. console エラーが出ない
//
// fetch はスタブして当月フィクスチャJSONを返すようにしておくが、
// standalone モードでは呼ばれないはず。呼ばれたら仕様違反 (スキルの
// 「mockモードでAPIを呼ぶのはmockモードではない」と同じ穴)。
//
// jsdom 30 は runScripts:'dangerously' でインライン<script>をそのまま
// 実行する (外部スクリプトは無いので resources 指定は不要)。
'use strict';
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require('jsdom');

const file = path.join(__dirname, '..', 'household', 'index.html');
if (!fs.existsSync(file)) {
  console.error('SKIP: file not found:', file);
  process.exit(3);
}
const html = fs.readFileSync(file, 'utf8');

// 当月 (JST想定のマシンタイムゾーンに従う) のフィクスチャ。
// 実際のページは内部の buildMockData() で当月の日付を使うので、
// ここで検証する日付も実行日の当月から作る。
const now = new Date();
const tzOff = now.getTimezoneOffset() * 60000;
const today = new Date(now - tzOff).toISOString().slice(0, 10);
const month = today.slice(0, 7);
const todayDay = Number(today.slice(8, 10));
const pad = n => (n < 10 ? '0' : '') + n;
const dim = new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0).getDate();
const fixtureDays = [1, Math.min(5, dim), Math.min(todayDay, dim)];

const virtualConsole = new VirtualConsole();
const errors = [];
virtualConsole.on('error', (e) => errors.push('console.error: ' + (e.message || e)));
virtualConsole.on('jsdomError', (e) => errors.push('jsdomError: ' + (e.message || e)));

const dom = new JSDOM(html, {
  // ?embedded を付けない = standalone モード
  url: 'http://localhost:11002/household/index.html',
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  virtualConsole,
});
const { window } = dom;
const doc = window.document;

// fetch スタブ: standalone モードでは呼ばれてはいけない。
// 万一呼ばれたら当月フィクスチャJSONを返して検証可能な状態にする
// (Response スタブには ok を必ず設定: ok = status>=200 && status<300)。
const fetchCalls = [];
window.Response = class {
  constructor(body, init) {
    this._body = body;
    this.status = (init && init.status) || 200;
    this.ok = this.status >= 200 && this.status < 300;
  }
  async json() { return JSON.parse(this._body); }
  async text() { return this._body; }
};
window.fetch = (url) => {
  fetchCalls.push(String(url));
  const fixtures = [
    { id: 'f1', spent_on: month + '-' + pad(fixtureDays[0]), item: 'スーパーで食材', category: '食費', amount: 4280, note: '週末の買い出し', created: month + '-' + pad(fixtureDays[0]) + 'T09:00:00+0900' },
    { id: 'f2', spent_on: month + '-' + pad(fixtureDays[1]), item: 'ガソリン', category: '交通費', amount: 3600, note: '', created: month + '-' + pad(fixtureDays[1]) + 'T09:00:00+0900' },
    { id: 'f3', spent_on: month + '-' + pad(fixtureDays[2]), item: 'カフェ', category: '趣味・娯楽', amount: 850, note: '友人と', created: month + '-' + pad(fixtureDays[2]) + 'T09:00:00+0900' },
    { id: 'f4', spent_on: month + '-' + pad(fixtureDays[0]), item: '薬局', category: '医療・健康', amount: 1580, note: '', created: month + '-' + pad(fixtureDays[0]) + 'T10:00:00+0900' },
    { id: 'f5', spent_on: month + '-' + pad(fixtureDays[1]), item: '本', category: '趣味・娯楽', amount: 2400, note: '技術書', created: month + '-' + pad(fixtureDays[1]) + 'T11:00:00+0900' },
  ];
  const total = fixtures.reduce((s, e) => s + e.amount, 0);
  const byCat = {};
  fixtures.forEach(e => {
    byCat[e.category] = byCat[e.category] || { amount: 0, count: 0 };
    byCat[e.category].amount += e.amount;
    byCat[e.category].count += 1;
  });
  const body = String(url).includes('/summary')
    ? { month, total, count: fixtures.length,
        by_category: Object.keys(byCat).map(k => ({ category: k, amount: byCat[k].amount, count: byCat[k].count })).sort((a, b) => b.amount - a.amount),
        prev_month: month, prev_total: Math.round(total * 0.92) }
    : { expenses: fixtures };
  return Promise.resolve(new window.Response(JSON.stringify(body), { status: 200 }));
};

setTimeout(() => {
  const fail = [];

  // ---- 1. カレンダー ----
  const cells = doc.querySelectorAll('#calendar .cal-cell');
  if (cells.length !== 42) fail.push('カレンダーセル数が42ではありません: ' + cells.length);
  const cellsWithAmount = Array.from(cells).filter(c => c.querySelector('.amt'));
  if (cellsWithAmount.length < 3) {
    fail.push('支出のある日のセルに金額が出ていません: ' + cellsWithAmount.length + '件');
  }
  const selectedCell = doc.querySelector('#calendar .cal-cell.selected');
  if (!selectedCell) fail.push('選択日のセルがハイライトされていません');

  // ---- 2. 選択日の明細 ----
  const dayItems = doc.querySelectorAll('#dayList .expense-item');
  if (dayItems.length < 1) fail.push('選択日の支出明細が描画されていません');
  const hasBadge = Array.from(doc.querySelectorAll('#dayList .cat-badge')).length >= 1;
  if (!hasBadge) fail.push('明細にカテゴリバッジがありません');

  // ---- 3. カテゴリ別サマリー ----
  const bars = doc.querySelectorAll('#catSummary .cat-bar-row');
  if (bars.length < 2) fail.push('カテゴリ別バーが2つ以上描画されていません: ' + bars.length);
  const barFill = doc.querySelector('#catSummary .cat-bar-fill');
  if (!barFill || !barFill.style.width) fail.push('バーの幅が設定されていません');
  const prevLine = doc.getElementById('prevMonthLine');
  if (!prevLine || !prevLine.textContent.includes('前月')) {
    fail.push('前月比フッターが描画されていません');
  }

  // ---- 4. 追加フォームが開く ----
  const formCard = doc.getElementById('formCard');
  const before = formCard.style.display;
  doc.getElementById('toggleFormBtn').click();
  const after = formCard.style.display;
  if (after === 'none' || after === before && before === 'none') {
    fail.push('追加フォームが開きません (display=' + after + ')');
  }

  // ---- 5. 月合計の書式 ----
  const monthTotal = doc.getElementById('monthTotal').textContent;
  if (!/^¥\d{1,3}(,\d{3})+$/.test(monthTotal)) {
    fail.push('月合計の書式が ¥xx,xxx ではありません: ' + monthTotal);
  }
  if (!doc.getElementById('monthLabel').textContent.match(/年\d+月/)) {
    fail.push('月ラベルが不正: ' + doc.getElementById('monthLabel').textContent);
  }

  // ---- 5b. 月別合計カード (standalone: 当月+先月プレビューの2行) ----
  const mtRows = doc.querySelectorAll('#monthlyTotals .month-total-row');
  if (mtRows.length < 2) {
    fail.push('月別合計の行が2行以上ありません: ' + mtRows.length);
  }
  const mtCurrent = doc.querySelector('#monthlyTotals .month-total-row.current');
  if (!mtCurrent) fail.push('当月の行がハイライトされていません');
  if (!doc.getElementById('monthlyTotals').textContent.match(/年\d+月/)) {
    fail.push('月別合計に年月が表示されていません');
  }
  // 当月以外の行をタップするとその月に移動する (standalone なので fetch は増えない)
  // 注: STATE は const 宣言なので window プロパティにならない (jsdom)。
  // 月移動は月ラベルのDOM変化で判定する。
  let mtTapMonth = '';
  const otherRow = Array.from(mtRows).find(r => !r.classList.contains('current'));
  if (otherRow) {
    const labelBefore = doc.getElementById('monthLabel').textContent;
    otherRow.click();
    const labelAfter = doc.getElementById('monthLabel').textContent;
    mtTapMonth = labelAfter;
    if (labelAfter === labelBefore) fail.push('月別合計の行をタップしても月が移動しません');
  } else {
    fail.push('当月以外の月別合計行がありません');
  }

  // ---- 6. standalone モードで API を呼ばない ----
  if (fetchCalls.length > 0) {
    fail.push('standalone モードで fetch が呼ばれました: ' + JSON.stringify(fetchCalls));
  }

  // ---- 7. console エラーなし ----
  if (errors.length) fail.push('console errors: ' + errors.join(' | '));

  const results = {
    month,
    selectedDate: window.STATE && window.STATE.selectedDate,
    calendarCells: cells.length,
    cellsWithAmount: cellsWithAmount.length,
    dayItems: dayItems.length,
    summaryBars: bars.length,
    formOpened: formCard.style.display,
    monthTotal,
    monthLabel: doc.getElementById('monthLabel').textContent,
    monthlyTotalsRows: mtRows.length,
    monthlyTotalsTapMonth: mtTapMonth,
    apiStatus: doc.getElementById('apiStatus').textContent,
    fetchCalls,
    consoleErrors: errors,
  };
  console.log('=== HOUSEHOLD RUNTIME PROBE RESULTS (standalone) ===');
  console.log(JSON.stringify(results, null, 2));

  if (fail.length) {
    console.error('FAIL\n - ' + fail.join('\n - '));
    process.exit(1);
  }

  // ---- embedded モード: postMessage ハンドシェイク + API往復 + 月別合計 ----
  // 本番と同じ経路 (host -> credentials -> summary/month/all の3fetch) を
  // スタブfetchで通し、月別合計カードが全期間データから描画されることを確認する。
  runEmbeddedChecks();
}, 800);

function runEmbeddedChecks() {
  const html2 = fs.readFileSync(path.join(__dirname, '..', 'household', 'index.html'), 'utf8');
  const virtualConsole2 = new VirtualConsole();
  const errors2 = [];
  virtualConsole2.on('error', (e) => errors2.push('console.error: ' + (e.message || e)));
  virtualConsole2.on('jsdomError', (e) => errors2.push('jsdomError: ' + (e.message || e)));

  const dom2 = new JSDOM(html2, {
    url: 'http://localhost:11002/household/index.html?embedded=1',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    virtualConsole: virtualConsole2,
  });
  const win2 = dom2.window;
  const doc2 = win2.document;

  const fetchCalls2 = [];
  win2.Response = class {
    constructor(body, init) {
      this._body = body;
      this.status = (init && init.status) || 200;
      this.ok = this.status >= 200 && this.status < 300;
    }
    async json() { return JSON.parse(this._body); }
    async text() { return this._body; }
  };
  win2.fetch = (url) => {
    fetchCalls2.push(String(url));
    const pad2 = n => (n < 10 ? '0' : '') + n;
    const prevYm = (() => {
      const y = Number(month.slice(0, 4));
      const m = Number(month.slice(5, 7));
      const d = new Date(y, m - 2, 1);
      return d.getFullYear() + '-' + pad2(d.getMonth() + 1);
    })();
    const fixtures = [
      { id: 'e1', spent_on: month + '-' + pad2(fixtureDays[0]), item: 'スーパー', category: '食費', amount: 4280, note: '', created: month + '-' + pad2(fixtureDays[0]) + 'T09:00:00+0900' },
      { id: 'e2', spent_on: month + '-' + pad2(fixtureDays[1]), item: 'ガソリン', category: '交通費', amount: 3600, note: '', created: month + '-' + pad2(fixtureDays[1]) + 'T09:00:00+0900' },
      { id: 'e3', spent_on: prevYm + '-12', item: '先月の接待', category: '交際費', amount: 7200, note: '', created: prevYm + '-12T09:00:00+0900' },
    ];
    const total = fixtures.reduce((s, e) => s + e.amount, 0);
    const byCat = {};
    fixtures.forEach(e => {
      byCat[e.category] = byCat[e.category] || { amount: 0, count: 0 };
      byCat[e.category].amount += e.amount;
      byCat[e.category].count += 1;
    });
    const body = String(url).includes('/summary')
      ? { month, total, count: fixtures.length,
          by_category: Object.keys(byCat).map(k => ({ category: k, amount: byCat[k].amount, count: byCat[k].count })).sort((a, b) => b.amount - a.amount),
          prev_month: prevYm, prev_total: 7200 }
      : { expenses: fixtures };
    return Promise.resolve(new win2.Response(JSON.stringify(body), { status: 200 }));
  };

  // ホストからの認証情報送信をシミュレート (postMessage)
  win2.dispatchEvent(new win2.MessageEvent('message', {
    data: { type: 'household-credentials', apiBase: 'http://localhost:11002', lineUserId: 'u-test', idToken: 't', authToken: 'tok' },
  }));

  setTimeout(() => {
    const fail2 = [];
    const authWait = doc2.getElementById('authWait').style.display;
    if (authWait !== 'none') fail2.push('認証後に authWait が隠れていません: ' + authWait);
    const mtRows2 = doc2.querySelectorAll('#monthlyTotals .month-total-row');
    if (mtRows2.length !== 2) {
      fail2.push('embedded: 月別合計が2行 (当月+先月) ではありません: ' + mtRows2.length);
    }
    const hasAllFetch = fetchCalls2.some(u => /\/household\/expenses$/.test(u) || /\/household\/expenses\?$/.test(u));
    if (!hasAllFetch) fail2.push('embedded: 全期間取得のfetchが呼ばれていません: ' + JSON.stringify(fetchCalls2));
    const cells2 = doc2.querySelectorAll('#calendar .cal-cell');
    if (cells2.length !== 42) fail2.push('embedded: カレンダーが42セルではありません: ' + cells2.length);
    if (errors2.length) fail2.push('console errors: ' + errors2.join(' | '));

    const results2 = {
      mode: 'embedded',
      fetchCalls: fetchCalls2,
      monthlyTotalsRows: mtRows2.length,
      calendarCells: cells2.length,
      apiStatus: doc2.getElementById('apiStatus').textContent,
      consoleErrors: errors2,
    };
    console.log('=== HOUSEHOLD RUNTIME PROBE RESULTS (embedded) ===');
    console.log(JSON.stringify(results2, null, 2));

    if (fail2.length) {
      console.error('FAIL\n - ' + fail2.join('\n - '));
      process.exit(1);
    }
    console.log('OK (standalone + embedded)');
    process.exit(0);
  }, 800);
}
