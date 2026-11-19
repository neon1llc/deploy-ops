// tests/backup_ui.cjs - Exercise the shipped backup handlers without claiming browser qualification.


const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let document;


// Retain only the native control and focus behavior used by the actual form handlers
class Node {
  // Keep authored values independent from refreshed inspection results
  constructor(tag) { this.tagName = tag; this.children = []; this.value = ''; this.textContent = ''; this.isConnected = true; }
  // Preserve native element ordering
  append(...nodes) { this.children.push(...nodes); }
  // Replace only the result region
  replaceChildren() { this.children = []; }
  // Find duplicate-submit controls inside the panel
  querySelectorAll(selector) { return this.children.flatMap(node => [node, ...node.querySelectorAll(selector)]).filter(node => selector !== 'button' || node.tagName === 'button'); }
  // Observe deliberate focus return
  focus() { document.activeElement = this; }
  // Fail if provider or path values ever become markup
  set innerHTML(value) { throw new Error('Unsafe HTML rendering'); }
}


// Supply the same shared view helpers while evaluating the real standalone client module
const nodes = new Map();
for (const id of ['backup-panel', 'backup-limits', 'backup-form', 'restore-form', 'backup-directory',
  'restore-backup', 'restore-digest', 'restore-workspace', 'restore-state', 'backup-options', 'backup-status', 'backup-detail']) nodes.set(id, new Node('div'));
const controls = [new Node('button'), new Node('button'), new Node('button')];
nodes.get('backup-panel').append(...controls); nodes.get('backup-panel').open = true;
document = {body: new Node('body')}; document.activeElement = controls[0];
const calls = [];
const manifest = {id: 'a'.repeat(32), sha256: 'b'.repeat(64), toolkit_version: 'fixture', capture: 'complete',
  release_count: 1, files: [], audit: {events: 3, head: 'head'}, missing: [], unavailable: [], omitted: ['runtime homes']};
const verified = {verified: true, bundle: '/private/verified-backup', manifest};
const context = vm.createContext({document, token: 'fixture', Set, Math, Object,
  element: id => nodes.get(id), confirmAction: async () => false,
  text: (parent, tag, value) => { const node = new Node(tag); node.textContent = value; parent.append(node); return node; },
  reviewFacts: (parent, rows) => { parent.facts = rows; },
  reviewEvidence: (parent, key, label, value) => { const node = new Node('details'); node.textContent = JSON.stringify(value); parent.append(node); },
  api: async (...args) => { calls.push(args); return verified; }});
vm.runInContext(fs.readFileSync('src/deploy_ops/ui/backups.js', 'utf8'), context);
const event = value => ({preventDefault() {}, submitter: {value}});


// Preserve paths and selected extent across delayed responses, cancellation, duplication and failed inspection
(async () => {
  let releaseOptions;
  context.api = () => new Promise(resolve => { releaseOptions = resolve; });
  const options = vm.runInContext('backupOptions()', context);
  nodes.get('backup-directory').value = '/private/owner-choice';
  releaseOptions({backup_directory: '/private/suggestion', workspace_directory: '/private/restore', max_entries: 20000, max_bytes: 512000000, max_file_bytes: 100000000});
  await options;
  assert.equal(nodes.get('backup-directory').value, '/private/owner-choice');
  assert.equal(nodes.get('restore-workspace').value, '/private/restore');
  context.api = async (...args) => { calls.push(args); return verified; };
  await nodes.get('backup-form').onsubmit(event('inspect'));
  assert.equal(nodes.get('restore-backup').value, verified.bundle);
  assert.equal(nodes.get('restore-digest').value, manifest.sha256);
  nodes.get('restore-state').value = '/private/retained-state';
  calls.length = 0;
  await nodes.get('restore-form').onsubmit(event('restore'));
  assert.equal(calls.length, 0);
  assert.equal(nodes.get('restore-state').value, '/private/retained-state');
  context.confirmAction = async () => true;
  context.api = async (...args) => { calls.push(args); return {status: 'restored-for-inspection', project: '/private/restore/deploy-ops.toml',
    state: '/private/retained-state', original_location: false, capture: 'complete', audit: {head: 'head'}, missing: [], unavailable: []}; };
  await nodes.get('restore-form').onsubmit(event('restore'));
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/backups/restore');
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0][2])), {backup_directory: verified.bundle, digest: manifest.sha256,
    workspace_directory: '/private/restore', state_directory: '/private/retained-state'});

  let releaseCapture;
  calls.length = 0;
  context.api = (...args) => { calls.push(args); return new Promise(resolve => { releaseCapture = resolve; }); };
  nodes.get('backup-directory').value = '/private/new-capture';
  const first = nodes.get('backup-form').onsubmit(event('capture'));
  assert(controls.every(node => node.disabled));
  await nodes.get('backup-form').onsubmit(event('capture'));
  assert.equal(calls.length, 1);
  nodes.get('backup-directory').value = '/private/new-owner-draft';
  releaseCapture(verified); await first;
  assert.equal(calls[0][2].backup_directory, '/private/new-capture');
  assert.equal(nodes.get('backup-directory').value, '/private/new-owner-draft');
  assert(controls.every(node => !node.disabled));

  const previous = nodes.get('backup-detail').children;
  context.api = async () => { throw new Error('fixture verification unavailable'); };
  await nodes.get('backup-form').onsubmit(event('inspect'));
  assert.equal(nodes.get('backup-detail').children, previous);
  assert.match(nodes.get('backup-status').textContent, /verification unavailable/);
  context.api = async () => ({});
  await nodes.get('backup-form').onsubmit(event('inspect'));
  assert.equal(nodes.get('backup-detail').children, previous);
  assert.equal(nodes.get('backup-directory').value, '/private/new-owner-draft');
  assert.equal(nodes.get('restore-digest').value, manifest.sha256);
  console.log('Backup DOM contract passed; browser/native validation/AT not qualified');
})().catch(error => { console.error(error); process.exitCode = 1; });
