const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

function element() {
  return { textContent: '', value: '', dataset: {}, children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute() {}, addEventListener() {},
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; } };
}
const nodes = new Map();
const context = vm.createContext({ console, Map, Set,
  document: { getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  }, createElement: element, createElementNS: element, querySelectorAll: () => [],
  body: element(), addEventListener() {} },
  window: { AstrBotPluginPage: {}, matchMedia: () => ({ matches: false }),
    setTimeout() {}, clearTimeout() {} },
});
let source = readFileSync(require.resolve('../pages/inspector/app.js'), 'utf8');
source = source.slice(0, source.indexOf('elements.captureToggle.addEventListener'));
vm.runInContext(source, context);
context.assert = assert;
vm.runInContext(`(async () => {
  const summary = {run_id: 'a', model: 'primary', actual_model: 'backup',
    status: 'completed', started_at: 1, finished_at: 2, event_count: 1};
  const event = {sequence: 1, timestamp: 1, event_type: 'llm.request',
    payload_loaded: false, payload: {request: {stats: {input_chars: 100}}}};
  let calls = 0;
  bridge.apiGet = async (path, params) => {
    calls++;
    if (params?.event_sequence) return {...event, payload_loaded: true,
      payload: {request: {contexts: ['original context'], prompt: 'original prompt'}}};
    return {...summary, run_id: path.split('/')[1], events: [{...event}]};
  };
  runs = [summary, {...summary, run_id: 'b'}];
  await handleRunClick('a');
  assert.equal(expandedRunId, 'a');
  assert.equal(elements.detailStatus.textContent, '已完成');
  assert.equal(elements.detailActualModel.textContent, '⤷ backup');
  assert.equal(calls, 1);
  await selectEvent(selectedRun.events[0], null);
  assert.match(elements.payloadView.textContent, /original context/);
  assert.equal(calls, 2);
  // Reuse the original button's summary reference after loading its payload.
  await selectEvent(event, null);
  assert.equal(calls, 2);
  assert.match(elements.payloadView.textContent, /original context/);
  await loadRun('a');
  assert.match(elements.payloadView.textContent, /original context/);
  assert.equal(calls, 3);
  const loading = handleRunClick('b');
  assert.equal(selectedEvent, null);
  assert.equal(elements.payloadView.textContent, '{}');
  await loading;
  assert.equal(selectedEvent, null);
  exportSelection = new Set([0]);
  await loadExportPayloads();
  assert.match(buildTraceExport(), /original context/);
  await handleRunClick('a');
  const beforeReload = calls;
  await selectEvent(selectedRun.events[0], null);
  assert.equal(calls, beforeReload + 1);
  await selectEvent(event, null);
  assert.equal(calls, beforeReload + 1);
})()`, context).then(() => console.log('WebUI interaction tests passed'))
  .catch(error => { console.error(error); process.exitCode = 1; });
