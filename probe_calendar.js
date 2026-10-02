#!/usr/bin/env node
/* Render the overtime calendar in jsdom-free fashion: extract the pure
 * functions from index.html and drive them with a tiny DOM stub. The goal is to
 * answer one question — does renderOvertimeCalendar() actually emit day cells,
 * or does it bail out before touching the grid? */
'use strict';
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');

// --- minimal DOM stub -------------------------------------------------------
const made = {};
function mk(id) {
  if (!made[id]) {
    made[id] = {
      id,
      textContent: '',
      innerHTML: '',
      style: {},
      children: [],
      className: '',
      classList: { add() {}, remove() {}, contains: () => false },
      appendChild(c) { this.children.push(c); },
      addEventListener() {},
      setAttribute() {},
      getAttribute: () => null
    };
  }
  return made[id];
}
global.document = {
  getElementById: mk,
  querySelector: () => mk('stub'),
  querySelectorAll: () => [],
  createElement: () => mk('created-' + Math.random().toString(36).slice(2)),
  addEventListener() {}
};
global.window = global;
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

// --- pull the calendar functions out of the page ----------------------------
function grab(name) {
  const start = html.indexOf('function ' + name + '(');
  if (start === -1) return null;
  // Walk braces to find the end of the function body.
  let depth = 0, i = html.indexOf('{', start);
  const from = i;
  for (; i < html.length; i++) {
    if (html[i] === '{') depth++;
    else if (html[i] === '}') { depth--; if (depth === 0) { i++; break; } }
  }
  return html.slice(start, i);
}

const names = ['renderOvertimeCalendar', 'drawOvertimeCalendar', 'calcPeriodTotal'];
const src = names.map(grab).filter(Boolean).join('\n\n');
if (!src) {
  console.log('FAIL: could not extract calendar functions from index.html');
  process.exit(1);
}

// loadSchedules normally fetches; stub it so drawing is synchronous.
const stub = `
  var allSchedules = [];
  var overtimeDate = new Date(2026, 9, 1);
  function loadSchedules(cb) { if (cb) cb(); }
  function log() {}
  function $(id) { return document.getElementById(id); }
`;

try {
  new Function('document', 'window', stub + '\n' + src + '\nrenderOvertimeCalendar();')(
    global.document, global.window);
} catch (err) {
  console.log('FAIL: ' + err.message);
  process.exit(1);
}

const grid = made['overtime-calendar-grid'];
const monthYear = made['overtime-month-year'];
const cells = grid && grid.children ? grid.children.length : 0;

console.log('=== renderOvertimeCalendar() ===');
console.log('  month-year label :', JSON.stringify(monthYear ? monthYear.textContent : null));
console.log('  grid cells built :', cells);
console.log('  grid innerHTML   :', (grid && grid.innerHTML ? grid.innerHTML.length : 0), 'chars');

if (cells === 0) {
  console.log('');
  console.log('VERDICT: the calendar renders NO day cells -> blank grid.');
  process.exit(2);
}
console.log('');
console.log('VERDICT: calendar builds cells fine; a blank grid is not this code path.');