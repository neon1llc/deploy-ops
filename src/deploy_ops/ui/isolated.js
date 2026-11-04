// ui/isolated.js - Review isolated infrastructure through the shared operator controller.
"use strict";


// Keep public observations in memory without retaining credentials or automatic write retries
let isolatedState = null;
let isolatedKey = "";
let isolatedBusy = false;
let isolatedFetch = 0;

// Retain operator inspection notes through refreshes and failed submissions
const isolatedNotes = new Map();


// Present immutable values as readable labels without interpreting approval policy
function isolatedFacts(parent, entries) {
  const list = document.createElement("dl");
  list.className = "isolated-facts";
  for (const [label, value] of entries) {
    if (value === undefined || value === null || value === "") continue;
    text(list, "dt", label);
    text(list, "dd", String(value));
  }
  parent.append(list);
}


// Keep native disclosures independently open and keyboard reachable across refreshes
function isolatedEvidence(parent, key, label, expanded, value) {
  const details = document.createElement("details");
  details.dataset.isolatedEvidence = key;
  details.open = expanded.has(key);
  const summary = text(details, "summary", label);
  summary.dataset.isolatedKey = key + ":evidence";
  if (value !== undefined) {
    const pre = document.createElement("pre");
    pre.tabIndex = 0;
    pre.dataset.isolatedKey = key + ":code";
    pre.dataset.isolatedScroll = key;
    text(pre, "code", JSON.stringify(value, null, 2));
    details.append(pre);
  }
  parent.append(details);
  return details;
}


// Display controller-normalized actions without reconstructing native plan semantics
function isolatedChanges(parent, key, changes, planning) {
  if (!changes.length) {
    text(parent, "p", planning ? "No resource declarations are recorded in this packet."
      : "No resource actions are recorded. Outputs or unknown values may still need review.");
    return;
  }
  const counts = new Map();
  for (const change of changes) counts.set(change.action, (counts.get(change.action) || 0) + 1);
  text(parent, "p", planning ? `${changes.length} declared resource(s) · actions determined by native planning`
    : [...counts].map(([action, count]) => `${count} ${action}`).join(" · "));

  // Preserve native header associations and allow keyboard scrolling on narrow layouts
  const region = document.createElement("div");
  region.className = "isolated-table";
  region.tabIndex = 0;
  region.setAttribute("role", "region");
  region.setAttribute("aria-label", planning ? "Declared infrastructure resources" : "Saved plan resource changes");
  region.dataset.isolatedKey = key + ":table";
  region.dataset.isolatedScroll = key + ":table";
  const table = document.createElement("table");
  text(table, "caption", planning ? "Generated module resource inventory" : "Controller-normalized saved plan changes");
  const head = document.createElement("thead");
  const headings = document.createElement("tr");
  for (const label of ["Resource address", "Resource type", "Action"]) {
    text(headings, "th", label).scope = "col";
  }
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


// Separate generated intent from observed native actions and keep review limits visible
function isolatedReview(card, request, expanded) {
  const manifest = request.manifest;
  const planning = manifest.engine === "manifest";
  const preview = manifest.preview || {};
  text(card, "p", planning
    ? "Planning only. This approval permits native planning; applying its saved plan needs a separate exact approval."
    : "Manual approval required. Approval does not apply this plan; execution is a separate action.");
  if (preview.manual_only) {
    text(card, "p", planning ? "Manual-only review: inspect the generated module before running planning."
      : "Manual-only review: the controller requires human inspection of this plan.").className = "isolated-warning";
  }
  if (preview.findings?.length) {
    text(card, "h4", "Review findings");
    const findings = document.createElement("ul");
    findings.className = "isolated-warning";
    for (const finding of preview.findings) text(findings, "li", finding);
    card.append(findings);
  }

  // Inventory declarations only for planning; saved-plan actions come directly from the shared core
  const packet = manifest.isolated_packet;
  const changes = planning ? Object.entries(packet?.module?.resource || {}).flatMap(([kind, resources]) =>
    Object.keys(resources).map(name => ({address: `${kind}.${name}`, resource_type: kind, action: "Not planned yet"})))
    : preview.changes || [];
  isolatedChanges(card, request.id, changes, planning);
  const binding = isolatedEvidence(card, request.id, "Exact approval and immutable evidence", expanded);
  isolatedFacts(binding, [
    ["Approval digest", request.digest], ["Request", request.id], ["Target", manifest.target],
    ["Cloud", manifest.cloud], ["Engine", manifest.engine], ["Tool version", manifest.tool_version],
    ["Source revision", manifest.source?.sha],
    [planning ? "Source packet digest" : "Saved plan digest", manifest.artifact_digest],
    ["Preview digest", manifest.preview_digest], ["Policy digest", manifest.policy_digest],
    ["Provider lock digest", packet?.lock_digest || manifest.lock_digest],
    ["Native tool digest", packet?.tool_digest], ["Provider binary digest", packet?.provider_digest],
    ["Planning request", manifest.planning_request],
    ["Request expires", request.expires_at ? new Date(request.expires_at * 1000).toLocaleString() : undefined],
  ]);
  isolatedEvidence(card, request.id + ":raw", planning ? "Generated module JSON" : "Normalized preview JSON",
    expanded, planning ? packet?.module : preview);
}


// Preserve keyboard position when an observed request changes its available actions
function isolatedRender(force = false) {
  if (!isolatedState) return;
  const key = JSON.stringify(isolatedState) + isolatedBusy;
  if (!force && key === isolatedKey) return;
  isolatedKey = key;
  const data = isolatedState;
  const focused = document.activeElement;
  const focusKey = focused?.dataset.isolatedKey;
  const selection = focused?.tagName === "TEXTAREA"
    ? [focused.selectionStart, focused.selectionEnd, focused.selectionDirection, focused.scrollTop] : null;
  const scrollPositions = new Map([...element("isolated-panel").querySelectorAll("[data-isolated-scroll]")]
    .map(node => [node.dataset.isolatedScroll, [node.scrollTop, node.scrollLeft]]));
  const expanded = new Set([...element("isolated-panel").querySelectorAll("details[open]")]
    .map(node => node.dataset.isolatedEvidence));
  const available = data.available && !isolatedBusy && !(data.active || []).length;
  element("isolated-availability").textContent = data.available
    ? `${data.name} · ${data.engine} · ${data.environment} · access expires ${new Date(data.expires_at * 1000).toLocaleString()}`
    : data.reason || "No isolated infrastructure registration is available.";
  element("isolated-prepare").disabled = !available;

  // Show the actual public target before exposing any approval control
  const recipe = element("isolated-recipe");
  recipe.replaceChildren();
  if (data.recipe) {
    isolatedFacts(recipe, [["Project", data.recipe.project], ["Region", data.recipe.region],
      ["Service", data.recipe.name], ["Runtime identity", data.recipe.runtime_service_account],
      ["Initial image", data.recipe.image_repository + "@" + data.recipe.image_digest]]);
    isolatedEvidence(recipe, "recipe", "Registered recipe, identities and pinned tools", expanded,
      {recipe: data.recipe, identities: data.identities, tools: data.tools, packet_digest: data.packet_digest});
  }

  // Each request retains its own evidence and exact digest instead of a global approve button
  const requests = element("isolated-requests");
  requests.replaceChildren();
  for (const request of data.requests || []) {
    const card = document.createElement("section");
    const planning = request.manifest.engine === "manifest";
    text(card, "h3", `${planning ? "Planning" : "Saved plan"} ${request.id.slice(0, 8)} · ${request.status}`);
    text(card, "p", request.reason || "");
    if (request.worker_error && request.worker_error !== request.reason) {
      text(card, "p", "Last worker error: " + request.worker_error);
    }
    isolatedReview(card, request, expanded);

    // Native submission forms keep approval and cloud execution as distinct decisions
    if (request.status === "pending" || request.status === "approved") {
      const action = request.status === "pending" ? "approve" : "execute";
      const form = document.createElement("form");
      form.action = "/api/isolated/requests/" + encodeURIComponent(request.id) + "/" + action;
      form.method = "post";
      const digest = document.createElement("input");
      digest.type = "hidden"; digest.name = "digest"; digest.value = request.digest;
      const submit = document.createElement("button");
      submit.type = "submit"; submit.disabled = !available;
      submit.dataset.isolatedKey = request.id + ":" + action;
      submit.textContent = action === "approve" ? `Approve ${planning ? "planning" : "saved plan"} ${request.id.slice(0, 8)}`
        : `${planning ? "Run approved planning" : "Apply approved saved plan"} ${request.id.slice(0, 8)}`;
      form.append(digest, submit);
      form.onsubmit = event => {
        event.preventDefault();
        isolatedRun(async () => {
          await api("/isolated/requests/" + encodeURIComponent(request.id) + "/" + action, "POST", {digest: request.digest});
          message(action === "approve" ? "Exact request approved. Execution remains a separate action."
            : "Infrastructure worker started. Refresh observes its outcome without repeating the operation.");
          await isolatedRefresh();
        });
      };
      card.append(form);
    }

    // Preserve uncertainty until an operator records an inspected outcome without replaying work
    if (["running", "uncertain"].includes(request.status) && !(data.active || []).includes(request.id)) {
      const form = document.createElement("form");
      form.action = "/api/isolated/requests/" + encodeURIComponent(request.id) + "/reconcile";
      form.method = "post";
      const fieldset = document.createElement("fieldset");
      text(fieldset, "legend", "Record inspected outcome " + request.id.slice(0, 8));
      text(fieldset, "p", "Inspect the provider operation and cleanup first. This records failure locally without stopping remote work or restoring an infrastructure receipt.");
      const id = "isolated-note-" + request.id;
      const label = text(fieldset, "label", "Observed provider state and cleanup (required)");
      label.htmlFor = id;
      const note = document.createElement("textarea");
      note.id = id; note.name = "note"; note.required = true; note.minLength = 10; note.maxLength = 500;
      note.dataset.isolatedKey = request.id + ":note";
      note.value = isolatedNotes.get(request.id) || "";
      note.oninput = () => isolatedNotes.set(request.id, note.value);
      const submit = document.createElement("button");
      submit.type = "submit"; submit.textContent = "Record inspected failure " + request.id.slice(0, 8);
      submit.disabled = isolatedBusy; submit.dataset.isolatedKey = request.id + ":reconcile";
      fieldset.append(note, submit); form.append(fieldset);
      form.onsubmit = event => {
        event.preventDefault();
        isolatedRun(async () => {
          await api("/isolated/requests/" + encodeURIComponent(request.id) + "/reconcile", "POST",
            {digest: request.digest, note: isolatedNotes.get(request.id) || ""});
          isolatedNotes.delete(request.id);
          message("Inspected failure recorded. No cloud operation was started or repeated.");
          await isolatedRefresh();
        });
      };
      card.append(form);
    }

    // A receipt only fills the application form and never starts an application release
    if (request.output_receipt) {
      const receipt = request.output_receipt;
      text(card, "p", "Infrastructure receipt: " + receipt.id);
      text(card, "p", request.receipt_status?.reason || "The controller revalidates this receipt before application execution.");
      if (data.application && request.receipt_status?.valid !== false) {
        const reuse = document.createElement("button");
        reuse.type = "button"; reuse.className = "secondary";
        reuse.textContent = "Use receipt " + receipt.id.slice(0, 8) + " in application form";
        reuse.dataset.isolatedKey = request.id + ":receipt";
        reuse.onclick = () => {
          element("environment").value = data.environment;
          element("environment").dispatchEvent(new Event("change", {bubbles: true}));
          element("component").value = data.application.component;
          element("receipts").value = JSON.stringify({[data.application.dependency]: receipt.id});
          element("only").checked = true;
          document.dispatchEvent(new CustomEvent("deployops:navigate", {detail: {view: "releases", compose: true}}));
          element("image").focus();
          message("Receipt selected. Choose an immutable image and prepare the application release when ready.");
        };
        card.append(reuse);
      }
    }
    requests.append(card);
  }
  // Keep long evidence and in-progress inspection text at the operator's current position
  for (const node of element("isolated-panel").querySelectorAll("[data-isolated-scroll]")) {
    const position = scrollPositions.get(node.dataset.isolatedScroll);
    if (position) [node.scrollTop, node.scrollLeft] = position;
  }
  if (focusKey && document.activeElement === document.body) {
    const controls = [...element("isolated-panel").querySelectorAll("[data-isolated-key]")];
    const replacement = controls.find(node => node.dataset.isolatedKey === focusKey && !node.disabled);
    const fallback = controls.find(node => node.dataset.isolatedKey === focusKey.split(":")[0] + ":evidence");
    (replacement || fallback || element("isolated-refresh")).focus({preventScroll: true});
    if (replacement?.tagName === "TEXTAREA" && selection) {
      replacement.setSelectionRange(selection[0], selection[1], selection[2]);
      replacement.scrollTop = selection[3];
    }
  }
}


// Refresh observations only; this path cannot prepare, approve or execute infrastructure
async function isolatedRefresh() {
  const sequence = ++isolatedFetch;
  const observed = await api("/isolated");
  if (sequence !== isolatedFetch) return;
  const previous = new Map((isolatedState?.requests || []).map(request => [request.id, request]));
  isolatedState = observed;
  isolatedRender();

  // Announce meaningful asynchronous outcomes without repeating heartbeat-only refreshes
  for (const request of observed.requests || []) {
    const before = previous.get(request.id);
    if (!before) continue;
    if (request.worker_error && request.worker_error !== before.worker_error) {
      message(request.worker_error, true);
    } else if (before.status !== request.status && ["succeeded", "failed", "uncertain", "expired", "denied"].includes(request.status)) {
      message(`Infrastructure request ${request.id.slice(0, 8)} is ${request.status}. ${request.reason || ""}`,
        ["failed", "uncertain", "denied"].includes(request.status));
    }
  }
}


// Serialize operator actions and preserve the last review when a request fails
async function isolatedRun(work) {
  if (isolatedBusy || !token) return;
  isolatedFetch += 1;
  isolatedBusy = true;
  isolatedRender();
  try { await work(); } catch (error) { message(error.message, true); }
  finally { isolatedBusy = false; isolatedRender(); }
}


// Preparing review does not approve or execute a native plan
element("isolated-prepare-form").onsubmit = event => {
  event.preventDefault();
  isolatedRun(async () => {
    await api("/isolated/prepare", "POST", {});
    message("Planning review prepared. Inspect its generated infrastructure before approval.");
    await isolatedRefresh();
  });
};
element("isolated-refresh").onclick = () => isolatedRun(isolatedRefresh);
element("isolated-panel").ontoggle = () => {
  if (element("isolated-panel").open && token) isolatedRun(isolatedRefresh);
};
setInterval(() => {
  if (token && element("isolated-panel").open && !isolatedBusy) safe(isolatedRefresh);
}, 5000);
