import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { openPage } from './browser-check.mjs';

// Uses an isolated Chrome profile. No packages, external resources or production data.
const page = await openPage();
const { evaluate: run, until, send, navigate, screenshot } = page;
const KEY = 'returns-desk-demo:v1';
const checks = [];
const check = (name, value) => { assert.ok(value, name); checks.push(name); console.log('PASS ' + name); };
const text = selector => run(`document.querySelector(${JSON.stringify(selector)}).textContent`);
const click = selector => run(`document.querySelector(${JSON.stringify(selector)}).click()`);
const input = (selector, value) => run(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); el.value = ${JSON.stringify(value)}; el.dispatchEvent(new Event('input', { bubbles: true })); })()`);
const storage = () => run(`localStorage.getItem(${JSON.stringify(KEY)})`);
const select = id => click(`.ticket-card[data-id="${id}"]`);
const note = () => run('document.querySelector("#handling-note").value');
const saved = () => until('!document.querySelector("#save-note").textContent.includes("保存中")');
const key = async (value, modifiers = 0) => {
  const keyCode = ({ Enter: 13, Escape: 27, Tab: 9 })[value];
  await send('Input.dispatchKeyEvent', { type: 'keyDown', key: value, code: value, modifiers, windowsVirtualKeyCode: keyCode, nativeVirtualKeyCode: keyCode, ...(value === 'Enter' ? { text: '\r', unmodifiedText: '\r' } : {}) });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: value, code: value, modifiers, windowsVirtualKeyCode: keyCode, nativeVirtualKeyCode: keyCode });
};
const waitDialog = async start => {
  for (let i = 0; i < 80; i++) {
    const dialog = page.events.slice(start).find(e => e.method === 'Page.javascriptDialogOpening');
    if (dialog) return dialog.params;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error('Confirmation dialog did not open');
};

try {
  await navigate();
  const seed = { notes: {
    'RT-10231': { text: '先前保存的备注', savedAt: '2026-10-22T08:00:00.000Z' },
    'RT-10234': { text: '保留另一工单的记录', savedAt: '2026-10-22T09:00:00.000Z' },
    'RT-99999': { text: '未知工单不显示', savedAt: '2026-10-22T09:00:00.000Z' }
  } };
  await run(`localStorage.setItem(${JSON.stringify(KEY)}, ${JSON.stringify(JSON.stringify(seed))})`);
  await navigate();
  check('读取既有 v1 格式，未修改时禁用保存', await note() === seed.notes['RT-10231'].text && await run('document.querySelector("#save-note").disabled'));
  check('五条工单、演示边界与帮助入口可见', await run('document.querySelectorAll(".ticket-card").length === 5 && !document.querySelector("#help-panel").open && !!document.querySelector("#help-toggle")') && (await text('.demo-notice')).includes('不上传服务器'));

  const queries = [['rt-1023', 5], ['AMINA', 1], ['远行文化', 1], ['500ML', 1], ['不存在的工单', 0], ['   ', 5]];
  for (const [query, count] of queries) {
    await input('#ticket-search', query);
    check('搜索子串匹配：' + JSON.stringify(query), await run('document.querySelectorAll(".ticket-card").length') === count);
  }
  await input('#ticket-search', '没有结果');
  check('无匹配有说明，当前详情对象仍明确', await run('!document.querySelector("#empty-results").hidden && !document.querySelector("#filter-context").hidden') && await text('#detail-title') === 'RT-10231');
  await click('#empty-clear');
  check('空结果可清空恢复五条，焦点回到搜索', await run('document.querySelectorAll(".ticket-card").length === 5 && document.activeElement.id === "ticket-search"'));

  const draft1 = '已核实杯盖问题。\n下一步：等待客户补充照片。<script>仅为文本</script>';
  await input('#handling-note', draft1);
  await input('#ticket-search', 'AMINA');
  check('搜索不改变草稿，也不提前写入', await note() === draft1 && await storage() === JSON.stringify(seed) && (await text('#draft-state')).includes('未保存'));
  await click('#clear-search');
  await select('RT-10232');
  check('长机构、长商品、原问题、金额和跟进标记完整保留', await run('document.querySelector(".field-customer dd").textContent === "上海远行文化创意工作室" && document.querySelector(".field-product dd").textContent === "多功能旅行收纳套装（大号衣物袋＋鞋袋＋数码配件袋，深灰色限量组合装）" && document.querySelector(".field-amount dd").textContent === "¥357.00" && document.querySelector(".field-priority dd").textContent === "需要跟进" && document.querySelector("#customer-reason").textContent === "订单包含三套收纳袋，其中一套少了数码配件袋；客户希望先确认能否补发，再决定是否退货。"'));
  const initial2 = await note();
  const draft2 = initial2 + '\n待下次跟进凭证。';
  await input('#handling-note', draft2);
  await select('RT-10231');
  check('切换保留每条独立草稿，并标出数量', await note() === draft1 && (await text('#draft-summary')).includes('2 条') && await run('[...document.querySelectorAll(".draft-marker")].filter(e => !e.hidden).length === 2'));
  await run('window.__nativeSetItem = Storage.prototype.setItem; window.__writeCount = 0; Storage.prototype.setItem = function(...args) { window.__writeCount++; return window.__nativeSetItem.apply(this, args); }');
  await click('#save-note');
  await click('#save-note');
  check('保存中禁止重复操作，尚未宣称成功', await run('document.querySelector("#save-note").disabled && document.querySelector("#handling-note").readOnly && !document.querySelector("#save-feedback").classList.contains("success")') && await storage() === JSON.stringify(seed));
  await saved();
  const stored1 = JSON.parse(await storage());
  check('真实写入后才显示成功，重复点击只写入一次', stored1.notes['RT-10231'].text === draft1 && !Number.isNaN(Date.parse(stored1.notes['RT-10231'].savedAt)) && await run('window.__writeCount') === 1 && (await text('#save-feedback')).includes('已保存到当前浏览器'));
  check('保存仅更新当前工单，其余与未知工单键保持', JSON.stringify(stored1.notes['RT-10234']) === JSON.stringify(seed.notes['RT-10234']) && JSON.stringify(stored1.notes['RT-99999']) === JSON.stringify(seed.notes['RT-99999']) && !stored1.notes['RT-10232']);
  check('当前工单干净时，其他工单草稿仍触发离页保护', await run('(() => { const event = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; })()'));
  await select('RT-10232');
  check('另一工单草稿不受当前保存影响', await note() === draft2);
  await click('#save-note');
  await saved();
  await navigate();
  check('刷新后读回相同已保存备注', await note() === draft1);
  await select('RT-10232');
  check('刷新后读回另一工单备注', await note() === draft2);
  await input('#handling-note', '');
  await click('#save-note');
  await saved();
  await navigate();
  await select('RT-10232');
  check('空备注可保存，刷新不回退到初始备注', await note() === '' && JSON.parse(await storage()).notes['RT-10232'].text === '');
  await input('#handling-note', '临时更改');
  await input('#handling-note', '');
  check('恢复原值后清除未保存状态', await run('document.querySelector("#save-note").disabled && document.querySelector("#draft-summary").hidden'));

  // Real keyboard events exercise native dialog behavior and queue focus.
  await run('document.querySelector("#help-toggle").focus()');
  await key('Enter');
  check('键盘打开帮助，焦点进入对话框', await run('document.querySelector("#help-panel").open && document.activeElement.id === "help-close"'));
  await screenshot('help');
  await key('Escape');
  check('Escape 关闭帮助并恢复入口焦点', await run('!document.querySelector("#help-panel").open && document.activeElement.id === "help-toggle"'));
  await run('document.querySelector(\'.ticket-card[data-id="RT-10233"]\').focus()');
  await key('Enter');
  check('键盘选择多语言工单并保留可见焦点', await text('#detail-title') === 'RT-10233' && await run('document.activeElement.dataset.id === "RT-10233" && document.activeElement.matches(":focus-visible")') && await text('.field-customer dd') === 'Amina Yusuf');

  const beforeFailure = await storage();
  await navigate('http://127.0.0.1:8765/?failSave=1');
  check('故障模式明确可见，原保存数据仍可读取', await run('!document.querySelector("#fault-warning").hidden') && await note() === draft1);
  const failureDraft = '故障时仍保留输入\n请在正常模式继续处理';
  await input('#handling-note', failureDraft);
  await click('#save-note');
  await saved();
  check('故障保存不写入，保留输入、未保存状态与恢复说明', await storage() === beforeFailure && await note() === failureDraft && (await text('#draft-state')).includes('未保存') && (await text('#save-feedback')).includes('复制文本') && await run('document.querySelector("#save-feedback").classList.contains("error") && !document.querySelector("#save-note").disabled'));
  await select('RT-10234');
  await select('RT-10231');
  check('故障后仍可查阅其他工单并返回原草稿', await note() === failureDraft && (await text('#save-feedback')).includes('备注未保存'));
  await screenshot('save-failure');
  let eventIndex = page.events.length;
  let leave = click('#exit-fault');
  const confirm = await waitDialog(eventIndex);
  check('返回正常模式前明确确认放弃未保存输入', confirm.type === 'confirm' && confirm.message.includes('复制文本'));
  await send('Page.handleJavaScriptDialog', { accept: false });
  await leave;
  check('取消离开保留故障草稿', await run('location.search === "?failSave=1"') && await note() === failureDraft);
  eventIndex = page.events.length;
  leave = click('#exit-fault');
  await waitDialog(eventIndex);
  await send('Page.handleJavaScriptDialog', { accept: true });
  await leave;
  await until('location.search === "" && document.readyState === "complete" && document.querySelector("#fault-warning").hidden');
  check('确认返回正常模式未把故障草稿持久化', await note() === draft1 && await storage() === beforeFailure);

  await input('#handling-note', '模拟浏览器拒绝写入');
  await run('window.__nativeSetItem = Storage.prototype.setItem; Storage.prototype.setItem = function() { throw new DOMException("blocked", "QuotaExceededError"); }');
  await click('#save-note');
  await saved();
  check('真实存储写入异常被捕获且不丢输入', await storage() === beforeFailure && await note() === '模拟浏览器拒绝写入' && (await text('#save-feedback')).includes('写入未完成') && (await text('#draft-state')).includes('未保存'));
  await run('Storage.prototype.setItem = window.__nativeSetItem');
  await click('#save-note');
  await saved();
  check('写入恢复后可以重试成功', JSON.parse(await storage()).notes['RT-10231'].text === '模拟浏览器拒绝写入' && await run('document.querySelector("#save-feedback").classList.contains("success")'));

  const beforeReadFailure = await storage();
  const injection = await send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.__nativeGetItem = Storage.prototype.getItem; Storage.prototype.getItem = function() { throw new DOMException("blocked", "SecurityError"); };' });
  await navigate();
  check('初始读取异常明确提示，仍可阅读和编辑', await run('!document.querySelector("#load-warning").hidden && document.querySelectorAll(".ticket-card").length === 5 && !document.querySelector("#handling-note").readOnly'));
  await input('#handling-note', '读取失败时的新草稿');
  await click('#save-note');
  await saved();
  check('无法确认旧记录时不覆盖存储，保留草稿', await note() === '读取失败时的新草稿' && (await text('#save-feedback')).includes('本次未写入') && await run(`window.__nativeGetItem.call(localStorage, ${JSON.stringify(KEY)})`) === beforeReadFailure);
  await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: injection.identifier });
  await run('Storage.prototype.getItem = window.__nativeGetItem');
  await click('#save-note');
  await saved();
  await select('RT-10234');
  check('读取恢复后保存保留旧记录，并恢复其他备注展示', await note() === seed.notes['RT-10234'].text && await run('document.querySelector("#load-warning").hidden'));

  await run(`localStorage.setItem(${JSON.stringify(KEY)}, '{broken JSON')`);
  await navigate();
  await input('#handling-note', '不覆盖损坏的旧数据');
  await click('#save-note');
  await saved();
  check('JSON 数据损坏时诚实失败，不覆盖未确认数据', await storage() === '{broken JSON' && await note() === '不覆盖损坏的旧数据' && (await text('#draft-state')).includes('未保存'));
  await run(`localStorage.setItem(${JSON.stringify(KEY)}, ${JSON.stringify(beforeReadFailure)})`);
  await click('#save-note');
  await saved();

  // Presentational checks use real representative content, including all statuses.
  const expected = [
    ['RT-10231', '陈晓敏', '待处理', '¥129.00'],
    ['RT-10232', '上海远行文化创意工作室', '等待凭证', '¥357.00'],
    ['RT-10233', 'Amina Yusuf', '待处理', '¥219.00'],
    ['RT-10234', '李维', '待收货', '¥89.00'],
    ['RT-10235', '周婉宁', '已完成', '¥78.00']
  ];
  for (const [id, customer, status, amount] of expected) {
    await select(id);
    check(id + ' 详情、状态和金额一致', await text('.field-customer dd') === customer && await text('#detail-status') === status && await text('.field-amount dd') === amount && await run('document.querySelector(".field-order dd").textContent.startsWith("OD-") && !!document.querySelector(".field-submitted dd").textContent && !!document.querySelector("#customer-reason").textContent'));
  }
  await select('RT-10231');
  await input('#handling-note', '已核实杯盖密封圈问题，等待补充照片。\n下次跟进：确认凭证后记录处理进展。');
  await screenshot('workspace');
  await select('RT-10232');
  await screenshot('long-content');
  for (const [width, height] of [[1366, 900], [1024, 768], [768, 1024], [390, 844], [320, 740]]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
    check(width + 'px 无页面横向溢出，长字段不裁切', await run('document.documentElement.scrollWidth <= innerWidth && [...document.querySelectorAll(".detail-fields dd, .ticket-product, #customer-reason")].every(el => el.scrollWidth <= el.clientWidth + 1)'));
    if (width === 1366) await screenshot('desktop-1366');
    if (width === 390) {
      await select('RT-10233');
      await select('RT-10232');
      await screenshot('mobile-detail');
      await run('document.querySelector("#save-note").scrollIntoView({block:"end"})');
      await screenshot('mobile-notes');
      await click('#help-toggle');
      check('窄屏帮助可完整滚动且不超出屏幕', await run('document.querySelector("#help-panel").getBoundingClientRect().width <= innerWidth && document.querySelector("#help-panel").getBoundingClientRect().height <= innerHeight'));
      await screenshot('mobile-help');
      await key('Escape');
    }
  }
  check('页面无未捕获脚本异常', !page.events.some(event => event.method === 'Runtime.exceptionThrown'));
  const requests = page.events.filter(event => event.method === 'Network.requestWillBeSent').map(event => event.params.request.url);
  check('页面请求仅使用本地资源，无外网请求', requests.every(url => url.startsWith('http://127.0.0.1:8765/') || url.startsWith('data:')));
  await writeFile('verification/results.json', JSON.stringify({ checkedAt: new Date().toISOString(), browser: await send('Browser.getVersion'), checks }, null, 2));
  console.log(`Completed ${checks.length} browser checks.`);
} finally {
  // CDP closes this test tab only. The disposable profile is removed separately.
  await page.close();
}
