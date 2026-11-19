// tests/local_settings_ui.cjs - Check retained field bases against the shipped draft handlers.


const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');


// Model only the native elements used by local field reconciliation
class Node {
  // Retain each field's identity and authored value
  constructor(tag) { this.tagName = tag; this.children = []; this.value = ''; this.dataset = {}; }
  // Attach rendered controls in their original order
  append(...nodes) { this.children.push(...nodes); }
  // Replace a refreshed local panel
  replaceChildren(...nodes) { this.children = nodes; }
  // Build fields do not contain environment pin buttons
  querySelectorAll() { return []; }
  // Observe deliberate keyboard focus return
  focus() { document.activeElement = this; }
}


// Resolve dynamically rendered fields as the browser would resolve stable IDs
const nodes = new Map();
const descendants = node => [node, ...node.children.flatMap(descendants)];
const element = id => nodes.get(id) || [...nodes.values()].flatMap(descendants).find(node => node.id === id);
for (const id of ['local-component', 'local-scope', 'local-environment', 'local-fields', 'local-apply',
  'local-environment-label', 'local-edit-fields', 'local-reload', 'local-edit-form', 'local-status',
  'config', 'config-diff', 'local-settings']) nodes.set(id, new Node('div'));
element('local-component').value = 'web'; element('local-scope').value = 'build';
element('local-environment').value = 'development'; element('config').value = 'original source';
const listeners = new Map();
const document = {createElement: tag => new Node(tag), addEventListener: (name, work) => listeners.set(name, work),
  dispatchEvent: event => listeners.get(event.type)?.(event)};
const contract = {source_hash: 'a'.repeat(64), local_edit_forms: {web: {fields: {}, base: {},
  build: {checks: [['python3', 'check.py']], command: ['python3', 'build.py']},
  environments: {development: {}}}}};
const calls = [];
const context = vm.createContext({document, element, formContract: contract, formSource: 'original source',
  structuredClone, JSON, Map, Set, Object, CustomEvent: class {constructor(type) {this.type = type;}},
  Option: class extends Node {constructor(label, value) {super('option'); this.textContent = label; this.value = value;}},
  text: (parent, tag, value) => {const node = new Node(tag); node.textContent = value; parent.append(node); return node;},
  api: async (...args) => {calls.push(args); return context.result;}});
vm.runInContext(fs.readFileSync('src/deploy_ops/ui/local-settings.js', 'utf8'), context);


// Exercise actual input handlers, not a second implementation of the merge
(async () => {
  vm.runInContext('localRender()', context);
  const arguments_ = descendants(element('local-field-command')).filter(node => node.tagName === 'input');
  arguments_[1].value = 'my-build.py'; arguments_[1].oninput();
  contract.local_edit_forms.web.build = {checks: [['python3', 'new-check.py']], command: ['python3', 'new-build.py']};
  contract.source_hash = 'b'.repeat(64); context.formSource = 'newer source'; element('config').value = 'newer source';
  vm.runInContext('localChoices()', context);
  assert.equal(vm.runInContext('localBases.get(localKey()).command.source_hash', context), 'a'.repeat(64));
  assert.equal(vm.runInContext('localBases.get(localKey()).checks.source_hash', context), 'b'.repeat(64));
  assert.equal(vm.runInContext('localDrafts.get(localKey()).checks[0][1]', context), 'new-check.py');
  assert.equal(vm.runInContext('localDrafts.get(localKey()).command[1]', context), 'my-build.py');
  await vm.runInContext('localApply()', context);
  assert.equal(calls.length, 0);
  assert.match(element('local-status').textContent, /Resolve the changed field/);

  // Resolve the exact conflict and preserve the untouched ordered check
  descendants(element('local-fields')).find(node => node.textContent === 'Keep my command').onclick();
  assert.equal(document.activeElement.id, 'local-field-command');
  const result = structuredClone(contract);
  result.toml = 'applied source'; result.diff = 'reviewed difference'; result.source_hash = 'c'.repeat(64);
  result.local_edit_forms.web.build.command = ['python3', 'my-build.py']; context.result = result;
  await vm.runInContext('localApply()', context);
  assert.equal(calls.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0][2].values)),
    {checks: [['python3', 'new-check.py']], command: ['python3', 'my-build.py']});
  assert.equal(vm.runInContext('localBases.get(localKey()).command.source_hash', context), 'c'.repeat(64));
  assert.equal(element('config').value, 'applied source');
  assert.equal(document.activeElement, element('local-apply'));
  console.log('Retained field conflict, exact base, ordered rebase and explicit resolution passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
