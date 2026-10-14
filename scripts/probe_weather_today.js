// 天気タブの「カード選択 → 一番上の表示が連動」を jsdom で検証するプローブ。
// 1. renderWeather で初期表示（今日=先頭カード）
// 2. カード2番目をクリック
// 3. 一番上の表示が2番目のカードの予報に切り替わることを確認
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

const today = new Date();
const ymd = (d) => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
const dayAfter = new Date(today.getTime() + 2 * 864e5);

const mockData = {
  place_name: '草津市',
  forecast: {
    current: { weather_code: 0, temperature_2m: 18.5, wind_speed_10m: 3.2, wind_direction_10m: 180 },
    daily: {
      time: [ymd(today), ymd(new Date(today.getTime() + 864e5)), ymd(dayAfter)],
      weather_code: [61, 2, 95],                     // 今日=雨, 明日=曇り時々晴, 明後日=雷雨
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

window.eval(weatherJs);
window.weatherData = mockData;
window.weatherLat = 35.0116;
window.weatherLng = 135.7681;
window.renderWeather(mockData, 35.0116, 135.7681);

function topText() {
  return window.document.getElementById('weather-current').textContent.replace(/\s+/g, ' ').trim();
}
function h2Text() {
  return window.document.getElementById('weather-status-h').textContent;
}

setTimeout(function () {
  console.log('=== 初期表示（今日=カード0） ===');
  console.log('上部:', topText());
  console.log('見出し:', h2Text());
  const ok1 = topText().includes('22.3') && topText().includes('今日') && h2Text().startsWith('今日の予報');

  // カード2番目（明後日・雷雨）をクリック
  const cards = window.document.querySelectorAll('.weather-day-card');
  console.log('\nカード数:', cards.length);
  cards[2].click();

  console.log('\n=== カード2（明後日・雷雨）選択後 ===');
  console.log('上部:', topText());
  console.log('見出し:', h2Text());
  const ok2 = topText().includes('26.8') && topText().includes('雷雨') && !topText().includes('22.3');
  const ok3 = h2Text().includes('の予報') && !h2Text().startsWith('今日の予報');
  const cardSelected = cards[2].classList.contains('selected') && !cards[0].classList.contains('selected');

  console.log('\n--- チェック ---');
  console.log('1. 初期表示が今日の予報:', ok1 ? 'OK' : 'NG');
  console.log('2. 選択カードの気温(26.8℃)に連動:', ok2 ? 'OK' : 'NG');
  console.log('3. 見出しが日付付き予報に変化:', ok3 ? 'OK' : 'NG (' + h2Text() + ')');
  console.log('4. カードの選択ハイライトも移動:', cardSelected ? 'OK' : 'NG');
  console.log('5. 時間帯別詳細も同じ日:', window.document.getElementById('weather-detail-title').textContent);

  // カード0（今日）に戻す
  cards[0].click();
  console.log('\n=== カード0（今日）に戻した後 ===');
  console.log('上部:', topText().slice(0, 60) + '...');
  console.log('見出し:', h2Text());
  console.log('6. 今日に戻ると「今日の予報」に復帰:', h2Text().startsWith('今日の予報') && topText().includes('22.3') ? 'OK' : 'NG');
}, 500);
