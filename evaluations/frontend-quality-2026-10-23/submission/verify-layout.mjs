import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import { openPage } from './browser-check.mjs';

const page = await openPage();
const checks = [];
const check = (name, value) => { assert.ok(value, name); checks.push(name); console.log('PASS ' + name); };
const input = text => page.evaluate(`(() => { const el = document.querySelector('#handling-note'); el.value = ${JSON.stringify(text)}; el.dispatchEvent(new Event('input', {bubbles:true})); })()`);
const select = id => page.evaluate(`document.querySelector('.ticket-card[data-id="${id}"]').click()`);
const viewport = (width, height) => page.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
try {
  await page.navigate();
  await page.evaluate("localStorage.removeItem('returns-desk-demo:v1')");
  await page.navigate();
  await page.evaluate("window.__stateChanges = 0; new MutationObserver(() => window.__stateChanges++).observe(document.querySelector('#draft-state'), {childList:true,characterData:true,subtree:true})");
  await input('已核实杯盖密封圈问题，等待补充照片。\n下次跟进：确认凭证后记录处理进展。');
  const changes = await page.evaluate('window.__stateChanges');
  await input('已核实杯盖密封圈问题，等待补充照片。\n下次跟进：确认凭证后记录处理进展。保留文本。');
  check('连续编辑不反复更新相同的朗读状态', changes === 1 && await page.evaluate('window.__stateChanges') === changes);
  check('状态优化后仍明确标出未保存且允许保存', await page.evaluate("document.querySelector('#draft-state').textContent.includes('未保存') && !document.querySelector('#save-note').disabled"));
  await page.screenshot('workspace');
  await select('RT-10232');
  await page.screenshot('long-content');
  await viewport(1366, 900);
  check('1366×900 长工单详情的保存按钮完整可见', await page.evaluate("document.querySelector('#save-note').getBoundingClientRect().bottom <= innerHeight && document.querySelector('#handling-note').getBoundingClientRect().height >= 140"));
  await page.screenshot('desktop-1366');
  for (const [width, height] of [[1024, 768], [768, 1024], [390, 844], [320, 740]]) {
    await viewport(width, height);
    check(width + 'px 最终布局无横向溢出', await page.evaluate("document.documentElement.scrollWidth <= innerWidth && [...document.querySelectorAll('.detail-fields dd, .ticket-product, #customer-reason')].every(el => el.scrollWidth <= el.clientWidth + 1)"));
    if (width === 390) {
      await select('RT-10233'); await select('RT-10232');
      await page.screenshot('mobile-detail');
      await page.evaluate("document.querySelector('#save-note').scrollIntoView({block:'end'})");
      await page.screenshot('mobile-notes');
    }
  }
  await viewport(1440, 1000);
  await select('RT-10231');
  await page.evaluate("document.querySelector('#save-note').click()");
  await page.until("!document.querySelector('#save-note').textContent.includes('保存中')");
  check('最终反馈仍在真实写入后显示成功', await page.evaluate("document.querySelector('#save-feedback').classList.contains('success') && JSON.parse(localStorage.getItem('returns-desk-demo:v1')).notes['RT-10231'].text === document.querySelector('#handling-note').value"));
  const reopened = await openPage();
  try {
    await reopened.navigate();
    check('重新打开页面可读取已保存备注', await reopened.evaluate("document.querySelector('#handling-note').value.includes('保留文本') && document.querySelector('#save-note').disabled"));
  } finally { await reopened.close(); }
  await page.navigate('http://127.0.0.1:8765/?failSave=1');
  await input('已记录客户反馈，尚未保存。\n请在正常模式继续处理。');
  await page.evaluate("document.querySelector('#save-note').click()");
  await page.until("document.querySelector('#save-feedback').classList.contains('error')");
  check('最终错误提示仍保留草稿和未保存状态', await page.evaluate("document.querySelector('#handling-note').value.includes('尚未保存') && document.querySelector('#draft-state').textContent.includes('未保存')"));
  await viewport(1440, 1100);
  await page.evaluate('window.scrollTo(0,0)');
  await page.screenshot('save-failure');
  const filePage = await openPage();
  try {
    const url = pathToFileURL(resolve('index.html')).href;
    await filePage.navigate(url);
    await filePage.evaluate("localStorage.removeItem('returns-desk-demo:v1')");
    await filePage.navigate(url);
    await filePage.evaluate("const el = document.querySelector('#handling-note'); el.value='direct file test'; el.dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('#save-note').click()");
    await filePage.until("document.querySelector('#save-feedback').classList.contains('success')");
    await filePage.navigate(url);
    check('直接打开 index.html 也能保存并重新读取', await filePage.evaluate("document.querySelector('#handling-note').value === 'direct file test' && document.querySelectorAll('.ticket-card').length === 5"));
  } finally { await filePage.close(); }
  check('最终定向检查无未捕获脚本异常', !page.events.some(event => event.method === 'Runtime.exceptionThrown'));
  const result = JSON.parse(await readFile('verification/results.json', 'utf8'));
  result.finalReview = { checkedAt: new Date().toISOString(), checks };
  await writeFile('verification/results.json', JSON.stringify(result, null, 2));
} finally { await page.close(); }
