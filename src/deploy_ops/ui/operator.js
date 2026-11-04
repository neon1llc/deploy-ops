// ui/operator.js - Inspect, stop and reconcile existing controller authority without executing work.


"use strict";


// Keep request selection and authored inspection notes independent of refreshes
let operatorSelected = null;
let operatorPaused = false;
let operatorBusy = false;


// Serialize operator actions and never retry an uncertain response automatically
async function operatorRun(action) {
  if (operatorBusy) return;
  operatorBusy = true;
  const buttons = element("operator-panel").querySelectorAll("button");
  buttons.forEach(node => { node.disabled = true; });
  try { await action(); }
  catch (error) { element("operator-status").textContent = error.message + " Refresh and inspect before retrying an action."; }
  finally { operatorBusy = false; operatorButtons(); }
}


// Show only decisions valid for the observed request and controller state
function operatorButtons() {
  element("operator-panel").querySelectorAll("button").forEach(node => { node.disabled = false; });
  element("operator-pause").disabled = operatorPaused;
  element("operator-resume").disabled = !operatorPaused;
  element("operator-approve").disabled = !operatorSelected || operatorSelected.status !== "pending" || operatorPaused;
  element("operator-reject").disabled = !operatorSelected || !["pending", "approved", "expired", "cancelled", "rejected"].includes(operatorSelected.status);
  element("operator-resolve").disabled = !operatorSelected || !["running", "uncertain"].includes(operatorSelected.status);
}


// Keep exact review bindings visible without including execution credentials or raw controller state
function operatorReview(request) {
  operatorSelected = request;
  const root = element("operator-detail"); root.replaceChildren();
  reviewFacts(root, [["Request", request.id], ["Status", request.status], ["Target", request.manifest.target],
    ["Exact digest", request.digest], ["Reason", request.reason]]);
  reviewChanges(root, "Controller request", request.manifest.preview, new Set());
  reviewEvidence(root, "operator-manifest", "Source, artifact and policy bindings", request.manifest);
  operatorButtons();
}


// Refresh observations without replacing the operator's unfinished reason
async function operatorLoad() {
  const status = await api("/operator/status");
  element("operator-controls").hidden = !status.available;
  element("operator-status").textContent = status.available ? "Local controller · " + (status.paused ? "Paused" : "Accepting new authority") : status.reason;
  if (!status.available) return;
  operatorPaused = status.paused;
  const requests = await api("/operator/requests");
  const root = element("operator-requests"); root.replaceChildren();
  if (!requests.length) text(root, "p", "No controller requests yet.");
  for (const request of requests) {
    const control = button(request.manifest.target + " · " + request.status + " · " + request.id.slice(0, 8), async () => {
      operatorReview(await api("/operator/request?identifier=" + encodeURIComponent(request.id)));
      element("operator-heading").focus();
    });
    control.className = "release"; root.append(control);
  }
  if (operatorSelected) operatorReview(await api("/operator/request?identifier=" + encodeURIComponent(operatorSelected.id)));
  const grants = await api("/operator/delegations");
  const list = element("operator-delegations"); list.replaceChildren();
  if (!grants.length) text(list, "p", "No bounded delegations recorded.");
  for (const grant of grants) {
    const row = document.createElement("div"); list.append(row);
    text(row, "p", grant.target + " · " + (grant.active ? "active" : "revoked") + " · " + grant.uses + "/" + grant.max_uses + " uses");
    if (grant.active) row.append(button("Revoke delegation", () => operatorRun(async () => {
      await api("/operator/revoke", "POST", {identifier: grant.id}); await operatorLoad();
    })));
  }
  const audit = await api("/operator/audit");
  element("operator-audit").textContent = audit.events + " verified events · chain head " + audit.head;
  operatorButtons();
}


// Pause and resume change authority only, never deployment execution
for (const action of ["pause", "resume"]) element("operator-" + action).onclick = () => operatorRun(async () => {
  await api("/operator/" + action, "POST", {}); await operatorLoad();
});
element("operator-load").onclick = () => operatorRun(operatorLoad);


// Bind decisions to the displayed request and record inspection separately from execution
element("operator-decision").onsubmit = (event) => {
  event.preventDefault();
  if (!operatorSelected) return;
  const request = operatorSelected;
  const choice = event.submitter?.value;
  if (!["approve", "reject", "resolve"].includes(choice)) return;
  const note = element("operator-note");
  note.setCustomValidity(choice === "resolve" && note.value.trim().length < 10 ? "Describe the inspected outcome in at least 10 characters." : "");
  if (!note.reportValidity()) { note.focus(); return; }
  const values = choice === "resolve" ? {outcome: element("operator-outcome").value, note: element("operator-note").value}
    : {digest: request.digest, decision: choice, note: element("operator-note").value};
  return operatorRun(async () => {
    await api("/operator/" + (choice === "resolve" ? "resolve" : "decision"), "POST", {identifier: request.id, values});
    await operatorLoad();
  });
};


// Clear an inspection-only error when the operator revises the note or chooses another decision
element("operator-note").oninput = () => element("operator-note").setCustomValidity("");
