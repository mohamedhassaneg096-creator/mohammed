import { createRequire } from 'module';
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

const require = createRequire('/usr/local/share/bun-global/node_modules/playwright/package.json');
const { chromium } = require('/usr/local/share/bun-global/node_modules/playwright');

const shotsDir = path.resolve('autocut/docs/screenshots');
fs.mkdirSync(shotsDir, { recursive: true });

const pyProc = spawn('python3', ['autocut/tests/ui_review_server.py', '8765'], {
  stdio: ['ignore', 'pipe', 'pipe'],
});

const ready = await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error('Server timeout')), 40000);
  pyProc.stdout.on('data', (d) => {
    const s = d.toString();
    if (s.includes('READY')) {
      clearTimeout(timer);
      resolve(s.trim());
    }
  });
  pyProc.stderr.on('data', (d) => console.error('PY ERR:', d.toString()));
});

console.log('Bridge:', ready);

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1366, height: 820 } });

async function checkOverflow(label) {
  const res = await page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
  }));
  console.log(`Overflow [${label}]:`, res);
  if (res.scrollW > res.clientW) {
    throw new Error(`Horizontal overflow on ${label}: ${res.scrollW} > ${res.clientW}`);
  }
}

try {
  // 1. Home (Blue Night default)
  await page.goto('http://127.0.0.1:8765/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(300);
  await checkOverflow('home_blue_night');
  await page.screenshot({ path: path.join(shotsDir, 'v12_01_home_blue_night.png') });

  // 2. Open project -> "مشروع جديد" 2x2 Materials Grid (Blue Night)
  await page.click('[data-open-proj]');
  await page.waitForTimeout(400);
  await checkOverflow('inputs_blue_night');
  await page.screenshot({ path: path.join(shotsDir, 'v12_02_inputs_blue_night.png') });

  // Switch live to Calm Forest
  await page.click('[data-theme-pick="calm_forest"]');
  await page.waitForTimeout(250);
  await checkOverflow('inputs_calm_forest');
  await page.screenshot({ path: path.join(shotsDir, 'v12_03_inputs_calm_forest.png') });

  // Switch live to Daylight
  await page.click('[data-theme-pick="daylight"]');
  await page.waitForTimeout(250);
  await checkOverflow('inputs_daylight');
  await page.screenshot({ path: path.join(shotsDir, 'v12_04_inputs_daylight.png') });

  // Switch back to Blue Night for Screen 2
  await page.click('[data-theme-pick="blue_night"]');
  await page.waitForTimeout(200);

  // 3. Screen 2: التفريغ (استيراد) - Preview + Suspicious lines
  await page.click('.stepper-list [data-stage="cuts"]');
  await page.waitForTimeout(350);
  await checkOverflow('cuts_preview');
  await page.screenshot({ path: path.join(shotsDir, 'v12_05_transcript_preview.png') });

  // 4. Screen 2: Cuts sub-tab (shows تقدير badge)
  await page.click('[data-cutstab="cuts"]');
  await page.waitForTimeout(300);
  await checkOverflow('cuts_table');
  await page.screenshot({ path: path.join(shotsDir, 'v12_06_cuts_estimated.png') });

  // 5. Screen 4: Motion Studio in Calm Forest & Daylight to confirm focus-dot stays blue (#4C8DFF)
  await page.click('.stepper-list [data-stage="motion"]');
  await page.waitForTimeout(350);
  await page.click('[data-theme-pick="calm_forest"]');
  await page.waitForTimeout(200);
  await checkOverflow('motion_calm_forest');
  await page.screenshot({ path: path.join(shotsDir, 'v12_07_motion_calm_forest.png') });

  await page.click('[data-theme-pick="daylight"]');
  await page.waitForTimeout(200);
  const dotBg = await page.evaluate(() => getComputedStyle(document.getElementById('focus-dot')).backgroundColor);
  console.log('Focus dot RGB in daylight:', dotBg);
  await page.screenshot({ path: path.join(shotsDir, 'v12_08_motion_daylight.png') });

  // 6. Settings Modal (verify NO Whisper controls)
  await page.click('#btn-open-settings');
  await page.waitForTimeout(200);
  const settingsText = await page.evaluate(() => document.getElementById('modal-root').innerText);
  if (settingsText.includes('Whisper')) {
    throw new Error('Whisper still appears in Settings modal!');
  }
  await page.screenshot({ path: path.join(shotsDir, 'v12_09_settings_modal.png') });
  await page.click('#set-close');
  await page.waitForTimeout(150);

  // 7. Create Demo Project via button from Home
  await page.click('#btn-go-home');
  await page.waitForSelector('#btn-demo-project');
  await page.click('#btn-demo-project');
  await page.waitForSelector('.material-card');
  await page.waitForTimeout(300);
  await checkOverflow('demo_project');
  await page.screenshot({ path: path.join(shotsDir, 'v12_10_demo_project_inputs.png') });
} finally {
  await browser.close();
  pyProc.kill();
}
