const bridge = window.AstrBotPluginPage;

const elements = {
  captureToggle: document.getElementById("captureToggle"),
  captureLabel: document.getElementById("captureLabel"),
  refreshButton: document.getElementById("refreshButton"),
  clearButton: document.getElementById("clearButton"),
  connectionStatus: document.getElementById("connectionStatus"),
  queueSize: document.getElementById("queueSize"),
  droppedRecords: document.getElementById("droppedRecords"),
  privacyMode: document.getElementById("privacyMode"),
  searchInput: document.getElementById("searchInput"),
  runList: document.getElementById("runList"),
  runCount: document.getElementById("runCount"),
  emptyState: document.getElementById("emptyState"),
  traceDetail: document.getElementById("traceDetail"),
  detailRunId: document.getElementById("detailRunId"),
  detailTitle: document.getElementById("detailTitle"),
  detailMeta: document.getElementById("detailMeta"),
  deleteButton: document.getElementById("deleteButton"),
  charStats: document.getElementById("charStats"),
  timeline: document.getElementById("timeline"),
  payloadTitle: document.getElementById("payloadTitle"),
  payloadView: document.getElementById("payloadView"),
  copyButton: document.getElementById("copyButton"),
  toast: document.getElementById("toast"),
  confirmOverlay: document.getElementById("confirmOverlay"),
  confirmMessage: document.getElementById("confirmMessage"),
  confirmCancel: document.getElementById("confirmCancel"),
  confirmAccept: document.getElementById("confirmAccept"),
};

let runs = [];
let selectedRun = null;
let selectedEvent = null;
let subscriptionId = null;
let searchTimer = null;
let refreshTimer = null;
let confirmResolver = null;

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("visible");
  window.setTimeout(() => elements.toast.classList.remove("visible"), 2200);
}

function closeConfirmation(accepted) {
  elements.confirmOverlay.classList.add("hidden");
  const resolve = confirmResolver;
  confirmResolver = null;
  if (resolve) resolve(accepted);
}

function askConfirmation(message) {
  if (confirmResolver) closeConfirmation(false);
  elements.confirmMessage.textContent = message;
  elements.confirmOverlay.classList.remove("hidden");
  window.setTimeout(() => elements.confirmCancel.focus(), 0);
  return new Promise((resolve) => { confirmResolver = resolve; });
}

function formatTime(timestamp, withDate = false) {
  if (!timestamp) return "—";
  return new Intl.DateTimeFormat(undefined, {
    month: withDate ? "2-digit" : undefined,
    day: withDate ? "2-digit" : undefined,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(timestamp * 1000));
}

function duration(run) {
  if (!run.finished_at) return "进行中";
  const milliseconds = Math.max(0, (run.finished_at - run.started_at) * 1000);
  return milliseconds < 1000 ? `${Math.round(milliseconds)} ms` : `${(milliseconds / 1000).toFixed(2)} s`;
}

function renderRuns() {
  elements.runList.replaceChildren();
  elements.runCount.textContent = String(runs.length);
  if (!runs.length) {
    const empty = document.createElement("div");
    empty.className = "list-empty";
    empty.textContent = "还没有 Trace 记录";
    elements.runList.append(empty);
    return;
  }
  for (const run of runs) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `run-item${selectedRun?.run_id === run.run_id ? " active" : ""}`;

    const top = document.createElement("div");
    top.className = "run-top";
    const model = document.createElement("span");
    model.className = "run-model";
    model.textContent = run.model || run.provider_id || "Unknown provider";
    const status = document.createElement("span");
    status.className = "run-status";
    status.textContent = run.status;
    top.append(model, status);

    const bottom = document.createElement("div");
    bottom.className = "run-bottom";
    const time = document.createElement("span");
    time.textContent = formatTime(run.started_at, true);
    const stats = document.createElement("span");
    stats.textContent = `${run.event_count} events · ${duration(run)}`;
    bottom.append(time, stats);
    item.append(top, bottom);
    item.addEventListener("click", () => loadRun(run.run_id));
    elements.runList.append(item);
  }
}

function eventSummary(event) {
  const payload = event.payload || {};
  if (event.event_type === "tool.start" || event.event_type === "tool.end") return payload.name || "tool";
  if (event.event_type === "llm.request") {
    const chars = payload.request?.stats?.input_chars;
    return `${payload.provider?.model || "request snapshot"}${Number.isFinite(chars) ? ` · ${chars.toLocaleString()} 字符` : ""}`;
  }
  if (event.event_type === "llm.response") {
    const chars = payload.stats?.output_chars;
    return `${payload.role || "response"}${Number.isFinite(chars) ? ` · ${chars.toLocaleString()} 字符` : ""}`;
  }
  const messageCount = Array.isArray(payload.messages) ? payload.messages.length : null;
  return messageCount === null ? "" : `${messageCount} messages`;
}

function renderCharacterStats() {
  elements.charStats.replaceChildren();
  const requestEvent = selectedRun?.events?.find((event) => event.event_type === "llm.request");
  const responseEvent = selectedRun?.events?.find((event) => event.event_type === "llm.response");
  const requestStats = requestEvent?.payload?.request?.stats || {};
  const responseStats = responseEvent?.payload?.stats || {};
  const toolResultChars = (selectedRun?.events || [])
    .filter((event) => event.event_type === "tool.end")
    .reduce((total, event) => total + (event.payload?.stats?.result_chars || 0), 0);
  const stats = [
    ["输入总字符", requestStats.input_chars],
    ["本轮提示", requestStats.prompt_chars],
    ["系统提示", requestStats.system_prompt_chars],
    ["历史上下文", requestStats.context_chars],
    ["最终输出", responseStats.output_chars],
    ["推理 / 工具结果", (responseStats.reasoning_chars || 0) + toolResultChars],
  ];
  for (const [label, value] of stats) {
    const card = document.createElement("div");
    card.className = "char-stat";
    const name = document.createElement("span");
    name.textContent = label;
    const number = document.createElement("strong");
    number.textContent = Number.isFinite(value) ? value.toLocaleString() : "—";
    card.append(name, number);
    elements.charStats.append(card);
  }
}

function selectEvent(event, button) {
  selectedEvent = event;
  for (const item of elements.timeline.querySelectorAll(".event-item")) item.classList.remove("active");
  if (button) button.classList.add("active");
  elements.payloadTitle.textContent = event.event_type;
  elements.payloadView.textContent = JSON.stringify(event.payload, null, 2);
}

function renderDetail() {
  if (!selectedRun) {
    elements.emptyState.classList.remove("hidden");
    elements.traceDetail.classList.add("hidden");
    return;
  }
  elements.emptyState.classList.add("hidden");
  elements.traceDetail.classList.remove("hidden");
  elements.detailRunId.textContent = selectedRun.run_id;
  elements.detailTitle.textContent = selectedRun.model || selectedRun.provider_id || "LLM Trace";
  elements.detailMeta.textContent = `${selectedRun.platform || "unknown"} · ${formatTime(selectedRun.started_at, true)} · ${duration(selectedRun)}`;
  renderCharacterStats();
  elements.timeline.replaceChildren();

  const events = selectedRun.events || [];
  for (const event of events) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "event-item";
    const type = document.createElement("span");
    type.className = "event-type";
    type.textContent = event.event_type;
    const summary = document.createElement("span");
    summary.className = "event-summary";
    summary.textContent = eventSummary(event);
    const time = document.createElement("span");
    time.className = "event-time";
    time.textContent = formatTime(event.timestamp);
    button.append(type, summary, time);
    button.addEventListener("click", () => selectEvent(event, button));
    elements.timeline.append(button);
  }
  if (events.length) {
    const firstButton = elements.timeline.querySelector(".event-item");
    selectEvent(events[0], firstButton);
  } else {
    elements.payloadTitle.textContent = "无事件";
    elements.payloadView.textContent = "{}";
  }
  renderRuns();
}

async function loadStatus() {
  const status = await bridge.apiGet("status");
  elements.captureToggle.checked = status.enabled;
  elements.captureLabel.textContent = status.enabled ? "采集已开启" : "采集已关闭";
  elements.queueSize.textContent = String(status.queue_size);
  elements.droppedRecords.textContent = String(status.dropped_records + status.write_errors);
  elements.privacyMode.textContent = `${status.hash_identity ? "身份哈希" : "原始身份"} · ${status.capture_content ? "正文" : "仅元数据"}`;
}

async function loadRuns() {
  const result = await bridge.apiGet("runs", { limit: 100, query: elements.searchInput.value.trim() });
  runs = result.items || [];
  renderRuns();
}

async function loadRun(runId) {
  try {
    selectedRun = await bridge.apiGet(`runs/${runId}`);
    renderDetail();
  } catch (error) {
    showToast(error.message || "加载 Trace 失败");
  }
}

async function refresh() {
  try {
    await Promise.all([loadStatus(), loadRuns()]);
  } catch (error) {
    elements.connectionStatus.textContent = "连接失败";
    showToast(error.message || "刷新失败");
  }
}

function scheduleRefresh() {
  window.clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(async () => {
    await loadRuns();
    if (selectedRun) await loadRun(selectedRun.run_id);
  }, 250);
}

async function initialize() {
  const context = await bridge.ready();
  document.documentElement.dataset.theme = context.isDark ? "dark" : "light";
  bridge.onContext((nextContext) => {
    document.documentElement.dataset.theme = nextContext.isDark ? "dark" : "light";
  });
  await refresh();
  subscriptionId = await bridge.subscribeSSE("stream", {
    onOpen() { elements.connectionStatus.textContent = "实时连接"; },
    onMessage(event) { if (event.eventType !== "ready") scheduleRefresh(); },
    onError() { elements.connectionStatus.textContent = "正在重连"; },
  });
}

elements.captureToggle.addEventListener("change", async () => {
  try {
    const status = await bridge.apiPost("status", { enabled: elements.captureToggle.checked });
    elements.captureLabel.textContent = status.enabled ? "采集已开启" : "采集已关闭";
    showToast(status.enabled ? "LLM Trace 已开启" : "LLM Trace 已关闭");
  } catch (error) {
    elements.captureToggle.checked = !elements.captureToggle.checked;
    showToast(error.message || "状态更新失败");
  }
});

elements.refreshButton.addEventListener("click", refresh);
elements.searchInput.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(loadRuns, 250);
});
elements.clearButton.addEventListener("click", async () => {
  if (!await askConfirmation("确定清空全部 LLM Trace 记录吗？此操作无法撤销。")) return;
  try {
    await bridge.apiPost("clear", {});
    selectedRun = null;
    renderDetail();
    await loadRuns();
    showToast("Trace 已清空");
  } catch (error) {
    showToast(error.message || "清空失败");
  }
});
elements.deleteButton.addEventListener("click", async () => {
  if (!selectedRun || !await askConfirmation("确定删除当前 Trace？此操作无法撤销。")) return;
  try {
    await bridge.apiPost(`runs/${selectedRun.run_id}/delete`, {});
    selectedRun = null;
    renderDetail();
    await loadRuns();
    showToast("Trace 已删除");
  } catch (error) {
    showToast(error.message || "删除失败");
  }
});
elements.confirmCancel.addEventListener("click", () => closeConfirmation(false));
elements.confirmAccept.addEventListener("click", () => closeConfirmation(true));
elements.confirmOverlay.addEventListener("click", (event) => {
  if (event.target === elements.confirmOverlay) closeConfirmation(false);
});
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && confirmResolver) closeConfirmation(false);
});
elements.copyButton.addEventListener("click", async () => {
  const text = elements.payloadView.textContent;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      showToast("JSON 已复制");
      return;
    }
  } catch (_) {
    // Restricted plugin iframes may reject the modern Clipboard API.
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.append(textarea);
  textarea.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } finally {
    textarea.remove();
  }
  if (copied) {
    showToast("JSON 已复制");
    return;
  }

  const selection = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(elements.payloadView);
  selection.removeAllRanges();
  selection.addRange(range);
  showToast("浏览器禁止自动复制，JSON 已选中，请按 Ctrl+C");
});

window.addEventListener("beforeunload", () => {
  if (subscriptionId !== null) bridge.unsubscribeSSE(subscriptionId);
});

initialize().catch((error) => {
  elements.connectionStatus.textContent = "初始化失败";
  showToast(error.message || "页面初始化失败");
});
