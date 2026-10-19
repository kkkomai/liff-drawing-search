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
  { symbol: '^FTSE', name: 'FTSE 100', price: 10881.9, previous_close: 10800.0, change: 81.9, change_percent: 0.76, currency: 'GBP', exchange: 'FGI', timezone: 'LON', market_time: 1792377303 },
  { symbol: '^GDAXI', name: 'DAX', price: 25622.61, previous_close: 25700.0, change: -77.39, change_percent: -0.30, currency: 'EUR', exchange: 'GER', timezone: 'CET', market_time: 1792377303 },
  { symbol: '^FCHI', name: 'CAC 40', price: 7956.7, previous_close: 7900.0, change: 56.7, change_percent: 0.72, currency: 'EUR', exchange: 'PAR', timezone: 'CET', market_time: 1792377303 },
  { symbol: '^HSI', name: 'ハンセン', price: 24186.83, previous_close: 24200.0, change: -13.17, change_percent: -0.05, currency: 'HKD', exchange: 'HKG', timezone: 'HKT', market_time: 1792377303 },
  { symbol: '^KS11', name: 'KOSPI', price: 5710.13, previous_close: 5700.0, change: 10.13, change_percent: 0.18, currency: 'KRW', exchange: 'KSC', timezone: 'KST', market_time: 1792377303 },
  { symbol: '000001.SS', name: '上海総合', price: 3715.97, previous_close: 3700.0, change: 15.97, change_percent: 0.43, currency: 'CNY', exchange: 'SHH', timezone: 'CST', market_time: 1792377303 },
  { symbol: 'BTC-USD', name: 'ビットコイン', price: 82627.45, previous_close: 82000.0, change: 627.45, change_percent: 0.77, currency: 'USD', exchange: 'CCC', timezone: 'UTC', market_time: 1792377303 },
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
        body = JSON.stringify({ cached: false, category: '経済', items: [
          { title: 'テストニース: 日経平均が上げ幅を拡大', link: 'https://example.com/news/1', pubDate: 'Mon, 19 Oct 2026 03:00:00 GMT', summary: 'テスト用の経済ニースです。' },
          { title: 'テストニース2: 場株高く', link: '', pubDate: 'Mon, 19 Oct 2026 02:00:00 GMT', summary: 'リンク無しのニースです。' },
        ] });
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

  const mainRows = document.querySelectorAll('#stocks-main .stock-row');
  const otherRows = document.querySelectorAll('#stocks-others .stock-row');
  const allRows = [...mainRows, ...otherRows];
  const names = allRows.map(c => c.querySelector('.stock-name').textContent);
  const prices = allRows.map(c => c.querySelector('.stock-price').textContent.trim());
  const upCount = allRows.filter(c => c.querySelector('.stock-change').classList.contains('up')).length;
  const downCount = allRows.filter(c => c.querySelector('.stock-change').classList.contains('down')).length;
  const newsItems = document.querySelectorAll('#stocks-news-list .news-item');

  console.log('--- fetch 呼び出し回数:', fetchCount, '(auth + stocks + news) ---');
  console.log('--- 株価行数: 主要 ' + mainRows.length + ' / その他 ' + otherRows.length + ' ---');
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
  console.log('5. 主要指数3行 + その他指数9行:', mainRows.length === 3 && otherRows.length === 9 ? 'OK' : 'NG (' + mainRows.length + '/' + otherRows.length + ')');
  console.log('6. 日経平均の価格(64,534.03):', prices.some(p => p.includes('64,534')) ? 'OK' : 'NG (' + prices[0] + ')');
  console.log('7. 騰落色 (up:' + upCount + ' down:' + downCount + '):', (upCount >= 1 && downCount >= 1) ? 'OK' : 'NG');
  console.log('8. 更新時刻が表示される:', document.getElementById('stocks-updated').textContent.includes('更新:') ? 'OK' : 'NG (' + document.getElementById('stocks-updated').textContent + ')');
  console.log('9. 自動更新タイマーが装着される:', window.stocksTimer !== null && window.stocksTimer !== undefined ? 'OK' : 'NG');

  // 別タブに移動したらタイマーが止まる
  window.showTab('emp');
  console.log('10. 他タブでタイマー解除:', window.stocksTimer === null ? 'OK' : 'NG');
  console.log('11. 経済ニュースが表示される:', newsItems.length >= 2 ? 'OK (' + newsItems.length + '件)' : 'NG (' + newsItems.length + ')');
  console.log('12. ニュースのタイトル:', newsItems[0] ? newsItems[0].querySelector('a, div').textContent : '(なし)');
  console.log('13. バージョン marker:', window.__v === '20261019stocks' ? 'OK (' + window.__v + ')' : 'NG (' + window.__v + ')');
  process.exit(0);
}
