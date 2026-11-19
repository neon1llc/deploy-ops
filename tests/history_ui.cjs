// tests/history_ui.cjs - Exercise shipped filter/page response ownership without a native browser claim.


const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const document = {body: {}, activeElement: null};
class Node {
  // Retain native values and observe meaningful status writes
  constructor() { this.value = ''; this.disabled = false; this.options = [{value: ''}]; this.writes = 0; }
  // Replace server-owned outcome choices while keeping typed filter state separate
  replaceChildren(...options) { this.options = options; }
  // Count live-region text updates rather than inferring assistive-technology acceptance
  set textContent(value) { this.text = value; this.writes++; }
  get textContent() { return this.text; }
  // Model the browser blur reproduced when a focused native pager is disabled
  set disabled(value) { this.inactive = value; if (value && document.activeElement === this) document.activeElement = document.body; }
  get disabled() { return this.inactive; }
  // Track deliberate focus return without moving a user from another control
  focus() { document.activeElement = this; }
}
const nodes = new Map(), element = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
element('history-limit').value = '20'; element('config').value = 'retained unrelated draft';
const requests = [], runs = [], rendered = [];
const context = vm.createContext({element, document, token: 'session-A', URLSearchParams, JSON, Object,
  Option: class {constructor(text, value) {this.textContent = text; this.value = value;}},
  safe: work => {const result = work(); runs.push(result); return result;},
  releaseRows: records => rendered.push(records.map(row => row.id)),
  api: url => new Promise((resolve, reject) => requests.push({url, resolve, reject}))});
vm.runInContext(fs.readFileSync('src/deploy_ops/ui/history.js', 'utf8'), context);
const page = (id, more = false) => ({releases: [{id}], total: 1, has_more: more,
  cursor: id + '-root', next_cursor: more ? id + '-older' : null, outcomes: ['failed', 'succeeded']});
const apply = query => {element('history-query').value = query; element('history-filters').onsubmit({preventDefault() {}}); return runs.at(-1);};
(async () => {
  const old = vm.runInContext('historyRefresh(true)', context);
  const current = apply('canary'); requests[1].resolve(page('canary', true)); await current;
  requests[0].reject(new Error('late old query failure')); await old;
  assert.deepEqual(rendered, [['canary']]); assert.doesNotMatch(element('history-status').textContent, /late old/);

  // A late older-page success cannot replace the results of a new filter/extent.
  element('history-older').onclick(); const older = runs.at(-1);
  assert.match(requests[2].url, /cursor=canary-older/);
  const next = apply('newer'); requests[3].resolve(page('newer')); await next;
  requests[2].resolve(page('late-older')); await older;
  assert.deepEqual(rendered, [['canary'], ['newer']]);
  assert.equal(element('history-newer').disabled, true); assert.equal(element('history-older').disabled, true);

  // Polling leaves unsubmitted input and unrelated drafts alone and does not repeat a live announcement.
  element('history-query').value = 'unfinished next search'; const writes = element('history-status').writes;
  const poll = vm.runInContext('historyRefresh()', context); requests[4].resolve(page('newer')); await poll;
  assert.equal(element('history-query').value, 'unfinished next search');
  assert.equal(element('config').value, 'retained unrelated draft');
  assert.equal(element('history-status').writes, writes); assert.deepEqual(rendered, [['canary'], ['newer']]);
  // A settled matching poll restores native pager focus; movement to a field takes precedence.
  element('history-older').disabled = false; element('history-older').focus();
  const focused = vm.runInContext('historyRefresh()', context); assert.equal(document.activeElement, document.body);
  requests[5].resolve(page('focused', true)); await focused;
  assert.equal(document.activeElement, element('history-older'));
  const moved = vm.runInContext('historyRefresh()', context); element('history-query').focus();
  requests[6].resolve(page('focused', true)); await moved;
  assert.equal(document.activeElement, element('history-query'));
  console.log('Shipped history query/page race fences, retained drafts and quiet polling passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
