#!/usr/bin/env node
/* Load index.html in a real DOM, run the inline script, and report what the
 * page actually did: did the tab strip get wired, did the calendar grid get
 * day cells, and what did console.error / uncaught exceptions report?
 *
 * This is the missing check: syntax parsing passed and static grep found every
 * function, yet the calendar stays blank in LINE — which means something throws
 * at runtime. Only executing the script surfaces that.
 */
'use strict';
const fs = require('fs');
const path = require('path');

let jsdom;
try {
  jsdom = require(path.join(__dirname, 'node_modules', 'jsdom'));
} catch (e) {
  try { jsdom = require('jsdom'); }
  catch (e2) {
    console.log('SKIP: jsdom is not installed.');
    console.log('      npm i -D jsdom   (from ' + __dirname + ')');
    process.exit(3);
  }
}

const file = process.argv[2] || 'index.html';
const html = fs.readFileSync(file, 'utf8');

const errors = [];
const logs = [];

const virtualConsole = new jsdom.VirtualConsole();
virtualConsole.on('jsdomError', (e) => errors.push('jsdomError: ' + (e.detail ? e.detail.stack || e.detail : e.message)));
virtualConsole.on('error', (...a) => errors.push('console.error: ' + a.join(' ')));
virtualConsole.on('log', (...a) => logs.push(a.join(' ')));
virtualConsole.on('warn', (...a) => logs.push('[warn] ' + a.join(' ')));

const dom = new jsdom.JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://localhost:10000/index.html',
  pretendToBeVisual: true,
  virtualConsole,
  resources: undefined, // do not fetch /sdk.js — stubbed below
  beforeParse(window) {
    // The page waits on liff.init(); stub the SDK so it resolves immediately.
    window.liff = {
      init: () => Promise.resolve({}),
      ready: Promise.resolve(),
      getDecodedID: () => 'Utest',
      idToken: 'stub.token.value'
    };
    // Keep fetch from touching the network in the probe.
    window.fetch = () => Promise.resolve({
      ok: true,
      status: 200,
      text: () => Promise.resolve(JSON.stringify({ schedules: [] }))
    });
    window.addEventListener('error', (e) => {
      errors.push('window.onerror: ' + (e.error ? (e.error.stack || e.error.message) : e.message));
    });
    window.addEventListener('unhandledrejection', (e) => {
      const r = e.reason;
      errors.push('unhandledrejection: ' + (r && r.stack ? r.stack : r));
    });
  }
});

setTimeout(() => {
  const doc = dom.window.document;
  const q = (s) => doc.querySelector(s);

  const grid = doc.getElementById('overtime-calendar-grid');
  const monthYear = doc.getElementById('overtime-month-year');
  const tabs = [...doc.querySelectorAll('.tab')];
  const active = q('.tab-content.active');

  console.log('=== ' + file + ' (runtime probe) ===');
  console.log('  tabs found            :', tabs.length, tabs.map(t => t.dataset.tab).join(','));
  console.log('  active tab content    :', active ? '#' + active.id : 'NONE');
  console.log('  month-year label      :', monthYear ? JSON.stringify(monthYear.textContent) : 'MISSING');
  console.log('  calendar grid exists  :', !!grid);
  console.log('  calendar day cells    :', grid ? grid.querySelectorAll('*').length : 0);

  console.log('');
  console.log('=== errors (' + errors.length + ') ===');
  errors.slice(0, 8).forEach(e => console.log('  ' + e.split('\n').slice(0, 3).join('\n  ')));

  console.log('');
  console.log('=== last log lines ===');
  logs.slice(-8).forEach(l => console.log('  ' + l));

  const cells = grid ? grid.querySelectorAll('*').length : 0;
  console.log('');
  console.log('VERDICT:', cells > 0
    ? 'calendar RENDERS — blank grid is not a code path in this environment'
    : 'calendar BLANK — see errors above');
  process.exit(0);
}, 4000);