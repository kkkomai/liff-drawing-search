// Runtime probe for /route/index.html
// Loads the page in jsdom, executes the inline script (Leaflet stubbed),
// stubs `liff` and `fetch`, simulates the host's postMessage credential
// handoff, and asserts: the auth wait element disappears, the map draws,
// the log list renders mock items, and clicking a GPS item selects that
// polyline on the map.
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require('jsdom');

const html = fs.readFileSync(path.join(__dirname, '..', 'route', 'index.html'), 'utf8');
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
  url: 'http://localhost:11001/route/index.html?embedded=1',
  runScripts: 'outside-only',
  pretendToBeVisual: true,
  virtualConsole,
});
const { window } = dom;

// Leaflet stub: track polylines added so we can assert selectRoute dims the
// others and highlights the selected one.
const polylineStyles = new Map();
const markerLog = [];
window.L = {
  map: () => {
    // Leaflet's map().setView() returns the map itself for chaining. The
    // route page does `L.map('map', ...).setView(...)` and assigns that
    // to STATE.map — the stub has to return `this` from setView so
    // STATE.map ends up holding the real map object (with fitBounds,
    // removeLayer, ...), not an empty {}.
    const m = {};
    m.setView = () => m;
    m.fitBounds = () => m;
    m.panTo = () => m;
    m.removeLayer = () => m;
    return m;
  },
  tileLayer: () => ({ addTo: () => ({}) }),
  polyline: (latlngs, opts) => {
    const layer = {
      _latlngs: latlngs,
      setStyle: (newOpts) => {
        const cur = polylineStyles.get(layer) || opts || {};
        polylineStyles.set(layer, Object.assign({}, cur, newOpts));
      },
      getLatLngs: () => latlngs,
      addTo: () => layer,
      bindTooltip: () => layer,
    };
    polylineStyles.set(layer, opts || {});
    return layer;
  },
  circleMarker: (p, opts) => {
    markerLog.push(p);
    const marker = {
      addTo: () => marker,
      bindTooltip: () => marker,
    };
    return marker;
  },
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

// Two logs: one with GPS polyline, one without. Verifies the page draws
// all-routes initially and selectRoute works on the GPS one.
const polylineA = [
  [35.0116, 135.7681], [35.0200, 135.7700], [35.0250, 135.7750], [35.0300, 135.7800],
];
window.fetch = (url) => {
  if (typeof url === 'string' && url.endsWith('/bike-logs')) {
    const body = JSON.stringify({ logs: [
      {
        id: 'gps-1', date: '2026-10-08', distance_km: 12.3, duration_min: 40,
        avg_speed_kmh: 18.5, title: 'A ルート', note: 'mock note A',
        polyline: JSON.stringify(polylineA),
        created: '2026-10-08T08:00:00+0900',
      },
      {
        id: 'nogps-1', date: '2026-10-07', distance_km: 5.0, duration_min: 20,
        avg_speed_kmh: 15.0, title: '手入力', note: 'GPSなし',
        polyline: '[]', created: '2026-10-07T19:00:00+0900',
      },
    ] });
    return Promise.resolve(new window.Response(body, {
      status: 200, headers: { 'Content-Type': 'application/json' }
    }));
  }
  return Promise.resolve(new window.Response('{"logs":[]}', { status: 200 }));
};

try { window.eval(bootJs); } catch (e) { console.error('boot script threw', e); process.exit(2); }

// jsdom does not implement scrollIntoView; the page calls it on the
// selected list item. Provide a no-op so the click handler does not throw.
window.HTMLElement.prototype.scrollIntoView = function () {};

(async () => {
  await new Promise(r => setTimeout(r, 200));
  window.postMessage({
    type: 'route-credentials',
    lineUserId: 'U-test-user',
    idToken: 'mock.jwt.token',
    apiBase: 'http://localhost:11001',
  }, window.location.origin);
  // Re-send after the parent-post loop may have raced; the boot script
  // asks the parent every 500ms.
  await new Promise(r => setTimeout(r, 1500));
  window.postMessage({
    type: 'route-credentials',
    lineUserId: 'U-test-user',
    idToken: 'mock.jwt.token',
    apiBase: 'http://localhost:11001',
  }, window.location.origin);
  await new Promise(r => setTimeout(r, 600));

  const doc = window.document;
  // Click the GPS log item and assert selectRoute fires.
  let selectError = null;
  let selectStack = null;
  try { window.selectRoute('gps-1'); } catch (e) { selectError = e.message; selectStack = e.stack; }
  await new Promise(r => setTimeout(r, 100));

  const results = {
    authWaitHidden: doc.getElementById('authWait').style.display === 'none',
    apiStatusText: doc.getElementById('apiStatus').textContent,
    listItemCount: doc.querySelectorAll('.item').length,
    listHasGps: doc.getElementById('listWrap').textContent.includes('A ルート'),
    listHasNoGps: doc.getElementById('listWrap').textContent.includes('GPSデータなし'),
    polylineCountAfterSelect: polylineStyles.size,
    selectedStyle: (() => {
      // After selectRoute('gps-1'), at least one polyline should be green (#16a34a).
      let found = null;
      polylineStyles.forEach((opts) => {
        if (opts && opts.color === '#16a34a') found = opts;
      });
      return found;
    })(),
    markers: markerLog.length,
    selectError,
    selectStack,
    consoleErrors: logs,
  };
  console.log('=== RUNTIME PROBE RESULTS ===');
  console.log(JSON.stringify(results, null, 2));

  const fail = [];
  if (!results.authWaitHidden) fail.push('authWait should be hidden after credentials arrive');
  if (!results.listHasGps) fail.push('list should contain GPS log');
  if (!results.listHasNoGps) fail.push('list should mark no-GPS log');
  if (results.listItemCount < 2) fail.push('expected 2 list items, got ' + results.listItemCount);
  if (results.polylineCountAfterSelect < 1) fail.push('expected at least one polyline drawn');
  if (!results.selectedStyle) fail.push('selected polyline should be highlighted in green');
  if (results.markers < 2) fail.push('expected start+end markers, got ' + results.markers);
  if (results.selectError) fail.push('selectRoute threw: ' + results.selectError + '\n' + (results.selectStack || ''));
  if (results.consoleErrors.length) fail.push('console errors: ' + results.consoleErrors.join(' | '));
  if (fail.length) {
    console.error('FAIL\n' + fail.map(f => ' - ' + f).join('\n'));
    process.exit(1);
  }
  console.log('OK');
  process.exit(0);
})().catch(e => { console.error('threw', e); process.exit(2); });
