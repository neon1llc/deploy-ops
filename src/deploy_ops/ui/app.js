// ui/app.js - Keep the browser a thin client of the same release operations used by the CLI.
"use strict";
let token = "";
let selected = null;
let detailKey = "";
let releaseKey = "";
let acting = false;
let selectedIncident = null;
let incidentRecords = [];
let incidentKey = "";
let incidentBusy = false;
let savedSourceHash = "";
let savedToml = "";
let reviewedConfiguration = null;
let formSource = "";
let formContract = null;
const formDrafts = new Map();
const element = (id) => document.getElementById(id);
const message = (text, error = false) => {
  element("message").textContent = text;
  element("message").className = error ? "error" : "";
};
async function api(path, method = "GET", body) {
  const response = await fetch("/api" + path, {
    method, headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store"
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
  return data;
}
function button(label, action, key = label) {
  const node = document.createElement("button"); node.textContent = label;
  node.dataset.key = key;
  node.onclick = () => safe(async () => {
    if (acting) return;
    acting = true; node.disabled = true;
    try { await action(); } finally { acting = false; node.disabled = false; }
  }); return node;
}
function text(parent, tag, value) {
  const node = document.createElement(tag); node.textContent = value; parent.append(node); return node;
}
async function safe(action) {
  try { await action(); } catch (error) { message(error.message, true); }
}
function selection() {
  return { environment: element("environment").value, component: element("component").value || null, only: element("only").checked, image: element("image").value || null,
    receipts: JSON.parse(element("receipts").value || "{}") };
}
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
      const node = button(release.environment + " · " + release.status + " · " + release.id.slice(0, 8), () => show(release.id), release.id);
      node.className = "release"; root.append(node);
      if (node.dataset.key === focused) node.focus({preventScroll: true});
    }
  }
  if (selected) await show(selected);
  await refreshIncidents();
}
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
  for (const [name, node] of Object.entries(release.nodes)) {
    const area = document.createElement("div"); area.className = "node"; root.append(area);
    text(area, "h3", name + " · " + node.status);
    if (node.infrastructure_receipts) {
      text(area, "h4", "Infrastructure evidence");
      text(area, "p", "Observed public outputs, not a live drift or health check. Expiry and revocation are checked by the controller.");
      text(area, "pre", JSON.stringify(node.infrastructure_receipts, null, 2));
    }
    if (node.preview) text(area, "pre", JSON.stringify(node.preview, null, 2));
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
  root.append(button("Inspect deployed files", async () => {
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
element("login").onsubmit = (event) => { event.preventDefault(); safe(async () => {
  token = element("token").value; element("token").value = "";
  const data = await api("/workspace");
  element("unlock").hidden = true; element("workspace").hidden = false;
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
  element("component").replaceChildren(new Option("All components", ""));
  for (const name of Object.keys(data.configuration.components)) element("component").append(new Option(name, name));
  message("Unlocked. Tokens are cleared when this tab reloads."); await refresh();
}); };
element("lock").onclick = () => { token = ""; selected = null; location.reload(); };
element("plan").onclick = () => safe(async () => { element("plan-output").textContent = JSON.stringify(await api("/plan", "POST", selection()), null, 2); element("plan-output").hidden = false; });
element("release-form").onsubmit = (event) => { event.preventDefault(); safe(async () => {
  const release = await api("/releases", "POST", selection()); selected = release.id; message("Release preparation started."); await refresh();
}); };
element("delegation").onsubmit = (event) => { event.preventDefault(); safe(async () => {
  if (!window.confirm("Grant bounded local authority for the selected environment?")) return;
  await api("/delegations", "POST", { environment: element("environment").value, hours: Number(element("hours").value), uses: Number(element("uses").value) });
  message("Local delegation granted. Production and unknown operations remain gated.");
}); };
element("refresh").onclick = () => safe(refresh);
setInterval(() => { if (token) safe(refresh); }, 4000);

// Configuration is operator-owned. Saving cannot grant cloud authority or alter the remote controller.
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

function formKey() { return element("config-section").value + "/" + element("config-name").value; }
function fillConfigNames() {
  const section = element("config-section").value;
  element("config-name").replaceChildren(...Object.keys(formContract.configuration[section]).map(name => new Option(name, name)));
  fillConfigFields();
}
function fillConfigFields() {
  const section = element("config-section").value, name = element("config-name").value;
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
element("config-section").onchange = fillConfigNames;
element("config-name").onchange = fillConfigFields;
element("load-forms").onclick = () => safe(async () => {
  const source = element("config").value;
  const result = await api("/config/inspect", "POST", {configuration: source});
  if (element("config").value !== source) throw new Error("Draft changed while loading. Load forms again.");
  formContract = result; formSource = source; formDrafts.clear(); fillConfigNames();
  element("config-status").textContent = "Forms loaded from the current draft. Applying fields does not save the file.";
});
element("config-form").onsubmit = (event) => {
  event.preventDefault();
  safe(async () => {
    const source = element("config").value;
    if (source !== formSource) throw new Error("TOML draft changed. Load forms from the current draft before applying fields.");
    const section = element("config-section").value, name = element("config-name").value;
    const current = formDrafts.get(formKey());
    const values = Object.fromEntries(Object.keys(formContract.forms[section]).map(key => [key, current[key]]));
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


// Incident forms stay mounted while polling, so a new event cannot discard an operator's note.
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
function renderIncident() {
  const record = incidentRecords.find(item => item.id === selectedIncident);
  element("incident-decision-form").hidden = !record || record.status === "resolved";
  if (!record) return;
  element("incident-detail").textContent = record.id + " · " + record.status + " · owner: " + record.owner + " · version " + record.version +
    " · escalation level " + record.level + "/" + record.limit + " · " + (record.next_escalation_at ?
      "next due " + new Date(record.next_escalation_at * 1000).toLocaleString() : "no further escalation scheduled") + ". Release: " + record.release_id;
  element("incident-acknowledge").hidden = record.status === "acknowledged";
}
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
function incidentMessage(value) {
  element("incident-status").textContent = value; element("incident-status").className = "";
}
element("incident-open-form").onsubmit = event => {
  event.preventDefault();
  incidentAction(event.currentTarget, async () => {
    const record = await api("/incidents", "POST", {release_id: element("incident-release").value,
      owner: element("incident-owner").value, note: element("incident-open-note").value,
      interval_seconds: Number(element("incident-interval").value), limit: Number(element("incident-limit").value)});
    if (!token) return;
    selectedIncident = record.id;
    incidentMessage("Incident recorded. Its notification is queued separately; no responder has been contacted by this action.");
  });
};
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
element("incident-tick").onclick = () => incidentAction(element("incident-panel"), async () => {
  const result = await api("/incidents/tick", "POST", {});
  if (token) incidentMessage(result.escalated.length + " escalation signals queued. Delivery requires a separate notification flush.");
});
