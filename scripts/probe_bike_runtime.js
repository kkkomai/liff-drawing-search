// Runtime probe for /bike/index.html
// Loads the page in jsdom, executes the inline script (Leaflet is mocked so
// the network call doesn't matter), stubs `liff` and `fetch`, simulates the
// host's postMessage credential handoff, and asserts the auth wait element
// disappears, the form is interactive, and the log list rendered the mock.
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require('jsdom');

const html = fs.readFileSync(path.join(__dirname, '..', 'bike', 'index.html'), 'utf8');

// Strip the Leaflet <script> tags — jsdom does not need them to validate the
// wiring, and the inline boot script does not call L.* during initial load.
const stripped = html
  .replace(/<link rel="stylesheet"[^>]*leaflet[^>]*>\s*/g, '')
  .replace(/<script src="https:\/\/unpkg\.com\/leaflet[^"]*"><\/script>\s*/g, '');

const inlineMatch = stripped.match(/<script>([\s\S]*?)<\/script>/);
if (!inlineMatch) { console.error('no inline script found'); process.exit(2); }
const bootJs = inlineMatch[1];

const virtualConsole = new VirtualConsole();
const logs = [];
virtualConsole.on('error', (e) => logs.push('ERR ' + (e.message || e)));
virtualConsole.on('jsdomError', (e) => logs.push('JSDOM ' + (e.message || e)));

const dom = new JSDOM(stripped, {
  url: 'http://localhost:11001/bike/index.html?embedded=1',
  runScripts: 'outside-only',
  pretendToBeVisual: true,
  virtualConsole,
});
const { window } = dom;

window.L = {
  map: () => ({ setView: () => ({}), panTo: () => ({}) }),
  tileLayer: () => ({ addTo: () => ({}) }),
  polyline: () => ({ setLatLngs: () => ({}), addTo: () => ({}) }),
  circleMarker: () => ({ setLatLng: () => ({}), addTo: () => ({}) }),
};

window.liff = {
  init: () => Promise.resolve(),
  isInClient: () => false,
  isLoggedIn: () => false,
  getContext: () => ({ userId: 'U-test-user' }),
  getDecodedIDToken: () => ({ jwt: 'mock.jwt.token' }),
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

window.fetch = (url) => {
  if (typeof url === 'string' && url.endsWith('/bike-logs')) {
    const body = JSON.stringify({ logs: [
      { id: 'x1', date: '2026-10-08', distance_km: 12.3, duration_min: 40,
        avg_speed_kmh: 18.5, title: 'mock ride', note: 'mock note',
        polyline: '[]', created: '2026-10-08T08:00:00+0900' }
    ] });
    return Promise.resolve(new window.Response(body, {
      status: 200, headers: { 'Content-Type': 'application/json' }
    }));
  }
  return Promise.resolve(new window.Response('{"logs":[]}', { status: 200 }));
};

try {
  window.eval(bootJs);
} catch (e) {
  console.error('boot script threw', e);
  process.exit(2);
}

(async () => {
  await new Promise(r => setTimeout(r, 250));

  window.postMessage({
    type: 'bike-credentials',
    lineUserId: 'U-test-user',
    idToken: 'mock.jwt.token',
    apiBase: 'http://localhost:11001',
  }, window.location.origin);

  // The boot script also starts a setInterval(postMessage(ask), 500ms) loop
  // because there is no real parent. Some of those events fire *after* the
  // initial credentials message; wait long enough for any race to settle.
  await new Promise(r => setTimeout(r, 1500));

  // Re-send in case the first one arrived before the listener was registered.
  window.postMessage({
    type: 'bike-credentials',
    lineUserId: 'U-test-user',
    idToken: 'mock.jwt.token',
    apiBase: 'http://localhost:11001',
  }, window.location.origin);

  await new Promise(r => setTimeout(r, 600));

  // Probe directly: does loadLogs succeed when called manually?
  let probeError = null;
  const fetchCalls = [];
  const origFetch = window.fetch;
  window.fetch = (u, o) => { fetchCalls.push(String(u)); return origFetch(u, o); };
  try { await window.loadLogs(0); } catch (e) { probeError = e.message; }
  await new Promise(r => setTimeout(r, 200));
  const doc2 = window.document;
  const diag = {
    STATE_logs_length: window.STATE && window.STATE.logs && window.STATE.logs.length,
    STATE_logs: window.STATE && window.STATE.logs,
    logListInnerHTML: doc2.getElementById('logList').innerHTML,
    logListText: doc2.getElementById('logList').textContent.slice(0, 200),
    probeError,
    fetchCalls,
  };
  const results = {
    authWaitHidden: doc2.getElementById('authWait').style.display === 'none',
    formCardOpacity: doc2.getElementById('formCard').style.opacity,
    apiStatusText: doc2.getElementById('apiStatus').textContent,
    logListTextHasMock: doc2.getElementById('logList').textContent.includes('mock ride'),
    saveBtnExists: !!doc2.getElementById('saveBtn'),
    dateInputValue: doc2.getElementById('date').value,
    consoleErrors: logs,
  };
  console.log('=== RUNTIME PROBE RESULTS ===');
  console.log(JSON.stringify({ ...results, diag }, null, 2));

  const fail = [];
  if (!results.authWaitHidden) fail.push('authWait should be hidden after credentials arrive');
  if (results.formCardOpacity !== '1') fail.push('formCard opacity should be 1, got: ' + results.formCardOpacity);
  if (!results.saveBtnExists) fail.push('save button missing');
  if (!results.logListTextHasMock) fail.push('log list should contain mock ride');
  if (!results.dateInputValue) fail.push('date input should be pre-filled');
  if (results.consoleErrors.length) fail.push('console errors: ' + results.consoleErrors.join(' | '));
  if (fail.length) {
    console.error('FAIL\n' + fail.map(f => ' - ' + f).join('\n'));
    process.exit(1);
  }
  console.log('OK');
  process.exit(0);
})().catch(e => { console.error('threw', e); process.exit(2); });
