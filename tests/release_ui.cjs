// tests/release_ui.cjs - Check explicit release approval and native provider readback actions.


// Evaluate the real rendering functions without starting browser timers or network requests
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");


// Keep only the DOM contract needed by the release inspector
class Node {
  // Retain text and stable control identity
  constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; this.scrollTop = 0; this.scrollLeft = 0; this.value = ""; this.listeners = new Map(); }


  // Attach rendered controls in their actual order
  append(...nodes) { for (const node of nodes) { node.parentElement = this; this.children.push(node); } }


  // Rebuild the inspected component
  replaceChildren() {
    if (this.contains(context.document.activeElement)) context.document.activeElement = context.document.body;
    this.children = [];
  }


  // Track focus when the inspector replaces rendered controls
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }


  // Retain native region attributes and keyboard focus
  setAttribute(name, value) { this[name] = value; }


  // Restore the operator's control after polling
  focus() { context.document.activeElement = this; }


  // Preserve native row insertion and removal order
  after(node) {
    const siblings = this.parentElement.children;
    node.parentElement = this.parentElement;
    siblings.splice(siblings.indexOf(this) + 1, 0, node);
  }


  // Remove one receipt without replacing neighboring drafts
  remove() { this.parentElement.children = this.parentElement.children.filter(node => node !== this); }


  // Find a disclosure that must open for invalid field focus
  closest(tag) { return this.tagName === tag ? this : this.parentElement?.closest(tag); }


  // Capture validation and user events without submitting a form
  setCustomValidity(value) { this.validationMessage = value; }


  // Observe native validation feedback requests
  reportValidity() { return !this.validationMessage; }


  // Register the real helper's event listeners
  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(callback);
  }


  // Dispatch one explicit user action
  dispatchEvent(event) { for (const callback of this.listeners.get(event.type) || []) callback(event); }


  // Resolve action buttons without relying on their displayed ordering
  querySelectorAll(tag) {
    const all = this.children.flatMap(node => [node, ...node.querySelectorAll("*")]);
    return all.filter(node => tag === "*" || (tag === "[data-key]" && node.dataset.key)
      || (tag === "[data-review-scroll]" && node.dataset.reviewScroll)
      || (tag === "details[open]" && node.tagName === "details" && node.open) || node.tagName === tag);
  }
}


// Keep every command observable and refuse unplanned backend calls
const nodes = new Map(["detail", "message", "plan", "plan-output", "detail-heading", "receipt-rows", "receipt-add", "receipt-status", "receipts", "environment", "component", "only", "image"].map(id => [id, new Node("div")]));
const calls = [];
const documentEvents = new Node("document");
const context = vm.createContext({
  document: {body: new Node("body"), addEventListener: (...args) => documentEvents.addEventListener(...args), dispatchEvent: event => documentEvents.dispatchEvent(event), getElementById: id => nodes.get(id), createElement: tag => new Node(tag)},
  window: {confirm: () => true}, console,
});
context.document.activeElement = context.document.body;
const source = fs.readFileSync("src/deploy_ops/ui/app.js", "utf8");
vm.runInContext(source.slice(0, source.indexOf('// Unlock from memory')), context);
vm.runInContext('token="fixture"; refresh=async()=>{}; const selectionWithReceipts=selection;', context);
context.fixture = {release: {id: "release-fixture", status: "waiting", environment: "development",
  config_digest: "contract-fixture", nodes: {web: {adapter: "ecs", status: "waiting", request_digest: "exact-fixture"}}},
  events: [], artifact_actions: [], request_statuses: {web: {status: "pending", digest: "exact-fixture"}}};
context.capture = async (path, method, body) => {
  calls.push({path, method, body});
  return method === "POST" ? {operation: "register-submitted"} : context.fixture;
};
vm.runInContext('api=capture;', context);


// Resolve controls by stable action keys rather than presentation copy
function control(key) {
  return nodes.get("detail").querySelectorAll("button").find(node => node.dataset.key === key);
}


// Confirm approval never submits execution and readback never submits a deployment
(async () => {
  context.fixture.release.nodes.web.preview = {changes: [
    {address: "fixture.web", resource_type: "application_revision", action: "update"},
    {address: "fixture.keep", resource_type: "fixture_type", action: "no-op"}],
    manual_only: true, findings: ["Inspect <script>literal fixture</script> values"]};
  await vm.runInContext('show("release-fixture")', context);
  const content = nodes.get("detail").querySelectorAll("*");
  assert(content.some(node => node.tagName === "th" && node.scope === "row" && node.textContent === "fixture.web"));
  assert(content.some(node => node.textContent === "1 update · 1 no-op"));
  assert(content.some(node => node.tagName === "li" && node.textContent === "Inspect <script>literal fixture</script> values"));
  assert(content.some(node => node.textContent === "Manual-only review required."));
  assert(content.filter(node => node.dataset.review).every(node => !node.open));

  // Preserve independently expanded evidence and focus through changed controller observations
  const disclosure = content.find(node => node.dataset.review === "web:preview");
  disclosure.open = true;
  const events = content.find(node => node.dataset.review === "events");
  events.open = true; events.children[1].scrollTop = 88;
  disclosure.children[0].focus();
  const table = content.find(node => node.dataset.reviewScroll === "web:changes");
  table.scrollLeft = 25; table.scrollTop = 40;
  context.fixture.release.reason = "Updated controller observation";
  await vm.runInContext('show("release-fixture")', context);
  assert.equal(context.document.activeElement.dataset.key, "web:preview:summary");
  const updated = nodes.get("detail").querySelectorAll("*");
  assert(updated.find(node => node.dataset.review === "web:preview").open);
  const retainedEvents = updated.find(node => node.dataset.review === "events");
  assert(retainedEvents.open);
  assert.equal(retainedEvents.children[1].scrollTop, 88);
  const preserved = updated.find(node => node.dataset.reviewScroll === "web:changes");
  assert.deepEqual([preserved.scrollLeft, preserved.scrollTop], [25, 40]);
  assert.equal(control("execute-release"), undefined);
  await control("approve-web").onclick();
  assert.deepEqual(calls.filter(call => call.method === "POST").map(call => [call.path, call.body.digest]),
    [["/releases/release-fixture/approve", "exact-fixture"]]);
  assert.match(nodes.get("message").textContent, /Execution is a separate action/);
  calls.length = 0;
  await control("provider-web").onclick();
  assert.deepEqual(calls.map(call => [call.path, call.body.component]), [["/releases/release-fixture/provider/inspect", "web"]]);
  assert.match(nodes.get("message").textContent, /register-submitted/);

  // Both journal-backed application adapters expose readback without enabling unsupported adapters
  for (const [adapter, visible] of [["container-apps", true], ["cloud-run", false], ["kubernetes", false]]) {
    context.fixture.release.nodes.web.adapter = adapter;
    await vm.runInContext('show("release-fixture")', context);
    assert.equal(Boolean(control("provider-web")), visible);
  }

  // Expired, missing or mismatched observations never expose an execution shortcut
  for (const request of [{status: "expired", digest: "exact-fixture"},
    {status: "approved", digest: "changed-fixture"}, {status: "unavailable", digest: null}]) {
    context.fixture.request_statuses.web = request;
    await vm.runInContext('show("release-fixture")', context);
    assert.equal(control("approve-web"), undefined);
    assert.equal(control("execute-release"), undefined);
  }

  // Only the separate explicit action may submit a currently approved release
  context.fixture.request_statuses.web = {status: "approved", digest: "exact-fixture"};
  await vm.runInContext('show("release-fixture")', context);
  assert.equal(control("approve-web"), undefined);
  calls.length = 0;
  await control("execute-release").onclick();
  assert.deepEqual(calls.map(call => call.path), ["/releases/release-fixture/resume"]);
  // Workflow preview uses the shared graph order and never submits a release
  context.workflow = {project: "fixture", environment: "development", provider: "gcp", config_digest: "frozen",
    components: [{name: "foundation", adapter: "opentofu", repository: "infra", needs: [], target: "fixture-development-foundation"},
      {name: "web", adapter: "cloud-run", repository: "app", needs: ["foundation"], target: "fixture-development-web"}]};
  context.workflowSelection = {environment: "development", component: null};
  context.planCapture = async (path, method, body) => {calls.push({path, method, body}); return context.workflow;};
  vm.runInContext('api=planCapture; selection=()=>workflowSelection;', context);
  vm.runInContext(source.slice(source.indexOf('// Describe dependencies'), source.indexOf('// Start preparation only')), context);
  calls.length = 0;
  await nodes.get("plan").onclick();
  const workflowContent = nodes.get("plan-output").querySelectorAll("*");
  assert.deepEqual(workflowContent.filter(node => node.tagName === "strong").map(node => node.textContent), ["foundation", "web"]);
  assert(workflowContent.some(node => node.textContent === "No dependencies"));
  assert(workflowContent.some(node => node.textContent === "fixture-development-web"));
  assert.deepEqual(calls.map(call => call.path), ["/plan"]);

  // A changed selection cannot display the previous asynchronous graph as current
  context.planCapture = async () => {context.workflowSelection = {environment: "production"}; return context.workflow;};
  vm.runInContext('api=planCapture;', context);
  await nodes.get("plan").onclick();
  assert.equal(nodes.get("plan-output").hidden, true);
  assert.match(nodes.get("message").textContent, /Selections changed/);
  // Guided rows offer actual infrastructure components and leave ID validation to the controller
  vm.runInContext('workspaceConfiguration={components:{foundation:{kind:"infrastructure"}, shared:{kind:"infrastructure"}, web:{kind:"application"}},environments:{development:{}}}; selection=selectionWithReceipts;', context);
  nodes.get("environment").value = "development";
  nodes.get("component").value = "web";
  documentEvents.dispatchEvent({type: "deployops:workspace"});
  assert.equal(nodes.get("receipts").value, "{}");
  nodes.get("receipt-add").dispatchEvent({type: "click"});
  let rows = vm.runInContext('receiptRows', context);
  assert.equal(context.document.activeElement, rows[0].component);
  assert(!rows[0].component.children.some(option => option.value === "web"));
  assert.throws(() => vm.runInContext('readReceipts()', context), /Complete or remove/);
  assert.match(nodes.get("receipt-status").textContent, /Choose a configured/);
  rows[0].component.value = "foundation";
  assert.throws(() => vm.runInContext('readReceipts()', context), /Complete or remove/);
  assert.equal(context.document.activeElement, rows[0].receipt);
  rows[0].receipt.value = "a".repeat(32);
  rows[0].receipt.dispatchEvent({type: "input"});
  assert.equal(nodes.get("receipts").value, JSON.stringify({foundation: "a".repeat(32)}));
  const labels = rows[0].row.querySelectorAll("label");
  assert(labels.some(label => label.htmlFor === rows[0].receipt.id));

  // Duplicate component rows prevent even a workflow request and focus the invalid association
  nodes.get("receipt-add").dispatchEvent({type: "click"});
  rows = vm.runInContext('receiptRows', context);
  rows[1].component.value = "foundation"; rows[1].receipt.value = "b".repeat(32);
  calls.length = 0;
  await nodes.get("plan").onclick();
  assert.equal(calls.length, 0);
  assert.match(nodes.get("receipt-status").textContent, /only once/);
  assert.equal(context.document.activeElement, rows[0].component);
  rows[1].component.value = "shared"; rows[1].component.dispatchEvent({type: "change"});
  assert.deepEqual(JSON.parse(nodes.get("receipts").value), {foundation: "a".repeat(32), shared: "b".repeat(32)});
  const remaining = rows[1].component;
  rows[0].row.querySelectorAll("button")[0].dispatchEvent({type: "click"});
  assert.equal(context.document.activeElement, remaining);
  assert.deepEqual(JSON.parse(nodes.get("receipts").value), {shared: "b".repeat(32)});

  // Ordinary navigation preserves incomplete drafts while an isolated prefill replaces changed references
  nodes.get("receipt-add").dispatchEvent({type: "click"});
  documentEvents.dispatchEvent({type: "deployops:navigate"});
  assert.equal(vm.runInContext('receiptRows.length', context), 2);
  nodes.get("receipts").value = JSON.stringify({foundation: "c".repeat(32)});
  documentEvents.dispatchEvent({type: "deployops:navigate"});
  assert.equal(vm.runInContext('receiptRows.length', context), 1);
  assert.equal(vm.runInContext('readReceipts().foundation', context), "c".repeat(32));
  // Valid component names that match object properties remain ordinary receipt keys
  vm.runInContext('workspaceConfiguration.components.constructor={kind:"infrastructure"};', context);
  nodes.get("receipts").value = JSON.stringify({constructor: "d".repeat(32)});
  documentEvents.dispatchEvent({type: "deployops:navigate"});
  assert.equal(vm.runInContext('Object.hasOwn(readReceipts(), "constructor")', context), true);
  assert.equal(JSON.parse(nodes.get("receipts").value).constructor, "d".repeat(32));
  nodes.get("receipts").value = '["private-sentinel"]';
  assert.throws(() => vm.runInContext('readReceipts()', context), /could not be read/);
  assert(!nodes.get("receipt-status").textContent.includes("private-sentinel"));
  console.log("PASS: readable review, exact actions, guided receipts, duplicate blocking, draft retention and isolated prefill");
})().catch(error => { console.error(error); process.exitCode = 1; });
