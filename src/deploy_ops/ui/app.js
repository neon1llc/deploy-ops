// ui/app.js - Keep the browser a thin client of the same release operations used by the CLI.
"use strict";
let token = "";
let selected = null;
let detailKey = "";
let releaseKey = "";
let acting = false;
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
  return { environment: element("environment").value, component: element("component").value || null, only: element("only").checked, image: element("image").value || null };
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
          const next = await api("/releases", "POST", { origin: id, component: name, environment: action.environment, rollback: action.rollback, only: true });
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
  const result = await api("/config/validate", "POST", { configuration: element("config").value });
  message("Valid workspace. Dependency order: " + result.order.join(" → ") + ". Digest: " + result.digest);
});
element("save-config").onclick = () => safe(async () => {
  if (!window.confirm("Save this local workspace? The previous file is retained in local state.")) return;
  await api("/config/save", "POST", { configuration: element("config").value });
  token = ""; location.reload();
});
