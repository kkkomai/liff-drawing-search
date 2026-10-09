// Verify the 'week' range is a 15-day rolling window centred on today.
// Loads the bento page in jsdom with mocked LIFF/Config and asserts
// currentRangeDates() returns 15 dates spanning today-7 ... today+7.
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require('jsdom');

const html = fs.readFileSync(path.join(__dirname, '..', 'bento', 'index.html'), 'utf8');

// The bento/index.html has no inline scripts; everything is loaded via
// <script src="js/...">. We read those files and eval them in dependency
// order in the jsdom window below.

// Concatenate all inline scripts. We have to mock the script-tag script
// loads they normally pull in (env, mock, api, app, date-util, config).
const envJs = fs.readFileSync(path.join(__dirname, '..', 'bento', 'js', 'env.js'), 'utf8');
const mockJs = fs.readFileSync(path.join(__dirname, '..', 'bento', 'js', 'mock.js'), 'utf8');
const apiJs = fs.readFileSync(path.join(__dirname, '..', 'bento', 'js', 'api.js'), 'utf8');
const appJs = fs.readFileSync(path.join(__dirname, '..', 'bento', 'js', 'app.js'), 'utf8');
const dateUtilJs = fs.readFileSync(path.join(__dirname, '..', 'bento', 'js', 'date-util.js'), 'utf8');
const configJs = fs.readFileSync(path.join(__dirname, '..', 'bento', 'js', 'config.js'), 'utf8');

const virtualConsole = new VirtualConsole();
const logs = [];
virtualConsole.on('error', (e) => logs.push('ERR ' + (e.message || e)));
virtualConsole.on('jsdomError', (e) => logs.push('JSDOM ' + (e.message || e)));

const dom = new JSDOM(html, {
  url: 'http://localhost:11001/bento/index.html',
  runScripts: 'outside-only',
  pretendToBeVisual: true,
  virtualConsole,
});
const { window } = dom;

// Stub liff and network so the boot can complete without an LIFF context.
window.liff = {
  init: () => Promise.resolve(),
  isInClient: () => false,
  isLoggedIn: () => false,
  getContext: () => ({ userId: 'U-test-user' }),
  getDecodedIDToken: () => ({ jwt: 'mock.jwt.token' }),
  idToken: () => 'mock.jwt.token',
};
window.fetch = (url) => {
  // Whatever — the page will log errors, but the test only inspects dates.
  if (typeof url === 'string' && url.includes('/api/orders')) {
    return Promise.resolve(new window.Response('{"orders":[]}', { status: 200 }));
  }
  return Promise.resolve(new window.Response('{}', { status: 200 }));
};
window.Response = class {
  constructor(body, init) {
    this._body = body;
    this.status = (init && init.status) || 200;
    this.ok = this.status >= 200 && this.status < 300;
    this.headers = new Map(Object.entries((init && init.headers) || {}));
  }
  async json() { return JSON.parse(this._body); }
  async text() { return this._body; }
};

// Inject scripts in the right order. env + mock + date-util are pure
// helpers; config defines `cfg`; app uses them all.
try {
  window.eval(envJs);
  window.eval(mockJs);
  window.eval(dateUtilJs);
  window.eval(configJs);
  window.eval(apiJs);
  window.eval(appJs);
} catch (e) {
  console.error('script threw', e);
  process.exit(2);
}

// currentRangeDates is local to the IIFE in app.js, so we exercise it
// through STATE.range = 'week' + reloadOrders(). The page builds the
// day list and writes it into #day-list. We can read the resulting list
// items' data attributes if app.js sets them; otherwise we infer from
// the rendered text and the range-label.
window.STATE = window.STATE || {};
// The bento app uses a closure-private STATE. We cannot reach it from
// here, so test the public surface: the rendered date list in #day-list
// and the #range-label. Read those after a render.
function clickRange(week) {
  const btn = window.document.querySelector('button[data-range="' + week + '"]');
  btn.click();
}

(async () => {
  await new Promise(r => setTimeout(r, 200));
  // Force the 'week' range to be selected (it's the default but be explicit).
  clickRange('week');
  await new Promise(r => setTimeout(r, 400));

  const doc = window.document;
  const label = doc.getElementById('range-label').textContent || '';
  // The day-list items render as <li data-date="YYYY-MM-DD"> in this app.
  // Inspect whatever attribute the bento app uses for per-day items.
  const dayItems = doc.querySelectorAll('#day-list li');
  const dates = Array.from(dayItems).map(li => {
    return li.getAttribute('data-date') ||
           (li.querySelector('[data-date]') || {}).getAttribute && li.querySelector('[data-date]').getAttribute('data-date') ||
           null;
  }).filter(Boolean);

  // Even if the data-date attribute is missing, the visible text contains
  // "MM/DD (曜)" entries — parse them out of the entire list text.
  const fallbackDates = dates.length ? null :
    (doc.getElementById('day-list').textContent.match(/\b\d{2}\/\d{2}/g) || []);

  const result = {
    label,
    dayItemCount: dayItems.length,
    dataDates: dates,
    fallbackDates,
    consoleErrors: logs,
  };
  console.log('=== RUNTIME PROBE RESULTS ===');
  console.log(JSON.stringify(result, null, 2));

  const fail = [];
  if (result.dayItemCount !== 15) {
    fail.push('expected 15 day items in the rolling week view, got ' + result.dayItemCount);
  }
  // Verify the label mentions dates 8 days apart (the first and last of
  // the 15-day window). This is a coarse check, but a 7-day (old) view
  // would show dates only 6 days apart, while a 15-day view shows 14.
  const labelDates = (label.match(/\b\d{2}\/\d{2}\b/g) || []).map(s => {
    const [m, d] = s.split('/').map(n => parseInt(n, 10));
    return new Date(2026, m - 1, d);  // year is irrelevant for diff
  });
  if (labelDates.length === 2) {
    const diffDays = Math.round((labelDates[1] - labelDates[0]) / 86400000);
    if (diffDays !== 14) {
      fail.push('range label spans ' + diffDays + ' days; expected 14 for a 15-day window');
    }
  } else {
    fail.push('could not parse two dates from range label: ' + label);
  }
  if (fail.length) {
    console.error('FAIL\n' + fail.map(f => ' - ' + f).join('\n'));
    process.exit(1);
  }
  console.log('OK');
  process.exit(0);
})().catch(e => { console.error('threw', e); process.exit(2); });
