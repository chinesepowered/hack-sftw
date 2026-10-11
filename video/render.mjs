// Render one HTML scene to JPEG frames by stepping every CSS animation deterministically.
// usage: node render.mjs <scene.html> <outDir> <durationSec> [fps] [width] [height]
import { chromium } from 'playwright-core';
import fs from 'node:fs';

const [, , htmlPath, outDir, durationSec, fps = '30', width = '1280', height = '720'] = process.argv;
const exe = process.env.CHROME_PATH || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
fs.mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ executablePath: exe, args: ['--no-sandbox', '--font-render-hinting=none'] });
const page = await browser.newPage({ viewport: { width: +width, height: +height }, deviceScaleFactor: 1 });
await page.goto('file://' + htmlPath, { waitUntil: 'load' });
await page.evaluate(() => document.fonts.ready);
await page.waitForTimeout(200);

const total = Math.ceil(+durationSec * +fps);
for (let i = 0; i < total; i++) {
  const t = (i * 1000) / +fps;
  await page.evaluate((ms) => {
    for (const a of document.getAnimations()) { a.pause(); a.currentTime = ms; }
    if (window.__tick) window.__tick(ms);
  }, t);
  await page.screenshot({ path: `${outDir}/f${String(i).padStart(5, '0')}.jpg`, type: 'jpeg', quality: 92 });
}
await browser.close();
console.log(`rendered ${total} frames to ${outDir}`);
