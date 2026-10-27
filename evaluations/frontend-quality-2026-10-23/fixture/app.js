"use strict";
(() => {
  const TICKETS = [
  {
    "id": "RT-10231",
    "customer": "陈晓敏",
    "order": "OD-20261020-0781",
    "product": "保温随行杯 500ml · 雾蓝",
    "reason": "杯盖密封圈破损，收到后发现渗水，希望退货退款。",
    "status": "待处理",
    "priority": "普通",
    "submitted": "2026-10-23 09:12",
    "amount": "¥129.00",
    "note": ""
  },
  {
    "id": "RT-10232",
    "customer": "上海远行文化创意工作室",
    "order": "OD-20261019-1229",
    "product": "多功能旅行收纳套装（大号衣物袋＋鞋袋＋数码配件袋，深灰色限量组合装）",
    "reason": "订单包含三套收纳袋，其中一套少了数码配件袋；客户希望先确认能否补发，再决定是否退货。",
    "status": "等待凭证",
    "priority": "需要跟进",
    "submitted": "2026-10-23 09:34",
    "amount": "¥357.00",
    "note": "已向客户索取包装及缺件照片。"
  },
  {
    "id": "RT-10233",
    "customer": "Amina Yusuf",
    "order": "OD-20261018-0635",
    "product": "桌面阅读灯 · 暖白",
    "reason": "灯臂无法固定，已提供视频。",
    "status": "待处理",
    "priority": "普通",
    "submitted": "2026-10-23 10:03",
    "amount": "¥219.00",
    "note": ""
  },
  {
    "id": "RT-10234",
    "customer": "李维",
    "order": "OD-20261021-0267",
    "product": "轻量折叠伞 · 黑色",
    "reason": "收到的颜色与订单不符。客户已寄回，仓库尚未签收。",
    "status": "待收货",
    "priority": "普通",
    "submitted": "2026-10-23 10:26",
    "amount": "¥89.00",
    "note": "退件运单：SF-DEMO-7086。等待仓库签收。"
  },
  {
    "id": "RT-10235",
    "customer": "周婉宁",
    "order": "OD-20261017-0974",
    "product": "棉麻靠垫套 · 自然白（两只装）",
    "reason": "与预期尺寸不符，已核验商品完好。",
    "status": "已完成",
    "priority": "普通",
    "submitted": "2026-10-22 16:48",
    "amount": "¥78.00",
    "note": "演示记录：退货审核已完成。此页面不会执行真实退款。"
  }
];
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
