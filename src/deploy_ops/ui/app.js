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


// Translate selected environment and dependencies into one release request
function selection() {
  return { environment: element("environment").value, component: element("component").value || null, only: element("only").checked, image: element("image").value || null,
    receipts: JSON.parse(element("receipts").value || "{}"),
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
  root.replaceChildren();
  text(root, "p", release.environment + " · " + release.status);
  text(root, "pre", release.id + "\nContract: " + release.config_digest);
  if (release.reason) text(root, "p", release.reason);
  if (release.preview) renderPreview(root, data);
  for (const [name, node] of Object.entries(release.nodes)) {
    const area = document.createElement("div"); area.className = "node"; root.append(area);
    text(area, "h3", name + " · " + node.status);
    if (node.infrastructure_receipts) {
      text(area, "h4", "Infrastructure evidence");
      text(area, "p", "Observed public outputs, not a live drift or health check. Expiry and revocation are checked by the controller.");
      text(area, "pre", JSON.stringify(node.infrastructure_receipts, null, 2));
    }
    if (node.preview) text(area, "pre", JSON.stringify(node.preview, null, 2));
    for (const warning of node.warnings || []) text(area, "p", "Review: " + warning);
    if (["cloudformation", "cdk", "arm", "bicep"].includes(node.adapter) && (node.request_digest || node.status === "uncertain")) {
      area.append(button("Read provider status", async () => {
        const observed = await api("/releases/" + id + "/provider/inspect", "POST", {component: name});
        message("Provider readback: " + (observed.status || observed.stack_status || "recorded") + ". No deployment was replayed.");
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
    if (node.request_digest) text(area, "pre", "Approval digest: " + node.request_digest);
    if (node.status === "waiting") area.append(button("Approve this exact change", async () => {
      if (!window.confirm("Approve the displayed change for " + name + "?")) return;
      await api("/releases/" + id + "/approve", "POST", { component: name, digest: node.request_digest });
      await api("/releases/" + id + "/resume", "POST", {}); message("Approved. Execution started."); await refresh();
    }, "approve-" + name));
    if (node.status === "succeeded") text(area, "pre", JSON.stringify(node.outputs, null, 2));
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
            receipts: JSON.parse(element("receipts").value || "{}") });
          selected = next.id; message("Artifact reuse requested. Inspect the new release and any required approval."); await refresh();
        }, label + "-" + name));
      }
    }
  }
  if (["waiting", "queued"].includes(release.status)) root.append(button("Resume", async () => {
    await api("/releases/" + id + "/resume", "POST", {}); message("Worker started.");
  }));
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
    const evidence = document.createElement("details"); text(evidence, "summary", "Observation evidence");
    text(evidence, "pre", JSON.stringify(observation, null, 2)); root.append(evidence);
  }
  text(root, "h3", "Event history");
  text(root, "pre", data.events.map((event) => new Date(event.time * 1000).toISOString() + " " + event.name + " " + JSON.stringify(event.detail)).join("\n"));
  // Polling preserves the operator's place when a status update replaces controls
  if (focused) {
    const replacement = [...root.querySelectorAll("button")].find((node) => node.dataset.key === focused);
    (replacement || element("detail-heading")).focus({preventScroll: true});
  }
}
// Unlock from memory without persisting the operator token
element("login").onsubmit = (event) => { event.preventDefault(); safe(async () => {
  token = element("token").value; element("token").value = "";
  const data = await api("/workspace");
  element("unlock").hidden = true; element("workspace").hidden = false;
  workspaceConfiguration = data.configuration;
  element("title").textContent = data.configuration.name;
  element("mode").textContent = data.mode;
  element("config").value = data.toml;
  savedSourceHash = data.source_hash;
  savedToml = data.toml;
  formSource = data.toml; formContract = data; formDrafts.clear(); fillConfigNames();
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
element("plan").onclick = () => safe(async () => { element("plan-output").textContent = JSON.stringify(await api("/plan", "POST", selection()), null, 2); element("plan-output").hidden = false; });


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
      const group = document.createElement("fieldset"); text(group, "legend", field.title); parent.append(group);
      const rows = document.createElement("div"); group.append(rows);
      // Public scalar maps use named rows rather than an opaque JSON editor
      const collect = () => {
        const values = {}; let duplicate = false;
        for (const row of rows.children) {
          const [nameInput, valueInput] = row.querySelectorAll("input");
          if (!nameInput.value) continue;
          if (Object.hasOwn(values, nameInput.value)) duplicate = true;
          values[nameInput.value] = valueInput.value;
        }
        rows.querySelector("input")?.setCustomValidity(duplicate ? "Use each parameter name once." : "");
        current[key] = values;
      };
      const add = (name = "", value = "") => {
        const row = document.createElement("div"); rows.append(row);
        for (const [part, initial] of [["name", name], ["value", value]]) {
          const label = document.createElement("label"); label.textContent = part === "name" ? "Name" : "Public value";
          const input = document.createElement("input"); input.name = key + "-" + part;
          input.value = initial; input.spellcheck = false; input.autocapitalize = "off";
          input.oninput = collect; label.append(input); row.append(label);
        }
        const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Remove row";
        remove.onclick = () => { row.remove(); collect(); }; row.append(remove);
      };
      for (const [name, value] of Object.entries(current[key])) add(name, value);
      const addButton = document.createElement("button"); addButton.type = "button"; addButton.textContent = "Add " + field.title.toLowerCase() + " row";
      addButton.onclick = () => { add(); rows.lastElementChild.querySelector("input").focus(); }; group.append(addButton);
      continue;
    }
    if (field.type === "array") {
      const group = document.createElement("fieldset"); text(group, "legend", field.title); parent.append(group);
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
  const current = formDrafts.get(formKey()) || structuredClone(formContract.configuration[section][name]);
  formDrafts.set(formKey(), current);
  const root = element("config-fields"); root.replaceChildren();
  for (const [key, field] of Object.entries(formContract.forms[section])) {
    const id = "setting-" + key;
    if (field.type === "array") {
      const group = document.createElement("fieldset"); text(group, "legend", field.title); root.append(group);
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
    if (field.type === "boolean") { input.checked = current[key]; label.className = "check"; label.append(input); }
    else {
      input.value = current[key]; input.required = true;
      if (field.minimum !== undefined) input.min = field.minimum;
      if (field.maximum !== undefined) input.max = field.maximum;
      input.spellcheck = false; input.autocapitalize = "off"; input.setAttribute("autocorrect", "off"); root.append(input);
    }
    input.oninput = () => { current[key] = field.type === "boolean" ? input.checked : field.type === "integer" ? Number(input.value) : input.value; };
  }
}


// Switch panels without clearing other entry drafts
element("config-section").onchange = fillConfigNames;
element("config-name").onchange = fillConfigFields;


// Reload forms only from the exact currently displayed TOML draft
element("load-forms").onclick = () => safe(async () => {
  const source = element("config").value;
  const result = await api("/config/inspect", "POST", {configuration: source});
  if (element("config").value !== source) throw new Error("Draft changed while loading. Load forms again.");
  formContract = result; formSource = source; formDrafts.clear(); fillConfigNames();
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
