const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function dashboard() {
  const elements = new Map();
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      value: '', validity: {valid: true}, listeners: {}, close() {},
      addEventListener(event, callback) {this.listeners[event] = callback;},
    });
    return elements.get(selector);
  };
  const requests = [];
  const context = vm.createContext({
    document: {querySelector: element, querySelectorAll: () => [], addEventListener() {}},
    URL, URLSearchParams, setInterval() {},
    fetch: url => {requests.push(url); return new Promise(() => {});},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../discovery_mvp/web/app.js'), 'utf8'), context);
  return {element, requests, context};
}

test('calendar selection queries the full library and clearing removes the date filter', () => {
  const ui = dashboard();
  vm.runInContext("state.scope = 'run'; state.selectedRun = '20261101-100000-aaaaaa'", ui.context);
  ui.element('#discovery-date').value = '2026-11-02';
  ui.element('#discovery-date').listeners.change();
  let query = new URL(ui.requests.at(-1), 'http://localhost').searchParams;
  assert.equal(query.get('discovery_date'), '2026-11-02');
  assert.equal(query.get('scope'), 'library');
  assert.equal(query.has('run'), false);
  ui.element('#clear-discovery-date').listeners.click();
  query = new URL(ui.requests.at(-1), 'http://localhost').searchParams;
  assert.equal(query.has('discovery_date'), false);
  assert.equal(ui.element('#discovery-date').value, '');
});

test('empty selected day explains how to return to all dates', () => {
  const ui = dashboard();
  vm.runInContext("state.discoveryDate = '2026-11-02'; state.data = {report:{candidates:[]},feedback:{}}; renderCandidates()", ui.context);
  assert.match(ui.element('#candidate-list').innerHTML, /No tenders discovered on 2026-11-02/);
  assert.match(ui.element('#candidate-list').innerHTML, /All dates/);
});
