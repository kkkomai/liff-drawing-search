#!/usr/bin/env node
/* Syntax-check EVERY inline <script> block in an HTML file.
 *
 * Why this exists: check_bento_js.js only validated the bento block. A single
 * stray character anywhere earlier in the same <script> makes the whole block
 * fail to parse, which kills every later statement in it — including the
 * DOMContentLoaded registration that draws the calendar. That failure looks
 * like "the calendar never appears", so the whole file has to be parsed, not
 * just the part we last edited.
 */
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const files = process.argv.slice(2);
if (!files.length) files.push('index.html', 'form.html');

let failed = 0;

for (const name of files) {
  if (!fs.existsSync(name)) { console.log('skip (missing): ' + name); continue; }
  const html = fs.readFileSync(name, 'utf8');
  const blocks = [];
  const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
  let m;
  while ((m = re.exec(html)) !== null) {
    const attrs = m[1] || '';
    const body = m[2] || '';
    if (/\bsrc\s*=/i.test(attrs)) continue; // external, nothing to parse
    const line = html.slice(0, m.index).split('\n').length;
    blocks.push({ line, body });
  }

  console.log('=== ' + name + ' ===');
  console.log('  inline script blocks: ' + blocks.length);

  blocks.forEach((b, i) => {
    if (!b.body.trim()) { console.log('    #' + (i + 1) + ' (line ' + b.line + '): empty, skipped'); return; }
    const tmp = path.join(os.tmpdir(), 'inline_' + name.replace(/\W/g, '_') + '_' + i + '.js');
    fs.writeFileSync(tmp, b.body, 'utf8');
    try {
      execFileSync(process.execPath, ['--check', tmp], { stdio: 'pipe' });
      console.log('    #' + (i + 1) + ' (line ' + b.line + '): parses OK  [' + b.body.split('\n').length + ' lines]');
    } catch (err) {
      const out = String(err.stderr || err.message);
      const detail = out.split('\n').filter(l => l.includes('Error') || l.includes('^')).slice(0, 2).join(' | ');
      console.log('    #' + (i + 1) + ' (line ' + b.line + '): SYNTAX ERROR — ' + detail);
      failed++;
    } finally {
      fs.unlinkSync(tmp);
    }
  });
}

console.log('');
console.log(failed ? 'VERDICT: ' + failed + ' block(s) failed to parse' : 'VERDICT: every inline block parses');
process.exit(failed ? 1 : 0);