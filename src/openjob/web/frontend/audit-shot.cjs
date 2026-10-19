const puppeteer = require('puppeteer-core');
const path = require('path');

const OUT = 'C:/Users/暴龙战士wink/Desktop/agent/OpenJob/landing';
const BASE = 'http://127.0.0.1:8686';
const PAGES = ['dashboard', 'jobs', 'confirm', 'resume', 'config', 'monitor', 'stats', 'inbox'];

(async () => {
  const browser = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true,
    args: ['--no-sandbox', '--user-data-dir=C:/Windows/Temp/oj-audit2-' + process.pid],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 950 });

  // 浅色全页面
  for (const p of PAGES) {
    await page.goto(`${BASE}/${p}?theme=light`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await new Promise(r => setTimeout(r, 1800));
    await page.screenshot({ path: path.join(OUT, `audit-${p}-light.png`) });
  }

  // 深色抽查（此前未审的页）
  for (const p of ['config', 'monitor', 'inbox', 'resume']) {
    await page.goto(`${BASE}/${p}?theme=dark`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await new Promise(r => setTimeout(r, 1800));
    await page.screenshot({ path: path.join(OUT, `audit-${p}-dark.png`) });
  }

  // 移动端 390 抽查
  await page.setViewport({ width: 390, height: 844 });
  for (const p of ['dashboard', 'jobs', 'confirm']) {
    await page.goto(`${BASE}/${p}?theme=light`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await new Promise(r => setTimeout(r, 1800));
    await page.screenshot({ path: path.join(OUT, `audit-${p}-m390.png`) });
  }

  console.log('audit matrix saved:', PAGES.length * 2 + 3, 'shots');
  await browser.close();
})().catch(e => { console.error('FAIL:', e.message.slice(0, 200)); process.exit(1); });
