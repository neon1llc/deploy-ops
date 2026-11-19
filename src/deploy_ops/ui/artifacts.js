// ui/artifacts.js - Observe frozen artifact bytes through the shared collector without changing deployment policy.
"use strict";

const artifactDrafts = new Map();
let artifactCollectionBusy = false;


// Retain per-subject paths, selection and results while ordinary release polling refreshes its view
function artifactControls(area, release, component, expanded) {
  const key = release + ":" + component;
  if (!artifactDrafts.has(key)) artifactDrafts.set(key, {path: "", inventory: true, vulnerabilities: false, network: false,
    timeout: "60", busy: false, status: "", error: false, result: null});
  const draft = artifactDrafts.get(key);
  const panel = document.createElement("details"); panel.dataset.review = key + ":artifact";
  panel.open = expanded.has(panel.dataset.review);
  text(panel, "summary", "Artifact evidence");
  text(panel, "p", "Observe this reviewed frozen static artifact. Collection runs maintained local tools, never builds, deploys or approves. Inventory, vulnerabilities and trust are separate facts.");
  const form = document.createElement("form");
  const prefix = "artifact-" + release + "-" + component;

  // Keep native labels, validation and keyboard submission with stable focus identities
  function field(label, type, suffix, value) {
    const row = document.createElement("div"); row.className = "field";
    const node = document.createElement("input"); node.type = type; node.id = prefix + "-" + suffix;
    node.name = suffix; node.dataset.key = node.id;
    const title = document.createElement("label"); title.htmlFor = node.id; title.textContent = label;
    row.append(title, node); form.append(row);
    if (type === "checkbox") node.checked = value; else node.value = value;
    return node;
  }
  const path = field("Private evidence directory (required)", "text", "directory", draft.path);
  path.required = true; path.maxLength = 4096; path.pattern = "(?:/|~/).*";
  path.autocomplete = "off"; path.spellcheck = false; path.setAttribute("autocapitalize", "off"); path.setAttribute("autocorrect", "off");
  text(form, "p", "Use an absolute path or ~/. Collect into a new directory outside the workspace/state, or select an existing report for inspection. Native reports can contain private package paths; nothing is uploaded automatically.");
  const inventory = field("Package inventory with Syft", "checkbox", "inventory", draft.inventory);
  const vulnerabilities = field("Vulnerability observation with Trivy", "checkbox", "vulnerabilities", draft.vulnerabilities);
  const network = field("Allow advisory database download", "checkbox", "network", draft.network);
  text(form, "p", "Advisory downloads are off by default. Trivy dependency requests/telemetry and Syft online enrichment/update checks stay disabled. Missing or stale data and no supported packages are not a clean scan.");
  const timeout = field("Maximum time per native command (seconds)", "number", "timeout", draft.timeout);
  timeout.min = "10"; timeout.max = "300"; timeout.step = "1"; timeout.required = true; timeout.inputMode = "numeric";
  const status = document.createElement("p"); status.setAttribute("role", "status"); status.textContent = draft.status;
  if (draft.error) status.className = "error";

  // Preserve authored choices rather than pairing them with a silently changed subject or output path
  function retain() {
    Object.assign(draft, {path: path.value, inventory: inventory.checked, vulnerabilities: vulnerabilities.checked,
      network: network.checked, timeout: timeout.value});
    inventory.setCustomValidity(inventory.checked || vulnerabilities.checked ? "" : "Select package inventory or vulnerability observation.");
    network.setCustomValidity(network.checked && !vulnerabilities.checked ? "Select vulnerability observation before allowing its database download." : "");
  }
  for (const node of [path, inventory, vulnerabilities, network, timeout]) node.addEventListener("input", retain);

  // Refresh only the still-selected subject; results remain available if the operator navigated elsewhere
  async function redraw() {
    if (selected === release && token) { detailKey = ""; await show(release); }
  }
  const buttons = [];
  function action(label, handler, submit = false) {
    const node = document.createElement("button"); node.type = submit ? "submit" : "button";
    node.textContent = label; node.dataset.key = prefix + "-" + buttons.length;
    node.disabled = draft.busy;
    if (handler) node.onclick = handler;
    buttons.push(node); form.append(node); return node;
  }
  action("Suggest a new private directory", async () => {
    try {
      const result = await api("/artifacts/options");
      if (!draft.path && !path.value) { draft.path = result.output_directory; path.value = result.output_directory; }
    } catch (error) { draft.status = error.message; draft.error = true; status.textContent = error.message; status.className = "error"; }
  });

  // A late reply must still belong to the selected frozen subject and supported projection
  function checked(result) {
    const report = result?.report;
    if (result?.verified !== true || report?.format !== "deploy-ops.artifact-observation" || report.version !== 1 ||
        report.release_id !== release || report.component !== component || !report.inventory || !report.vulnerabilities) {
      throw new Error("No supported observation for this exact artifact was returned. Preserve the previous report and inspect its directory.");
    }
    return result;
  }

  // Inspection validates retained bytes without a native tool, regardless of unused collection choices
  action("Inspect evidence directory", async () => {
    if (!path.reportValidity() || draft.busy) return;
    draft.path = path.value;
    try {
      const result = await api("/releases/" + release + "/artifacts/inspect", "POST", {component, output_directory: draft.path});
      checked(result);
      draft.result = result; draft.error = false; draft.status = result.meaning; await redraw();
    } catch (error) { draft.status = error.message; draft.error = true; status.textContent = error.message; status.className = "error"; }
  });
  const submit = action("Collect artifact observations", null, true);
  form.onsubmit = async event => {
    event.preventDefault(); retain();
    if (!form.reportValidity() || draft.busy) return;
    if (artifactCollectionBusy) { status.textContent = "Another artifact collection is running. Wait for it or inspect an existing report."; return; }
    const body = {component, output_directory: draft.path, inventory: draft.inventory, vulnerabilities: draft.vulnerabilities,
      allow_network: draft.network, timeout_seconds: Number(draft.timeout)};
    draft.busy = true; artifactCollectionBusy = true; draft.error = false;
    draft.status = "Collecting bounded native observations. Navigating away keeps this collection running; its result stays with this artifact.";
    status.textContent = draft.status; status.className = "";
    buttons.forEach(node => { node.disabled = true; });
    try {
      const result = await api("/releases/" + release + "/artifacts/collect", "POST", body);
      checked(result);
      draft.result = result; draft.status = result.meaning;
    } catch (error) { draft.status = error.message; draft.error = true; }
    finally {
      draft.busy = false; artifactCollectionBusy = false; buttons.forEach(node => { node.disabled = false; });
      status.textContent = draft.status; status.className = draft.error ? "error" : "";
      await redraw();
      if (document.activeElement === document.body) {
        [...(element("detail")?.querySelectorAll("[data-key]") || [])].find(node => node.dataset.key === submit.dataset.key)?.focus({preventScroll: true});
      }
    }
  };
  panel.append(form, status);
  if (draft.result) {
    const result = draft.result; const report = result.report;
    text(panel, "h4", "Recorded artifact observation");
    const labels = {collected: "Results recorded", no_data: "No supported packages", partial: "Partial coverage", unavailable: "Unavailable", not_requested: "Not selected"};
    const counts = report.vulnerabilities.severity_counts || {};
    reviewFacts(panel, [["Report directory", result.directory], ["Captured at", new Date(report.captured_at * 1000).toLocaleString()], ["Frozen subject SHA-256", report.subject_sha256], ["Frozen files / bytes", report.subject_file_count + " / " + report.subject_bytes],
      ["Inventory", labels[report.inventory.status] || "Unknown"], ["Inventory packages", report.inventory.package_count ?? "Unavailable"],
      ["Vulnerabilities", labels[report.vulnerabilities.status] || "Unknown"], ["Advisory freshness", report.vulnerabilities.database_freshness],
      ["Reported findings", report.vulnerabilities.package_count == null ? "Unavailable" : "Critical " + (counts.CRITICAL || 0) + " · High " + (counts.HIGH || 0) + " · Medium " + (counts.MEDIUM || 0) + " · Low " + (counts.LOW || 0) + " · Unknown " + (counts.UNKNOWN || 0)],
      ["Authenticity / trusted signer", "Not checked"], ["Release policy", "Not evaluated"]]);
    text(panel, "p", report.inventory.reason); text(panel, "p", report.vulnerabilities.reason);
    reviewEvidence(panel, key + ":artifact-report", "Exact observation and native report hashes", report, expanded);
  }
  area.append(panel);
}
