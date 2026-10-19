// 管理ページ(admin.html)の株価タブを jsdom で検証するプローブ。
// 認証まわり（liff.init / /api/auth/login）はスタブし、showTab('stocks')
// 後の描画と自動更新タイマーを確認する。
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('admin.html', 'utf8');

let fetchCount = 0;
const mockQuotes = [
  { symbol: '^N225', name: '日経平均', price: 64534.03, previous_close: 65324.29, change: -790.26, change_percent: -1.21, currency: 'JPY', exchange: 'OSA', timezone: 'JST', market_time: 1792377303 },
  { symbol: '^GSPC', name: 'S&P 500', price: 7659.44, previous_close: 7740.1, change: -80.66, change_percent: -1.04, currency: 'USD', exchange: 'SNP', timezone: 'EDT', market_time: 1792390000 },
  { symbol: '^NSEI', name: 'Nifty 50', price: 23146.15, previous_close: 22997.3, change: 148.85, change_percent: 0.65, currency: 'INR', exchange: 'NSI', timezone: 'IST', market_time: 1792144867 },
  { symbol: '^DJI', name: 'ダウ平均', price: 52002.41, previous_close: 52102.2, change: -99.79, change_percent: -0.19, currency: 'USD', exchange: 'DJI', timezone: 'EDT', market_time: 1792390000 },
  { symbol: '^IXIC', name: 'NASDAQ総合', price: 26140.03, previous_close: 26089.5, change: 50.53, change_percent: 0.19, currency: 'USD', exchange: 'NIM', timezone: 'EDT', market_time: 1792390000 },
];

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'https://liff-drawing-search.onrender.com/admin.html',
  beforeParse(window) {
    // liff SDK スタブ（liff_admin の liffId で init 成功扱い）
    window.liff = {
      init: () => Promise.resolve(),
      isInClient: () => true,
      isLoggedIn: () => true,
      getContext: () => ({ userId: 'Uadmin0001aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }),
      getIDToken: () => 'fake-id-token',
    };
    // URL ごとにレスポンスを振り分ける fetch スタブ
    window.fetch = function (url, opts) {
      fetchCount++;
      console.log('[fetch #' + fetchCount + ']', String(url));
      let body;
      if (String(url).includes('/api/stocks')) {
        body = JSON.stringify({ cached: false, cached_at: Date.now() / 1000, quotes: mockQuotes });
      } else if (String(url).includes('/api/auth/login')) {
        body = JSON.stringify({ success: true, token: 'fake-session-token', employee: { id: 1, name: '管理者', role: 'admin' } });
      } else if (String(url).includes('/api/admin/employees')) {
        body = JSON.stringify({ employees: [] });
      } else if (String(url).includes('/api/news')) {
        body = JSON.stringify({ items: [] });
      } else {
        body = '{}';
      }
      const parsed = JSON.parse(body);
      return Promise.resolve({
        ok: true,
        status: 200,
        text: () => Promise.resolve(body),
        json: () => Promise.resolve(parsed),
      });
    };
    window.alert = () => {};
    window.HTMLElement.prototype.scrollIntoView = function () {};
  },
});
const { window } = dom;
const { document } = window;

setTimeout(function () {
  // LINE認証が通った想定で株価タブを開く。
  // loadStocks の Promise チェーン（マイクロタスク）を先に完了させるため、
  // チェックは1tick遅らせる（process.exit がマイクロタスクを殺す）。
  window.showTab('stocks');
  setTimeout(runChecks, 300);
}, 1500);

function runChecks() {

  const mainCards = document.querySelectorAll('#stocks-main .stock-card');
  const otherCards = document.querySelectorAll('#stocks-others .stock-card');
  const names = [...mainCards, ...otherCards].map(c => c.querySelector('.stock-name').textContent);
  const prices = [...mainCards, ...otherCards].map(c => c.querySelector('.stock-price').textContent.trim());
  const upCount = [...mainCards, ...otherCards].filter(c => c.querySelector('.stock-change').classList.contains('up')).length;
  const downCount = [...mainCards, ...otherCards].filter(c => c.querySelector('.stock-change').classList.contains('down')).length;

  console.log('--- fetch 呼び出し回数:', fetchCount, '(auth login + stocks) ---');
  console.log('--- 株価カード数:', mainCards.length + '/', otherCards.length, '---');
  console.log('--- 名前 ---');
  names.forEach(n => console.log('  *', n));
  console.log('--- 価格 ---');
  prices.forEach(p => console.log('  *', p));

  // デバッグ: エラーバナーとローディング状態
  console.log('\n--- デバッグ ---');
  console.log('  stocks-error 表示:', document.getElementById('stocks-error').style.display, '| 内容:', JSON.stringify(document.getElementById('stocks-error').textContent.slice(0, 120)));
  console.log('  stocks-loading 表示:', document.getElementById('stocks-loading').style.display);
  console.log('  stocks-grid 表示:', document.getElementById('stocks-grid').style.display);
  console.log('  stocks-updated:', JSON.stringify(document.getElementById('stocks-updated').textContent));

  console.log('\n--- チェック ---');
  console.log('1. LINE認証でガードが消える:', document.getElementById('liff-guard').style.display === 'none' ? 'OK' : 'NG');
  console.log('2. 株価タブが表示される:', document.getElementById('tab-stocks').style.display === 'block' ? 'OK' : 'NG');
  console.log('3. 社員管理タブが隠れる:', document.getElementById('tab-emp').style.display === 'none' ? 'OK' : 'NG');
  console.log('4. 株価タブボタンがハイライト:', document.getElementById('tabbtn-stocks').style.background === 'rgb(226, 232, 240)' ? 'OK' : 'NG');
  console.log('5. 主要指数3枚 + その他2枚:', mainCards.length === 3 && otherCards.length === 2 ? 'OK' : 'NG (' + mainCards.length + '/' + otherCards.length + ')');
  console.log('6. 日経平均の価格(64,534.03):', prices.some(p => p.includes('64,534')) ? 'OK' : 'NG (' + prices[0] + ')');
  console.log('7. 騰落色 (up:' + upCount + ' down:' + downCount + '):', (upCount === 2 && downCount === 3) ? 'OK' : 'NG');
  console.log('8. 更新時刻が表示される:', document.getElementById('stocks-updated').textContent.includes('更新:') ? 'OK' : 'NG (' + document.getElementById('stocks-updated').textContent + ')');
  console.log('9. 自動更新タイマーが装着される:', window.stocksTimer !== null && window.stocksTimer !== undefined ? 'OK' : 'NG');

  // 別タブに移動したらタイマーが止まる
  window.showTab('emp');
  console.log('10. 他タブでタイマー解除:', window.stocksTimer === null ? 'OK' : 'NG');
  console.log('11. バージョン marker:', window.__v === '20261019stocks' ? 'OK (' + window.__v + ')' : 'NG (' + window.__v + ')');
  process.exit(0);
}
