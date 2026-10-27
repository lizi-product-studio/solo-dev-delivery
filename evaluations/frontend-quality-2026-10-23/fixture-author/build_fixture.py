"""Build an isolated, deliberately unpolished returns-desk product-design task.

No model, package installation, remote request, or repository code is used.
Only a new/empty output directory is accepted. The author guidance stays here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


TICKETS = [
    {"id": "RT-10231", "customer": "陈晓敏", "order": "OD-20261020-0781", "product": "保温随行杯 500ml · 雾蓝", "reason": "杯盖密封圈破损，收到后发现渗水，希望退货退款。", "status": "待处理", "priority": "普通", "submitted": "2026-10-23 09:12", "amount": "¥129.00", "note": ""},
    {"id": "RT-10232", "customer": "上海远行文化创意工作室", "order": "OD-20261019-1229", "product": "多功能旅行收纳套装（大号衣物袋＋鞋袋＋数码配件袋，深灰色限量组合装）", "reason": "订单包含三套收纳袋，其中一套少了数码配件袋；客户希望先确认能否补发，再决定是否退货。", "status": "等待凭证", "priority": "需要跟进", "submitted": "2026-10-23 09:34", "amount": "¥357.00", "note": "已向客户索取包装及缺件照片。"},
    {"id": "RT-10233", "customer": "Amina Yusuf", "order": "OD-20261018-0635", "product": "桌面阅读灯 · 暖白", "reason": "灯臂无法固定，已提供视频。", "status": "待处理", "priority": "普通", "submitted": "2026-10-23 10:03", "amount": "¥219.00", "note": ""},
    {"id": "RT-10234", "customer": "李维", "order": "OD-20261021-0267", "product": "轻量折叠伞 · 黑色", "reason": "收到的颜色与订单不符。客户已寄回，仓库尚未签收。", "status": "待收货", "priority": "普通", "submitted": "2026-10-23 10:26", "amount": "¥89.00", "note": "退件运单：SF-DEMO-7086。等待仓库签收。"},
    {"id": "RT-10235", "customer": "周婉宁", "order": "OD-20261017-0974", "product": "棉麻靠垫套 · 自然白（两只装）", "reason": "与预期尺寸不符，已核验商品完好。", "status": "已完成", "priority": "普通", "submitted": "2026-10-22 16:48", "amount": "¥78.00", "note": "演示记录：退货审核已完成。此页面不会执行真实退款。"},
]

TASK = r'''# 退货工单工作台整理任务

你接手了一份可运行的退货工单演示原型。使用者是每天反复处理退货请求的普通客服，而不是产品的首次访客或系统开发者。他们需要从队列找到工单、理解客户问题，记录处理进展，并在工作间隙继续上次的工作。

请在本目录内把它整理成更适合真实日常工作的前端。自行判断信息优先级、阅读与操作空间、辅助内容的呈现时机以及视觉表达。可以调整布局、组件和文案，不要求沿用当前页面结构。你需要让真实内容与必要状态依旧清楚，并把关键交互做完整。

必须继续支持：搜索工单、清空搜索、选择与查看工单、编辑处理备注、在当前浏览器本机保存、再次打开后读取已保存的备注、获取帮助。不能让用户误以为页面连接了真实订单系统、执行了退款或把备注上传到了服务器。演示环境与本机保存边界、输入字段含义、未保存变更、保存成功和失败都必须诚实可知，具体措辞和呈现形式可自行选择。不要为了画面效果编造真实业务能力或保存结果。

功能、数据和故障演示契约在 APP_CONTRACT.md。先检查页面与现有功能，再完成你的改动及适当验证。只修改本公开任务目录；不得改动真实项目、其他实验、用户安装或系统设置，不得访问外网、引入远程资源或安装依赖。不要启动其他模型。不要执行任何退款、发信或外部数据操作。

运行方式：无需构建或第三方依赖。可直接打开 index.html；更稳定的方式是在本目录运行 `python -m http.server 8765 --bind 127.0.0.1`，然后访问 http://127.0.0.1:8765/ 。本地服务仅用于此演示。
'''

CONTRACT = r'''# 公开功能契约

## 数据与边界

- 内置五条虚构工单，工单号 RT-10231 至 RT-10235。工单基本字段、金额与状态只供阅读；本页不执行真实退款、联系客户或改写真实订单。
- 一条长机构名、一条长商品名、多行问题描述、不同状态与语言，都是有意保留的代表性内容。不得通过删掉难排版数据来改善页面。
- 页面没有后端。网络资源、外部字体、CDN、遥测和线上 API 均不需要。
- 用户必须能知道当前为演示环境，备注只保存在当前浏览器。换浏览器、清除站点数据或使用不同本地地址可能看不到原备注。表达方式可改，不要求逐字保留。

## 搜索、选择与帮助

- 搜索按工单号、客户名和商品名做不区分大小写的子串匹配；空搜索显示全部记录。清空后恢复全部五条；无匹配时需要有可理解的结果状态和继续操作方式。
- 搜索不得偷偷保存或丢弃正在编辑的备注。当前详情可以继续显示，也可以采用其他合理方式保留工作，但所显示对象必须明确。
- 选择工单可查看客户、订单、商品、问题、状态、提交时间、金额及备注。切换过程中不能无提示丢失未保存输入；允许确认丢弃、保留每条草稿或其他语义等价做法。
- 帮助内容必须仍有可发现的入口，能让用户理解如何查找工单、保存位置和未保存变更。其容器、位置和是否初始展开均可改变。

## 备注、本机保存及状态

- 本机存储键为 `returns-desk-demo:v1`，JSON 数据格式为 `{ "notes": { "RT-10231": { "text": "备注", "savedAt": "ISO时间" } } }`。未知工单键不参与显示。请保持已有此格式数据仍可读取。
- 未保存的编辑与已保存值需要区分。保存只更新当前工单备注，不能改到其他工单、提前显示持久化成功或在失败时清除输入。
- 保存成功后，重新选择或刷新页面应读到相同备注；展示成功必须发生在真实 localStorage 写入完成之后。成功状态不代表退款完成。
- 没有修改时可以禁用保存、提示无需保存或采取其他清楚的做法。保存中的重复操作需要合理处理。
- 离开页面或切换会丢失输入时，需要提示或保留输入。浏览器可能不展示定制的离页文案；测试切换行为与页面可见的未保存状态即可检查主要语义。

## 可复现失败，不需要破坏代码

- 在同一本地地址加上 `?failSave=1`（如 http://127.0.0.1:8765/?failSave=1）会启用演示保存故障。页面应让用户知道故障演示已启用；每次保存都会失败，不能向 localStorage 写入该次草稿。
- 故障页仍可查找、选择、阅读和编辑。保存失败后保留输入和未保存状态，并让用户理解没有保存及如何继续；不能只改变按钮颜色。
- 回到不带该参数的地址恢复正常模式。离开故障页前先保存文本副本或确认放弃；不要宣称故障页中的未保存输入已经持久化。
- 真实浏览器存储读写异常也应被捕获并诚实反馈；不要编造未获证实的原因。故障注入参数及其语义需要保留，内部实现可改。

## 交付边界

只交付这个演示。任何“发送给客户”“批准退款”等新增表达都必须明确只是演示，且本题不需要这些新增能力。功能验收看任务结果与真实状态，不要求使用某个 DOM 结构、组件库、固定字数或特定布局。
'''

INDEX = r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>退货工单工作台 · 演示</title>
  <link rel="stylesheet" href="styles.css">
  <script src="app.js" defer></script>
</head>
<body>
  <header class="topbar"><strong>Horizon Service</strong><span>客户体验解决方案</span><button id="help-toggle" type="button" aria-expanded="true" aria-controls="help-panel">收起帮助</button></header>
  <div class="demo-notice">演示环境：所有工单均为虚构数据。处理备注只保存在当前浏览器，不会上传服务器或执行真实退款。</div>
  <div id="load-warning" class="warning" hidden></div>
  <div id="fault-warning" class="warning" hidden>演示保存故障已启用：本页的保存会模拟失败，不会写入本机存储。</div>
  <main>
    <section class="hero"><div class="eyebrow">下一代智能服务中枢</div><h1>让每一次退货<br>都成为信任的起点</h1><p>以清晰洞察连接客户与业务，让高效协作释放服务价值，助力团队从容应对每一份期待。</p><span class="hero-badge">退货工单工作台</span></section>
    <div class="work-layout">
      <section class="queue-panel" aria-labelledby="queue-title">
        <h2 id="queue-title">工单队列</h2><p class="queue-intro">从这里开启您的高效服务旅程，轻松发现每个客户的需求。</p>
        <div class="search-area"><label for="ticket-search">搜索工单、客户或商品</label><input id="ticket-search" type="search" autocomplete="off" placeholder="输入关键词"><button id="clear-search" type="button">清空搜索</button></div>
        <p id="result-count" role="status" aria-live="polite"></p><div id="ticket-list" class="ticket-list"></div><div id="empty-results" class="empty" hidden>没有找到匹配的工单。您可以调整关键词或清空搜索。</div>
        <aside class="adapter"><strong>实现适配器说明</strong><p>当前使用 LocalStorageNoteAdapter，通过统一 repository 接口读写工单备注。后续可将 provider 替换为服务端适配器，保留 view-model 与事件分发协议。</p></aside>
      </section>
      <section class="detail-panel" aria-labelledby="detail-title">
        <div class="detail-heading"><div><span class="eyebrow">工单详情</span><h2 id="detail-title"></h2></div><span id="detail-status" class="status-chip"></span></div>
        <div id="detail-fields" class="detail-fields"></div>
        <section class="issue-box"><h3>客户问题</h3><p id="customer-reason"></p></section>
        <section class="note-section"><label for="handling-note">处理备注</label><p id="note-help">记录本次处理进展。点击保存后仅写入当前浏览器。</p><textarea id="handling-note" rows="7" aria-describedby="note-help draft-state" placeholder="输入处理备注"></textarea><div class="note-footer"><span id="draft-state">尚未修改</span><button id="save-note" class="primary" type="button" disabled>保存备注</button></div><div id="save-feedback" class="save-feedback" role="status" aria-live="polite"></div><p id="last-saved" class="last-saved"></p></section>
      </section>
      <aside id="help-panel" class="help-panel" aria-labelledby="help-title"><span class="eyebrow">工作台使用指南</span><h2 id="help-title">服务，从有序开始</h2><ol><li><strong>查找客户请求</strong><p>在搜索框输入工单号、客户名或商品关键词，从列表中选择一条工单。</p></li><li><strong>了解退货情况</strong><p>查看客户问题、订单和当前状态，确认本次需要跟进的事项。</p></li><li><strong>留下处理记录</strong><p>在处理备注中记录进展，点击保存。备注只保存在当前浏览器。</p></li><li><strong>继续下一项任务</strong><p>存在未保存变更时，切换工单前会提示您确认是否放弃。</p></li></ol><div class="help-tip">这是演示工作台。页面不会联系客户、调用订单系统或执行退款。</div></aside>
    </div>
  </main>
</body>
</html>
'''

CSS = r'''*{box-sizing:border-box}body{margin:0;background:#f4f6fa;color:#26334a;font-family:"Microsoft YaHei",Arial,sans-serif;font-size:14px}button,input,textarea{font:inherit}button{cursor:pointer;border:1px solid #cbd4e3;background:white;color:#324866;border-radius:8px;padding:10px 15px}button:hover{background:#eaf0ff}button:disabled{cursor:default;opacity:.5}button:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid #8baaff;outline-offset:2px}[hidden]{display:none!important}.topbar{height:72px;background:white;padding:0 38px;display:flex;align-items:center;gap:24px;border-bottom:1px solid #e1e6ed}.topbar strong{font-size:22px;letter-spacing:.4px}.topbar span{color:#8a96a9}.topbar button{margin-left:auto}.demo-notice{padding:12px 38px;background:#e9effe;color:#2c4678;line-height:1.6}.warning{padding:12px 38px;background:#fff0d5;color:#72521d;line-height:1.6}main{padding:32px}.hero{padding:46px 50px 42px;background:linear-gradient(120deg,#e2e8ff,#f3edff 65%,#e8f5f9);border:1px solid #e0e5f6;border-radius:24px;margin-bottom:28px;position:relative}.eyebrow{font-size:12px;font-weight:700;letter-spacing:2px;color:#6c78a0}.hero h1{font-size:52px;line-height:1.35;color:#283c74;letter-spacing:2px;margin:18px 0}.hero p{font-size:17px;line-height:1.9;max-width:800px;color:#6e7c9d}.hero-badge{position:absolute;right:36px;bottom:36px;padding:16px 22px;background:rgba(255,255,255,.8);border-radius:20px;color:#697ab0}.work-layout{display:grid;grid-template-columns:290px minmax(330px,1fr) 300px;gap:26px;align-items:start}.queue-panel,.detail-panel,.help-panel{background:#fff;border:1px solid #e1e6ef;border-radius:20px;padding:24px;box-shadow:0 10px 25px rgba(45,66,109,.025)}h2{font-size:22px;line-height:1.4;margin:0 0 16px}.queue-intro{color:#8b97ab;line-height:1.9;margin-bottom:22px}.search-area{display:flex;flex-direction:column;gap:10px}.search-area label{font-size:13px;color:#53617a}.search-area input{width:100%;padding:12px;border:1px solid #cdd6e4;border-radius:8px}.search-area button{align-self:start}#result-count{font-size:12px;color:#7c88a0;margin:22px 0 14px}.ticket-list{display:flex;flex-direction:column;gap:18px}.ticket-card{width:100%;padding:22px 18px;text-align:left;line-height:1.7;border:1px solid #e0e6f0;border-radius:15px;background:#fbfcff;display:block}.ticket-card[aria-pressed="true"]{border:2px solid #7d9de4;background:#f0f4ff;padding:21px 17px}.ticket-card strong{display:block;margin:8px 0;font-size:15px}.ticket-card .ticket-product{display:block;color:#8793a7;font-size:12px;line-height:1.6;overflow-wrap:anywhere}.ticket-card .ticket-meta{display:block;color:#6a7894;font-size:12px;margin-top:14px}.adapter{margin-top:32px;background:#f5f6fa;padding:18px;border-radius:12px;color:#8b95a8;font-size:12px;line-height:1.9;overflow-wrap:anywhere}.adapter strong{color:#78849a}.detail-heading{display:flex;justify-content:space-between;align-items:center;gap:14px;margin-bottom:30px}.detail-heading h2{margin:12px 0 0;font-size:28px}.status-chip{white-space:nowrap;border-radius:20px;padding:9px 12px;background:#edf2fc;color:#4b6595;font-size:12px}.detail-fields{display:grid;grid-template-columns:1fr 1fr;gap:24px 32px;margin-bottom:32px}.field-label{display:block;font-size:12px;color:#929caf;margin-bottom:9px}.field-value{font-size:15px;line-height:1.9;overflow-wrap:anywhere}.field-product{grid-column:1/-1}.issue-box{padding:26px;background:#f7f8fb;border-radius:15px;margin-bottom:32px}.issue-box h3{font-size:14px;margin:0 0 18px}.issue-box p{font-size:15px;line-height:2;margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.note-section>label{display:block;font-weight:700;font-size:16px;margin-bottom:12px}#note-help{color:#8b96aa;font-size:12px;line-height:1.8;margin-bottom:18px}textarea{width:100%;resize:vertical;min-height:180px;padding:16px;border:1px solid #cdd7e6;border-radius:12px;color:#32445c;line-height:1.8}.note-footer{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-top:20px}#draft-state{font-size:12px;color:#7e8a9f}#draft-state.dirty{color:#9b6500}.primary{background:#6481c4;border-color:#6481c4;color:#fff;padding:13px 22px}.primary:hover{background:#4d6caf}.save-feedback{margin-top:20px;line-height:1.8;min-height:25px}.save-feedback.success{color:#217149}.save-feedback.error{color:#ac3548}.last-saved{font-size:12px;color:#8b97a8;line-height:1.8}.help-panel{background:#fafbff;padding:28px}.help-panel h2{font-size:25px;margin-top:18px;line-height:1.6}.help-panel ol{padding-left:22px;margin-top:30px}.help-panel li{padding:0 0 26px 6px;color:#71809b}.help-panel strong{font-size:14px;color:#566988}.help-panel p{font-size:13px;line-height:2;margin:12px 0 0}.help-tip{border-top:1px solid #e5e9f3;padding-top:24px;color:#8a96af;line-height:1.9;font-size:12px}.empty{padding:24px 0;color:#6d7c92;line-height:1.9}@media(max-width:1100px){.work-layout{grid-template-columns:260px minmax(300px,1fr)}.help-panel{grid-column:1/-1}.help-panel ol{display:grid;grid-template-columns:1fr 1fr;gap:24px}.hero h1{font-size:44px}}@media(max-width:680px){main{padding:16px}.topbar{padding:0 16px;gap:12px}.topbar strong{font-size:17px}.topbar span{display:none}.demo-notice,.warning{padding:12px 16px}.hero{padding:28px 24px}.hero h1{font-size:32px}.hero p{font-size:14px}.hero-badge{position:static;display:inline-block;margin-top:10px}.work-layout{display:block}.queue-panel,.detail-panel,.help-panel{margin-bottom:20px;padding:20px}.detail-fields{grid-template-columns:1fr}.field-product{grid-column:auto}.help-panel ol{display:block}}
'''

APP_JS = r'''"use strict";
(() => {
  const TICKETS = __SEED_DATA__;
  const STORAGE_KEY = "returns-desk-demo:v1";
  const failSave = new URLSearchParams(window.location.search).get("failSave") === "1";
  const $ = id => document.getElementById(id);
  let savedNotes = {};
  let currentId = TICKETS[0].id;
  let baseline = "";
  let saving = false;
  let helpOpen = true;

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw !== null) {
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed.notes !== "object" || parsed.notes === null || Array.isArray(parsed.notes)) throw new Error("Invalid saved notes");
      for (const ticket of TICKETS) {
        const item = parsed.notes[ticket.id];
        if (item && typeof item.text === "string" && typeof item.savedAt === "string") savedNotes[ticket.id] = {text:item.text, savedAt:item.savedAt};
      }
    }
  } catch (_) {
    $("load-warning").hidden = false;
    $("load-warning").textContent = "未能读取本机保存的备注。当前显示演示初始内容；已有本机数据的情况尚未确认。";
  }
  $("fault-warning").hidden = !failSave;

  const ticketById = id => TICKETS.find(ticket => ticket.id === id);
  const noteFor = ticket => savedNotes[ticket.id] ? savedNotes[ticket.id].text : ticket.note;
  const dirty = () => $("handling-note").value !== baseline;

  function updateDraftState() {
    const changed = dirty();
    $("draft-state").textContent = changed ? "有未保存的变更" : "当前内容无未保存变更";
    $("draft-state").classList.toggle("dirty", changed);
    $("save-note").disabled = saving || !changed;
    $("handling-note").disabled = saving;
    $("save-note").textContent = saving ? "保存中…" : "保存备注";
  }

  function renderList() {
    const query = $("ticket-search").value.trim().toLocaleLowerCase();
    const matches = TICKETS.filter(ticket => [ticket.id,ticket.customer,ticket.product].join(" ").toLocaleLowerCase().includes(query));
    $("result-count").textContent = query ? `找到 ${matches.length} 条匹配工单` : `共 ${matches.length} 条工单`;
    $("empty-results").hidden = matches.length !== 0;
    $("ticket-list").replaceChildren();
    for (const ticket of matches) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ticket-card";
      button.setAttribute("aria-pressed", String(ticket.id === currentId));
      button.disabled = saving;
      const id = document.createElement("span"); id.textContent = ticket.id;
      const name = document.createElement("strong"); name.textContent = ticket.customer;
      const product = document.createElement("span"); product.className = "ticket-product"; product.textContent = ticket.product;
      const meta = document.createElement("span"); meta.className = "ticket-meta"; meta.textContent = `${ticket.status} · ${ticket.submitted}`;
      button.append(id,name,product,meta);
      button.addEventListener("click", () => selectTicket(ticket.id));
      $("ticket-list").append(button);
    }
  }

  function renderDetails() {
    const ticket = ticketById(currentId);
    $("detail-title").textContent = ticket.id;
    $("detail-status").textContent = ticket.status;
    $("detail-fields").replaceChildren();
    const fields = [["客户",ticket.customer],["订单号",ticket.order],["提交时间",ticket.submitted],["申请金额",ticket.amount],["跟进标记",ticket.priority],["商品",ticket.product]];
    for (const [label,value] of fields) {
      const box = document.createElement("div");
      if (label === "商品") box.className = "field-product";
      const title = document.createElement("span"); title.className = "field-label"; title.textContent = label;
      const text = document.createElement("span"); text.className = "field-value"; text.textContent = value;
      box.append(title,text); $("detail-fields").append(box);
    }
    $("customer-reason").textContent = ticket.reason;
    baseline = noteFor(ticket);
    $("handling-note").value = baseline;
    $("save-feedback").textContent = "";
    $("save-feedback").className = "save-feedback";
    const saved = savedNotes[currentId];
    $("last-saved").textContent = saved ? `最近本机保存：${formatTime(saved.savedAt)}` : "尚无本机保存记录；当前可见备注来自演示初始数据。";
    updateDraftState();
  }

  function formatTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN",{hour12:false});
  }

  function selectTicket(id) {
    if (saving || id === currentId) return;
    if (dirty() && !window.confirm("当前备注有未保存变更。放弃这些变更并切换工单？")) return;
    currentId = id;
    renderDetails();
    renderList();
  }

  $("ticket-search").addEventListener("input",renderList);
  $("clear-search").addEventListener("click",() => {$("ticket-search").value="";renderList();$("ticket-search").focus();});
  $("handling-note").addEventListener("input",() => {
    $("save-feedback").textContent=""; $("save-feedback").className="save-feedback"; updateDraftState();
  });
  $("help-toggle").addEventListener("click",() => {
    helpOpen = !helpOpen;
    $("help-panel").hidden = !helpOpen;
    $("help-toggle").setAttribute("aria-expanded",String(helpOpen));
    $("help-toggle").textContent=helpOpen ? "收起帮助" : "帮助";
  });
  $("save-note").addEventListener("click",() => {
    if (saving || !dirty()) return;
    const id = currentId;
    const text = $("handling-note").value;
    saving = true;updateDraftState();renderList();
    $("save-feedback").textContent="正在保存到当前浏览器…";
    $("save-feedback").className="save-feedback";
    window.setTimeout(() => {
      try {
        if (failSave) throw new Error("Demonstration failure");
        const savedAt = new Date().toISOString();
        const next = {...savedNotes,[id]:{text,savedAt}};
        window.localStorage.setItem(STORAGE_KEY,JSON.stringify({notes:next}));
        savedNotes = next;
        baseline = text;
        $("save-feedback").textContent="备注已保存到当前浏览器。此操作未执行退款。";
        $("save-feedback").className="save-feedback success";
        $("last-saved").textContent=`最近本机保存：${formatTime(savedAt)}`;
      } catch (_) {
        $("save-feedback").textContent=failSave ? "备注未保存：演示保存故障已触发。输入仍保留，可复制内容后退出故障演示，再重新编辑保存。" : "备注未保存：本机写入没有完成。输入仍保留，可检查浏览器存储权限后重试。";
        $("save-feedback").className="save-feedback error";
      } finally {
        saving=false;updateDraftState();renderList();
      }
    },250);
  });
  window.addEventListener("beforeunload",event => {if (dirty()) {event.preventDefault();event.returnValue="";}});
  renderDetails();renderList();
})();
'''


def fixture_files() -> dict[str, str]:
    return {
        "TASK.md": TASK,
        "APP_CONTRACT.md": CONTRACT,
        "index.html": INDEX,
        "styles.css": CSS,
        "app.js": APP_JS.replace("__SEED_DATA__", json.dumps(TICKETS, ensure_ascii=False, indent=2)),
    }


def build_fixture(output: Path) -> dict[str, str]:
    output = output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"Refusing to overwrite a non-empty target: {output}")
    output.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for relative, content in fixture_files().items():
        target = output / relative
        target.write_text(content, encoding="utf-8", newline="\n")
        hashes[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
    return hashes


def self_test() -> dict:
    temp_root = Path(tempfile.gettempdir()).resolve()
    with tempfile.TemporaryDirectory(prefix="returns-fixture-selftest-") as temp_name:
        temp = Path(temp_name).resolve()
        if temp.parent != temp_root:
            raise RuntimeError("Unexpected self-test workspace")
        first, second = temp / "first", temp / "second"
        hashes = build_fixture(first)
        assert hashes == build_fixture(second), "Generated contents must be deterministic"
        assert set(hashes) == {"TASK.md", "APP_CONTRACT.md", "index.html", "styles.css", "app.js"}
        try:
            build_fixture(first)
        except ValueError:
            pass
        else:
            raise AssertionError("Non-empty destination was overwritten")
        assert len({ticket["id"] for ticket in TICKETS}) == 5
        assert all(ticket["id"] in (first / "app.js").read_text(encoding="utf-8") for ticket in TICKETS)
        assert "__SEED_DATA__" not in (first / "app.js").read_text(encoding="utf-8")
        assert all("https://" not in content for name, content in fixture_files().items() if name in {"index.html", "styles.css", "app.js"})
        node = shutil.which("node")
        if node:
            subprocess.run([node, "--check", str(first / "app.js")], check=True, capture_output=True, text=True)
        return {"passed": True, "checks": ["deterministic generation", "expected public files", "refuse overwrite", "five unique representative records", "seed substitution", "no remote page resources", "JavaScript syntax" if node else "JavaScript syntax unavailable: node absent"], "functional_ui_test": "not claimed by generator self-test"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "fixture-public")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return
    try:
        hashes = build_fixture(args.out)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({"output": str(args.out.resolve()), "sha256": hashes}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
