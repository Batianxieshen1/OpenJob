const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');

const baseUrl = (process.env.OPENJOB_UI_BASE_URL || 'http://127.0.0.1:8686').replace(/\/$/, '');
const shotDir = process.env.OPENJOB_UI_SCREENSHOT_DIR || path.resolve('ui-acceptance-screenshots');
const bundledChrome = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
].find(candidate => fs.existsSync(candidate));
const chrome = process.env.CHROME_BIN || bundledChrome;

if (!chrome) {
  throw new Error('未找到 Chrome。请设置 CHROME_BIN，或在本机安装 Google Chrome。');
}

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function open(page, route, width) {
  await page.setViewport({ width, height: 844 });
  const separator = route.includes('?') ? '&' : '?';
  await page.goto(`${baseUrl}${route}${separator}theme=light`, { waitUntil: 'networkidle2', timeout: 30000 });
  await pause(500);
  return page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    text: document.body.innerText,
  }));
}

async function capture(browser) {
  fs.mkdirSync(shotDir, { recursive: true });
  const page = await browser.newPage();
  for (const route of ['/', '/jobs', '/confirm', '/stats', '/monitor', '/config', '/resume']) {
    await page.setViewport({ width: 1280, height: 900 });
    await page.goto(`${baseUrl}${route}?theme=light`, { waitUntil: 'networkidle2', timeout: 30000 });
    await pause(500);
    const name = `${route === '/' ? 'dashboard' : route.slice(1)}-light-1280.png`;
    await page.screenshot({ path: path.join(shotDir, name), fullPage: true });
  }
  await page.close();
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: chrome,
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  const results = [];

  for (const width of [390, 1280]) {
    for (const route of ['/', '/jobs', '/confirm']) {
      const view = await open(page, route, width);
      results.push({
        test: `${route}-${width}-no-horizontal-overflow`,
        ok: view.scrollWidth <= view.clientWidth + 1,
        scrollWidth: view.scrollWidth,
        clientWidth: view.clientWidth,
      });
    }
  }

  const dashboard = await open(page, '/', 1280);
  results.push({ test: 'dashboard-scheduled-collection-card', ok: dashboard.text.includes('计划任务') });
  results.push({ test: 'dashboard-no-automatic-send-copy', ok: dashboard.text.includes('不会自动发送任何内容') });

  const config = await open(page, '/config?section=collection_schedule', 1280);
  results.push({ test: 'config-scheduled-collection-controls', ok: config.text.includes('定时采集') && config.text.includes('只采集 + AI 评分') });
  results.push({ test: 'config-platform-capability-copy', ok: config.text.includes('不会自动发送或监听') });

  await capture(browser);
  let allOk = true;
  for (const result of results) {
    if (!result.ok) allOk = false;
    console.log(`${result.ok ? 'PASS' : 'FAIL'} ${JSON.stringify(result)}`);
  }
  await browser.close();
  if (!allOk) process.exitCode = 1;
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
