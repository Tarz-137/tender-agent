const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function dashboard() {
  const elements = new Map();
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      value: '', valueAsNumber: NaN, validity: {valid: true}, hidden: true,
      listeners: {}, addEventListener(event, callback) {this.listeners[event] = callback;},
    });
    return elements.get(selector);
  };
  const context = vm.createContext({
    document: {querySelector: element, querySelectorAll: () => [], addEventListener() {}},
    URL, URLSearchParams, location: {origin: 'http://localhost'},
    fetch: () => new Promise(() => {}), setInterval() {},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../discovery_mvp/web/app.js'), 'utf8'), context);
  const records = [
    ['small', 100000, .99], ['large', 5000000, .2],
    ['missing', null, 1], ['conflicting', null, .9], ['middle', 1000000, .8],
  ].map(([id, tender_value_inr, score]) => ({id, title: id, tender_value_inr, score,
    url: `https://example.com/${id}`, providers: ['codex'], snippets: [], groups: [],
    status: 'shortlist', reason: '', evidence: '', queries: [], evidence_kind: []}));
  vm.runInContext(`state.data = ${JSON.stringify({report: {candidates: records}, feedback: {}})}`, context);
  element('#provider-filter').value = 'all';
  const render = () => {
    vm.runInContext('renderCandidates()', context);
    return [...element('#candidate-list').innerHTML.matchAll(/class="candidate-title" data-detail="([^"]+)"/g)].map(match => match[1]);
  };
  return {element, render};
}

test('price sorts ignore capability score and keep unknown prices last in either direction', () => {
  const ui = dashboard();
  ui.element('#sort').value = 'price-desc';
  assert.deepEqual(ui.render(), ['large', 'middle', 'small', 'conflicting', 'missing']);
  ui.element('#sort').value = 'price-asc';
  assert.deepEqual(ui.render(), ['small', 'middle', 'large', 'conflicting', 'missing']);
});

test('price sorting combines with keyword filtering', () => {
  const ui = dashboard();
  ui.element('#sort').value = 'price-desc';
  ui.element('#search').value = 'middle';
  assert.deepEqual(ui.render(), ['middle']);
});
