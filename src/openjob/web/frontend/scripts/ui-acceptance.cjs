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

  // 推荐页计划收尾 Batch E：E1 默认关闭 / E2 配置显示与安全提示 / E3 只推荐页说明
  const collectDialog = await open(page, '/', 1280);
  const collectOpened = await page.evaluate(() => {
    const trigger = document.querySelector('button[aria-label="单独采集岗位"]');
    if (trigger) trigger.click();
    return Boolean(trigger);
  });
  await new Promise(r => setTimeout(r, 800));
  const dialogText = await page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"]');
    return dialog ? dialog.innerText : '';
  });
  const recChecked = await page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"]');
    if (!dialog) return null;
    const labels = Array.from(dialog.querySelectorAll('label'));
    const recLabel = labels.find(l => /推荐页/.test(l.innerText || ''));
    const input = recLabel ? recLabel.querySelector('input[type="checkbox"]') : null;
    return input ? input.checked : null;
  });
  results.push({ test: 'recommend-toggle-exists-and-default-off', ok: collectOpened && recChecked === false });
  results.push({ test: 'recommend-search-default-on', ok: dialogText.includes('搜索流') });
  // E2：勾选推荐页后出现安全提示与参数
  await page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"]');
    if (!dialog) return;
    const labels = Array.from(dialog.querySelectorAll('label'));
    const recLabel = labels.find(l => /推荐页/.test(l.innerText || ''));
    const input = recLabel ? recLabel.querySelector('input[type="checkbox"]') : null;
    if (input) input.click();
  });
  await new Promise(r => setTimeout(r, 500));
  const dialogAfterRec = await page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"]');
    return dialog ? dialog.innerText : '';
  });
  results.push({ test: 'recommend-copy-no-auto-send', ok: dialogAfterRec.includes('不会自动发送招呼语、简历或回复') });
  results.push({ test: 'recommend-copy-personalized', ok: dialogAfterRec.includes('推荐页来自 BOSS 个性化推荐') });
  results.push({ test: 'recommend-params-visible', ok: dialogAfterRec.includes('分页轮次') && dialogAfterRec.includes('最多处理候选数') && dialogAfterRec.includes('连续无新增上限') });
  results.push({ test: 'recommend-duplicate-note', ok: dialogAfterRec.includes('重复岗位不会占用') });
  // E3：只推荐页说明
  const searchUnchecked = await page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"]');
    if (!dialog) return false;
    const labels = Array.from(dialog.querySelectorAll('label'));
    const searchLabel = labels.find(l => /搜索流/.test(l.innerText || ''));
    const input = searchLabel ? searchLabel.querySelector('input[type="checkbox"]') : null;
    if (input) input.click();
    return true;
  });
  await new Promise(r => setTimeout(r, 400));
  const dialogRecOnly = await page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"]');
    return dialog ? dialog.innerText : '';
  });
  results.push({ test: 'recommend-only-hint', ok: searchUnchecked && dialogRecOnly.includes('推荐页') });
  await page.keyboard.press('Escape');
  await new Promise(r => setTimeout(r, 400));

  // E5：进度来源状态（mock workbench 响应）
  page.removeAllListeners('request');
  await page.setRequestInterception(true);
  page.on('request', intercepted => {
    const url = intercepted.url();
    if (url.includes('/api/workbench')) {
      intercepted.respond({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          funnel: {}, funnel_today: {}, pending_confirmation: [], pending_greetings: [],
          send_errors: [], needs_resume: [],
          send_quota: { daily_limit: 10, sent: 0, remaining: 10, exhausted: false },
          delivery_aging: { approved_total: 0, approved_overdue_7d: 0, stale_total: 0, pending_replies: 0 },
          scheduled_collection: { enabled: false, times: [], weekdays_only: true, max_pages: 1, platforms: ['boss'], pause_today: false, today_executed: 0, last_run: null, last_skip_reason: '', next_run_at: null },
          task: {
            id: 'mock-task', mode: 'collect', label: '岗位采集', status: 'running',
            logs: [], created_at: '', updated_at: '', error: null,
            progress: { platforms: { boss: { status: 'running', seen: 14, new: 6, duplicate: 8, filtered: 0, parse_failed: 0, save_failed: 0, sources: { recommendation: { label: '推荐页', status: 'running', seen: 6, new: 2, duplicate: 4, parse_failed: 0 } } } } },
          },
          last_task: null,
        }),
      });
    } else {
      intercepted.continue();
    }
  });
  const dashMock = await open(page, '/', 1280);
  const sourcesTextOk = dashMock.text.includes('推荐页') && dashMock.text.includes('扫描') && dashMock.text.includes('新增') && dashMock.text.includes('重复');
  results.push({ test: 'recommend-progress-sources', ok: sourcesTextOk });
  page.removeAllListeners('request');
  await page.setRequestInterception(false);

  // E4：来源徽标（mock /api/jobs 返回双来源；先启用拦截再导航）
  await page.setRequestInterception(true);
  page.on('request', intercepted => {
    const url = intercepted.url();
    if (url.includes('/api/jobs/search')) {
      intercepted.respond({
        status: 200,
        contentType: 'application/json',
        headers: { 'X-Total-Count': '1' },
        body: JSON.stringify({
          items: [{
            id: 'mock-rec-job', title: '数据分析实习生', company: '示例公司',
            salary: '150-200元/天', city: '杭州', score: 80, status: 'ready',
            source_platform: 'boss', source_channel: 'recommendation',
            source_channels: ['search', 'recommendation'], source_labels: ['搜索流', '推荐页'],
            jd: '数据看板建设', created_at: '2026-09-19T10:00:00', updated_at: '2026-09-19T10:00:00',
          }],
          total: 1,
          all_total: 1,
        }),
      });
    } else {
      intercepted.continue();
    }
  });
  await page.setViewport({ width: 1280, height: 844 });
  await page.goto(`${baseUrl}/jobs?theme=light`, { waitUntil: 'networkidle2', timeout: 30000 });
  await pause(1200);
  const badgeText = await page.evaluate(() => document.body.innerText);
  results.push({ test: 'recommend-source-badge-dual', ok: badgeText.includes('搜索流 + 推荐页') || (badgeText.includes('搜索流') && badgeText.includes('推荐页')) });
  page.removeAllListeners('request');
  await page.setRequestInterception(false);

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
