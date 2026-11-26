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
  const timer = setTimeout(() => reject(new Error('Server timeout')), 45000);
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
  // 1. Home (Blue Night default + v1.3.0 badge)
  await page.goto('http://127.0.0.1:8765/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(300);
  const verText = await page.textContent('#app-version-tag');
  if (!verText || !verText.includes('1.3.0')) {
    throw new Error(`Expected v1.3.0 version tag, got: ${verText}`);
  }
  await checkOverflow('home_blue_night');
  await page.screenshot({ path: path.join(shotsDir, 'v13_01_home_blue_night.png') });

  // 2. Open project -> "مشروع جديد" 2x2 Materials Grid (Blue Night, Calm Forest, Daylight)
  await page.click('[data-open-proj]');
  await page.waitForTimeout(400);
  await checkOverflow('inputs_blue_night');
  await page.screenshot({ path: path.join(shotsDir, 'v13_02_inputs_blue_night.png') });

  await page.click('[data-theme-pick="calm_forest"]');
  await page.waitForTimeout(200);
  await checkOverflow('inputs_calm_forest');

  await page.click('[data-theme-pick="daylight"]');
  await page.waitForTimeout(200);
  await checkOverflow('inputs_daylight');

  await page.click('[data-theme-pick="blue_night"]');
  await page.waitForTimeout(200);

  // 3. Screen 2: التفريغ وقص الصوت (CapCut Horizontal Audio Track visible immediately)
  await page.click('.stepper-list [data-stage="cuts"]');
  await page.waitForTimeout(350);
  const clipCount = await page.locator('.capcut-clip-block').count();
  console.log('Stage 1 CapCut audio clips rendered:', clipCount);
  if (clipCount < 3) {
    throw new Error(`Expected >= 3 CapCut sliced audio blocks, found ${clipCount}`);
  }
  await checkOverflow('cuts_capcut_track');
  await page.screenshot({ path: path.join(shotsDir, 'v13_03_cuts_capcut_audio_track.png') });

  // 4. Screen 4: Free-order Motion Studio + Instant Live Animation + Snap Zoom + #4C8DFF focus dot
  await page.click('.stepper-list [data-stage="motion"]');
  await page.waitForTimeout(350);

  // Free-order jump directly to the last image scene (#5) without animating earlier scenes!
  const lastThumb = page.locator('.strip-item').last();
  await lastThumb.click();
  await page.waitForTimeout(250);
  const selectedLabel = await page.locator('.motion-controls-bar .meta-line').textContent();
  console.log('Free-order jump label:', selectedLabel);
  if (!selectedLabel || !selectedLabel.includes('#5')) {
    throw new Error(`Free-order jump did not land on scene #5: ${selectedLabel}`);
  }

  // Click Instant Preview ("▶ تحريك المشهد") and Snap Zoom ("⚡ حركة خاطفة")
  await page.click('#btn-animate-scene-now');
  await page.waitForTimeout(250);
  await page.click('#btn-snap-zoom-now');
  await page.waitForTimeout(350);

  await page.click('[data-theme-pick="daylight"]');
  await page.waitForTimeout(200);
  const dotBg = await page.evaluate(() => {
    const dot = document.getElementById('focus-dot');
    return dot ? getComputedStyle(dot).backgroundColor : '';
  });
  console.log('Focus dot RGB in daylight:', dotBg);
  await checkOverflow('motion_studio_daylight');
  await page.screenshot({ path: path.join(shotsDir, 'v13_04_motion_live_snap_daylight.png') });

  // Switch back to blue_night and inspect Export screen (per-scene controls)
  await page.click('[data-theme-pick="blue_night"]');
  await page.waitForTimeout(150);
  await page.click('.stepper-list [data-stage="export"]');
  await page.waitForTimeout(350);
  await checkOverflow('export_v13');
  await page.screenshot({ path: path.join(shotsDir, 'v13_05_export_per_scene.png') });

  // 5. Settings Modal: verify resource_mode options + default_motion_duration + NO Whisper strings
  await page.click('#btn-open-settings');
  await page.waitForTimeout(200);
  const settingsText = await page.evaluate(() => document.getElementById('modal-root').innerText);
  if (settingsText.includes('Whisper')) {
    throw new Error('Whisper still appears in Settings modal!');
  }
  if (!settingsText.includes('خفيف') || !settingsText.includes('متوسط') || !settingsText.includes('سريع')) {
    throw new Error('Resource mode selector missing in Settings modal!');
  }
  await page.screenshot({ path: path.join(shotsDir, 'v13_06_settings_resource_modes.png') });
  await page.click('#set-close');
} finally {
  await browser.close();
  pyProc.kill();
}
