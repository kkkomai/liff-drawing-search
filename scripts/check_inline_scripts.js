// index.html と household/index.html の全インライン<script>を
// node --check にかける。1つの脚本の構文エラーがブロック全体を
// 止めるので、HTML内の全<script>ブロースを抽出して1つずつ検査する。
'use strict';
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const os = require('os');

const files = process.argv.slice(2);
let fail = 0;

for (const file of files) {
  if (!fs.existsSync(file)) {
    console.error('SKIP: not found: ' + file);
    continue;
  }
  const html = fs.readFileSync(file, 'utf8');
  // <script>...</script> のインラインブロックのみ (src= は除外)
  const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g;
  let m;
  let idx = 0;
  while ((m = re.exec(html)) !== null) {
    idx += 1;
    const js = m[1];
    const tmp = path.join(os.tmpdir(), `hh_check_${path.basename(file)}_${idx}.js`);
    fs.writeFileSync(tmp, js, 'utf8');
    try {
      execFileSync(process.execPath, ['--check', tmp], { stdio: 'pipe' });
      console.log(`OK   ${file} [script #${idx}] (${js.length} chars)`);
    } catch (e) {
      fail += 1;
      console.error(`FAIL ${file} [script #${idx}]:`);
      console.error(String(e.stderr || e.message));
    } finally {
      try { fs.unlinkSync(tmp); } catch (_) {}
    }
  }
  if (idx === 0) console.error(`FAIL ${file}: no inline <script> found`);
}

if (fail) {
  console.error(`\nFAIL: ${fail} script block(s) failed syntax check`);
  process.exit(1);
}
console.log('\nOK: all inline scripts pass node --check');
