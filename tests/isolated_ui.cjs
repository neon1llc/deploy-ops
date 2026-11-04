// tests/isolated_ui.cjs - Check review content and operator state without cloud or browser dependencies.


// Load the real console script against a minimal DOM contract
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let document;


// Model the DOM behavior needed by the actual render and action handlers
class Node {


  // Keep native element state and child ownership
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.disabled = false; this.scrollTop = 0; this.scrollLeft = 0; }


  // Append children in visible and keyboard order
  append(...nodes) { for (const node of nodes) { node.parentElement = this; this.children.push(node); } }


  // Find focus inside a replaced subtree
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }


  // Release focus when a rendered subtree is replaced
  replaceChildren(...nodes) {
    if (this.children.some(child => child.contains(document.activeElement))) document.activeElement = document.body;
    this.children = []; this.append(...nodes);
  }


  // Support only selectors exercised by the production review
  querySelectorAll(selector) {
    const values = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
    if (selector === '*') return values;
    return values.filter(node => selector === '[data-isolated-key]' ? node.dataset.isolatedKey
      : selector === 'details[open]' ? node.tagName === 'DETAILS' && node.open
      : selector === '[data-isolated-scroll]' ? node.dataset.isolatedScroll
      : selector === 'details[data-isolated-evidence] pre' ? node.tagName === 'PRE' && node.parentElement.dataset.isolatedEvidence
      : false);
  }


  // Preserve attributes used by semantic table regions
  setAttribute(name, value) { this[name] = value; }


  // Restore the same operator control
  focus() { document.activeElement = this; }


  // Restore in-progress textarea selection
  setSelectionRange(start, end, direction) { this.selectionStart = start; this.selectionEnd = end; this.selectionDirection = direction; }


  // Reproduce the existing receipt form change event
  dispatchEvent(event) { if (this.onchange && event.type === 'change') this.onchange(event); }
}


// Initialize the existing isolated panel and application prefill controls
const nodes = new Map();
const element = id => nodes.get(id);
const navigation = [];
document = {body: new Node('body'), createElement: tag => new Node(tag), dispatchEvent: event => navigation.push(event)};
document.activeElement = document.body;
for (const id of ['isolated-panel', 'isolated-availability', 'isolated-prepare', 'isolated-recipe', 'isolated-requests', 'isolated-refresh', 'isolated-prepare-form', 'environment', 'component', 'receipts', 'only', 'image']) {
  const node = new Node(id === 'isolated-panel' ? 'details' : 'div'); node.id = id; nodes.set(id, node);
  (id === 'isolated-panel' ? document.body : element('isolated-panel')).append(node);
}


// Capture only explicit native action requests
const messages = [], calls = [];
let environmentChanged = false;
element('environment').onchange = () => { environmentChanged = true; };
const fixture = {available: true, name: 'fixture', engine: 'terraform', environment: 'development', expires_at: 1900000000,
  recipe: {project: 'fixture-project'}, identities: {planner: 'reader@example.invalid'}, tools: {}, packet_digest: 'a'.repeat(64),
  active: [], application: {component: 'web', dependency: 'foundation'}, requests: [
    {id: 'b'.repeat(32), digest: 'c'.repeat(64), status: 'uncertain', reason: 'Inspect fixture', manifest: {engine: 'terraform', preview: {changes: [{address: 'google_cloud_run_v2_service.web', resource_type: 'google_cloud_run_v2_service', action: 'replace'}], manual_only: true, findings: ['Review <script>fixture</script> finding']} },
      output_receipt: {id: 'd'.repeat(32)}, receipt_status: {valid: true, reason: 'Current fixture'}}]};


// Evaluate the shipped script without running its background timer
const context = vm.createContext({document, element, fixture, token: 'fixture-token', console, CustomEvent: class {constructor(type, options) {this.type = type; this.detail = options.detail;}}, Event: class {constructor(type) {this.type = type;}},
  text: (parent, tag, value) => { const node = new Node(tag); node.textContent = value; parent.append(node); return node; },
  message: (...args) => messages.push(args), api: async (...args) => {calls.push(args); return fixture;},
  safe: async work => work(), setInterval: () => 0});
vm.runInContext(fs.readFileSync('src/deploy_ops/ui/isolated.js', 'utf8'), context);
vm.runInContext('isolatedState = fixture; isolatedRender();', context);


// Resolve stable controls rather than depending on presentation order
const find = key => element('isolated-panel').querySelectorAll('[data-isolated-key]').find(node => node.dataset.isolatedKey === key);


// Keep readable actions and literal findings outside collapsed raw evidence
const all = element('isolated-requests').querySelectorAll('*');
assert(all.some(node => node.tagName === 'TH' && node.textContent === 'Resource address' && node.scope === 'col'));
assert(all.some(node => node.tagName === 'TH' && node.textContent === 'google_cloud_run_v2_service.web' && node.scope === 'row'));
assert(all.some(node => node.textContent === 'replace' && node.className === 'isolated-warning'));
assert(all.some(node => node.tagName === 'LI' && node.textContent === 'Review <script>fixture</script> finding'));
assert(all.filter(node => node.tagName === 'DETAILS').every(node => !node.open));
assert(all.some(node => node.textContent === 'Manual-only review: the controller requires human inspection of this plan.'));

// Keep disclosure focus and each independent scrolling region stable
find('recipe:evidence').focus();
vm.runInContext('isolatedRender(true)', context);
assert.equal(document.activeElement.dataset.isolatedKey, 'recipe:evidence');
const note = find('b'.repeat(32) + ':note');
note.value = 'Inspected fixture and confirmed remaining state'; note.oninput(); note.focus(); note.setSelectionRange(3, 12, 'backward'); note.scrollTop = 24;
const table = find('b'.repeat(32) + ':table'); table.scrollTop = 45; table.scrollLeft = 60;
const pre = element('isolated-panel').querySelectorAll('details[data-isolated-evidence] pre')[0]; pre.scrollTop = 80; pre.scrollLeft = 9;
vm.runInContext('isolatedRender(true)', context);
assert.equal(document.activeElement.value, note.value);
assert.deepEqual([document.activeElement.selectionStart, document.activeElement.selectionEnd, document.activeElement.selectionDirection, document.activeElement.scrollTop], [3, 12, 'backward', 24]);
const renewed = element('isolated-panel').querySelectorAll('details[data-isolated-evidence] pre')[0];
assert.deepEqual([renewed.scrollTop, renewed.scrollLeft], [80, 9]);
assert.deepEqual([find('b'.repeat(32) + ':table').scrollTop, find('b'.repeat(32) + ':table').scrollLeft], [45, 60]);

// Receipt selection only prefills a draft and restores image focus
find('b'.repeat(32) + ':receipt').onclick();
assert(environmentChanged);
assert.equal(element('receipts').value, JSON.stringify({foundation: 'd'.repeat(32)}));
assert.equal(document.activeElement, element('image'));
assert.equal(navigation[0].type, 'deployops:navigate');
assert.equal(navigation[0].detail.view, 'releases');
assert.equal(navigation[0].detail.compose, true);
assert.equal(calls.length, 0);
context.fixture = structuredClone(fixture);
context.fixture.requests[0].status = 'failed';
context.api = async (...args) => {calls.push(args); return context.fixture;};


// Verify explicit approval binds the displayed digest and never automatically executes
(async () => {
  await vm.runInContext('isolatedRefresh()', context);
  assert(messages.some(item => item[0].includes('is failed')));
  const count = messages.length;
  await vm.runInContext('isolatedRefresh()', context);
  assert.equal(messages.length, count);
  context.fixture = structuredClone(fixture);
  const pending = context.fixture.requests[0];
  pending.status = 'pending';
  pending.manifest.engine = 'manifest';
  pending.manifest.isolated_packet = {module: {resource: {google_cloud_run_v2_service: {web: {}}}}};
  await vm.runInContext('isolatedRefresh()', context);
  const planning = element('isolated-requests').querySelectorAll('*');
  assert(planning.some(node => node.textContent === 'Not planned yet'));
  assert(!planning.some(node => node.textContent === 'replace'));
  assert.equal(calls.filter(call => call[1] === 'POST').length, 0);
  const approve = find(pending.id + ':approve');
  const form = approve.parentElement;
  assert.equal(form.children.find(node => node.name === 'digest').value, pending.digest);
  form.onsubmit({preventDefault() {}});
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls.filter(call => call[1] === 'POST').map(call => [call[0], call[2].digest]),
    [['/isolated/requests/' + pending.id + '/approve', pending.digest]]);
  console.log('PASS: disclosure focus, textarea selection/draft, evidence scroll, environment change on receipt prefill, no automatic mutation, nonrepeating outcome announcement');
})().catch(error => { console.error(error); process.exitCode = 1; });
