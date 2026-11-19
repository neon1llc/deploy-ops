// ui/signatures.js - Review one frozen file under an independently selected local public key.
"use strict";

const signatureDrafts = new Map();
let signatureVerificationBusy = false;


function signatureControls(area, release, component, prepared, expanded) {
  const identity = release + ":" + component;
  if (!signatureDrafts.has(identity)) signatureDrafts.set(identity, {file: "", key: "", bundle: "", pin: "", path: "", timeout: "60",
    query: "", matches: null, matchesQuery: "", busy: false, searching: false, status: "", error: false, result: null});
  const draft = signatureDrafts.get(identity);
  const prefix = "signature-" + release + "-" + component;
  const panel = document.createElement("details"); panel.dataset.review = identity + ":signature";
  panel.open = expanded.has(panel.dataset.review);
  text(panel, "summary", "Selected-file signature");
  text(panel, "p", "Verify one frozen file with Cosign, a modern signature bundle and a public key you selected independently. This covers that file's content, not other files, release policy, transparency, time or revocation.");

  // The optional search is a separate native form, so Enter searches without submitting verification
  const finder = document.createElement("details"); finder.dataset.review = identity + ":signature-finder";
  finder.open = expanded.has(finder.dataset.review); text(finder, "summary", "Find a frozen file");
  const searchForm = document.createElement("form"); searchForm.method = "get";
  const queryLabel = text(searchForm, "label", "Filename search"); queryLabel.htmlFor = prefix + "-query";
  const query = document.createElement("input"); query.id = queryLabel.htmlFor; query.type = "search"; query.maxLength = 200;
  query.value = draft.query; query.dataset.key = query.id; query.oninput = () => { draft.query = query.value; };
  const search = document.createElement("button"); search.type = "submit"; search.textContent = "Find frozen files";
  search.dataset.key = prefix + "-find"; search.disabled = draft.searching;
  searchForm.append(query, search); finder.append(searchForm); panel.append(finder);

  const form = document.createElement("form"); form.method = "post";
  function field(label, type, key, value, required = true) {
    const row = document.createElement("div"); row.className = "field";
    const title = text(row, "label", label); title.htmlFor = prefix + "-" + key;
    const input = document.createElement("input"); input.id = title.htmlFor; input.type = type;
    input.dataset.key = input.id; input.name = key; input.value = value; input.required = required;
    if (type === "text") {
      input.maxLength = 4096; input.autocomplete = "off"; input.spellcheck = false;
      input.setAttribute("autocapitalize", "off"); input.setAttribute("autocorrect", "off");
    }
    row.append(input); form.append(row); return input;
  }
  const file = field("Selected file inside the frozen artifact", "text", "file", draft.file); file.maxLength = 2048;
  const key = field("Independently selected local public-key file", "text", "key", draft.key);
  const bundle = field("Modern Sigstore bundle file", "text", "bundle", draft.bundle);
  for (const input of [key, bundle]) input.pattern = "(?:/|~/).*";
  const pin = field("Expected public-key file SHA-256 (optional)", "text", "pin", draft.pin, false);
  pin.pattern = "[0-9a-f]{64}"; pin.maxLength = 64;
  const path = field("Private evidence directory", "text", "directory", draft.path); path.pattern = "(?:/|~/).*";
  const timeout = field("Maximum time per native command (seconds)", "number", "timeout", draft.timeout);
  timeout.min = "10"; timeout.max = "300"; timeout.step = "1";
  text(form, "p", "Use absolute or ~/ local paths. Verification creates a new directory containing a bounded copy of the selected file, public key and bundle. Nothing is uploaded automatically. A key-file fingerprint prevents an unexpected byte change; it does not establish who should be trusted.");
  const status = document.createElement("p"); status.setAttribute("role", "status"); status.textContent = draft.status;
  if (draft.error) status.className = "error";
  function retain() {
    Object.assign(draft, {file: file.value, key: key.value, bundle: bundle.value, pin: pin.value, path: path.value, timeout: timeout.value});
  }
  for (const input of [file, key, bundle, pin, path, timeout]) input.addEventListener("input", retain);
  async function redraw() {
    if (selected === release && token) { detailKey = ""; await show(release); }
  }
  const buttons = [];
  function action(label, handler, submit = false) {
    const node = document.createElement("button"); node.type = submit ? "submit" : "button";
    node.textContent = label; node.dataset.key = prefix + "-" + buttons.length; node.disabled = draft.busy;
    if (handler) node.onclick = handler;
    buttons.push(node); form.append(node); return node;
  }

  // Commit a late search only to its named query and frozen prepared identity
  searchForm.onsubmit = async event => {
    event.preventDefault(); if (!searchForm.reportValidity() || draft.searching) return;
    const wanted = query.value; draft.query = wanted; draft.searching = true; search.disabled = true;
    try {
      const result = await api("/releases/" + release + "/signatures/files?component=" + encodeURIComponent(component) + "&query=" + encodeURIComponent(wanted));
      if (result.release_id !== release || result.component !== component || result.prepared_sha256 !== prepared) throw new Error("Frozen file-list identity changed; inspect this release before selecting a file.");
      draft.matches = result; draft.matchesQuery = wanted; draft.error = false;
      draft.status = "Loaded " + result.files.length + " of " + result.total + " matches for “" + wanted + "”. Refine the search when there are more than 100 matches.";
    } catch (error) { draft.status = error.message; draft.error = true; }
    finally { draft.searching = false; search.disabled = false; await redraw(); }
  };
  if (draft.matches) {
    const label = text(finder, "label", "Matches for “" + draft.matchesQuery + "”"); label.htmlFor = prefix + "-matches";
    const matches = document.createElement("select"); matches.id = label.htmlFor; matches.dataset.key = matches.id;
    const empty = document.createElement("option"); empty.value = ""; empty.textContent = "Choose a frozen file"; matches.append(empty);
    for (const item of draft.matches.files) {
      const option = document.createElement("option"); option.value = item.name;
      option.textContent = item.name + " · " + item.size + " bytes"; matches.append(option);
    }
    matches.value = draft.matches.files.some(item => item.name === draft.file) ? draft.file : "";
    matches.onchange = () => { if (matches.value) { file.value = matches.value; retain(); } };
    finder.append(matches);
  }
  action("Read selected key fingerprint", async () => {
    retain(); if (!key.reportValidity()) return;
    const wanted = key.value;
    try {
      const result = await api("/signatures/key-info", "POST", {key_file: wanted});
      if (typeof result.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(result.sha256)) throw new Error("Public-key fingerprint response is unsupported; authored values were kept.");
      if (draft.key !== wanted) draft.status = "Key selection changed; the earlier fingerprint response was not applied.";
      else {
        if (!draft.pin) { draft.pin = result.sha256; if (!pin.value) pin.value = result.sha256; }
        draft.status = draft.pin !== result.sha256
          ? "Selected key fingerprint " + result.sha256 + " differs from the authored pin. Inspect the key choice before changing that pin."
          : "Key fingerprint: " + result.sha256 + ". Trust was not established.";
      }
      draft.error = false;
    } catch (error) { draft.status = error.message; draft.error = true; }
    await redraw();
  });
  action("Suggest a new private directory", async () => {
    try {
      const result = await api("/artifacts/options");
      if (typeof result.output_directory !== "string" || !/^(?:\/|~\/)/.test(result.output_directory)) throw new Error("Directory suggestion is unavailable; authored values were kept.");
      if (!draft.path) { draft.path = result.output_directory; if (!path.value) path.value = result.output_directory; }
      draft.status = "New-directory suggestion available; authored paths were kept."; draft.error = false;
    } catch (error) { draft.status = error.message; draft.error = true; }
    await redraw();
  });
  action("Verify selected frozen file", null, true);

  async function run(inspecting) {
    retain();
    if (draft.busy || signatureVerificationBusy) { draft.status = "Another signature observation is still running. Inspect its result before retrying."; await redraw(); return; }
    const request = inspecting ? {component, output_directory: path.value} : {component, output_directory: path.value,
      selected_file: file.value, key_file: key.value, bundle_file: bundle.value, expected_key_sha256: pin.value || null, timeout_seconds: Number(timeout.value)};
    draft.busy = true; signatureVerificationBusy = true;
    draft.status = inspecting ? "Inspecting the retained report bytes…" : "Verifying “" + request.selected_file + "” under the selected public key…";
    draft.error = false; status.textContent = draft.status; status.className = "";
    for (const node of buttons) node.disabled = true;
    try {
      const result = await api("/releases/" + release + "/signatures/" + (inspecting ? "inspect" : "verify"), "POST", request);
      const report = result.report;
      if (!result.integrity_verified || report?.format !== "deploy-ops.file-signature-observation" || report.version !== 1
          || report.release_id !== release || report.component !== component
          || (!inspecting && (report.prepared_sha256 !== prepared || report.selected_file !== request.selected_file
              || report.profile.timeout_seconds !== request.timeout_seconds || report.profile.expected_key_sha256 !== request.expected_key_sha256))) throw new Error("Signature report identity differs from this request; the previous result was kept.");
      draft.result = {directory: request.output_directory, report}; draft.error = false;
      draft.status = inspecting
        ? "Retained report says “" + report.status + "”. Only report-byte integrity was checked; native verification was not rerun."
        : "Native selected-file result: " + report.status + ". No deployment policy was evaluated.";
    } catch (error) { draft.status = error.message; draft.error = true; }
    finally {
      draft.busy = false; signatureVerificationBusy = false; for (const node of buttons) node.disabled = false;
      await redraw();
      if (selected === release && token && document.activeElement === document.body) {
        const focusKey = prefix + "-" + (inspecting ? 3 : 2);
        [...element("detail").querySelectorAll("[data-key]")].find(node => node.dataset.key === focusKey)?.focus({preventScroll: true});
      }
    }
  }
  form.onsubmit = async event => { event.preventDefault(); if (form.reportValidity()) await run(false); };
  action("Inspect an existing signature report", async () => { if (path.reportValidity()) await run(true); });
  form.append(status); panel.append(form);
  if (draft.result) {
    const report = draft.result.report;
    text(panel, "h4", "Recorded selected-file signature: " + report.status);
    reviewFacts(panel, [["Selected frozen file", report.selected_file], ["File SHA-256", report.selected_file_sha256],
      ["Selected public-key file SHA-256", report.selected_key_sha256], ["Tool version", report.tool_version || "Unavailable"],
      ["Captured at", new Date(report.captured_at * 1000).toLocaleString()], ["Scope", "This file's content only"],
      ["Signer trust", "Operator-selected key only"], ["Transparency / time / revocation", "Not checked"], ["Release policy", "Not evaluated"]]);
    text(panel, "p", report.reason);
    reviewEvidence(panel, identity + ":signature-report", "Exact observation, artifact binding and input hashes", report, expanded);
  }
  area.append(panel);
}
