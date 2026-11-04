// ui/shell.js - Keep navigation and settings discovery separate from deployment behavior.


"use strict";


// Describe each operator destination without duplicating workflow rules
const views = {
  home: ["Home", "YOUR WORKSPACE", "Infrastructure and applications, from one release history."],
  releases: ["Releases", "DELIVERY", "Prepare, review, deploy and recover changes across environments."],
  infrastructure: ["Infrastructure", "FOUNDATIONS", "Inspect native infrastructure changes and isolated saved plans."],
  incidents: ["Incidents", "OPERATIONS", "Investigate releases, record decisions and manage notification delivery."],
  controls: ["Controls", "WORKSPACE SETTINGS", "Manage delivery settings, local permissions and appearance."]
};
let connected = false;
let settingsIndex = [];
const shellElement = (id) => document.getElementById(id);


// Reveal one retained view so navigation never discards a form draft
function navigate(focus = false) {
  const name = Object.hasOwn(views, location.hash.slice(1)) ? location.hash.slice(1) : "home";
  const [title, eyebrow, description] = views[name];
  document.querySelectorAll("[data-view]").forEach((view) => { view.hidden = view.dataset.view !== name; });
  document.querySelectorAll("[data-view-link]").forEach((link) => {
    if (link.dataset.viewLink === name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  shellElement("view-label").textContent = title;
  shellElement("view-title").textContent = title;
  shellElement("view-eyebrow").textContent = eyebrow;
  shellElement("view-description").textContent = description;
  shellElement("appearance-view").hidden = name !== "controls" || location.protocol === "file:";
  shellElement("unlock").hidden = connected || location.protocol === "file:" || name === "controls";
  document.title = title + " · DeployOps";
  if (focus) shellElement("view-title").focus({preventScroll: true});
}


// Open the existing release composer rather than introducing another request form
function compose() {
  location.hash = "releases";
  navigate();
  shellElement("release-composer").open = true;
  if (connected) shellElement("environment").focus();
  else shellElement("token").focus();
}


// Search grouped settings without rebuilding their inputs or clearing unsaved values
function searchSettings() {
  const query = shellElement("settings-search").value.trim().toLocaleLowerCase();
  let matches = 0;
  document.querySelectorAll("[data-setting]").forEach((group) => {
    const labels = [...group.querySelectorAll("label, legend, summary")].map((node) => node.textContent).join(" ");
    const match = (group.dataset.setting + " " + labels).toLocaleLowerCase().includes(query);
    group.hidden = !match;
    if (match) {
      matches += 1;
      if (query) group.open = true;
    }
  });
  const results = shellElement("settings-matches"); results.replaceChildren();
  const fields = query ? settingsIndex.filter((entry) => entry.search.includes(query)) : [];
  for (const entry of fields) {
    const control = document.createElement("button");
    control.type = "button"; control.className = "setting-result secondary";
    control.textContent = entry.label;
    control.addEventListener("click", () => {
      shellElement("settings-search").value = "";
      searchSettings();
      document.dispatchEvent(new CustomEvent("deployops:setting", {detail: entry}));
    });
    results.append(control);
  }
  shellElement("settings-results").textContent = query
    ? `${fields.length} workspace settings and ${matches} groups match “${query}”` : "";
}


// Index every schema-provided field rather than only the currently selected form
function indexSettings(event) {
  const contract = event.detail;
  settingsIndex = [];
  for (const [section, fields] of Object.entries(contract.forms)) {
    for (const name of Object.keys(contract.configuration[section])) {
      for (const [key, field] of Object.entries(fields)) {
        const label = `${section} / ${name} / ${field.title}`;
        settingsIndex.push({section, name, key, label,
          search: (label + " " + key + " " + (field.description || "")).toLocaleLowerCase()});
      }
    }
  }
  for (const [name, form] of Object.entries(contract.option_forms || {})) {
    for (const [key, field] of Object.entries(form.fields)) {
      const label = `Infrastructure / ${name} / ${field.title}`;
      settingsIndex.push({section: "options", name, key, label,
        search: (label + " " + key + " " + (field.description || "")).toLocaleLowerCase()});
    }
  }
  searchSettings();
}


// Populate overview facts only from the authenticated workspace response
function workspaceLoaded(event) {
  const config = event.detail;
  connected = true;
  shellElement("lock").hidden = false;
  shellElement("session-label").textContent = config.name;
  for (const [kind, entries] of [["environment", config.environments], ["component", config.components]]) {
    const names = Object.keys(entries);
    shellElement(kind + "-count").textContent = String(names.length);
    shellElement(kind + "-summary").textContent = names.join(" · ") || "None configured";
  }
  navigate();
}


// Summarize recorded release state without inferring current provider health
function releasesLoaded(event) {
  const records = event.detail;
  shellElement("release-count").textContent = String(records.length);
  const attention = records.filter((release) => ["failed", "uncertain", "waiting"].includes(release.status)).length;
  shellElement("release-summary").textContent = records.length ? `${attention} waiting, failed or uncertain` : "No releases yet. Prepare your first change.";
}


// Honor direct links, browser history and contextual navigation from feature modules
window.addEventListener("hashchange", () => navigate());
document.querySelectorAll("[data-view-link]").forEach((link) => link.addEventListener("click", (event) => {
  event.preventDefault();
  location.hash = link.dataset.viewLink;
  navigate(true);
}));
document.querySelectorAll("[data-compose]").forEach((control) => control.addEventListener("click", compose));
shellElement("settings-search").addEventListener("input", searchSettings);
document.addEventListener("deployops:workspace", workspaceLoaded);
document.addEventListener("deployops:configuration", indexSettings);
document.addEventListener("deployops:releases", releasesLoaded);
document.addEventListener("deployops:navigate", (event) => {
  if (!Object.hasOwn(views, event.detail.view)) return;
  location.hash = event.detail.view;
  navigate();
  if (event.detail.compose) compose();
});


// Make a direct file opening useful without suggesting its disconnected controls work
if (location.protocol === "file:") {
  shellElement("file-help").hidden = false;
  shellElement("login").querySelectorAll("input, button").forEach((control) => { control.disabled = true; });
  shellElement("session-label").textContent = "Server not connected";
}
navigate();
