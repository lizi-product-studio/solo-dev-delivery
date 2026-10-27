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
  const ticketById = id => TICKETS.find(ticket => ticket.id === id);
  const statusClass = status => ({
    "待处理": "status-pending",
    "等待凭证": "status-waiting",
    "待收货": "status-transit",
    "已完成": "status-done"
  })[status] || "";
  const drafts = new Map();
  const feedback = new Map();
  let savedNotes = {};
  let currentId = TICKETS[0].id;
  let saving = false;
  let allowUnload = false;

  // Re-read before writing so a save changes only the selected ticket.
  // Unknown keys are preserved in storage, but never appear in the queue.
  function readStorage() {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === null) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) ||
        !parsed.notes || typeof parsed.notes !== "object" || Array.isArray(parsed.notes)) {
      throw new Error("Invalid saved notes");
    }
    for (const ticket of TICKETS) {
      const item = parsed.notes[ticket.id];
      if (item !== undefined && (!item || typeof item.text !== "string" || typeof item.savedAt !== "string")) {
        throw new Error("Invalid saved note");
      }
    }
    return parsed.notes;
  }

  function showLoadWarning() {
    $("load-warning").hidden = false;
    $("load-warning").textContent = "未能读取本机备注，当前显示初始演示内容。已有保存记录尚未确认；请保留输入，保存时将重新尝试读取。";
  }

  try {
    const stored = readStorage();
    for (const ticket of TICKETS) {
      if (stored[ticket.id]) savedNotes[ticket.id] = stored[ticket.id];
    }
  } catch (_) {
    showLoadWarning();
  }
  $("fault-warning").hidden = !failSave;

  const noteFor = id => savedNotes[id] ? savedNotes[id].text : ticketById(id).note;
  const dirty = () => drafts.has(currentId);
  const setText = (id, value) => {
    if ($(id).textContent !== value) $(id).textContent = value;
  };

  function formatTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN", { hour12: false });
  }

  function updateDraftState() {
    const changed = dirty();
    setText("draft-state", changed ? "未保存变更" : savedNotes[currentId] ? "已保存到此浏览器" : "未修改");
    $("draft-state").className = changed ? "dirty" : savedNotes[currentId] ? "saved" : "";
    $("save-note").disabled = saving || !changed;
    $("save-note").textContent = saving ? "保存中…" : "保存备注";
    $("handling-note").readOnly = saving;
    document.querySelector(".note-section").setAttribute("aria-busy", String(saving));
    $("draft-summary").hidden = drafts.size === 0;
    setText("draft-summary", drafts.size + " 条备注未保存");
    for (const button of document.querySelectorAll(".ticket-card")) {
      button.disabled = saving;
      button.querySelector(".draft-marker").hidden = !drafts.has(button.dataset.id);
    }
  }

  function renderFeedback() {
    const item = feedback.get(currentId);
    $("save-feedback").hidden = !item;
    $("save-feedback").className = "save-feedback" + (item ? " " + item.kind : "");
    setText("save-feedback", item ? item.text : "");
  }

  function makeText(tag, className, text) {
    const element = document.createElement(tag);
    element.className = className;
    element.textContent = text;
    return element;
  }

  function renderList() {
    const query = $("ticket-search").value.trim().toLocaleLowerCase();
    const matches = TICKETS.filter(ticket =>
      [ticket.id, ticket.customer, ticket.product].some(value => value.toLocaleLowerCase().includes(query))
    );
    $("result-count").textContent = query ? "找到 " + matches.length + " 条匹配工单" : "显示全部 " + TICKETS.length + " 条工单";
    $("clear-search").hidden = $("ticket-search").value.length === 0;
    $("empty-results").hidden = matches.length !== 0;
    $("filter-context").hidden = matches.some(ticket => ticket.id === currentId);
    $("ticket-list").replaceChildren();
    for (const ticket of matches) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ticket-card";
      button.dataset.id = ticket.id;
      button.setAttribute("aria-pressed", String(ticket.id === currentId));
      button.disabled = saving;
      const top = document.createElement("span");
      top.className = "ticket-top";
      top.append(
        makeText("span", "ticket-id", ticket.id),
        makeText("span", "status-chip " + statusClass(ticket.status), ticket.status)
      );
      const meta = document.createElement("span");
      meta.className = "ticket-meta";
      const marker = makeText("span", "draft-marker", "未保存");
      marker.hidden = !drafts.has(ticket.id);
      const time = makeText("time", "", ticket.submitted);
      time.dateTime = ticket.submitted.replace(" ", "T") + ":00+08:00";
      meta.append(time, marker);
      button.append(top, makeText("strong", "", ticket.customer), makeText("span", "ticket-product", ticket.product), meta);
      button.addEventListener("click", () => selectTicket(ticket.id));
      $("ticket-list").append(button);
    }
  }

  function renderDetails() {
    const ticket = ticketById(currentId);
    $("detail-title").textContent = ticket.id;
    $("detail-status").textContent = ticket.status;
    $("detail-status").className = "status-chip " + statusClass(ticket.status);
    $("detail-fields").replaceChildren();
    $("detail-followup").replaceChildren();
    const fields = [
      ["客户", ticket.customer, "customer"],
      ["订单号", ticket.order, "order"],
      ["申请金额", ticket.amount, "amount"],
      ["商品", ticket.product, "product"],
      ["提交时间", ticket.submitted, "submitted"],
      ["跟进标记", ticket.priority, "priority"]
    ];
    for (const [label, value, key] of fields) {
      const box = document.createElement("div");
      box.className = "field-" + key + (key === "priority" && ticket.priority === "需要跟进" ? " needs-followup" : "");
      box.append(makeText("dt", "", label), makeText("dd", "", value));
      $(key === "priority" ? "detail-followup" : "detail-fields").append(box);
    }
    $("customer-reason").textContent = ticket.reason;
    $("handling-note").value = drafts.has(currentId) ? drafts.get(currentId) : noteFor(currentId);
    const saved = savedNotes[currentId];
    $("last-saved").textContent = saved ? "最近保存：" + formatTime(saved.savedAt) : "初始演示备注 · 尚无本机保存记录";
    renderFeedback();
    updateDraftState();
  }

  function selectTicket(id) {
    if (saving || id === currentId) return;
    currentId = id;
    renderDetails();
    // Update selection in place to preserve keyboard focus on the queue.
    for (const button of document.querySelectorAll(".ticket-card")) {
      button.setAttribute("aria-pressed", String(button.dataset.id === currentId));
    }
    $("filter-context").hidden = !!document.querySelector('.ticket-card[aria-pressed="true"]');
    if (window.matchMedia("(max-width: 600px)").matches) {
      $("detail-title").tabIndex = -1;
      $("detail-title").focus({ preventScroll: true });
      $("detail-title").scrollIntoView({ block: "start" });
    }
  }

  function clearSearch() {
    $("ticket-search").value = "";
    renderList();
    $("ticket-search").focus();
  }
  $("ticket-search").addEventListener("input", renderList);
  $("clear-search").addEventListener("click", clearSearch);
  $("empty-clear").addEventListener("click", clearSearch);

  $("handling-note").addEventListener("input", () => {
    const text = $("handling-note").value;
    if (text === noteFor(currentId)) drafts.delete(currentId);
    else drafts.set(currentId, text);
    if (feedback.get(currentId)?.kind === "success") feedback.delete(currentId);
    renderFeedback();
    updateDraftState();
  });

  $("help-toggle").addEventListener("click", () => {
    $("help-panel").showModal();
    $("help-toggle").setAttribute("aria-expanded", "true");
  });
  $("help-close").addEventListener("click", () => $("help-panel").close());
  $("help-panel").addEventListener("close", () => {
    $("help-toggle").setAttribute("aria-expanded", "false");
    $("help-toggle").focus({ preventScroll: true });
  });
  $("help-panel").addEventListener("click", event => {
    if (event.target !== $("help-panel")) return;
    const rect = $("help-panel").getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) {
      $("help-panel").close();
    }
  });

  $("exit-fault").addEventListener("click", () => {
    if (drafts.size && !window.confirm("仍有未保存的备注。请先复制文本；返回正常模式会放弃这些草稿。确认放弃并返回？")) return;
    const url = new URL(window.location.href);
    url.searchParams.delete("failSave");
    allowUnload = true;
    window.location.assign(url.href);
  });

  $("save-note").addEventListener("click", () => {
    if (saving || !dirty()) return;
    const id = currentId;
    const text = $("handling-note").value;
    saving = true;
    updateDraftState();
    feedback.set(id, { kind: "", text: "正在保存到当前浏览器…" });
    renderFeedback();

    // Let the busy state paint; no server or network request is involved.
    window.setTimeout(() => {
      let reading = true;
      try {
        if (failSave) throw new Error("Demonstration failure");
        const latest = readStorage();
        reading = false;
        const savedAt = new Date().toISOString();
        const next = { ...latest, [id]: { text, savedAt } };
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ notes: next }));
        // Success is shown only after setItem returns without an exception.
        savedNotes = {};
        for (const ticket of TICKETS) {
          if (next[ticket.id]) savedNotes[ticket.id] = next[ticket.id];
        }
        drafts.delete(id);
        for (const [draftId, draftText] of drafts) {
          if (draftText === noteFor(draftId)) drafts.delete(draftId);
        }
        feedback.set(id, { kind: "success", text: "备注已保存到当前浏览器。未执行退款或更改订单。" });
        $("last-saved").textContent = "最近保存：" + formatTime(savedAt);
        $("load-warning").hidden = true;
      } catch (_) {
        const message = failSave
          ? "备注未保存：演示保存故障已触发。输入和草稿仍保留。请复制文本，再返回正常模式重新编辑保存。"
          : reading
            ? "备注未保存：无法读取原有本机记录，本次未写入。输入和草稿仍保留；请复制文本备份，稍后重试。"
            : "备注未保存：浏览器存储写入未完成。输入和草稿仍保留，可重试保存，或先复制文本备份。";
        feedback.set(id, { kind: "error", text: message });
      } finally {
        saving = false;
        renderFeedback();
        updateDraftState();
      }
    }, 180);
  });

  window.addEventListener("beforeunload", event => {
    if (drafts.size && !allowUnload) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  renderDetails();
  renderList();
})();
