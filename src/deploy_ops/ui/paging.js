// ui/paging.js - Configure named destinations and inspect independent paging outcomes.
"use strict";


// Keep authored drafts in memory until a successful explicit save
let pagingConfig = null;
let pagingDraft = null;
let pagingDestination = {};
let pagingRoute = {};
let pagingBusy = false;
let pagingStatusKey = "";


// Report paging failures without replacing the current form draft
async function pagingRun(work) {
  if (pagingBusy) return;
  pagingBusy = true;
  const focused = document.activeElement;
  const buttons = [...element("paging-panel").querySelectorAll("button")];
  buttons.forEach(node => { node.disabled = true; });
  try {
    await work();
    element("paging-status").className = "";
  } catch (error) {
    element("paging-status").textContent = error.message;
    element("paging-status").className = "error";
  } finally {
    pagingBusy = false;
    buttons.forEach(node => { node.disabled = false; });
    if (document.activeElement === document.body) (focused?.isConnected ? focused : element("paging-status")).focus({preventScroll: true});
  }
}


// Create labeled native controls from the shared schema's type and constraints
function pagingField(parent, key, title, value, schema, update, required = false) {
  const id = parent.id + "-" + key;
  const label = document.createElement("label"); label.htmlFor = id;
  label.textContent = title + (required ? " (required)" : "");
  const choices = schema.enum;
  const input = document.createElement(choices ? "select" : "input");
  input.id = id; input.name = key; input.required = required;
  if (choices) input.append(...choices.map(choice => new Option(choice, choice)));
  else input.type = schema.type === "boolean" ? "checkbox" : schema.type === "integer" ? "number" : "text";
  if (input.type === "checkbox") { input.checked = value; label.className = "check"; }
  else input.value = Array.isArray(value) ? value.join(", ") : value ?? "";
  if (schema.minimum !== undefined) input.min = schema.minimum;
  if (schema.maximum !== undefined) input.max = schema.maximum;
  if (input.type === "text") { input.spellcheck = false; input.autocapitalize = "off"; input.setAttribute("autocorrect", "off"); }
  input.oninput = () => update(input.type === "checkbox" ? input.checked : schema.type === "integer" ? Number(input.value) :
    schema.type === "array" ? input.value.split(",").map(item => item.trim()).filter(Boolean) : input.value);
  label.append(input); parent.append(label); return input;
}


// Offer multiple explicit destination choices without an executable routing language
function pagingChoices(parent, selected, update, id) {
  for (const name of Object.keys(pagingDraft.destinations)) {
    pagingField(parent, id + "-" + name, name, selected.includes(name), {type: "boolean"}, checked => {
      const next = checked ? [...new Set([...selected, name])] : selected.filter(item => item !== name);
      selected = next; update(next);
    });
  }
}


// Show provider-specific fields while keeping worker credentials outside browser input
function pagingDestinationFields() {
  const root = element("paging-destination-fields"); root.replaceChildren();
  const provider = element("paging-provider").value;
  const schema = pagingConfig.forms.destination.properties;
  const native = ["pagerduty", "goalert"].includes(provider);
  const keys = ["enabled", ...(native ? ["key_env"] : ["endpoint_env"]), ...(provider === "goalert" ? ["endpoint"] : [])];
  const labels = {enabled: "Enabled for new routes", key_env: "Integration key environment reference", endpoint_env: "Endpoint URL environment reference",
    endpoint: "GoAlert HTTPS generic endpoint", token_env: "Optional bearer token reference", signing_env: "Optional HMAC secret reference",
    allowed_networks: "Approved private networks, comma separated", attempts: "Maximum attempts", retry_seconds: "Initial retry delay (seconds)"};
  for (const key of keys) {
    const value = pagingDestination[key] ?? schema[key].default ?? "";
    pagingDestination[key] = value;
    pagingField(root, key, labels[key], value, schema[key], next => { pagingDestination[key] = next; }, key !== "enabled");
  }
  const advanced = document.createElement("fieldset"); advanced.id = "paging-advanced"; text(advanced, "legend", "Delivery limits and network scope");
  const extras = ["attempts", "retry_seconds", ...(provider !== "pagerduty" ? ["allowed_networks"] : []), ...(!native ? ["token_env", "signing_env"] : [])];
  for (const key of extras) {
    const value = pagingDestination[key] ?? schema[key].default ?? (schema[key].type === "array" ? [] : "");
    pagingDestination[key] = value;
    pagingField(advanced, key, labels[key], value, schema[key], next => { pagingDestination[key] = next; });
  }
  root.append(advanced);
  element("paging-provider-help").textContent = native ? "Use DEPLOY_OPS_PAGING_ references, never key values. Native schedules remain provider-managed. " +
    (provider === "goalert" ? "GoAlert supports create/update and close. Acknowledgement and readback are unsupported." : "PagerDuty supports trigger, acknowledge and resolve through its US Events API. Readback is unsupported.") :
    "Use DEPLOY_OPS_PAGING_ references. These transports deliver notifications without native acknowledgement or resolution.";
}


// Render route matches and selected destinations from the shared model
function pagingRouteFields() {
  const root = element("paging-route-fields"); root.replaceChildren();
  const schema = pagingConfig.forms.route.properties;
  for (const key of ["projects", "environments", "components", "events", "severities"]) {
    pagingRoute[key] ??= [];
    pagingField(root, key, key + " (comma separated; blank matches all)", pagingRoute[key], schema[key], value => { pagingRoute[key] = value; });
  }
  const group = document.createElement("fieldset"); group.id = "paging-route-destinations";
  text(group, "legend", "Send to these destinations"); pagingRoute.destinations ??= [];
  pagingChoices(group, pagingRoute.destinations, value => { pagingRoute.destinations = value; }, "route"); root.append(group);
  pagingField(root, "fallback", "Fallback after definite transport failure only", pagingRoute.fallback || "",
    {enum: ["", ...Object.keys(pagingDraft.destinations)]}, value => { pagingRoute.fallback = value; });
  pagingField(root, "quiet_until", "Quiet until (ISO date/time with timezone offset)", pagingRoute.quiet_until || "", {type: "string"}, value => { pagingRoute.quiet_until = value; });
}


// Keep route order and defaults visible as part of the authored draft
function pagingLists() {
  const destinationSelect = element("paging-destination-select"), routeSelect = element("paging-route-select");
  const previousDestination = destinationSelect.value, previousRoute = routeSelect.value;
  destinationSelect.replaceChildren(new Option("New destination", ""), ...Object.keys(pagingDraft.destinations).map(name => new Option(name, name)));
  routeSelect.replaceChildren(new Option("New route", ""), ...pagingDraft.routes.map(route => new Option(route.name, route.name)));
  destinationSelect.value = previousDestination; routeSelect.value = previousRoute;
  const root = element("paging-routes"); root.replaceChildren();
  pagingDraft.routes.forEach((route, index) => {
    const row = text(root, "div", (index + 1) + ". " + route.name + " → " + route.destinations.join(", "));
    if (index) row.append(button("Move " + route.name + " earlier", async () => {
      [pagingDraft.routes[index - 1], pagingDraft.routes[index]] = [pagingDraft.routes[index], pagingDraft.routes[index - 1]];
      pagingLists();
    }));
    row.append(button("Remove " + route.name + " from draft", async () => { pagingDraft.routes.splice(index, 1); pagingLists(); }));
  });
  const defaults = element("paging-defaults"); defaults.replaceChildren(); text(defaults, "legend", "Default destinations");
  pagingChoices(defaults, pagingDraft.default_destinations, value => { pagingDraft.default_destinations = value; }, "default");
  element("paging-revision").textContent = "Saved draft revision " + pagingConfig.revision + "; active revision " + pagingConfig.active_revision + ". Form changes remain local until saved.";
}


// Load only on explicit request so polling never destroys unsaved form values
async function pagingLoad() {
  const loaded = await api("/paging/config");
  if (!token) return;
  pagingConfig = loaded; pagingDraft = structuredClone(loaded.draft);
  element("paging-provider").replaceChildren(...loaded.forms.destination.properties.provider.enum.map(name => new Option(name, name)));
  pagingDestination = {}; pagingRoute = {}; pagingDestinationFields(); pagingRouteFields(); pagingLists();
  element("paging-test-destination").replaceChildren(...Object.keys(loaded.active.destinations).map(name => new Option(name, name)));
  const preview = element("paging-preview-fields"); preview.replaceChildren();
  for (const key of ["project", "environment", "component", "event", "severity"]) {
    pagingField(preview, key, key, key === "event" ? "incident.opened" : key === "severity" ? "error" : "", {type: "string"}, () => {});
  }
  await pagingRefresh();
}


// Refresh delivery evidence without re-rendering unchanged controls or configuration drafts
async function pagingRefresh() {
  const state = await api("/paging/status");
  if (!token) return;
  element("paging-health").textContent = "Worker: " + (state.worker.healthy ? "recent heartbeat" : "no recent heartbeat") +
    ". Delivery counts: " + (Object.entries(state.counts).map(([name, count]) => name + " " + count).join(", ") || "none") + ".";
  const key = JSON.stringify([state.bindings, state.deliveries]);
  if (key === pagingStatusKey) return;
  pagingStatusKey = key;
  const root = element("paging-bindings"); root.replaceChildren(); text(root, "h3", "Native incident bindings");
  if (!state.bindings.length) text(root, "p", "No native incidents have been routed.");
  for (const binding of state.bindings) {
    const row = document.createElement("section"); root.append(row);
    text(row, "h4", binding.destination + " · " + binding.provider);
    text(row, "p", "Incident " + binding.incident_id + ". " + binding.state + "; requested " + binding.desired_action +
      "; accepted action " + (binding.accepted_action || "none") + "; remote state " + binding.remote_state + "." +
      (binding.locally_resolved ? " The local incident is resolved. Inspect and resolve this destination separately." : ""));
    if (binding.remote_id) text(row, "p", "Provider alert ID: " + binding.remote_id);
    for (const action of ["acknowledge", "resolve", "retry"]) {
      if (action === "acknowledge" && binding.provider === "goalert") { text(row, "p", "GoAlert generic acknowledgement is unsupported."); continue; }
      if (binding.desired_action === "resolve" && action !== "retry") continue;
      if (action === "retry" && !["unknown", "failed"].includes(binding.state)) continue;
      const control = button(action + " at " + binding.destination, async () => pagingRun(async () => {
        if (action === "retry" && !window.confirm("Retry the selected operation? A lost response can mean it already succeeded, and another trigger may create a duplicate page.")) return;
        await api("/paging/" + action, "POST", {binding: binding.id, version: binding.version, allow_duplicate: action === "retry"});
        element("paging-status").textContent = "Selected destination action queued. Local incident and deployment state are unchanged.";
        await pagingRefresh();
      }), binding.id + "-" + action);
      row.append(control);
    }
  }
  const deliveries = element("paging-deliveries"); deliveries.replaceChildren(); text(deliveries, "h3", "Recent delivery evidence");
  for (const item of state.deliveries) {
    text(deliveries, "p", item.destination + " · " + item.action + " · " + item.state + " · attempts " + item.attempts + "/" + item.attempt_limit + (item.exhausted ? " · exhausted" : "") + (item.overdue ? " · overdue" : "") +
      (item.result.reason ? " · " + item.result.reason : "") + (item.state === "queued" ? " · due " + new Date(item.due * 1000).toLocaleString() : ""));
  }
}


// Switch existing entries deliberately while retaining values during failed submissions
element("paging-destination-select").onchange = () => {
  const name = element("paging-destination-select").value;
  pagingDestination = structuredClone(pagingDraft.destinations[name] || {});
  element("paging-destination-name").value = name;
  element("paging-destination-name").readOnly = Boolean(name);
  element("paging-provider").value = pagingDestination.provider || "pagerduty";
  pagingDestinationFields();
};
element("paging-provider").onchange = pagingDestinationFields;
element("paging-route-select").onchange = () => {
  const name = element("paging-route-select").value;
  pagingRoute = structuredClone(pagingDraft.routes.find(route => route.name === name) || {});
  element("paging-route-name").value = name;
  element("paging-route-name").readOnly = Boolean(name);
  pagingRouteFields();
};


// Apply guided destination fields to the draft without transmitting any credential values
element("paging-destination-form").onsubmit = event => {
  event.preventDefault();
  pagingRun(async () => {
    if (!pagingDraft) throw new Error("Load paging settings first.");
    const provider = element("paging-provider").value, name = element("paging-destination-name").value;
    const values = {provider};
    for (const input of element("paging-destination-fields").querySelectorAll("input,select")) values[input.name] = pagingDestination[input.name];
    pagingDraft.destinations[name] = values;
    pagingLists(); pagingRouteFields();
    element("paging-status").textContent = "Destination applied to the local draft. Save and activate explicitly when reviewed.";
  });
};


// Preserve ordered route edits and explain that field edits do not activate routing
element("paging-route-form").onsubmit = event => {
  event.preventDefault();
  pagingRun(async () => {
    const route = {...pagingRoute, name: element("paging-route-name").value};
    const index = pagingDraft.routes.findIndex(item => item.name === route.name);
    if (index < 0) pagingDraft.routes.push(route); else pagingDraft.routes[index] = route;
    pagingLists();
    element("paging-status").textContent = "Route applied to the local draft. Review order, preview and save before activation.";
  });
};


// Save validates the complete shared model while activation identifies the exact saved revision
element("paging-save").onclick = () => pagingRun(async () => {
  if (!pagingDraft) throw new Error("Load paging settings first.");
  pagingConfig = await api("/paging/save", "POST", {paging_configuration: pagingDraft, revision: pagingConfig.revision});
  pagingDraft = structuredClone(pagingConfig.draft); pagingLists();
  element("paging-status").textContent = "Paging draft saved. Active routing is unchanged.";
});
element("paging-activate").onclick = () => pagingRun(async () => {
  if (!pagingConfig) throw new Error("Load and save paging settings first.");
  if (JSON.stringify(pagingDraft) !== JSON.stringify(pagingConfig.draft)) throw new Error("Save the current draft before activation.");
  pagingConfig = await api("/paging/activate", "POST", {revision: pagingConfig.revision});
  pagingLists();
  element("paging-test-destination").replaceChildren(...Object.keys(pagingConfig.active.destinations).map(name => new Option(name, name)));
  element("paging-status").textContent = "Saved paging revision activated for new events. No test was sent.";
});


// Preview uses the same route evaluator as durable event production
element("paging-preview-form").onsubmit = event => {
  event.preventDefault();
  pagingRun(async () => {
    const context = Object.fromEntries(new FormData(event.target));
    const result = await api("/paging/preview", "POST", {context, paging_configuration: pagingDraft});
    const root = element("paging-preview-result"); root.replaceChildren();
    for (const item of result.destinations) text(root, "p", item.name + ": " + (item.selected ? "selected" : "not selected") + " · " + item.reason);
    element("paging-status").textContent = "Route preview only. No provider was contacted.";
  });
};


// Require an explicit synthetic test action independently of configuration saves
element("paging-test-form").onsubmit = event => {
  event.preventDefault();
  pagingRun(async () => {
    const name = element("paging-test-destination").value;
    await api("/paging/test", "POST", {name, revision: pagingConfig.active_revision});
    element("paging-test-form").reset();
    element("paging-status").textContent = "Synthetic test queued for " + name + ". A running worker may send it now. Inspect and resolve any native test binding.";
    await pagingRefresh();
  });
};


// Keep explicit drains separate from status refresh and the managed foreground worker
element("paging-drain").onclick = () => pagingRun(async () => {
  const result = await api("/paging/drain", "POST", {});
  element("paging-status").textContent = "Drain finished: " + result.accepted + " accepted, " + result.failed + " failed, " + result.unknown + " unknown.";
  await pagingRefresh();
});
element("paging-refresh").onclick = () => pagingRun(pagingRefresh);
element("paging-load").onclick = () => pagingRun(pagingLoad);
element("paging-panel").ontoggle = () => {
  if (element("paging-panel").open && token && !pagingConfig) pagingRun(pagingLoad);
};
setInterval(() => { if (token && element("paging-panel").open && !pagingBusy && pagingConfig) pagingRun(pagingRefresh); }, 5000);
