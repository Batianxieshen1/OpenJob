const puppeteer = require('puppeteer-core');
const fs = require('fs');
const chrome = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe'].find(p => fs.existsSync(p));

async function check(page, url, width) {
  await page.setViewport({ width, height: 844 });
  await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise(r => setTimeout(r, 1200));
  return page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
  }));
}

const SHOT_DIR = 'C:/Users/暴龙战士wink/Desktop/agent/OpenJob/ui-acceptance-screenshots';

async function captureRegressionMatrix(browser) {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const page = await browser.newPage();
  const matrix = [];
  for (const theme of ['light', 'dark']) {
    for (const path of ['/', '/jobs', '/confirm', '/stats', '/monitor', '/config', '/resume']) {
      const width = path === '/' ? 1280 : 1280;
      await page.setViewport({ width, height: 900 });
      await page.goto(`http://127.0.0.1:8686${path}?theme=${theme}`, { waitUntil: 'networkidle2', timeout: 30000 });
      await new Promise(r => setTimeout(r, 1200));
      const name = `${path.replace(/\//g, '_') || '_home'}-${theme}-1280.png`.replace(/^_/, '');
      await page.screenshot({ path: `${SHOT_DIR}/${name}` });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      matrix.push({ name, overflowPx: overflow });
      console.log(`📸 ${name} 溢出=${overflow}px`);
    }
  }
  const bad = matrix.filter(m => m.overflowPx > 1);
  console.log(bad.length ? `FAIL ${bad.length} 个页面横向溢出` : 'ALL-PASS 视觉回归矩阵已存档');
}

(async () => {
  const browser = await puppeteer.launch({ executablePath: chrome, headless: 'new', args: ['--no-sandbox'] });
  if (process.argv.includes('--shots')) {
    await captureRegressionMatrix(browser);
    await browser.close();
    return;
  }
  const page = await browser.newPage();
  const results = [];

  for (const width of [390, 1280]) {
    for (const path of ['/', '/jobs', '/confirm']) {
      const r = await check(page, `http://127.0.0.1:8686${path}?theme=light`, width);
      results.push({ width, path, ok: r.scrollW <= r.clientW + 1, ...r });
    }
  }

  // ISSUE-002 回归：/resume 390px main 不得横向溢出（selector+按钮堆叠验证）
  await page.setViewport({ width: 390, height: 844 });
  await page.goto('http://127.0.0.1:8686/resume?theme=light', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise(r => setTimeout(r, 1200));
  const resumeCheck = await page.evaluate(() => {
    const main = document.querySelector('main');
    const button = [...document.querySelectorAll('button')].find(b => b.textContent.includes('按此 JD 优化'));
    const br = button ? button.getBoundingClientRect() : null;
    return {
      mainScrollW: main ? main.scrollWidth : -1,
      mainClientW: main ? main.clientWidth : -1,
      docScrollW: document.documentElement.scrollWidth,
      buttonVisible: br ? br.right <= document.documentElement.clientWidth + 1 && br.width > 50 : false,
    };
  });
  results.push({ test: 'resume-390-no-overflow', ok: resumeCheck.mainScrollW <= resumeCheck.mainClientW + 1 && resumeCheck.docScrollW <= 391, ...resumeCheck });
  results.push({ test: 'resume-390-primary-action-visible', ok: resumeCheck.buttonVisible });

  // ISSUE-003 回归：/jobs 390px 移动卡必须有「查看详情」且打开弹窗
  await page.setViewport({ width: 390, height: 844 });
  await page.goto('http://127.0.0.1:8686/jobs?theme=light', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise(r => setTimeout(r, 1500));
  const detailCheck = await page.evaluate(() => {
    const buttons = [...document.querySelectorAll('button')].filter(b => b.textContent.trim() === '查看详情');
    const first = buttons[0];
    if (first) {
      first.click();
      return { count: buttons.length, clicked: true, hasAria: Boolean(first.getAttribute('aria-label')) };
    }
    return { count: 0, clicked: false };
  });
  await new Promise(r => setTimeout(r, 500));
  const dialogOpen = await page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"]');
    if (!dialog) return { open: false };
    const r = dialog.getBoundingClientRect();
    return { open: true, inViewport: r.top >= 0 && r.bottom <= innerHeight + 1 };
  });
  results.push({ test: 'jobs-390-view-details-action', ok: detailCheck.count > 0 && detailCheck.clicked && detailCheck.hasAria, count: detailCheck.count });
  results.push({ test: 'jobs-390-detail-dialog-opens-in-viewport', ok: dialogOpen.open && dialogOpen.inViewport });

  // P0-1 弹窗测试：/confirm 滚动到中部打开详情，弹窗必须完整在视口内
  await page.setViewport({ width: 1280, height: 900 });
  await page.goto('http://127.0.0.1:8686/confirm?theme=light', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise(r => setTimeout(r, 1500));
  await page.evaluate(() => {
    const main = document.querySelector('main');
    if (main) main.scrollTop = main.scrollHeight / 2;
  });
  await new Promise(r => setTimeout(r, 300));
  const detailBtn = await page.$('::-p-text(查看详情)');
  if (detailBtn) {
    await detailBtn.click();
    await new Promise(r => setTimeout(r, 500));
    const modal = await page.evaluate(() => {
      const dialog = document.querySelector('[role="dialog"]');
      if (!dialog) return { found: false };
      const r = dialog.getBoundingClientRect();
      return { found: true, top: Math.round(r.top), bottom: Math.round(r.bottom), left: Math.round(r.left), right: Math.round(r.right), vh: innerHeight, vw: innerWidth };
    });
    const visible = modal.found && modal.top >= 0 && modal.left >= 0 && modal.right <= modal.vw + 1 && modal.bottom <= modal.vh + 1;
    results.push({ test: 'modal-visible-at-scroll', ok: visible, ...modal });
    // Esc 关闭
    await page.keyboard.press('Escape');
    await new Promise(r => setTimeout(r, 300));
    const stillOpen = await page.evaluate(() => Boolean(document.querySelector('[role="dialog"]')));
    results.push({ test: 'modal-esc-close', ok: !stillOpen });
  } else {
    results.push({ test: 'modal-visible-at-scroll', ok: false, note: '未找到查看详情按钮（可能无待确认岗位）' });
  }

  let allOk = true;
  for (const r of results) {
    const pass = r.ok !== false;
    if (!pass) allOk = false;
    console.log(`${pass ? 'PASS' : 'FAIL'} ${JSON.stringify(r)}`);
  }
  console.log(allOk ? 'ALL-PASS' : 'HAS-FAILURES');
  await browser.close();
})();
