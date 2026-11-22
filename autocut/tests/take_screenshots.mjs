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

try {
  // 1. Home (populated)
  await page.goto('http://127.0.0.1:8765/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(shotsDir, '01_home_projects.png') });

  // 2. Error banner state (FFmpeg missing)
  await page.evaluate(async () => {
    await fetch('/api/test/toggle-ffmpeg-error', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ missing: true }),
    });
  });
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(250);
  await page.screenshot({ path: path.join(shotsDir, '02_error_state_ffmpeg.png') });

  // Restore FFmpeg OK
  await page.evaluate(async () => {
    await fetch('/api/test/toggle-ffmpeg-error', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ missing: false }),
    });
  });
  await page.reload({ waitUntil: 'networkidle' });

  // Open demo project -> 3. Inputs screen
  await page.click('[data-open-proj]');
  await page.waitForTimeout(350);
  await page.screenshot({ path: path.join(shotsDir, '03_inputs.png') });

  // 4. Cuts Review screen
  await page.click('.stepper-list [data-stage="cuts"]');
  await page.waitForTimeout(350);
  await page.screenshot({ path: path.join(shotsDir, '04_cuts_review.png') });

  // 5. Scenes Grid screen
  await page.click('.stepper-list [data-stage="scenes"]');
  await page.waitForTimeout(350);
  await page.screenshot({ path: path.join(shotsDir, '05_scenes_grid.png') });

  // 6. Motion Studio - Zoom In with Blue Dot
  await page.click('.stepper-list [data-stage="motion"]');
  await page.waitForTimeout(350);
  await page.screenshot({ path: path.join(shotsDir, '06_motion_zoom_in.png') });

  // 7. Motion Studio - Zoom Out (note #1 is excluded because assigned in Zoom In!)
  await page.click('[data-mtab="zoom_out"]');
  await page.waitForTimeout(300);
  // Click on stage image to place a blue dot in Zoom Out
  const stageBox = await page.locator('#stage-image-box').boundingBox();
  if (stageBox) {
    await page.mouse.click(stageBox.x + stageBox.width * 0.65, stageBox.y + stageBox.height * 0.42);
    await page.waitForTimeout(250);
  }
  await page.screenshot({ path: path.join(shotsDir, '07_motion_zoom_out.png') });

  // 8. Export & Report screen
  await page.click('.stepper-list [data-stage="export"]');
  await page.waitForTimeout(350);
  await page.screenshot({ path: path.join(shotsDir, '08_export_report.png') });

  // 9. Post-result Manual Review screen
  await page.click('.stepper-list [data-stage="review"]');
  await page.waitForTimeout(350);
  await page.screenshot({ path: path.join(shotsDir, '09_review_manual.png') });

  // 10. Empty state (delete demo project and return home)
  await page.click('#btn-go-home');
  await page.waitForTimeout(250);
  await page.click('[data-del-proj]');
  await page.waitForTimeout(150);
  await page.click('#m-ok');
  await page.waitForTimeout(350);
  await page.screenshot({ path: path.join(shotsDir, '10_home_empty.png') });

  // 11. Updates Modal - Delta summary & backups (click update badge or updates button)
  await page.click('#btn-open-updates');
  await page.waitForTimeout(300);
  await page.click('#upd-check-btn');
  await page.waitForTimeout(350);
  await page.screenshot({ path: path.join(shotsDir, '11_updates_delta.png') });

  // 12. Updates Modal - Source settings + Test Connection success
  await page.click('[data-utab="source"]');
  await page.waitForTimeout(250);
  await page.click('#src-test-btn');
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(shotsDir, '12_updates_source.png') });

  // 13. Updates Modal - Local ZIP/Folder fallback
  await page.click('[data-utab="local"]');
  await page.waitForTimeout(250);
  await page.screenshot({ path: path.join(shotsDir, '13_updates_local.png') });
  await page.click('#upd-close');
  await page.waitForTimeout(150);

  // Check layout overflow (no horizontal scroll)
  const overflowCheck = await page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
  }));
  console.log('Overflow check:', overflowCheck);
} finally {
  await browser.close();
  pyProc.kill();
}
