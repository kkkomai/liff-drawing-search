// 天気タブのカード順序を検証するプローブ (2026-10-15 ユーザー指示版)。
// 「今後16日」カードを一番上に移動した後の順序と、
// 既存の連動挙動（カード選択 → 上部表示切替）が壊れていないことを確認する。
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('index.html', 'utf8');

function extractFn(src, name) {
  const i = src.indexOf('function ' + name);
  if (i < 0) return null;
  const j = src.indexOf('\n    function ', i + 10);
  return src.slice(i, j > 0 ? j : i + 6000);
}
function extractVar(src, name, endMarker) {
  const i = src.indexOf('var ' + name);
  if (i < 0) return null;
  return src.slice(i, src.indexOf(endMarker, i));
}

const weatherJs = [
  extractVar(html, 'WEATHER_DEFAULT', 'var weatherTabLoaded'),
  extractFn(html, 'loadWeatherTab'),
  extractVar(html, 'WMO_CODES', 'function wmoLookup'),
  extractFn(html, 'wmoLookup'),
  extractFn(html, 'degToCompass'),
  extractFn(html, 'rideVerdict'),
  extractFn(html, 'escHtml'),
  extractFn(html, 'renderWeather'),
  extractFn(html, 'renderWeatherTop'),
  extractFn(html, 'renderWeatherDetail'),
].filter(Boolean).join('\n');

const dom = new JSDOM(`<!DOCTYPE html><html><body>
<div id="weather-root"></div>
<div id="weather-current"></div>
<div id="weather-status-h"></div>
<div id="weather-daily"></div>
<div id="weather-detail-card"></div>
<div id="weather-detail-title"></div>
<div id="weather-detail-body"></div>
</body></html>`, { runScripts: 'dangerously', url: 'https://liff-drawing-search.onrender.com/' });

const { window } = dom;
window.weatherData = null;
window.weatherLat = null;
window.weatherLng = null;
window.log = function () {};

window.eval(weatherJs);
window.loadWeatherTab();

const root = window.document.getElementById('weather-root');
const cards = root.querySelectorAll('.card');
const results = [];
function check(name, cond) {
  results.push([name, !!cond]);
  console.log((cond ? 'OK  ' : 'NG  ') + name);
}

console.log('=== カード順序 (loadWeatherTab 直後) ===');
console.log('カード数:', cards.length);
cards.forEach((c, i) => console.log('  [' + i + ']', c.textContent.replace(/\s+/g, ' ').trim().slice(0, 40)));

// 1. 一番上のカードが「今後16日」
check('1. 一番上のカードが「今後16日」', cards[0] && cards[0].textContent.includes('今後16日'));
check('2. 一番上に weather-daily スクローラー', !!cards[0].querySelector('#weather-daily'));
// 2. 2番目のカードが「今日の予報」
check('3. 2番目のカードが「今日の予報」', cards[1] && !!cards[1].querySelector('#weather-status-h') && cards[1].textContent.includes('今日の予報'));
// 3. 3番目が時間帯別詳細
check('4. 最後のカードが時間帯別詳細 (weather-detail-card)', cards[2] && cards[2].id === 'weather-detail-card');

// 位置情報CTAも2番目のカード内に存在すること
check('5. 現在地/草津駅ボタンが2番目カード内', !!cards[1].querySelector('#weather-locate-btn') && !!cards[1].querySelector('#weather-default-btn'));

// モックデータで既存の連動挙動を回帰確認
const today = new Date();
const ymd = (d) => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
const dayAfter = new Date(today.getTime() + 2 * 864e5);
const mockData = {
  place_name: '草津市',
  forecast: {
    current: { weather_code: 0, temperature_2m: 18.5, wind_speed_10m: 3.2, wind_direction_10m: 180 },
    daily: {
      time: [ymd(today), ymd(new Date(today.getTime() + 864e5)), ymd(dayAfter)],
      weather_code: [61, 2, 95],
      temperature_2m_max: [22.3, 24.0, 26.8],
      temperature_2m_min: [15.1, 16.0, 19.4],
      precipitation_probability_max: [80, 10, 90],
      wind_speed_10m_max: [5.5, 4.0, 9.9],
      wind_direction_10m_dominant: [90, 180, 270],
    },
    hourly: {
      time: [], temperature_2m: [], precipitation_probability: [], wind_speed_10m: [], weather_code: [],
    },
  },
};
window.weatherData = mockData;
window.weatherLat = 35.0116;
window.weatherLng = 135.7681;
window.renderWeather(mockData, 35.0116, 135.7681);

setTimeout(function () {
  console.log('\n=== 回帰: カード選択連動 ===');
  function topText() {
    return window.document.getElementById('weather-current').textContent.replace(/\s+/g, ' ').trim();
  }
  function h2Text() {
    return window.document.getElementById('weather-status-h').textContent;
  }
  check('6. 初期表示が今日の予報 (22.3℃)', topText().includes('22.3') && h2Text().startsWith('今日の予報'));
  const dayCards = window.document.querySelectorAll('.weather-day-card');
  dayCards[2].click();
  check('7. カード2選択で上部が連動 (26.8℃/雷雨)', topText().includes('26.8') && topText().includes('雷雨') && !topText().includes('22.3'));
  check('8. 見出しが日付付きに変化', h2Text().includes('の予報') && !h2Text().startsWith('今日の予報'));
  check('9. カードハイライトが移動', dayCards[2].classList.contains('selected') && !dayCards[0].classList.contains('selected'));
  dayCards[0].click();
  check('10. 今日に戻ると「今日の予報」に復帰', h2Text().startsWith('今日の予報') && topText().includes('22.3'));

  const ng = results.filter(r => !r[1]).length;
  console.log('\n=== 結果: ' + (results.length - ng) + '/' + results.length + ' OK ===');
  process.exit(ng ? 1 : 0);
}, 500);
