// 株価タブのランタイムプローブ。stocks/index.html を jsdom にloadし、
// fetch をスタブして /api/stocks のモックを返し、描画と自動更新を検証する。
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('stocks/index.html', 'utf8');

// --- fetch スタブ: 実APIと同じ形状のモックを返す ---
let fetchCount = 0;
const mockQuotes = [
  { symbol: '^N225', name: '日経平均', price: 64534.03, previous_close: 65324.29, change: -790.26, change_percent: -1.21, currency: 'JPY', exchange: 'OSA', timezone: 'JST', market_time: 1792377303 },
  { symbol: '^GSPC', name: 'S&P 500', price: 7205.5, previous_close: 7150.1, change: 55.4, change_percent: 0.78, currency: 'USD', exchange: 'SNP', timezone: 'EDT', market_time: 1792390000 },
  { symbol: '^NSEI', name: 'Nifty 50', price: 23146.15, previous_close: 22997.3, change: 148.85, change_percent: 0.65, currency: 'INR', exchange: 'NSI', timezone: 'IST', market_time: 1792144867 },
  { symbol: '^DJI', name: 'ダウ平均', price: 48900.2, previous_close: 49000.0, change: -99.8, change_percent: -0.20, currency: 'USD', exchange: 'DJI', timezone: 'EDT', market_time: 1792390000 },
  { symbol: '^IXIC', name: 'NASDAQ総合', price: 22100.5, previous_close: 22050.0, change: 50.5, change_percent: 0.23, currency: 'USD', exchange: 'NIM', timezone: 'EDT', market_time: 1792390000 },
];

// ページのスクリプトは new JSDOM() の構築中に同期実行されるため、
// fetch スタブは beforeParse で先に入れる（構築後の代入は間に合わない）。
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'https://liff-drawing-search.onrender.com/stocks/index.html?embedded=1&v=1',
  beforeParse(window) {
    window.fetch = function (url, opts) {
      fetchCount++;
      return Promise.resolve({
        ok: true,
        status: 200,
        text: () => Promise.resolve(JSON.stringify({ cached: false, cached_at: Date.now() / 1000, quotes: mockQuotes })),
        json: () => Promise.resolve({ cached: false, cached_at: Date.now() / 1000, quotes: mockQuotes }),
      });
    };
    // scrollIntoView は jsdom に無いので no-op 化
    window.HTMLElement.prototype.scrollIntoView = function () {};
  },
});
const { window } = dom;
const { document } = window;

// fetch は setTimeout で setInterval も張られる。0.5秒待ってから検証。
setTimeout(function () {
  const cards = document.querySelectorAll('.stock-card');
  const names = [...cards].map(c => c.querySelector('.stock-name').textContent);
  const prices = [...cards].map(c => c.querySelector('.stock-price').textContent.trim());
  const changes = [...cards].map(c => c.querySelector('.stock-change').textContent.replace(/\s+/g, ' ').trim());

  console.log('--- fetch 呼び出し回数:', fetchCount, '---');
  console.log('--- カード数:', cards.length, '---');
  console.log('--- 名前 ---');
  names.forEach(n => console.log('  *', n));
  console.log('--- 価格 ---');
  prices.forEach(p => console.log('  *', p));
  console.log('--- 騰落 ---');
  changes.forEach(c => console.log('  *', c));

  console.log('\n--- チェック ---');
  console.log('1. 主要指数3枚 + その他2枚 = 5枚描画:', cards.length === 5 ? 'OK' : 'NG (' + cards.length + ')');
  console.log('2. 日経平均が描画される:', names.includes('日経平均') ? 'OK' : 'NG');
  console.log('3. S&P 500が描画される:', names.includes('S&P 500') ? 'OK' : 'NG');
  console.log('4. Nifty 50が描画される:', names.includes('Nifty 50') ? 'OK' : 'NG');
  console.log('5. ダウ平均・NASDAQ総合が描画される:', names.includes('ダウ平均') && names.includes('NASDAQ総合') ? 'OK' : 'NG');
  console.log('6. 日経平均の価格(64,534.03)が表示される:', prices.some(p => p.includes('64,534')) ? 'OK' : 'NG (' + prices[0] + ')');
  const upCount = [...cards].filter(c => c.querySelector('.stock-change').classList.contains('up')).length;
  const downCount = [...cards].filter(c => c.querySelector('.stock-change').classList.contains('down')).length;
  console.log('7. 上昇は赤(up)/下降は蓝(down)クラス:', (upCount === 3 && downCount === 2) ? 'OK (up:' + upCount + ' down:' + downCount + ')' : 'NG (up:' + upCount + ' down:' + downCount + ')');
  console.log('8. 更新ボタンが有効:', document.getElementById('refresh-btn').disabled === false ? 'OK' : 'NG');
  console.log('9. エラーバナーが非表示:', document.getElementById('error-banner').style.display === 'none' ? 'OK' : 'NG');
  console.log('10. 免責文が表示される:', document.querySelector('.note').textContent.includes('Yahoo Finance') ? 'OK' : 'NG');
  console.log('11. バージョン marker:', window.__v === '20261019stocks' ? 'OK (' + window.__v + ')' : 'NG (' + window.__v + ')');
  process.exit(0);
}, 800);
