// ui/isolated.js - Review isolated infrastructure through the shared operator controller.
"use strict";


// Keep public observations in memory without retaining credentials or automatic write retries
let isolatedState = null;
let isolatedKey = "";
let isolatedBusy = false;
let isolatedFetch = 0;

// Retain operator inspection notes through refreshes and failed submissions
const isolatedNotes = new Map();


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
  const scrollPositions = new Map([...element("isolated-panel").querySelectorAll("details[data-isolated-evidence] pre")]
    .map(node => [node.parentElement.dataset.isolatedEvidence, [node.scrollTop, node.scrollLeft]]));
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
    const details = document.createElement("details");
    details.dataset.isolatedEvidence = "recipe"; details.open = expanded.has("recipe");
    const summary = text(details, "summary", "Registered recipe and pinned tools");
    summary.dataset.isolatedKey = "recipe:evidence";
    text(details, "pre", JSON.stringify({recipe: data.recipe, identities: data.identities,
      tools: data.tools, packet_digest: data.packet_digest}, null, 2));
    recipe.append(details);
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
    text(card, "p", "Approval digest: " + request.digest);
    const evidence = document.createElement("details");
    evidence.dataset.isolatedEvidence = request.id; evidence.open = expanded.has(request.id);
    const summary = text(evidence, "summary", (planning ? "Review generated infrastructure and tool pins " : "Review saved plan changes ")
      + request.id.slice(0, 8));
    summary.dataset.isolatedKey = request.id + ":evidence";
    text(evidence, "pre", JSON.stringify(planning ? request.manifest.isolated_packet : request.manifest.preview, null, 2));
    card.append(evidence);

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
          element("image").focus();
          message("Receipt selected. Choose an immutable image and prepare the application release when ready.");
        };
        card.append(reuse);
      }
    }
    requests.append(card);
  }
  // Keep long evidence and in-progress inspection text at the operator's current position
  for (const node of element("isolated-panel").querySelectorAll("details[data-isolated-evidence] pre")) {
    const position = scrollPositions.get(node.parentElement.dataset.isolatedEvidence);
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
