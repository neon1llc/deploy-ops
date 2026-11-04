// ui/local-settings.js - Edit ordered build arguments and local option inheritance in retained drafts.


"use strict";


// Retain each component and scope independently from the authored TOML draft
const localDrafts = new Map();
const localDirty = new Map();
let localBusy = false;
const localKey = () => [element("local-component").value, element("local-scope").value, element("local-environment").value].join("/");


// Keep ordered argument vectors editable without shell parsing or JSON text input
function localArguments(parent, title, commands, changed) {
  const group = document.createElement("fieldset"); text(group, "legend", title); parent.append(group);
  commands.forEach((arguments_, index) => {
    const row = document.createElement("fieldset"); text(row, "legend", "Command " + (index + 1)); group.append(row);
    arguments_.forEach((value, position) => {
      const label = document.createElement("label"); label.textContent = "Argument " + (position + 1);
      const input = document.createElement("input"); input.value = value; input.spellcheck = false;
      input.oninput = () => { arguments_[position] = input.value; changed(); };
      label.append(input); row.append(label);
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Remove argument " + (position + 1);
      remove.onclick = () => { arguments_.splice(position, 1); changed(); localRender(); }; row.append(remove);
    });
    const add = document.createElement("button"); add.type = "button"; add.textContent = "Add argument";
    add.onclick = () => { arguments_.push(""); changed(); localRender(); }; row.append(add);
    const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Remove command " + (index + 1);
    remove.onclick = () => { commands.splice(index, 1); changed(); localRender(); }; row.append(remove);
  });
  return group;
}


// Present effective values and their authored source beside the editable fields
function localRender() {
  const name = element("local-component").value;
  const contract = formContract?.local_edit_forms?.[name];
  const root = element("local-fields"); root.replaceChildren();
  element("local-apply").disabled = !contract || localBusy;
  if (!contract) { text(root, "p", "No supported local directory/static components. Other adapters use their existing settings controls."); return; }
  const scope = element("local-scope").value, environment = element("local-environment").value;
  element("local-environment-label").hidden = scope !== "environment";
  const metadata = contract.environments[environment] || {};
  if (!localDirty.has(localKey())) localDirty.set(localKey(), new Set());
  let draft = localDrafts.get(localKey());
  if (!draft) {
    draft = scope === "build" ? {checks: structuredClone(contract.build.checks), command: structuredClone(contract.build.command)}
      : Object.fromEntries(Object.entries(contract.fields).map(([key, field]) => [key, scope === "environment"
        ? metadata[key]?.effective ?? field.default ?? "" : contract.base[key] ?? field.default ?? ""]));
    localDrafts.set(localKey(), draft);
  }
  if (scope === "build") {
    text(root, "p", "Checks source: " + (contract.build_sources?.checks || "component") + " · command source: " + (contract.build_sources?.command || "component"));
    const command = draft.command.length ? [draft.command] : [];
    const group = localArguments(root, "Build command", command, () => { draft.command = command[0] || []; });
    group.id = "local-field-command"; group.tabIndex = -1;
    if (!command.length) {
      const add = document.createElement("button"); add.type = "button"; add.textContent = "Set build command";
      add.onclick = () => { draft.command = [""]; localRender(); }; group.append(add);
    }
    const checks = localArguments(root, "Checks in execution order", draft.checks, () => {});
    checks.id = "local-field-checks"; checks.tabIndex = -1;
    const add = document.createElement("button"); add.type = "button"; add.textContent = "Add check command";
    add.onclick = () => { draft.checks.push([""]); localRender(); }; checks.append(add);
    text(root, "p", "An empty command/check list explicitly clears it. Arguments execute directly, without shell parsing.");
    return;
  }
  for (const [key, field] of Object.entries(contract.fields)) {
    const label = document.createElement("label"); label.textContent = field.title;
    const input = document.createElement("input"); input.id = "local-field-" + key; input.value = draft[key]; input.spellcheck = false;
    input.oninput = () => { draft[key] = input.value; localDirty.get(localKey()).add(key);
      root.querySelectorAll("[data-pin]").forEach(button => { if (button.dataset.pin === key) button.disabled = false; }); }; label.append(input); root.append(label);
    const info = metadata[key];
    text(root, "p", scope === "environment" && info
      ? "Effective: " + String(info.runtime_default ? "environment/component destination at preparation" : info.effective ?? "omitted") + " · source: " + info.source + " · component authored: " + String(info.component_authored ? info.component : "omitted")
      : "Authored: " + String(Object.hasOwn(contract.base, key) ? contract.base[key] : "omitted") + " · adapter default: " + String(field.default ?? "required"));
    if (scope === "environment") {
      const pin = document.createElement("button"); pin.type = "button"; pin.textContent = "Pin " + field.title.toLowerCase() + " override";
      pin.dataset.pin = key;
      pin.disabled = localBusy || Boolean(info?.runtime_default && !localDirty.get(localKey()).has(key));
      pin.onclick = () => localApply("", key); root.append(pin);
      const reset = document.createElement("button"); reset.type = "button"; reset.textContent = "Use inherited " + field.title.toLowerCase(); reset.disabled = !info?.override_authored || localBusy;
      reset.onclick = () => localApply(key); root.append(reset);
    }
  }
}


// Apply only through the shared configuration owner and preserve conflicting drafts
async function localApply(field = "", pin = "") {
  if (localBusy) return;
  localBusy = true; element("local-edit-fields").disabled = true;
  for (const id of ["local-component", "local-scope", "local-environment", "local-reload"]) element(id).disabled = true;
  const key = localKey(), environment = element("local-environment").value;
  const source = element("config").value;
  try {
    if (source !== formSource) throw new Error("TOML changed. Load forms from the current draft before applying local fields.");
    const scope = element("local-scope").value, name = element("local-component").value;
    const draft = localDrafts.get(key);
    const values = field ? {} : scope !== "environment" ? draft : pin ? {[pin]: draft[pin]}
      : Object.fromEntries([...localDirty.get(key)].map(key => [key, draft[key]]));
    if (!field && !Object.keys(values).length) throw new Error("Change a field or choose Pin to author an explicit override.");
    const result = await api("/config/" + (field ? "reset" : "edit"), "POST", {configuration: source, name, scope,
      edit_environment: scope === "environment" ? environment : "", field,
      values});
    if (element("config").value !== source) throw new Error("Draft changed while applying. Your text was preserved.");
    element("config").value = result.toml; formSource = result.toml; formContract = result;
    if (field) localDrafts.get(key)[field] = result.local_edit_forms[name].environments[environment][field].effective ?? "";
    for (const changed of field ? [field] : Object.keys(values)) localDirty.get(key).delete(changed);
    element("config-diff").textContent = result.diff || "No changes.";
    element("local-status").textContent = "Draft updated. Review, validate and explicitly save before preparing a release.";
    document.dispatchEvent(new CustomEvent("deployops:configuration", {detail: result}));
  } catch (error) { element("local-status").textContent = error.message; }
  finally {
    localBusy = false; element("local-edit-fields").disabled = false;
    for (const id of ["local-component", "local-scope", "local-environment", "local-reload"]) element(id).disabled = false;
    localRender();
  }
}


// Refresh choices from the shared schema without discarding other component drafts
function localChoices() {
  const selected = element("local-component").value, environment = element("local-environment").value;
  const entries = formContract?.local_edit_forms || {};
  element("local-component").replaceChildren(...Object.keys(entries).map(name => new Option(name, name)));
  if (Object.hasOwn(entries, selected)) element("local-component").value = selected;
  const environments = entries[element("local-component").value]?.environments || {};
  element("local-environment").replaceChildren(...Object.keys(environments).map(name => new Option(name, name)));
  if (Object.hasOwn(environments, environment)) element("local-environment").value = environment;
  localRender();
}


// Select a searched local setting using the same retained draft path
function localSetting(entry) {
  if (localBusy) return;
  element("local-settings").open = true;
  element("local-component").value = entry.name; element("local-scope").value = entry.scope;
  localChoices();
  if (entry.environment) element("local-environment").value = entry.environment;
  localRender(); (element("local-field-" + entry.key) || element("local-component")).focus();
}


// Keep editing separate from validation, persistence and deployment
for (const id of ["local-component", "local-scope"]) element(id).onchange = localChoices;
element("local-environment").onchange = localRender;
element("local-edit-form").onsubmit = event => { event.preventDefault(); return localApply(); };
element("local-reload").onclick = () => { localDrafts.clear(); localDirty.clear(); localChoices(); };
document.addEventListener("deployops:configuration", localChoices);
