// 天気タブの「今日の予報」表示を jsdom で検証するプローブ。
// renderWeather にモックデータ（current + daily）を流し、
// 一番上の表示が「今日の予報」（日別データ）になっているか確認する。
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('index.html', 'utf8');

function extractFn(src, name) {
  const i = src.indexOf('function ' + name);
  if (i < 0) return null;
  const j = src.indexOf('\n    function ', i + 10);
  return src.slice(i, j > 0 ? j : i + 4000);
}

// renderWeather は長いので丸ごと抽出（次の function まで）
function extractLong(src, name) {
  const i = src.indexOf('function ' + name);
  if (i < 0) return null;
  const j = src.indexOf('\n    function ', i + 10);
  return src.slice(i, j > 0 ? j : i + 6000);
}

// var XXX = ...; 形式の宣言を抽出（function キーワードではない）
// 内部に ; が多数あるので、次の function 宣言までを範囲とする
function extractVar(src, name, endMarker) {
  const i = src.indexOf('var ' + name);
  if (i < 0) return null;
  const end = src.indexOf(endMarker, i);
  return src.slice(i, end);
}

const weatherJs = [
  extractVar(html, 'WMO_CODES', 'function wmoLookup'),
  extractFn(html, 'wmoLookup'),
  extractFn(html, 'degToCompass'),
  extractFn(html, 'rideVerdict'),
  extractFn(html, 'escHtml'),
  extractLong(html, 'renderWeather'),
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
window.log = function () {};
window.alert = function () {};

// モック: Open-Meteo のレスポンス形状（current と daily の値が違うことに注目）
const today = new Date();
const ymd = (d) => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
const mockData = {
  place_name: '草津市',
  forecast: {
    current: {
      weather_code: 0,          // 現在: 快晴（今は晴れ）
      temperature_2m: 18.5,     // 現在の気温
      wind_speed_10m: 3.2,
      wind_direction_10m: 180,
    },
    daily: {
      time: [ymd(today), ymd(new Date(today.getTime() + 864e5)), ymd(new Date(today.getTime() + 2 * 864e5))],
      weather_code: [61, 0, 2],                    // 今日の予報: 雨（61=雨）
      temperature_2m_max: [22.3, 24.0, 20.1],
      temperature_2m_min: [15.1, 16.0, 14.2],
      precipitation_probability_max: [80, 10, 30], // 今日の降水確率: 80%
      wind_speed_10m_max: [5.5, 4.0, 6.2],
      wind_direction_10m_dominant: [90, 180, 270],
    },
    hourly: {
      time: [ymd(today) + 'T00:00', ymd(today) + 'T03:00', ymd(today) + 'T06:00'],
      temperature_2m: [16, 18, 19],
      precipitation_probability: [40, 80, 60],
      wind_speed_10m: [3, 5, 4],
      weather_code: [61, 61, 3],
    },
  },
};

window.eval(weatherJs);
window.weatherData = mockData;
window.renderWeather(mockData, 35.0, 135.9);

setTimeout(function () {
  const cur = window.document.getElementById('weather-current');
  const h2 = window.document.getElementById('weather-status-h');
  console.log('=== 見出し ===');
  console.log(h2.textContent);
  console.log();
  console.log('=== 一番上の表示 ===');
  console.log(cur.textContent.replace(/\s+/g, ' ').trim());
  console.log();
  console.log('--- チェック ---');
  const text = cur.textContent;
  console.log('1. 今日の日付が明記されている:', /今日\s*\d+\/\d+/.test(text) ? 'OK' : 'NG');
  console.log('2. 天気が「雨」（日別61=雨、current=0=快晴ではない）:', text.includes('雨') ? 'OK（日別予報を使用）' : 'NG');
  console.log('3. 最高気温22.3℃（日別の値）:', text.includes('22.3') ? 'OK' : 'NG');
  console.log('4. 最低気温15.1℃（日別の値）:', text.includes('15.1') ? 'OK' : 'NG');
  console.log('5. 降水確率80%（日別の値）:', text.includes('80') ? 'OK' : 'NG');
  console.log('6. 最大風速5.5m/s（日別の値）:', text.includes('5.5') ? 'OK' : 'NG');
  console.log('7. 現在値18.5℃が混ざっていない:', !text.includes('18.5') ? 'OK' : 'NG（currentが混入）');
  console.log('8. 見出しが「今日の予報」:', h2.textContent.startsWith('今日の予報') ? 'OK' : 'NG');
}, 500);
