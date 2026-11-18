const bridge = window.AstrBotPluginPage;

const elements = {
  captureToggle: document.getElementById("captureToggle"),
  probeToggle: document.getElementById("probeToggle"),
  probeLabel: document.getElementById("probeLabel"),
  refreshButton: document.getElementById("refreshButton"),
  clearButton: document.getElementById("clearButton"),
  connectionStatus: document.getElementById("connectionStatus"),
  connectionLabel: document.getElementById("connectionLabel"),
  queueSize: document.getElementById("queueSize"),
  droppedRecords: document.getElementById("droppedRecords"),
  privacyMode: document.getElementById("privacyMode"),
  searchInput: document.getElementById("searchInput"),
  runList: document.getElementById("runList"),
  runCount: document.getElementById("runCount"),
  emptyState: document.getElementById("emptyState"),
  traceDetail: document.getElementById("traceDetail"),
  backButton: document.getElementById("backButton"),
  detailRunId: document.getElementById("detailRunId"),
  detailTitle: document.getElementById("detailTitle"),
  detailStatus: document.getElementById("detailStatus"),
  detailMeta: document.getElementById("detailMeta"),
  exportButton: document.getElementById("exportButton"),
  exportOverlay: document.getElementById("exportOverlay"),
  exportDialog: document.getElementById("exportDialog"),
  exportDescription: document.getElementById("exportDescription"),
  exportSelectAll: document.getElementById("exportSelectAll"),
  exportSelectedCount: document.getElementById("exportSelectedCount"),
  exportEventList: document.getElementById("exportEventList"),
  exportError: document.getElementById("exportError"),
  exportCancel: document.getElementById("exportCancel"),
  exportAccept: document.getElementById("exportAccept"),
  deleteButton: document.getElementById("deleteButton"),
  deletePopover: document.getElementById("deletePopover"),
  deleteMessage: document.getElementById("deleteMessage"),
  deleteError: document.getElementById("deleteError"),
  deleteCancel: document.getElementById("deleteCancel"),
  deleteAccept: document.getElementById("deleteAccept"),
  charStats: document.getElementById("charStats"),
  mobileTimeline: document.getElementById("mobileTimeline"),
  mobileEventCount: document.getElementById("mobileEventCount"),
  payloadTitle: document.getElementById("payloadTitle"),
  payloadEmpty: document.getElementById("payloadEmpty"),
  payloadView: document.getElementById("payloadView"),
  copyButton: document.getElementById("copyButton"),
  toast: document.getElementById("toast"),
  clearOverlay: document.getElementById("clearOverlay"),
  clearDialog: document.getElementById("clearDialog"),
  clearError: document.getElementById("clearError"),
  clearCancel: document.getElementById("clearCancel"),
  clearAccept: document.getElementById("clearAccept"),
};

let runs = [];
let selectedRun = null;
let selectedEvent = null;
let expandedRunId = null;
let subscriptionId = null;
let streamConnectPromise = null;
let streamReconnectTimer = null;
let streamWatchdogTimer = null;
let streamEpoch = 0;
let streamReconnectAttempts = 0;
let streamLastActivity = 0;
let unloading = false;
let searchTimer = null;
let refreshTimer = null;
let toastTimer = null;
let clearPreviousFocus = null;
let exportPreviousFocus = null;
let deletePending = false;
let clearPending = false;
let exportPending = false;
let exportSelection = new Set();
let payloadCopyText = "{}";
const mobileLayout = window.matchMedia("(max-width: 720px)");
const PAYLOAD_PREVIEW_MAX_CHARS = 250_000;
const STREAM_HEARTBEAT_TIMEOUT_MS = 40_000;
const STREAM_RECONNECT_BASE_MS = 1_000;
const STREAM_RECONNECT_MAX_MS = 15_000;

const STATUS_LABELS = {
  completed: "已完成",
  error: "错误",
  running: "进行中",
};

function normalizeStatus(status) {
  return Object.hasOwn(STATUS_LABELS, status) ? status : "unknown";
}

function statusLabel(status) {
  return STATUS_LABELS[status] || status || "未知";
}

function setConnection(label, state) {
  elements.connectionLabel.textContent = label;
  elements.connectionStatus.dataset.state = state;
}

function setButtonBusy(button, busy) {
  button.disabled = busy;
  button.setAttribute("aria-busy", String(busy));
  button.classList.toggle("is-spinning", busy);
}

function showToast(message) {
  window.clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.add("visible");
  toastTimer = window.setTimeout(() => elements.toast.classList.remove("visible"), 2600);
}

function setConfirmationError(element, message = "") {
  element.textContent = message;
  element.classList.toggle("hidden", !message);
}

async function copyTextToClipboard(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
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
  try {
    return document.execCommand("copy");
  } finally {
    textarea.remove();
  }
}

function applyTheme(context) {
  const theme = context?.isDark ? "dark" : "light";
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

function formatTime(timestamp, withDate = false) {
  if (!timestamp) return "-";
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
  return milliseconds < 1000
    ? `${Math.round(milliseconds)} ms`
    : `${(milliseconds / 1000).toFixed(2)} s`;
}

function focusSelectedRun() {
  const activeEvent = elements.runList.querySelector(".event-item.active");
  if (activeEvent) {
    activeEvent.focus();
    return;
  }
  const item = [...elements.runList.querySelectorAll(".run-item")]
    .find((button) => button.dataset.runId === selectedRun?.run_id);
  item?.focus();
}

function showMobileList() {
  document.body.classList.remove("mobile-detail");
  window.setTimeout(focusSelectedRun, 0);
}

function createIcon(symbolId) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  svg.classList.add("icon");
  svg.setAttribute("aria-hidden", "true");
  use.setAttribute("href", `#${symbolId}`);
  svg.append(use);
  return svg;
}

function renderRuns() {
  elements.runList.replaceChildren();
  elements.runList.setAttribute("aria-busy", "false");
  elements.runCount.textContent = String(runs.length);
  elements.runCount.setAttribute("aria-label", `${runs.length} 条记录`);

  if (!runs.length) {
    const empty = document.createElement("div");
    empty.className = "list-empty";
    empty.textContent = elements.searchInput.value.trim()
      ? "没有匹配的 Trace"
      : "还没有 Trace 记录";
    elements.runList.append(empty);
    return;
  }

  for (const [index, run] of runs.entries()) {
    const group = document.createElement("div");
    const item = document.createElement("button");
    const isActive = selectedRun?.run_id === run.run_id;
    const isExpanded = !mobileLayout.matches
      && expandedRunId === run.run_id
      && isActive;
    const state = normalizeStatus(run.status);
    const modelName = run.model || run.provider_id || "Unknown provider";
    const timelineId = `run-timeline-${index}`;
    group.className = `run-group${isActive ? " active" : ""}${isExpanded ? " expanded" : ""}`;
    item.type = "button";
    item.className = `run-item${isActive ? " active" : ""}`;
    item.dataset.runId = run.run_id;
    item.setAttribute("aria-current", isActive ? "true" : "false");
    if (!mobileLayout.matches) {
      item.setAttribute("aria-expanded", String(isExpanded));
      item.setAttribute("aria-controls", timelineId);
    }
    item.title = modelName;

    const top = document.createElement("div");
    top.className = "run-top";
    const model = document.createElement("span");
    model.className = "run-model";
    model.textContent = modelName;
    const actualModelName = run.actual_model && run.actual_model !== run.model
      ? run.actual_model
      : "";
    const actualModel = document.createElement("span");
    actualModel.className = "run-actual-model";
    actualModel.textContent = actualModelName ? `↳ ${actualModelName}` : "";
    const status = document.createElement("span");
    status.className = "run-status";
    status.dataset.status = state;
    status.textContent = statusLabel(run.status);
    const actions = document.createElement("span");
    actions.className = "run-actions";
    actions.append(status);
    if (!mobileLayout.matches) {
      const chevron = document.createElement("span");
      chevron.className = "run-chevron";
      chevron.append(createIcon("icon-chevron-down"));
      actions.append(chevron);
    }
    const modelBlock = document.createElement("span");
    modelBlock.className = "run-model-block";
    modelBlock.append(model);
    if (actualModelName) modelBlock.append(actualModel);
    top.append(modelBlock, actions);

    const bottom = document.createElement("div");
    bottom.className = "run-bottom";
    const time = document.createElement("span");
    time.textContent = formatTime(run.started_at, true);
    const stats = document.createElement("span");
    stats.textContent = `${run.event_count} 个事件 · ${duration(run)}`;
    bottom.append(time, stats);

    item.append(top, bottom);
    item.addEventListener("click", () => handleRunClick(run.run_id));
    group.append(item);

    if (isExpanded) {
      const timeline = document.createElement("div");
      timeline.id = timelineId;
      timeline.className = "run-timeline";
      timeline.setAttribute("aria-label", `${modelName} 的事件时间线`);
      renderEventList(timeline, selectedRun.events || []);
      group.append(timeline);
    }

    elements.runList.append(group);
  }
}

function eventSummary(event) {
  const payload = event.payload || {};
  if (event.event_type === "tool.start" || event.event_type === "tool.end") {
    return payload.name || "tool";
  }
  if (event.event_type === "llm.request" || event.event_type === "provider.request") {
    const chars = payload.request?.stats?.input_chars;
    const model = payload.provider?.model || "request snapshot";
    return `${model}${Number.isFinite(chars) ? ` · ${chars.toLocaleString()} 字符` : ""}`;
  }
  if (event.event_type === "llm.response" || event.event_type === "provider.response") {
    const chars = payload.stats?.output_chars;
    return `${payload.role || "response"}${Number.isFinite(chars) ? ` · ${chars.toLocaleString()} 字符` : ""}`;
  }
  if (event.event_type === "provider.fallback") {
    const from = payload.from_model || payload.from_provider || "previous provider";
    const to = payload.to_model || payload.to_provider || "fallback provider";
    return `${from} -> ${to}`;
  }
  const messageCount = Array.isArray(payload.messages) ? payload.messages.length : null;
  return messageCount === null ? "" : `${messageCount} 条消息`;
}

function renderExportSelectionControls() {
  const events = selectedRun?.events || [];
  const selectedCount = exportSelection.size;
  const hasEvents = events.length > 0;
  elements.exportSelectAll.checked = hasEvents && selectedCount === events.length;
  elements.exportSelectAll.indeterminate = selectedCount > 0 && selectedCount < events.length;
  elements.exportSelectAll.disabled = !hasEvents;
  elements.exportSelectedCount.textContent = `${selectedCount} / ${events.length}`;
  elements.exportAccept.disabled = exportPending || selectedCount === 0;
  if (!exportPending) {
    elements.exportAccept.textContent = `复制 ${selectedCount} 项内容`;
  }
}

function renderExportEventList() {
  const events = selectedRun?.events || [];
  elements.exportEventList.replaceChildren();

  for (const [index, event] of events.entries()) {
    const option = document.createElement("label");
    option.className = "export-event-option";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = exportSelection.has(index);
    checkbox.setAttribute("aria-label", `${event.event_type} ${formatTime(event.timestamp, true)}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        exportSelection.add(index);
      } else {
        exportSelection.delete(index);
      }
      option.classList.toggle("selected", checkbox.checked);
      renderExportSelectionControls();
    });

    const detail = document.createElement("span");
    detail.className = "export-event-detail";
    const title = document.createElement("strong");
    title.textContent = event.event_type;
    const summary = document.createElement("span");
    summary.textContent = eventSummary(event) || "-";
    summary.title = summary.textContent;
    detail.append(title, summary);

    const time = document.createElement("time");
    time.className = "export-event-time";
    time.textContent = formatTime(event.timestamp, true);
    time.dateTime = new Date(event.timestamp * 1000).toISOString();

    option.classList.toggle("selected", checkbox.checked);
    option.append(checkbox, detail, time);
    elements.exportEventList.append(option);
  }

  renderExportSelectionControls();
}

function buildTraceExport() {
  if (!selectedRun) return "";
  const modelName = selectedRun.model || selectedRun.provider_id || "LLM Trace";
  const events = (selectedRun.events || []).filter((_, index) => exportSelection.has(index));
  const lines = [
    "# LLM Trace",
    `模型: ${modelName}`,
    `平台: ${selectedRun.platform || "unknown"}`,
    `状态: ${statusLabel(selectedRun.status)}`,
    `开始时间: ${formatTime(selectedRun.started_at, true)}`,
    `运行时长: ${duration(selectedRun)}`,
    `Trace ID: ${selectedRun.run_id}`,
  ];

  for (const event of events) {
    lines.push(
      "",
      `## ${event.event_type} · ${formatTime(event.timestamp, true)}`,
      `摘要: ${eventSummary(event) || "-"}`,
      "```json",
      JSON.stringify(event.payload ?? {}, null, 2),
      "```",
    );
  }
  return lines.join("\n");
}

function renderCharacterStats() {
  elements.charStats.replaceChildren();
  const requestEvent = selectedRun?.events?.find(
    (event) => event.event_type === "llm.request" || event.event_type === "provider.request",
  );
  const responseEvent = selectedRun?.events?.find(
    (event) => event.event_type === "llm.response" || event.event_type === "provider.response",
  );
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
    const cell = document.createElement("div");
    cell.className = "char-stat";
    const name = document.createElement("span");
    name.textContent = label;
    const number = document.createElement("strong");
    number.textContent = Number.isFinite(value) ? value.toLocaleString() : "-";
    number.title = number.textContent;
    cell.append(name, number);
    elements.charStats.append(cell);
  }
}

function createEventButton(event) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "event-item";
  const isActive = selectedEvent?.sequence === event.sequence
    && selectedEvent?.event_type === event.event_type;
  button.classList.toggle("active", isActive);
  button.setAttribute("aria-pressed", String(isActive));

  const type = document.createElement("span");
  type.className = "event-type";
  type.textContent = event.event_type;
  const summary = document.createElement("span");
  summary.className = "event-summary";
  summary.textContent = eventSummary(event);
  summary.title = summary.textContent;
  const time = document.createElement("span");
  time.className = "event-time";
  time.textContent = formatTime(event.timestamp);
  button.append(type, summary, time);
  button.addEventListener("click", () => selectEvent(event, button));
  return button;
}

function renderEventList(container, events) {
  container.replaceChildren();
  if (!events.length) {
    const empty = document.createElement("div");
    empty.className = "timeline-empty";
    empty.textContent = "这条记录没有事件";
    container.append(empty);
    return;
  }
  for (const event of events) container.append(createEventButton(event));
}

function renderMobileTimeline() {
  const events = selectedRun?.events || [];
  elements.mobileEventCount.textContent = `${events.length} 个事件`;
  renderEventList(elements.mobileTimeline, events);
}

function renderPayload(event) {
  if (!event) {
    elements.payloadTitle.textContent = "选择时间线事件";
    elements.payloadEmpty.classList.remove("hidden");
    elements.payloadView.classList.add("hidden");
    elements.payloadView.textContent = "{}";
    payloadCopyText = "{}";
    elements.copyButton.disabled = true;
    return;
  }
  const compactPayload = JSON.stringify(event.payload ?? {});
  elements.payloadTitle.textContent = event.event_type;
  elements.payloadEmpty.classList.add("hidden");
  elements.payloadView.classList.remove("hidden");
  if (compactPayload.length > PAYLOAD_PREVIEW_MAX_CHARS) {
    payloadCopyText = compactPayload;
    elements.payloadView.textContent = `${compactPayload.slice(0, PAYLOAD_PREVIEW_MAX_CHARS)}\n\n[Payload 预览已截断，复制按钮仍会复制完整 JSON]`;
  } else {
    payloadCopyText = JSON.stringify(event.payload ?? {}, null, 2);
    elements.payloadView.textContent = payloadCopyText;
  }
  elements.copyButton.disabled = false;
}

function selectEvent(event, button) {
  selectedEvent = event;
  for (const item of document.querySelectorAll(".event-item")) {
    const isActive = item === button;
    item.classList.toggle("active", isActive);
    item.setAttribute("aria-pressed", String(isActive));
  }
  renderPayload(event);
  if (mobileLayout.matches) document.body.classList.add("mobile-detail");
}

function renderDetail() {
  if (!selectedRun) {
    elements.emptyState.classList.remove("hidden");
    elements.traceDetail.classList.add("hidden");
    return;
  }

  elements.emptyState.classList.add("hidden");
  elements.traceDetail.classList.remove("hidden");
  const modelName = selectedRun.model || selectedRun.provider_id || "LLM Trace";
  const state = normalizeStatus(selectedRun.status);
  elements.detailRunId.textContent = selectedRun.run_id;
  elements.detailRunId.title = selectedRun.run_id;
  elements.detailTitle.textContent = modelName;
  elements.detailTitle.title = modelName;
  elements.detailStatus.textContent = statusLabel(selectedRun.status);
  elements.detailStatus.dataset.status = state;
  elements.detailMeta.textContent = `${selectedRun.platform || "unknown"} · ${formatTime(selectedRun.started_at, true)} · ${duration(selectedRun)}`;
  elements.deleteMessage.textContent = `${modelName} 的请求快照、事件和响应数据都会被永久删除。`;
  elements.exportButton.disabled = !(selectedRun.events || []).length;
  renderCharacterStats();
  renderMobileTimeline();
  renderPayload(selectedEvent);
  renderRuns();
}

async function handleRunClick(runId) {
  if (mobileLayout.matches) {
    expandedRunId = runId;
    await loadRun(runId, { selectFirst: true, openMobile: true });
    return;
  }
  if (expandedRunId === runId) {
    expandedRunId = null;
    renderRuns();
    return;
  }
  expandedRunId = runId;
  if (selectedRun?.run_id === runId) {
    renderRuns();
    return;
  }
  await loadRun(runId);
}

function renderStatus(status) {
  elements.captureToggle.checked = status.enabled;
  elements.probeToggle.checked = Boolean(status.experimental_provider_probe);
  elements.probeToggle.disabled = !status.enabled;
  elements.probeLabel.textContent = status.provider_probe_active ? "探针中" : "探针";
  elements.queueSize.textContent = String(status.queue_size);
  elements.droppedRecords.textContent = `${status.dropped_records} / ${status.write_errors}`;
  elements.privacyMode.textContent = `${status.hash_identity ? "身份哈希" : "原始身份"} · ${status.capture_content ? "记录正文" : "仅元数据"} · ${status.provider_probe_active ? "实验探针" : "主链 Hook"}`;
}

async function loadStatus() {
  renderStatus(await bridge.apiGet("status"));
}

async function loadRuns() {
  elements.runList.setAttribute("aria-busy", "true");
  try {
    const result = await bridge.apiGet("runs", {
      limit: 100,
      query: elements.searchInput.value.trim(),
    });
    runs = result.items || [];
    renderRuns();
  } finally {
    elements.runList.setAttribute("aria-busy", "false");
  }
}

async function loadRun(runId, { selectFirst = false, openMobile = false } = {}) {
  const changedRun = selectedRun?.run_id !== runId;
  const priorSequence = changedRun ? null : selectedEvent?.sequence;
  const priorType = changedRun ? null : selectedEvent?.event_type;
  try {
    const run = await bridge.apiGet(`runs/${runId}`);
    selectedRun = run;
    if (selectFirst) {
      selectedEvent = run.events?.[0] || null;
    } else if (priorSequence !== null) {
      selectedEvent = (run.events || []).find(
        (event) => event.sequence === priorSequence && event.event_type === priorType,
      ) || null;
    } else {
      selectedEvent = null;
    }
    renderDetail();
    if (openMobile) document.body.classList.add("mobile-detail");
  } catch (error) {
    showToast(error.message || "加载 Trace 失败");
  }
}

async function refresh() {
  if (elements.refreshButton.disabled) return;
  setButtonBusy(elements.refreshButton, true);
  try {
    await Promise.all([loadStatus(), loadRuns()]);
    if (selectedRun) await loadRun(selectedRun.run_id);
  } catch (error) {
    setConnection("连接失败", "error");
    showToast(error.message || "刷新失败");
  } finally {
    setButtonBusy(elements.refreshButton, false);
  }
}

function scheduleRefresh() {
  window.clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(async () => {
    try {
      await loadRuns();
      if (selectedRun) await loadRun(selectedRun.run_id);
    } catch (error) {
      setConnection("同步失败", "error");
      showToast(error.message || "同步 Trace 失败");
    }
  }, 250);
}

function clearStreamWatchdog() {
  window.clearTimeout(streamWatchdogTimer);
  streamWatchdogTimer = null;
}

function armStreamWatchdog(epoch) {
  streamLastActivity = Date.now();
  clearStreamWatchdog();
  streamWatchdogTimer = window.setTimeout(() => {
    scheduleStreamReconnect(epoch, { immediate: true });
  }, STREAM_HEARTBEAT_TIMEOUT_MS);
}

async function releaseStreamSubscription() {
  const activeSubscriptionId = subscriptionId;
  subscriptionId = null;
  if (activeSubscriptionId === null) return;
  try {
    await bridge.unsubscribeSSE(activeSubscriptionId);
  } catch (_) {
    // A dead parent-side stream may already have removed this subscription.
  }
}

function scheduleStreamReconnect(epoch, { immediate = false } = {}) {
  if (unloading || epoch !== streamEpoch) return;
  if (streamReconnectTimer !== null) {
    if (!immediate) return;
    window.clearTimeout(streamReconnectTimer);
    streamReconnectTimer = null;
  }
  streamEpoch += 1;
  clearStreamWatchdog();
  setConnection("正在重连", "pending");
  const delay = immediate
    ? 0
    : Math.min(
      STREAM_RECONNECT_BASE_MS * (2 ** streamReconnectAttempts),
      STREAM_RECONNECT_MAX_MS,
    );
  streamReconnectAttempts += 1;
  streamReconnectTimer = window.setTimeout(() => {
    streamReconnectTimer = null;
    ensureStreamConnected();
  }, delay);
}

async function connectStream() {
  if (unloading) return;
  const epoch = ++streamEpoch;
  await releaseStreamSubscription();
  try {
    const nextSubscriptionId = await bridge.subscribeSSE("stream", {
      onOpen() {
        if (epoch !== streamEpoch || unloading) return;
        streamReconnectAttempts = 0;
        setConnection("实时连接", "connected");
        armStreamWatchdog(epoch);
      },
      onMessage(event) {
        if (epoch !== streamEpoch || unloading) return;
        streamReconnectAttempts = 0;
        setConnection("实时连接", "connected");
        armStreamWatchdog(epoch);
        if (event.eventType !== "ready" && event.eventType !== "heartbeat") {
          scheduleRefresh();
        }
      },
      onError() {
        scheduleStreamReconnect(epoch);
      },
    });
    if (epoch !== streamEpoch || unloading) {
      try {
        await bridge.unsubscribeSSE(nextSubscriptionId);
      } catch (_) {
        // The stale stream may already be closed by the bridge.
      }
      return;
    }
    subscriptionId = nextSubscriptionId;
    streamReconnectAttempts = 0;
    setConnection("实时连接", "connected");
    armStreamWatchdog(epoch);
  } catch (_) {
    scheduleStreamReconnect(epoch);
  }
}

function ensureStreamConnected() {
  if (unloading || streamConnectPromise !== null) return streamConnectPromise;
  streamConnectPromise = connectStream().finally(() => {
    streamConnectPromise = null;
  });
  return streamConnectPromise;
}

function openDeleteConfirmation() {
  if (!selectedRun || deletePending) return;
  setConfirmationError(elements.deleteError);
  elements.deletePopover.classList.remove("hidden");
  elements.deleteButton.setAttribute("aria-expanded", "true");
  window.setTimeout(() => elements.deleteCancel.focus(), 0);
}

function openExportDialog() {
  const events = selectedRun?.events || [];
  if (!events.length || exportPending) return;
  exportPreviousFocus = document.activeElement;
  exportSelection = new Set(events.map((_, index) => index));
  elements.exportDescription.textContent = `${selectedRun.model || selectedRun.provider_id || "LLM Trace"} · ${events.length} 个时间线事件`;
  setConfirmationError(elements.exportError);
  renderExportEventList();
  elements.exportOverlay.classList.remove("hidden");
  window.setTimeout(() => elements.exportSelectAll.focus(), 0);
}

function closeExportDialog({ restoreFocus = true } = {}) {
  if (exportPending) return;
  elements.exportOverlay.classList.add("hidden");
  setConfirmationError(elements.exportError);
  if (restoreFocus && exportPreviousFocus instanceof HTMLElement) {
    exportPreviousFocus.focus();
  }
  exportPreviousFocus = null;
}

function closeDeleteConfirmation({ restoreFocus = true } = {}) {
  if (deletePending) return;
  elements.deletePopover.classList.add("hidden");
  elements.deleteButton.setAttribute("aria-expanded", "false");
  setConfirmationError(elements.deleteError);
  if (restoreFocus) elements.deleteButton.focus();
}

function openClearDialog() {
  clearPreviousFocus = document.activeElement;
  setConfirmationError(elements.clearError);
  elements.clearOverlay.classList.remove("hidden");
  window.setTimeout(() => elements.clearCancel.focus(), 0);
}

function closeClearDialog({ restoreFocus = true } = {}) {
  if (clearPending) return;
  elements.clearOverlay.classList.add("hidden");
  setConfirmationError(elements.clearError);
  if (restoreFocus && clearPreviousFocus instanceof HTMLElement) {
    clearPreviousFocus.focus();
  }
  clearPreviousFocus = null;
}

function trapDialogFocus(event) {
  if (event.key !== "Tab") return;
  const dialog = !elements.exportOverlay.classList.contains("hidden")
    ? elements.exportDialog
    : !elements.clearOverlay.classList.contains("hidden")
      ? elements.clearDialog
      : null;
  if (!dialog) return;
  const focusable = [...dialog.querySelectorAll("button:not(:disabled), input:not(:disabled)")];
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

async function initialize() {
  if (!bridge) throw new Error("AstrBot Plugin Page bridge is unavailable");
  const context = await bridge.ready();
  applyTheme(context);
  bridge.onContext(applyTheme);
  await refresh();
  await ensureStreamConnected();
}

elements.captureToggle.addEventListener("change", async () => {
  const requested = elements.captureToggle.checked;
  elements.captureToggle.disabled = true;
  try {
    const status = await bridge.apiPost("status", { enabled: requested });
    renderStatus(status);
    showToast(status.enabled ? "LLM Trace 已开启" : "LLM Trace 已关闭");
  } catch (error) {
    elements.captureToggle.checked = !requested;
    showToast(error.message || "状态更新失败");
  } finally {
    elements.captureToggle.disabled = false;
  }
});

elements.probeToggle.addEventListener("change", async () => {
  const requested = elements.probeToggle.checked;
  elements.probeToggle.disabled = true;
  try {
    const status = await bridge.apiPost("status", { experimental_provider_probe: requested });
    renderStatus(status);
    showToast(status.provider_probe_active ? "实验 Provider 探针已开启" : "实验 Provider 探针已关闭");
  } catch (error) {
    elements.probeToggle.checked = !requested;
    showToast(error.message || "实验探针更新失败");
  } finally {
    elements.probeToggle.disabled = !elements.captureToggle.checked;
  }
});

elements.refreshButton.addEventListener("click", refresh);
elements.searchInput.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    loadRuns().catch((error) => showToast(error.message || "搜索失败"));
  }, 250);
});
elements.backButton.addEventListener("click", showMobileList);
mobileLayout.addEventListener("change", () => {
  if (!mobileLayout.matches) document.body.classList.remove("mobile-detail");
  renderRuns();
});
window.addEventListener("online", () => {
  scheduleStreamReconnect(streamEpoch, { immediate: true });
});
document.addEventListener("visibilitychange", () => {
  if (
    document.visibilityState === "visible"
    && Date.now() - streamLastActivity > STREAM_HEARTBEAT_TIMEOUT_MS
  ) {
    scheduleStreamReconnect(streamEpoch, { immediate: true });
  }
});

elements.exportButton.addEventListener("click", openExportDialog);
elements.exportSelectAll.addEventListener("change", () => {
  const events = selectedRun?.events || [];
  exportSelection = elements.exportSelectAll.checked
    ? new Set(events.map((_, index) => index))
    : new Set();
  renderExportEventList();
});
elements.exportCancel.addEventListener("click", () => closeExportDialog());
elements.exportOverlay.addEventListener("pointerdown", (event) => {
  if (event.target === elements.exportOverlay && !exportPending) closeExportDialog();
});
elements.exportAccept.addEventListener("click", async () => {
  if (exportPending || !exportSelection.size) return;
  exportPending = true;
  elements.exportCancel.disabled = true;
  elements.exportAccept.disabled = true;
  elements.exportAccept.setAttribute("aria-busy", "true");
  elements.exportAccept.textContent = "正在复制";
  setConfirmationError(elements.exportError);
  try {
    const copied = await copyTextToClipboard(buildTraceExport());
    if (!copied) {
      setConfirmationError(elements.exportError, "浏览器禁止自动复制，请允许剪贴板权限后重试。");
      return;
    }
    exportPending = false;
    closeExportDialog();
    showToast("所选 Trace 内容已复制");
  } catch (error) {
    setConfirmationError(elements.exportError, error.message || "复制失败，请重试。");
  } finally {
    exportPending = false;
    elements.exportCancel.disabled = false;
    elements.exportAccept.setAttribute("aria-busy", "false");
    renderExportSelectionControls();
  }
});

elements.deleteButton.addEventListener("click", () => {
  if (elements.deletePopover.classList.contains("hidden")) {
    openDeleteConfirmation();
  } else {
    closeDeleteConfirmation();
  }
});
elements.deleteCancel.addEventListener("click", () => closeDeleteConfirmation());
elements.deleteAccept.addEventListener("click", async () => {
  if (!selectedRun || deletePending) return;
  deletePending = true;
  const runId = selectedRun.run_id;
  elements.deleteCancel.disabled = true;
  elements.deleteAccept.disabled = true;
  elements.deleteAccept.setAttribute("aria-busy", "true");
  elements.deleteAccept.textContent = "正在删除";
  setConfirmationError(elements.deleteError);
  try {
    await bridge.apiPost(`runs/${runId}/delete`, {});
    selectedRun = null;
    selectedEvent = null;
    expandedRunId = null;
    elements.deletePopover.classList.add("hidden");
    elements.deleteButton.setAttribute("aria-expanded", "false");
    document.body.classList.remove("mobile-detail");
    renderDetail();
    await loadRuns();
    showToast("Trace 已删除");
  } catch (error) {
    setConfirmationError(elements.deleteError, error.message || "删除失败，请重试");
  } finally {
    deletePending = false;
    elements.deleteCancel.disabled = false;
    elements.deleteAccept.disabled = false;
    elements.deleteAccept.setAttribute("aria-busy", "false");
    elements.deleteAccept.textContent = "删除 Trace";
  }
});

document.addEventListener("pointerdown", (event) => {
  const isOpen = !elements.deletePopover.classList.contains("hidden");
  if (
    isOpen
    && !deletePending
    && !elements.deletePopover.contains(event.target)
    && !elements.deleteButton.contains(event.target)
  ) {
    closeDeleteConfirmation({ restoreFocus: false });
  }
});

elements.clearButton.addEventListener("click", openClearDialog);
elements.clearCancel.addEventListener("click", () => closeClearDialog());
elements.clearOverlay.addEventListener("pointerdown", (event) => {
  if (event.target === elements.clearOverlay && !clearPending) closeClearDialog();
});
elements.clearAccept.addEventListener("click", async () => {
  if (clearPending) return;
  clearPending = true;
  elements.clearCancel.disabled = true;
  elements.clearAccept.disabled = true;
  elements.clearAccept.setAttribute("aria-busy", "true");
  elements.clearAccept.textContent = "正在清空";
  setConfirmationError(elements.clearError);
  try {
    await bridge.apiPost("clear", {});
    selectedRun = null;
    selectedEvent = null;
    expandedRunId = null;
    document.body.classList.remove("mobile-detail");
    renderDetail();
    await loadRuns();
    clearPending = false;
    closeClearDialog();
    showToast("全部 Trace 已清空");
  } catch (error) {
    setConfirmationError(elements.clearError, error.message || "清空失败，请重试");
  } finally {
    clearPending = false;
    elements.clearCancel.disabled = false;
    elements.clearAccept.disabled = false;
    elements.clearAccept.setAttribute("aria-busy", "false");
    elements.clearAccept.textContent = "清空全部";
  }
});

window.addEventListener("keydown", (event) => {
  trapDialogFocus(event);
  if (event.key !== "Escape") return;
  if (!elements.exportOverlay.classList.contains("hidden") && !exportPending) {
    closeExportDialog();
    return;
  }
  if (!elements.clearOverlay.classList.contains("hidden") && !clearPending) {
    closeClearDialog();
    return;
  }
  if (!elements.deletePopover.classList.contains("hidden") && !deletePending) {
    closeDeleteConfirmation();
  }
});

elements.copyButton.addEventListener("click", async () => {
  if (await copyTextToClipboard(payloadCopyText)) {
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
  unloading = true;
  streamEpoch += 1;
  window.clearTimeout(streamReconnectTimer);
  clearStreamWatchdog();
  if (subscriptionId !== null) bridge.unsubscribeSSE(subscriptionId);
});

initialize().catch((error) => {
  setConnection("初始化失败", "error");
  elements.runList.setAttribute("aria-busy", "false");
  showToast(error.message || "页面初始化失败");
});
