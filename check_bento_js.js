#!/usr/bin/env node
/* Syntax-check the bento-related script block that lives inside form.html.
 * The HTML has one big <script>, so extract the block we edited and run
 * `node --check` on it to catch a typo before it ships to LINE. */
'use strict';
const fs = require('fs');
const path = require('path');

const root = process.argv[2] || '.';
const targets = ['form.html', 'index.html'];

for (const name of targets) {
  const file = path.join(root, name);
  if (!fs.existsSync(file)) { console.log('skip (missing): ' + name); continue; }
  const html = fs.readFileSync(file, 'utf8');

  const start = html.indexOf('// === BENTO TAB');
  const end = html.indexOf('// === LOGGING ===');
  if (start === -1 || end === -1 || end < start) {
    console.log('FAIL  ' + name + ': bento block not found');
    process.exitCode = 1;
    continue;
  }
  const block = html.slice(start, end);
  const tmp = path.join(require('os').tmpdir(), 'bento_block_' + name.replace(/\W/g, '_') + '.js');
  fs.writeFileSync(tmp, '(function(){\n' + block + '\n});\n', 'utf8');
  try {
    require('child_process').execFileSync(process.execPath, ['--check', tmp], { stdio: 'pipe' });
    console.log('OK    ' + name + ': bento block parses (' + block.split('\n').length + ' lines)');
  } catch (err) {
    console.log('FAIL  ' + name + ': ' + String(err.stderr || err.message).split('\n').slice(0, 3).join(' '));
    process.exitCode = 1;
  } finally {
    fs.unlinkSync(tmp);
  }
}