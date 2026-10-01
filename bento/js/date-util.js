// 日付ユーティリティ（すべて Asia/Tokyo 基準、YYYY-MM-DD 文字列で扱う）
(function (global) {
  'use strict';

  var WEEKDAYS = ['日', '月', '火', '水', '木', '金', '土'];

  function pad(n) {
    return n < 10 ? '0' + n : String(n);
  }

  // JST の「今日」を YYYY-MM-DD で返す（PC のローカルタイムゾーンに依存させない）
  function todayJST() {
    var now = new Date(Date.now() + 9 * 60 * 60 * 1000); // UTC に +9h した「JST相当」
    return toDateStr(now.getUTCFullYear(), now.getUTCMonth() + 1, now.getUTCDate());
  }

  function toDateStr(y, m, d) {
    return y + '-' + pad(m) + '-' + pad(d);
  }

  // YYYY-MM-DD -> {y,m,d}
  function parse(dateStr) {
    var parts = String(dateStr).split('-');
    return {
      y: parseInt(parts[0], 10),
      m: parseInt(parts[1], 10),
      d: parseInt(parts[2], 10)
    };
  }

  // 日付文字列に days を加算（UTC 演算で DST 等 Textile 影響を受けない）
  function addDays(dateStr, days) {
    var p = parse(dateStr);
    var dt = new Date(Date.UTC(p.y, p.m - 1, p.d));
    dt.setUTCDate(dt.getUTCDate() + days);
    return toDateStr(dt.getUTCFullYear(), dt.getUTCMonth() + 1, dt.getUTCDate());
  }

  // 曜日 (0=日)
  function weekday(dateStr) {
    var p = parse(dateStr);
    return new Date(Date.UTC(p.y, p.m - 1, p.d)).getUTCDay();
  }

  function weekdayLabel(dateStr) {
    return WEEKDAYS[weekday(dateStr)];
  }

  // 「10/01 (木)」形式（曜日付き）
  function formatLabel(dateStr) {
    var p = parse(dateStr);
    return pad(p.m) + '/' + pad(p.d) + ' (' + weekdayLabel(dateStr) + ')';
  }

  // 「10/01(木)」
  function formatShort(dateStr) {
    var p = parse(dateStr);
    return pad(p.m) + '/' + pad(p.d) + '(' + weekdayLabel(dateStr) + ')';
  }

  function compare(a, b) {
    return a < b ? -1 : a > b ? 1 : 0;
  }

  function isBefore(dateStr, base) {
    return compare(dateStr, base) < 0;
  }

  function isWeekend(dateStr) {
    var w = weekday(dateStr);
    return w === 0 || w === 6;
  }

  // today を含む [pastDays, futureDays] の日付配列
  function range(today, pastDays, futureDays) {
    var start = addDays(today, -Math.max(0, pastDays));
    var end = addDays(today, Math.max(0, futureDays));
    var out = [];
    var cur = start;
    var guard = 0;
    while (compare(cur, end) <= 0 && guard < 2000) {
      out.push(cur);
      cur = addDays(cur, 1);
      guard++;
    }
    return out;
  }

  global.DateUtil = {
    WEEKDAYS: WEEKDAYS,
    todayJST: todayJST,
    addDays: addDays,
    weekday: weekday,
    weekdayLabel: weekdayLabel,
    formatLabel: formatLabel,
    formatShort: formatShort,
    compare: compare,
    isBefore: isBefore,
    isWeekend: isWeekend,
    range: range,
    parse: parse
  };
})(window);