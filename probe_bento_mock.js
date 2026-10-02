#!/usr/bin/env node
/* Verify the bento app boots into a visible UI in mock mode.
 *
 * Mock mode exists so the screen can be reviewed without the FastAPI service;
 * before, the mock path still called the API and showed a 404. This drives the
 * real page in jsdom and asserts the employee view is actually visible. */
'use strict';
const path = require('path');
const fs = require('fs');

let jsdom;
try { jsdom = require('jsdom'); }
catch (e) {
  console.log('SKIP: jsdom not installed (npm i -D jsdom)');
  process.exit(3);
}

const file = process.argv[2] || 'bento/index.html';
const dir = path.dirname(file);
const html = fs.readFileSync(file, 'utf8');

const errors = [];
const vc = new jsdom.VirtualConsole();
vc.on('jsdomError', (e) => errors.push('jsdomError: ' + (e.detail ? (e.detail.stack || e.detail) : e.message)));
vc.on('error', (...a) => errors.push('console.error: ' + a.join(' ')));

// jsdom >=27 replaced ResourceLoader with requestInterceptor. Serve the sibling
// scripts off disk so <script src="js/..."> resolves relative to the file.
const baseDir = path.resolve(dir);
let interceptor = null;
if (typeof jsdom.requestInterceptor === 'function') {
  interceptor = jsdom.requestInterceptor((req) => {
    let pathname = '';
    try { pathname = new URL(req.url).pathname; } catch (e) { return; }
    const rel = decodeURIComponent(pathname).replace(/^\/+/, '');
    const local = path.resolve(baseDir, rel);
    if (local.startsWith(baseDir) && fs.existsSync(local) && fs.statSync(local).isFile()) {
      req.respond({
        statusCode: 200,
        headers: { 'content-type': 'text/javascript; charset=utf-8' },
        body: fs.readFileSync(local, 'utf8')
      });
    } else {
      req.respond({ statusCode: 404, headers: {}, body: '' });
    }
  });
}

const dom = new jsdom.JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://localhost:10000/bento/index.html?mockLineUserId=Utest',
  pretendToBeVisual: true,
  resources: "usable",
  virtualConsole: vc,
  beforeParse(window) {
    window.fetch = () => Promise.resolve({
      ok: true, status: 200,
      text: () => Promise.resolve('{"success":true}')
    });
    window.liff = { init: () => Promise.resolve({}), ready: Promise.resolve(), idToken: null };
    window.addEventListener('error', (e) => errors.push('onerror: ' + (e.error ? (e.error.stack || e.error.message) : e.message)));
  }
});

setTimeout(() => {
  const doc = dom.window.document;
  const app = doc.getElementById('app');
  const splash = doc.getElementById('splash');

  const isHidden = (el) => {
    if (!el) return true;
    if (el.classList && el.classList.contains('hidden')) return true;
    const s = doc.defaultView.getComputedStyle(el);
    return s.display === 'none' || s.visibility === 'hidden';
  };

  const splashTitle = doc.getElementById('splash-title');
  const splashMsg = doc.getElementById('splash-msg');
  const userName = doc.getElementById('user-name');
  const dayList = doc.getElementById('day-list');

  console.log('=== bento mock-mode boot (' + file + ') ===');
  console.log('  #app hidden?      :', isHidden(app));
  console.log('  #splash hidden?   :', isHidden(splash));
  console.log('  splash title      :', splashTitle ? JSON.stringify(splashTitle.textContent) : '-');
  console.log('  splash message    :', splashMsg ? JSON.stringify(splashMsg.textContent) : '-');
  console.log('  employee name     :', userName ? JSON.stringify(userName.textContent) : '-');
  console.log('  day-list children :', dayList ? dayList.children.length : 0);
  console.log('  runtime errors    :', errors.length);
  errors.slice(0, 5).forEach((e) => console.log('    ' + String(e).split('\n')[0]));

  const booted = !isHidden(app);
  console.log('');
  console.log('VERDICT:', booted
    ? 'UI RENDERS in mock mode'
    : 'UI did NOT render — splash is still showing');
  process.exit(booted ? 0 : 1);
}, 4000);