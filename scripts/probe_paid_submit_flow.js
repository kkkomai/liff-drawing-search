// 申請ボタン → 履歴表示 のフローを jsdom で再現するプローブ。
// fetch をスタブして、POST 201 / GET 200 を返し、#paid-histories の
// 描画結果を検証する。
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('index.html', 'utf8');

// 有休タブのHTML部分と、申請フローのJS部分を抽出
const paidTabMatch = html.match(/<div class="tab-content" id="tab-paid">[\s\S]*?<\/div>\s*<\/div>/);
const paidTabHtml = paidTabMatch ? paidTabMatch[0] : '<div id="tab-paid"></div>';

function extractFn(src, name) {
  const i = src.indexOf('function ' + name);
  if (i < 0) return null;
  // 次の function 宣言まで（ネストはない前提の簡易抽出）
  const j = src.indexOf('\n    function ', i + 10);
  return src.slice(i, j > 0 ? j : i + 3000);
}

const paidJs = [
  extractFn(html, 'showToast'),
  extractFn(html, 'loadPaidHistories'),
  extractFn(html, 'renderPaidHistories'),
  extractFn(html, 'deletePaidHistory'),
  extractFn(html, 'paidHeaders'),
].filter(Boolean).join('\n');

const dom = new JSDOM(`<!DOCTYPE html><html><body>
<div id="tab-paid">${paidTabHtml}</div>
<div id="toast"></div>
</body></html>`, { runScripts: 'dangerously', url: 'https://liff-drawing-search.onrender.com/' });

const { window } = dom;
const AUTH_TOKEN_KEY = 'bentoAuthToken';
window[AUTH_TOKEN_KEY] = 'test-token-123';

// --- fetch スタブ ---
let postCalls = 0, getCalls = 0;
const serverLeaves = [
  { id: 'existing-1', date: '2026-10-01', type: '終日有休', reason: '既存エントリ', created: '2026-10-01T10:00:00+0900' },
];
window.fetch = function (url, opts) {
  opts = opts || {};
  if (opts.method === 'POST') {
    postCalls++;
    const body = JSON.parse(opts.body);
    const leave = { id: 'new-' + postCalls, date: body.date, type: body.type, reason: body.reason, created: '2026-10-14T18:00:00+0900' };
    serverLeaves.unshift(leave);
    return Promise.resolve({ ok: true, status: 201, json: () => Promise.resolve({ success: true, leave }) });
  }
  getCalls++;
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ leaves: serverLeaves.slice() }) });
};

window.getAuthToken = function () { return window[AUTH_TOKEN_KEY] || null; };
window.log = function () {};
window.alert = function (msg) { window.__alert = msg; };
// showToast はスタブしない（ページから抽出した本物を使う）

// paidSubmitBtn の onclick を本文から抽出（paidSubmitBtn の取得部分を含む）
const btnIdx = html.indexOf('var paidSubmitBtn');
const btnEnd = html.indexOf('// === ORIGINAL IMAGE PREVIEW');
const submitCode = html.slice(btnIdx, btnEnd);

window.eval('var paidHistories = [];\n' + paidJs + '\n' + submitCode);

// --- 申請をシミュレート ---
window.document.getElementById('paid-date').value = '2026-10-20';
window.document.getElementById('paid-type').value = '午前有休';
window.document.getElementById('paid-reason').value = 'jsdom検証';

window.document.getElementById('paid-submit').click();

setTimeout(function () {
  const div = window.document.getElementById('paid-histories');
  const toastEl = window.document.getElementById('app-toast');
  console.log('POST calls:', postCalls, '/ GET calls:', getCalls);
  console.log('toast表示:', toastEl ? JSON.stringify(toastEl.textContent) : '(要素なし)');
  console.log('toast色:', toastEl ? toastEl.style.background : '-');
  console.log('alert:', window.__alert || '(なし)');
  const items = div ? div.querySelectorAll('.paid-history-item') : [];
  console.log('--- 描画された履歴件数:', items.length, '---');
  items.forEach(function (it) {
    console.log('  *', it.querySelector('.paid-history-date').textContent, '/', it.querySelector('.paid-history-type').textContent, '/', it.querySelector('.paid-history-reason').textContent);
  });
}, 3000);
