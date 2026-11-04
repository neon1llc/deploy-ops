// ui/app.js - Keep the browser a thin client of the same release operations used by the CLI.
"use strict";



// Keep session identity and release selection in memory only
let token = "";
let selected = null;
let workspaceConfiguration = null;
let detailKey = "";
let releaseKey = "";
let acting = false;


// Retain incident selection independently from deployment status
let selectedIncident = null;
let incidentRecords = [];
let incidentKey = "";
let incidentBusy = false;


// Track saved text and unsaved form drafts as separate revisions
let savedSourceHash = "";
let savedToml = "";
let reviewedConfiguration = null;
let formSource = "";
let formContract = null;
const formDrafts = new Map();
const creationDrafts = new Map();


// Resolve stable control IDs from the static console document
const element = (id) => document.getElementById(id);


// Announce action results without rendering server strings as markup
const message = (text, error = false) => {
  element("message").textContent = text;
  element("message").className = error ? "error" : "";
};


// Send authenticated operations to the same local core as the CLI
async function api(path, method = "GET", body) {
  const response = await fetch("/api" + path, {
    method, headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store"
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
  return data;
}


// Prevent overlapping actions while preserving a stable keyboard focus key
function button(label, action, key = label) {
  const node = document.createElement("button"); node.textContent = label;
  node.dataset.key = key;
  node.onclick = () => safe(async () => {
    if (acting) return;
    acting = true; node.disabled = true;
    try { await action(); } finally { acting = false; node.disabled = false; }
  }); return node;
}


// Render provider and operator values as plain text
function text(parent, tag, value) {
  const node = document.createElement(tag); node.textContent = value; parent.append(node); return node;
}


// Keep recoverable errors visible without discarding drafts
async function safe(action) {
  try { await action(); } catch (error) { message(error.message, true); }
}


// Keep receipt drafts in the current page while preserving the shared JSON input contract
let receiptRows = [];
let receiptSerial = 0;
let receiptSnapshot = null;
let receiptImportError = "";


// Offer configured infrastructure components without claiming a controller receipt inventory
function receiptComponents() {
  return Object.entries(workspaceConfiguration?.components || {})
    .filter(([, component]) => component.kind === "infrastructure").map(([name]) => name);
}


// Validate row completeness and uniqueness before constructing the same component-to-ID mapping
function receiptValue(showErrors = false, focusError = false) {
  const known = new Set(receiptComponents());
  const seen = new Map();
  const errors = new Map();
  for (const row of receiptRows) {
    const name = row.component.value;
    if (!known.has(name)) errors.set(row.component, "Choose a configured infrastructure component.");
    if (!row.receipt.value.trim()) errors.set(row.receipt, "Enter the public infrastructure receipt ID.");
    if (seen.has(name) && name) {
      errors.set(seen.get(name), "Use each infrastructure component only once.");
      errors.set(row.component, "Use each infrastructure component only once.");
    }
    seen.set(name, row.component);
  }
  for (const row of receiptRows) {
    for (const field of [row.component, row.receipt]) {
      field.setCustomValidity(showErrors ? errors.get(field) || "" : "");
      field.setAttribute("aria-invalid", String(showErrors && errors.has(field)));
    }
  }
  const status = element("receipt-status");
  status.textContent = errors.size ? (showErrors ? [...errors.values()][0] : "")
    : receiptRows.length ? `${receiptRows.length} infrastructure receipt(s) selected. The controller validates them before execution.`
      : "No infrastructure receipts selected.";
  status.className = errors.size && showErrors ? "error" : "muted";
  if (errors.size) {
    if (focusError) {
      const field = errors.keys().next().value;
      const details = field.closest("details");
      if (details) details.open = true;
      field.focus(); field.reportValidity();
    }
    return null;
  }
  const values = Object.fromEntries(receiptRows.map(row => [row.component.value, row.receipt.value.trim()]));
  receiptSnapshot = JSON.stringify(values);
  element("receipts").value = receiptSnapshot;
  receiptImportError = "";
  return values;
}


// Add independently labelled fields without rebuilding another row's unfinished input
function addReceiptRow(name = "", value = "", focus = false) {
  const key = "receipt-" + ++receiptSerial;
  const row = document.createElement("fieldset");
  text(row, "legend", "Infrastructure receipt " + receiptSerial);
  const componentLabel = text(row, "label", "Infrastructure component (required)");
  componentLabel.htmlFor = key + "-component";
  const component = document.createElement("select");
  component.id = key + "-component"; component.name = key + "-component"; component.required = true;
  const placeholder = text(component, "option", "Choose infrastructure component"); placeholder.value = "";
  const names = receiptComponents();
  for (const choice of names) text(component, "option", choice).value = choice;
  if (name && !names.includes(name)) text(component, "option", "Unavailable component: " + name).value = name;
  component.value = name;
  const receiptLabel = text(row, "label", "Public receipt ID (required)");
  receiptLabel.htmlFor = key + "-id";
  const receipt = document.createElement("input");
  receipt.id = key + "-id"; receipt.name = key + "-id"; receipt.type = "text";
  receipt.required = true; receipt.value = value; receipt.spellcheck = false;
  receipt.setAttribute("autocorrect", "off"); receipt.setAttribute("autocapitalize", "off");
  const remove = document.createElement("button");
  remove.type = "button"; remove.className = "secondary";
  remove.textContent = "Remove receipt " + receiptSerial;
  const entry = {row, component, receipt};
  remove.addEventListener("click", () => {
    const index = receiptRows.indexOf(entry);
    receiptRows.splice(index, 1); row.remove(); receiptValue();
    (receiptRows[index]?.component || receiptRows[index - 1]?.component || element("receipt-add")).focus();
  });

  // Clear feedback during typing and validate after leaving a field or explicitly submitting
  for (const field of [component, receipt]) {
    field.setAttribute("aria-describedby", "receipt-status");
    field.addEventListener("input", () => receiptValue());
    field.addEventListener("change", () => receiptValue(true));
    field.addEventListener("blur", () => receiptValue(true));
    field.addEventListener("invalid", () => {
      const details = field.closest("details");
      if (details) details.open = true;
      receiptValue(true);
    });
  }
  componentLabel.after(component);
  row.append(receipt, remove); element("receipt-rows").append(row);
  receiptRows.push(entry);
  if (focus) component.focus();
}


// Import changed receipt references from isolated prefill without discarding ordinary navigation drafts
function loadReceiptRows(force = false) {
  const raw = element("receipts").value || "{}";
  if (!force && raw === receiptSnapshot) return;
  let values;
  try {
    values = JSON.parse(raw);
    if (!values || Array.isArray(values) || typeof values !== "object"
      || Object.values(values).some(value => typeof value !== "string")) throw new Error();
  } catch {
    receiptImportError = "Receipt references could not be read. Remove or replace the receipt rows.";
    element("receipt-status").textContent = receiptImportError;
    element("receipt-status").className = "error";
    return;
  }
  receiptSnapshot = raw;
  receiptRows = []; element("receipt-rows").replaceChildren();
  for (const [name, value] of Object.entries(values)) addReceiptRow(name, value);
  receiptValue(true);
}


// Block both workflow preview and release reuse on incomplete receipt drafts
function readReceipts() {
  loadReceiptRows();
  if (receiptImportError) throw new Error(receiptImportError);
  const values = receiptValue(true, true);
  if (values === null) throw new Error("Complete or remove invalid infrastructure receipt rows.");
  return values;
}


// Synchronize only explicit workspace or prefill changes rather than background polling
if (element("receipt-add")) {
  element("receipt-add").addEventListener("click", () => { addReceiptRow("", "", true); receiptValue(); });
  document.addEventListener("deployops:workspace", () => {
    element("receipt-add").disabled = !receiptComponents().length;
    loadReceiptRows(true);
  });
  document.addEventListener("deployops:navigate", () => loadReceiptRows());
}


// Translate selected environment and dependencies into one release request
function selection() {
  return { environment: element("environment").value, component: element("component").value || null, only: element("only").checked, image: element("image").value || null,
    receipts: readReceipts(),
    ...(workspaceConfiguration?.environments[element("environment").value]?.preview ? {
      preview_owner: element("preview-owner").value, preview_hours: Number(element("preview-hours").value),
      retain_preview_data: element("preview-retain").checked} : {}) };
}


// Refresh status only when returned data changes
async function refresh() {
  const releases = await api("/releases");
  if (!token) return;
  const key = JSON.stringify(releases);
  if (key !== releaseKey) {
    releaseKey = key;
    document.dispatchEvent(new CustomEvent("deployops:releases", {detail: releases}));
    const root = element("releases");
    const focused = root.contains(document.activeElement) ? document.activeElement.dataset.key : null;
    root.replaceChildren();
    if (!releases.length) text(root, "p", "No releases yet. Preview the workflow, then prepare one.");
    for (const release of releases) {
      const previewState = release.preview ? " · preview " + (release.cleanup?.status || (Date.now() >= release.preview.expires_at * 1000 ? "cleanup due" : "active")) : "";
      const node = button(release.environment + " · " + release.status + previewState + " · " + release.id.slice(0, 8), () => show(release.id), release.id);
      node.className = "release"; root.append(node);
      if (node.dataset.key === focused) node.focus({preventScroll: true});
    }
  }
  if (selected) await show(selected);
  await refreshIncidents();
}


// Present recorded facts without inferring provider behavior from names or adapter labels
function reviewFacts(parent, entries) {
  const list = document.createElement("dl");
  list.className = "isolated-facts";
  for (const [label, value] of entries) {
    if (value === undefined || value === null || value === "") continue;
    text(list, "dt", label);
    text(list, "dd", String(value));
  }
  parent.append(list);
}


// Retain complete evidence behind a native disclosure without changing its approval binding
function reviewEvidence(parent, key, label, value, expanded = new Set()) {
  const details = document.createElement("details");
  details.dataset.review = key;
  details.open = expanded.has(key);
  text(details, "summary", label).dataset.key = key + ":summary";
  const pre = document.createElement("pre");
  pre.tabIndex = 0; pre.dataset.key = key + ":code"; pre.dataset.reviewScroll = key;
  text(pre, "code", JSON.stringify(value, null, 2));
  details.append(pre); parent.append(details);
}


// Show the adapter's normalized change contract rather than interpreting its native resource format
function reviewChanges(parent, name, preview, expanded) {
  text(parent, "h4", "Proposed changes");
  if (preview.manual_only) text(parent, "p", "Manual-only review required.").className = "isolated-warning";
  if (preview.findings?.length) {
    text(parent, "h4", "Review findings");
    const list = document.createElement("ul");
    list.className = "isolated-warning";
    for (const finding of preview.findings) text(list, "li", finding);
    parent.append(list);
  }
  const changes = preview.changes || [];
  if (!changes.length) text(parent, "p", "No resource actions are recorded. Review the remaining evidence and outputs.");
  else {
    const counts = new Map();
    for (const change of changes) counts.set(change.action, (counts.get(change.action) || 0) + 1);
    text(parent, "p", [...counts].map(([action, count]) => `${count} ${action}`).join(" · "));

    // Keep the resource address associated with its exact type and action on narrow screens
    const region = document.createElement("div");
    region.className = "isolated-table"; region.tabIndex = 0;
    region.dataset.key = name + ":changes"; region.dataset.reviewScroll = name + ":changes";
    region.setAttribute("role", "region"); region.setAttribute("aria-label", name + " proposed changes");
    const table = document.createElement("table");
    text(table, "caption", name + " · adapter-reported actions");
    const head = document.createElement("thead");
    const headings = document.createElement("tr");
    for (const label of ["Resource address", "Resource type", "Action"]) text(headings, "th", label).scope = "col";
    head.append(headings); table.append(head);
    const body = document.createElement("tbody");
    for (const change of changes) {
      const row = document.createElement("tr");
      text(row, "th", change.address).scope = "row";
      text(row, "td", change.resource_type);
      const action = text(row, "td", change.action);
      if (["delete", "replace"].includes(change.action)) action.className = "isolated-warning";
      body.append(row);
    }
    table.append(body); region.append(table); parent.append(region);
  }
  reviewEvidence(parent, name + ":preview", "Complete normalized preview", preview, expanded);
}


// Explain the shared graph in its returned order without preparing source or provider changes
function reviewWorkflow(root, workflow) {
  root.replaceChildren();
  text(root, "h3", "Workflow preview");
  reviewFacts(root, [["Project", workflow.project], ["Environment", workflow.environment], ["Provider", workflow.provider]]);
  text(root, "p", "Review the component order and dependencies below. No repository or provider commands were run.");
  const order = document.createElement("ol");
  for (const component of workflow.components || []) {
    const item = document.createElement("li");
    text(item, "strong", component.name);
    reviewFacts(item, [["Adapter", component.adapter], ["Repository", component.repository],
      ["Needs", component.needs?.length ? component.needs.join(", ") : "No dependencies"], ["Target", component.target]]);
    order.append(item);
  }
  root.append(order);
  if (!order.children.length) text(root, "p", "No components are included in this workflow.");
  reviewEvidence(root, "workflow", "Workflow contract and complete evidence", workflow);
  root.hidden = false;
}


// Keep local serving separate from activation, authority and preview cleanup
function renderServing(area, release, name, sessions) {
  text(area, "h4", "Serve this static revision");
  text(area, "p", "Serving opens this retained revision on a separate loopback origin. It does not deploy or change the current target. If an action response is lost, refresh to inspect its session before retrying.");
  const session = sessions.find(item => item.component === name);
  if (!session) {
    area.append(button("Start serving", async () => {
      await api("/serving/start", "POST", {release: release.id, component: name}); await refresh();
    }, "serving-start-" + name));
    return;
  }
  text(area, "p", "Session: " + session.status + " · selected artifact " + session.digest);
  const link = document.createElement("a"); link.href = session.url; link.textContent = session.url;
  link.target = "_blank"; link.rel = "noopener noreferrer"; link.referrerPolicy = "no-referrer"; area.append(link);
  area.append(button("Stop serving", async () => {
    await api("/serving/stop", "POST", {id: session.id}); await refresh();
  }, "serving-stop-" + name));
}


// Explain reviewed evidence and expose actions valid for the current release
async function show(id) {
  selected = id;
  const data = await api("/releases/" + id);
  if (selected !== id || !token) return;
  const key = JSON.stringify(data);
  if (key === detailKey) return;
  detailKey = key;
  const release = data.release;
  const root = element("detail");
  const focused = root.contains(document.activeElement) ? document.activeElement.dataset.key : null;
  const expanded = new Set([...root.querySelectorAll("details[open]")].map(node => node.dataset.review));
  const scrolls = new Map([...root.querySelectorAll("[data-review-scroll]")]
    .map(node => [node.dataset.reviewScroll, [node.scrollTop, node.scrollLeft]]));
  root.replaceChildren();
  text(root, "p", release.environment + " · " + release.status);
  reviewEvidence(root, "release-binding", "Release identity and source binding",
    {release: release.id, contract_digest: release.config_digest, source_digest: release.source_digest}, expanded);
  if (release.reason) text(root, "p", release.reason);
  if (release.preview) renderPreview(root, data);
  for (const [name, node] of Object.entries(release.nodes)) {
    const area = document.createElement("div"); area.className = "node"; root.append(area);
    text(area, "h3", name + " · " + node.status);
    if (node.infrastructure_receipts) {
      text(area, "h4", "Infrastructure evidence");
      text(area, "p", "Observed public outputs, not a live drift or health check. Expiry and revocation are checked by the controller.");
      reviewEvidence(area, name + ":receipts", "Infrastructure receipt details", node.infrastructure_receipts, expanded);
    }
    reviewFacts(area, [["Adapter", node.adapter], ["Source revision", node.source?.revision]]);
    if (node.preview) reviewChanges(area, name, node.preview, expanded);
    for (const warning of node.warnings || []) text(area, "p", "Review: " + warning);
    if (["cloudformation", "cdk", "arm", "bicep", "ecs", "container-apps"].includes(node.adapter) && (node.request_digest || node.status === "uncertain")) {
      area.append(button("Read provider status", async () => {
        const observed = await api("/releases/" + id + "/provider/inspect", "POST", {component: name});
        message("Provider readback: " + (observed.status || observed.stack_status || observed.operation || "recorded") + ". No deployment was replayed.");
        await refresh();
      }, "provider-" + name));
      if (["cloudformation", "cdk"].includes(node.adapter) && release.status === "waiting") {
        area.append(button("Discard unexecuted change set", async () => {
          if (!window.confirm("Delete this release's unexecuted change set and cancel the release? Existing deployed resources remain.")) return;
          await api("/releases/" + id + "/provider/discard", "POST", {component: name});
          message("Change set discarded. Inspect any retained stack record in provider status."); await refresh();
        }, "discard-" + name));
      }
    }
    if (node.request_digest) reviewEvidence(area, name + ":approval", "Exact approval and artifact identity",
      {approval_digest: node.request_digest, artifact_digest: node.artifact_digest, source: node.source}, expanded);

    // Use the current controller observation rather than a remembered successful approval click
    const request = data.request_statuses?.[name];
    const matchingRequest = request?.digest === node.request_digest && Boolean(node.request_digest);
    if (node.status === "waiting") {
      text(area, "p", matchingRequest ? "Approval: " + request.status + (request.status === "approved"
        ? ". Ready for separate execution." : ".") : "Approval status unavailable or changed. Refresh before acting.");
    }
    if (node.status === "waiting" && matchingRequest && request.status === "pending") area.append(button("Approve this exact change", async () => {
      if (!window.confirm("Approve the displayed change for " + name + "?")) return;
      await api("/releases/" + id + "/approve", "POST", { component: name, digest: node.request_digest });
      message("Exact change approved. Execution is a separate action."); await refresh();
    }, "approve-" + name));
    if (node.status === "succeeded" && node.adapter === "static") renderServing(area, release, name, data.serving_sessions || []);
    if (node.status === "succeeded") reviewEvidence(area, name + ":outputs", "Recorded deployment outputs", node.outputs, expanded);
    const actions = data.artifact_actions.filter((action) => action.component === name);
    if (actions.length) {
      text(area, "p", "Reuse this exact artifact in a new reviewed release. Rollback restores the application artifact, not database changes.");
      const controls = document.createElement("div"); controls.className = "controls"; area.append(controls);
      for (const action of actions) {
        const label = (action.rollback ? "Rollback in " : "Promote to ") + action.environment;
        if (!action.available) { text(area, "p", label + ": " + action.reason); continue; }
        controls.append(button(label, async () => {
          if (!window.confirm(label + " using the displayed " + name + " artifact? A new release will check authority and target state.")) return;
          const next = await api("/releases", "POST", { origin: id, component: name, environment: action.environment, rollback: action.rollback, only: true,
            receipts: readReceipts() });
          selected = next.id; message("Artifact reuse requested. Inspect the new release and any required approval."); await refresh();
        }, label + "-" + name));
      }
    }
  }

  // Offer execution only for current exact approvals while leaving final enforcement to the core
  const waiting = Object.entries(release.nodes).filter(([, node]) => node.status === "waiting");
  const approved = waiting.length > 0 && waiting.every(([name, node]) => {
    const request = data.request_statuses?.[name];
    return request?.status === "approved" && request.digest === node.request_digest && Boolean(node.request_digest);
  });
  if (release.status === "queued" || (release.status === "waiting" && approved)) root.append(button(
    release.status === "waiting" ? "Execute approved release" : "Run release", async () => {
    await api("/releases/" + id + "/resume", "POST", {}); message("Worker started. The controller rechecks approval before applying each component."); await refresh();
  }, "execute-release"));
  if (["waiting", "queued", "running"].includes(release.status)) root.append(button("Cancel", async () => {
    await api("/releases/" + id + "/cancel", "POST", {}); await refresh();
  }));
  if (release.status === "uncertain") root.append(button("Reconcile inspected target", async () => {
    const note = window.prompt("After stopping the worker and inspecting the target, record what you found:");
    if (!note) return;
    await api("/releases/" + id + "/recover", "POST", { note }); await refresh();
  }));
  if (["failed", "uncertain"].includes(release.status)) root.append(button("Track a local incident", async () => {
    element("incident-release").value = id;
    document.dispatchEvent(new CustomEvent("deployops:navigate", {detail: {view: "incidents"}}));
    element("incident-panel").open = true;
    element("incident-owner").focus();
  }, "track-incident"));
  text(root, "h3", "Deployment observation");
  text(root, "p", "Compare local deployed files with reviewed evidence. This records an observation and makes no repairs. Cloud and runtime health checks are not included.");
  if (release.preview_retiring) text(root, "p", "Preview retirement has started. Inspect the cleanup and retained-data report above; ordinary deployment drift comparison no longer applies.");
  else root.append(button("Inspect deployed files", async () => {
    const observation = await api("/releases/" + id + "/observe", "POST", {});
    if (selected !== id || !token) return;
    await show(id);
    if (document.activeElement === document.body) element("detail").querySelector('[data-key="observe"]')?.focus({preventScroll: true});
    message("Deployment observation: " + observation.status + ". No repairs were made.");
  }, "observe"));
  const observation = data.events.filter(event => event.name === "release.observed").at(-1)?.detail;
  if (observation) {
    text(root, "p", "Observed " + new Date(observation.observed_at * 1000).toLocaleString() + " · " + observation.status);
    const list = document.createElement("ul"); root.append(list);
    for (const [name, result] of Object.entries(observation.components)) {
      text(list, "li", name + ": " + result.status + (result.reason ? ". " + result.reason : ". " + result.scope));
    }
    reviewEvidence(root, "observation", "Observation evidence", observation, expanded);
  }
  reviewEvidence(root, "events", `Event history (${data.events.length})`, data.events, expanded);
  // Polling preserves expanded evidence, scroll and keyboard position without changing drafts
  for (const node of root.querySelectorAll("[data-review-scroll]")) {
    const position = scrolls.get(node.dataset.reviewScroll);
    if (position) [node.scrollTop, node.scrollLeft] = position;
  }
  if (focused) {
    const replacement = [...root.querySelectorAll("[data-key]")].find((node) => node.dataset.key === focused);
    (replacement || element("detail-heading")).focus({preventScroll: true});
  }
}
// Unlock from memory without persisting the operator token
element("login").onsubmit = (event) => { event.preventDefault(); safe(async () => {
  token = element("token").value; element("token").value = "";
  const data = await api("/workspace");
  element("unlock").hidden = true; element("workspace").hidden = false;
  workspaceConfiguration = data.configuration;
  document.dispatchEvent(new CustomEvent("deployops:workspace", {detail: data.configuration}));
  element("title").textContent = data.configuration.name;
  element("mode").textContent = data.mode;
  element("config").value = data.toml;
  savedSourceHash = data.source_hash;
  savedToml = data.toml;
  formSource = data.toml; formContract = data; formDrafts.clear(); fillConfigNames();
  document.dispatchEvent(new CustomEvent("deployops:configuration", {detail: formContract}));
  fillCreationFields();
  element("environment").replaceChildren();
  for (const name of Object.keys(data.configuration.environments)) {
    const option = document.createElement("option"); option.value = name; option.textContent = name; element("environment").append(option);
  }
  configurePreviewFields();
  element("component").replaceChildren(new Option("All components", ""));
  for (const name of Object.keys(data.configuration.components)) element("component").append(new Option(name, name));
  message("Unlocked. Tokens are cleared when this tab reloads." + (data.cloud_access ? " Cloud access: " + data.cloud_access : "")); await refresh();
}); };


// Discard session state on explicit lock
element("lock").onclick = () => { token = ""; selected = null; location.reload(); };


// Describe dependencies without starting a provider operation
element("plan").onclick = () => safe(async () => {
  const selectedWorkflow = selection();
  element("plan-output").hidden = true;
  const workflow = await api("/plan", "POST", selectedWorkflow);
  if (JSON.stringify(selectedWorkflow) !== JSON.stringify(selection())) {
    message("Selections changed. Preview the workflow again.");
    return;
  }
  reviewWorkflow(element("plan-output"), workflow);
});


// Start preparation only through an explicit release submission
element("release-form").onsubmit = (event) => { event.preventDefault(); safe(async () => {
  const release = await api("/releases", "POST", selection()); selected = release.id; message("Release preparation started."); await refresh();
}); };


// Keep bounded local delegation a separate operator decision
element("delegation").onsubmit = (event) => { event.preventDefault(); safe(async () => {
  if (!window.confirm("Grant bounded local authority for the selected environment?")) return;
  await api("/delegations", "POST", { environment: element("environment").value, hours: Number(element("hours").value), uses: Number(element("uses").value) });
  message("Local delegation granted. Production and unknown operations remain gated.");
}); };


// Poll observations without replaying a deployment action
element("refresh").onclick = () => safe(refresh);
setInterval(() => { if (token) safe(refresh); }, 4000);

// Configuration is operator-owned. Saving cannot grant cloud authority or alter the remote controller
element("validate-config").onclick = () => safe(async () => {
  const source = element("config").value;
  const result = await api("/config/validate", "POST", { configuration: source });
  if (element("config").value !== source) throw new Error("Draft changed while validating. Validate again.");
  element("config-diff").textContent = result.diff || "No changes."; reviewedConfiguration = source;
  message("Valid workspace. Dependency order: " + result.order.join(" → ") + ". Digest: " + result.digest);
});
element("save-config").onclick = () => safe(async () => {
  const source = element("config").value;
  if (source !== savedToml && source !== reviewedConfiguration) {
    element("validate-config").click();
    message("Validate and review all changes below, then save the configuration."); return;
  }
  if (!window.confirm("Save this local workspace? The previous file is retained in local state.")) return;
  await api("/config/save", "POST", { configuration: element("config").value, source_hash: savedSourceHash });
  token = ""; location.reload();
});


// Keep drafts separate for every settings group and entry
function formKey() { return element("config-section").value + "/" + element("config-name").value; }


// Render creation from server metadata while retaining each entry type's unfinished inputs
function fillCreationFields() {
  const type = element("config-add-type").value;
  const contract = formContract?.creation_forms?.[type];
  if (!contract) return;
  const draft = creationDrafts.get(type) || {name: "", fields: {}};
  creationDrafts.set(type, draft);
  const name = element("config-add-name"); name.value = draft.name;
  name.oninput = () => { draft.name = name.value; };
  const root = element("config-add-fields"); root.replaceChildren();
  for (const [key, field] of Object.entries(contract.fields)) {
    const id = "create-" + key.replaceAll(".", "-");
    const required = contract.required.includes(key);
    const initial = draft.fields[key] ?? contract.values[key] ?? field.default ?? (field.type === "boolean" ? false : field.type === "array" ? [] : "");
    if (field.type === "array" && field.items?.enum) {
      const group = document.createElement("fieldset"); text(group, "legend", field.title); root.append(group);
      for (const choice of field.items.enum) {
        const label = document.createElement("label"); label.className = "check";
        const input = document.createElement("input"); input.type = "checkbox"; input.name = key; input.value = choice;
        input.checked = initial.includes(choice);
        input.onchange = () => { draft.fields[key] = [...group.querySelectorAll("input:checked")].map(item => item.value); };
        label.append(input, document.createTextNode(choice)); group.append(label);
      }
      continue;
    }
    const label = document.createElement("label"); label.htmlFor = id;
    label.textContent = field.title + (required ? " (required)" : ""); root.append(label);
    const input = document.createElement(field.enum ? "select" : field.type === "array" ? "textarea" : "input");
    input.id = id; input.name = key; input.required = required;
    if (field.enum) input.append(...field.enum.map(value => new Option(value, value)));
    if (field.type === "boolean") { input.type = "checkbox"; input.checked = initial; }
    else {
      input.value = field.type === "array" ? (draft.fields[key] ?? JSON.stringify(initial)) : initial;
      if (field.type === "integer") { input.type = "number"; input.min = field.minimum; input.max = field.maximum; }
    }
    input.spellcheck = false; input.autocapitalize = "off"; input.setAttribute("autocorrect", "off");
    input.oninput = () => { draft.fields[key] = field.type === "boolean" ? input.checked : input.value; };
    root.append(input);
    if (field.description) {
      const hint = text(root, "p", field.description); hint.id = id + "-help"; input.setAttribute("aria-describedby", hint.id);
    }
  }
}


// Collect typed values as data; validation and creation stay in the shared configuration owner
function creationValues(contract, draft) {
  const values = structuredClone(contract.values);
  for (const [key, field] of Object.entries(contract.fields)) {
    const raw = draft.fields[key];
    if (raw === undefined || raw === "") continue;
    const value = field.type === "array" && !field.items?.enum ? JSON.parse(raw)
      : field.type === "integer" ? Number(raw) : raw;
    const parts = key.split(".");
    if (parts.length === 2) (values[parts[0]] ||= {})[parts[1]] = value;
    else values[key] = value;
  }
  return values;
}


// Add only to the current draft and keep the original saved-source identity for explicit save
element("config-add-type").onchange = fillCreationFields;
element("config-add-form").onsubmit = (event) => {
  event.preventDefault();
  safe(async () => {
    const source = element("config").value;
    const type = element("config-add-type").value;
    const contract = formContract?.creation_forms?.[type];
    const draft = creationDrafts.get(type);
    try {
      if (source !== formSource || !contract) throw new Error("Load forms from the current draft before adding an entry.");
      const values = creationValues(contract, draft);
      element("config-add-controls").disabled = true;
      const result = await api("/config/add", "POST", {configuration: source, section: contract.section, name: draft.name, values});
      if (element("config").value !== source) throw new Error("Draft changed while adding. Your text was preserved; reload forms to retry.");
      element("config").value = result.toml; formSource = result.toml; formContract = result;
      reviewedConfiguration = null;
      document.dispatchEvent(new CustomEvent("deployops:configuration", {detail: formContract}));
      element("config-diff").textContent = result.diff || "No changes.";
      creationDrafts.delete(type); fillCreationFields();
      const section = element("config-section").value, name = element("config-name").value;
      fillConfigNames(); element("config-section").value = section; element("config-name").value = name; fillConfigFields();
      element("config-status").textContent = "Entry added to the draft. Validate and review before explicitly saving.";
      element("config-add-name").focus();
    } catch (error) {
      element("config-status").textContent = error.message; element("config-status").focus(); throw error;
    } finally { element("config-add-controls").disabled = false; }
  });
};


// Render typed provider inputs without making cloud authority editable in the browser
function fillOptionFields(name) {
  const root = element("config-fields"); root.replaceChildren();
  const contract = formContract.option_forms?.[name];
  if (!contract) { text(root, "p", "No native infrastructure components in this workspace. Import a CloudFormation, CDK, ARM or Bicep example first."); return; }
  const current = formDrafts.get(formKey()) || structuredClone(contract.values);
  formDrafts.set(formKey(), current);
  text(root, "p", "Effective " + contract.adapter + " settings for " + contract.component + " in " + contract.environment + ". Changes become environment overrides. Public inputs only; credentials and live-test grants stay outside this workspace.");
  const advanced = document.createElement("details"); text(advanced, "summary", "Parameters and advanced settings");
  for (const [key, field] of Object.entries(contract.fields)) {
    const required = contract.required.includes(key);
    const parent = required ? root : advanced;
    if (field.type === "object") {
      const group = document.createElement("fieldset"); group.id = "option-" + key; group.tabIndex = -1;
      text(group, "legend", field.title); parent.append(group);
      const rows = document.createElement("div"); group.append(rows);
      // Public scalar maps use named rows rather than an opaque JSON editor
      const collect = () => {
        const values = Object.create(null); let duplicate = false;
        for (const row of rows.children) {
          const nameInput = row.querySelector("[data-map-name]");
          const valueInput = row.querySelector("[data-map-value]");
          if (!nameInput.value) continue;
          if (Object.hasOwn(values, nameInput.value)) duplicate = true;
          const kind = valueInput.dataset.valueType;
          values[nameInput.value] = kind === "boolean" ? valueInput.value === "true"
            : kind === "integer" ? Number(valueInput.value) : valueInput.value;
        }
        rows.querySelector("input")?.setCustomValidity(duplicate ? "Use each parameter name once." : "");
        current[key] = values;
      };
      const add = (name = "", value = "") => {
        const row = document.createElement("div"); row.className = "parameter-row"; rows.append(row);
        const label = document.createElement("label"); label.textContent = "Name";
        const nameInput = document.createElement("input"); nameInput.name = key + "-name";
        nameInput.dataset.mapName = ""; nameInput.value = name; nameInput.spellcheck = false;
        nameInput.oninput = collect; label.append(nameInput); row.append(label);

        // Preserve schema-supported scalar types when editing a different map entry
        const choices = field.additionalProperties?.anyOf?.map((item) => item.type) || ["string"];
        const typeLabel = document.createElement("label"); typeLabel.textContent = "Value type";
        const typeInput = document.createElement("select"); typeInput.name = key + "-type";
        typeInput.append(...choices.map((kind) => new Option({string: "Text", integer: "Whole number", boolean: "On / off"}[kind], kind)));
        typeInput.value = typeof value === "boolean" ? "boolean" : typeof value === "number" ? "integer" : "string";
        typeLabel.append(typeInput); if (choices.length > 1) row.append(typeLabel);
        const valueLabel = document.createElement("label"); valueLabel.textContent = "Public value"; row.append(valueLabel);

        // Use a choice control for booleans and a constrained number input for integers
        const renderValue = (initial) => {
          valueLabel.replaceChildren(document.createTextNode("Public value"));
          const kind = typeInput.value;
          const input = document.createElement(kind === "boolean" ? "select" : "input");
          input.name = key + "-value"; input.dataset.mapValue = ""; input.dataset.valueType = kind;
          if (kind === "boolean") input.append(new Option("On", "true"), new Option("Off", "false"));
          else if (kind === "integer") { input.type = "number"; input.step = "1"; input.required = true; }
          input.value = String(initial); input.spellcheck = false; input.oninput = collect;
          valueLabel.append(input);
        };
        renderValue(value);
        typeInput.onchange = () => { renderValue(typeInput.value === "boolean" ? false : typeInput.value === "integer" ? 0 : ""); collect(); };
        const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Remove row";
        remove.onclick = () => { row.remove(); collect(); }; row.append(remove);
      };
      for (const [name, value] of Object.entries(current[key])) add(name, value);
      const addButton = document.createElement("button"); addButton.type = "button"; addButton.textContent = "Add " + field.title.toLowerCase() + " row";
      addButton.onclick = () => { add(); rows.lastElementChild.querySelector("input").focus(); }; group.append(addButton);
      continue;
    }
    if (field.type === "array") {
      const group = document.createElement("fieldset"); group.id = "option-" + key; group.tabIndex = -1;
      text(group, "legend", field.title); parent.append(group);
      for (const value of field.items.enum || []) {
        const label = document.createElement("label"); label.className = "check";
        const input = document.createElement("input"); input.type = "checkbox"; input.name = key; input.value = value;
        input.checked = current[key].includes(value);
        input.onchange = () => { current[key] = [...group.querySelectorAll("input:checked")].map(item => item.value); };
        label.append(input, document.createTextNode(value)); group.append(label);
      }
      continue;
    }
    const label = document.createElement("label"); label.htmlFor = "option-" + key;
    label.textContent = field.title + (required ? " (required)" : ""); parent.append(label);
    const choices = field.enum || (field.const ? [field.const] : null);
    const input = document.createElement(choices ? "select" : "input"); input.id = label.htmlFor; input.name = key;
    if (choices) input.append(...choices.map(value => new Option(value, value)));
    input.value = current[key]; input.required = required; input.spellcheck = false;
    input.autocapitalize = "off"; input.setAttribute("autocorrect", "off");
    input.oninput = () => { current[key] = input.value; }; parent.append(input);
    if (field.description) {
      const hint = text(parent, "p", field.description); hint.id = "hint-" + key; input.setAttribute("aria-describedby", hint.id);
    }
  }
  root.append(advanced);
}


// Offer only entries described by the shared server schema
function fillConfigNames() {
  const section = element("config-section").value;
  const entries = section === "options" ? formContract.option_forms : formContract.configuration[section];
  element("config-name").replaceChildren(...Object.keys(entries || {}).map(name => new Option(name, name)));
  fillConfigFields();
}


// Render ordinary settings with native controls and retained values
function fillConfigFields() {
  const section = element("config-section").value, name = element("config-name").value;
  if (section === "options") { fillOptionFields(name); return; }
  const root = element("config-fields"); root.replaceChildren();
  if (!name || !formContract.configuration[section][name]) {
    text(root, "p", "No entries in this group. Add an entry to the draft first."); return;
  }
  const current = formDrafts.get(formKey()) || structuredClone(formContract.configuration[section][name]);
  formDrafts.set(formKey(), current);
  for (const [key, field] of Object.entries(formContract.forms[section])) {
    const id = "setting-" + key;
    if (field.type === "array") {
      const group = document.createElement("fieldset"); group.id = id; group.tabIndex = -1;
      text(group, "legend", field.title); root.append(group);
      for (const choice of field.items.enum.filter(value => value !== name)) {
        const label = document.createElement("label"); label.className = "check";
        const input = document.createElement("input"); input.type = "checkbox"; input.name = key; input.value = choice;
        input.id = id + "-" + choice; label.htmlFor = input.id; input.checked = current[key].includes(choice);
        input.onchange = () => { current[key] = [...group.querySelectorAll("input:checked")].map(item => item.value); };
        label.append(input, document.createTextNode(choice)); group.append(label);
      }
      if (!group.querySelector("input")) text(group, "p", "No other entries available.");
      continue;
    }
    const label = document.createElement("label"); label.htmlFor = id; label.textContent = field.title; root.append(label);
    const input = document.createElement(field.enum ? "select" : "input"); input.id = id; input.name = key;
    if (field.enum) input.append(...field.enum.map(value => new Option(value, value)));
    else input.type = field.type === "boolean" ? "checkbox" : field.type === "integer" ? "number" : "text";
    if (field.type === "boolean") {
      input.checked = current[key]; input.setAttribute("role", "switch");
      label.className = "setting-switch"; label.append(input);
    }
    else {
      input.value = current[key]; input.required = true;
      if (field.minimum !== undefined) input.min = field.minimum;
      if (field.maximum !== undefined) input.max = field.maximum;
      input.spellcheck = false; input.autocapitalize = "off"; input.setAttribute("autocorrect", "off"); root.append(input);
    }
    input.oninput = () => { current[key] = field.type === "boolean" ? input.checked : field.type === "integer" ? Number(input.value) : input.value; };
    if (field.description) {
      const hint = text(root, "p", field.description); hint.className = "setting-help";
      hint.id = id + "-help"; input.setAttribute("aria-describedby", hint.id);
    }
  }
}


// Switch panels without clearing other entry drafts
element("config-section").onchange = fillConfigNames;
element("config-name").onchange = fillConfigFields;


// Reveal a searched setting using the same draft-preserving selection handlers
 document.addEventListener("deployops:setting", (event) => {
  const {section, name, key} = event.detail;
  if (section === "local") { localSetting(event.detail); return; }
  element("workspace-settings").open = true;
  element("config-section").value = section; fillConfigNames();
  element("config-name").value = name; fillConfigFields();
  const target = element((section === "options" ? "option-" : "setting-") + key)
    || element("config-fields").querySelector(`[name="${CSS.escape(key)}"]`);
  if (!target) return;
  for (let parent = target.parentElement; parent && parent !== element("workspace-settings"); parent = parent.parentElement) {
    if (parent.tagName === "DETAILS") parent.open = true;
  }
  target.focus();
});


// Reload forms only from the exact currently displayed TOML draft
element("load-forms").onclick = () => safe(async () => {
  const source = element("config").value;
  const result = await api("/config/inspect", "POST", {configuration: source});
  if (element("config").value !== source) throw new Error("Draft changed while loading. Load forms again.");
  formContract = result; formSource = source; formDrafts.clear(); fillConfigNames();
  fillCreationFields();
  document.dispatchEvent(new CustomEvent("deployops:configuration", {detail: formContract}));
  element("config-status").textContent = "Forms loaded from the current draft. Applying fields does not save the file.";
});


// Apply fields to a draft while rejecting concurrent text changes
element("config-form").onsubmit = (event) => {
  event.preventDefault();
  safe(async () => {
    const source = element("config").value;
    if (source !== formSource) throw new Error("TOML draft changed. Load forms from the current draft before applying fields.");
    const section = element("config-section").value, name = element("config-name").value;
    const current = formDrafts.get(formKey());
    const fields = section === "options" ? formContract.option_forms[name].fields : formContract.forms[section];
    const values = Object.fromEntries(Object.keys(fields).map(key => [key, current[key]]));
    element("config-controls").disabled = true;
    element("load-forms").disabled = true;
    try {
      const result = await api("/config/patch", "POST", {configuration: source, section, name, values});
      if (element("config").value !== source) throw new Error("Draft changed while applying. Your text was preserved; reload forms to retry.");
      element("config").value = result.toml; formSource = result.toml; formContract = result;
      document.dispatchEvent(new CustomEvent("deployops:configuration", {detail: formContract}));
      element("config-diff").textContent = result.diff || "No changes.";
      element("config-status").textContent = "Fields applied to the draft. Review the diff, then save the local configuration.";
    } catch (error) {
      element("config-status").textContent = error.message; element("config-status").focus(); throw error;
    } finally { element("config-controls").disabled = false; element("load-forms").disabled = false; }
  });
};


// Incident forms stay mounted while polling, so a new event cannot discard an operator's note
async function refreshIncidents() {
  const [records, outbox] = await Promise.all([api("/incidents"), api("/notifications")]);
  if (!token) return;
  incidentRecords = records;
  const key = JSON.stringify(records);
  if (key !== incidentKey) {
    incidentKey = key;
    const root = element("incidents");
    const focused = root.contains(document.activeElement) ? document.activeElement.dataset.key : null;
    root.replaceChildren();
    if (!records.length) text(root, "p", "No local incidents have been opened.");
    for (const record of records) {
      const control = button(record.status + " · " + record.owner + " · " + record.id.slice(0, 8), async () => {
        selectedIncident = record.id; renderIncident();
      }, record.id);
      control.className = "release"; root.append(control);
      if (control.dataset.key === focused) control.focus({preventScroll: true});
    }
  }
  renderIncident();
  element("notification-status").textContent = outbox.pending + " pending · " + outbox.delivered + " HTTP accepted · " + outbox.superseded + " superseded · " + outbox.exhausted + " exhausted (" + outbox.attempt_limit + " attempts).";
}


// Show incident response state without changing deployment authority
function renderIncident() {
  const record = incidentRecords.find(item => item.id === selectedIncident);
  element("incident-decision-form").hidden = !record || record.status === "resolved";
  if (!record) return;
  element("incident-detail").textContent = record.id + " · " + record.status + " · owner: " + record.owner + " · version " + record.version +
    " · escalation level " + record.level + "/" + record.limit + " · " + (record.next_escalation_at ?
      "next due " + new Date(record.next_escalation_at * 1000).toLocaleString() : "no further escalation scheduled") + ". Release: " + record.release_id;
  element("incident-acknowledge").hidden = record.status === "acknowledged";
}


// Serialize incident decisions and restore focus after network work
async function incidentAction(form, action) {
  if (incidentBusy) return;
  incidentBusy = true;
  const buttons = [...form.querySelectorAll("button")];
  buttons.forEach(control => { control.disabled = true; });
  try {
    await action();
    await refresh();
  } catch (error) {
    element("incident-status").textContent = error.message;
    element("incident-status").className = "error";
  } finally {
    incidentBusy = false; buttons.forEach(control => { control.disabled = false; });
    if (document.activeElement === document.body) element("incident-status").focus({preventScroll: true});
  }
}


// Announce incident results through their own status region
function incidentMessage(value) {
  element("incident-status").textContent = value; element("incident-status").className = "";
}


// Open an incident without treating notification delivery as acknowledgement
element("incident-open-form").onsubmit = event => {
  event.preventDefault();
  incidentAction(event.currentTarget, async () => {
    const record = await api("/incidents", "POST", {release_id: element("incident-release").value,
      owner: element("incident-owner").value, note: element("incident-open-note").value,
      interval_seconds: Number(element("incident-interval").value), limit: Number(element("incident-limit").value)});
    if (!token) return;
    selectedIncident = record.id;
    incidentMessage("Incident recorded. Destination work is queued; a running paging worker may send it now.");
  });
};


// Bind response decisions to the selected incident version
element("incident-decision-form").onsubmit = event => {
  event.preventDefault();
  const action = event.submitter?.value;
  const record = incidentRecords.find(item => item.id === selectedIncident);
  const note = element("incident-note").value;
  incidentAction(event.currentTarget, async () => {
    if (!record || !action) throw new Error("Select an incident and a decision first.");
    const updated = await api("/incidents/" + record.id + "/" + action, "POST", {version: record.version, note});
    if (!token) return;
    incidentMessage("Incident " + updated.status + ". Deployment outcome is unchanged.");
  });
};


// Advance due local escalation only through an explicit operator action
element("incident-tick").onclick = () => incidentAction(element("incident-panel"), async () => {
  const result = await api("/incidents/tick", "POST", {});
  if (token) incidentMessage(result.escalated.length + " escalation signals queued. Delivery requires a separate notification flush.");
});


// Require ownership fields only for an opted-in local preview
function configurePreviewFields() {
  const preview = Boolean(workspaceConfiguration?.environments[element("environment").value]?.preview);
  element("preview-options").hidden = !preview;
  element("preview-owner").required = preview;
  element("preview-hours").required = preview;
}


// Refresh preview requirements when the selected target changes
element("environment").onchange = configurePreviewFields;


// Expose exact cleanup approval separately from deployment results
function renderPreview(root, data) {
  const release = data.release;
  const id = release.id;
  text(root, "h3", "Isolated static preview");
  text(root, "p", "Owner: " + release.preview.owner + " · expires " + new Date(release.preview.expires_at * 1000).toLocaleString() +
    (Date.now() >= release.preview.expires_at * 1000 ? " · cleanup due" : "") + ". Expiry never deletes files.");
  text(root, "p", release.preview.retain_data ? "Retention requested: deployed files will remain." : "Only verified generated files may be removed. Unknown or modified data stays.");
  text(root, "p", "The ownership record, source, artifacts and audit remain after retirement. Preparing a cleanup plan does not delete files.");
  const cleanup = release.cleanup;
  async function act(action, body, status) {
    await api("/previews/" + id + "/" + action, "POST", body);
    if (selected !== id || !token) return;
    await show(id);
    if (document.activeElement === document.body) element("detail-heading").focus({preventScroll: true});
    message(status);
  }
  if (["succeeded", "failed", "cancelled"].includes(release.status) && (!cleanup || cleanup.status === "reconciled")) {
    root.append(button("Prepare preview cleanup", () => act("prepare-cleanup", {}, "Cleanup prepared. Review exact removals and retained data before approving."), "prepare-cleanup"));
  }
  if (!cleanup) return;
  text(root, "p", "Cleanup: " + cleanup.status + ". Original deployment outcome: " + release.status + ".");
  if (cleanup.reason) text(root, "p", "Cleanup needs inspection: " + cleanup.reason);
  if (cleanup.plan) {
    text(root, "p", cleanup.plan.delete.length + " generated files selected for removal · " + cleanup.plan.retain.length + " retained entries.");
    const evidence = document.createElement("details");
    text(evidence, "summary", "Exact cleanup and retention plan");
    text(evidence, "pre", JSON.stringify(cleanup.plan, null, 2)); root.append(evidence);
  }
  const request = data.cleanup_request;
  if (request) text(root, "pre", "Cleanup approval: " + request.status + "\nDigest: " + request.digest);
  if (cleanup.status === "waiting" && request?.status === "pending") {
    root.append(button("Approve exact preview cleanup", async () => {
      if (!window.confirm("Approve only the displayed preview file removals? Retained source, artifacts, audit and listed data remain.")) return;
      await act("approve-cleanup", {digest: request.digest}, "Cleanup approved. Execution is a separate action.");
    }, "approve-cleanup"));
  }
  if (cleanup.status === "waiting" && request?.status === "approved") {
    root.append(button("Execute approved preview cleanup", async () => {
      await act("cleanup", {}, "Preview retirement recorded. Inspect the retained-data result.");
    }, "execute-cleanup"));
  }
  if (["preparing", "waiting", "running", "uncertain"].includes(cleanup.status)) {
    root.append(button("Inspect and reconcile cleanup", async () => {
      const note = window.prompt("Stop any worker, inspect the owned preview namespace, and describe what remains:");
      if (!note) return;
      const removed = window.prompt("Type removed only if you inspected and confirmed the entire owned namespace is absent. Leave blank to prepare a new plan for remaining files:", "");
      if (removed === null) return;
      if (removed !== "" && removed !== "removed") throw new Error("Use removed or leave the inspection choice blank.");
      await act("reconcile", {note, confirm_removed: removed === "removed"}, "Cleanup inspection recorded. Original deployment outcome is unchanged.");
    }, "reconcile-cleanup"));
  }
}
