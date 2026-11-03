// ui/appearance.mjs - Keep validated appearance independent of workspace authority and drafts.

import { cssVariables, defaultPreference, parsePreference, presets, resolveTheme } from "./theme.mjs";

const storageKey = "deploy-ops.appearance.v1";
const element = (id) => document.getElementById(id);
const system = matchMedia("(prefers-color-scheme: dark)");
let preference = parsePreference(defaultPreference);
let formDirty = false;
let jsonDirty = false;

function notice(message, error = false) {
  element("appearance-status").textContent = message;
  element("appearance-status").classList.toggle("error", error);
}

function render() {
  const theme = resolveTheme(preference, system.matches);
  const root = document.documentElement;
  for (const [key, value] of Object.entries(cssVariables(theme))) root.style.setProperty(key, value);
  root.style.colorScheme = theme.scheme;
  root.dataset.theme = theme.id;
  root.dataset.themeDensity = theme.tokens.density;
  document.querySelector('meta[name="color-scheme"]').content = theme.scheme;
  element("appearance-summary").textContent = preference.mode === "system"
    ? `Following system preference · ${theme.label}.` : `Using ${theme.label}.`;
  if (!formDirty) {
    element("appearance-theme").value = preference.mode === "system" ? "system" : preference.preset;
    element("appearance-density").value = theme.tokens.density;
    element("appearance-radius").value = theme.tokens.radius;
  }
  if (!jsonDirty) element("appearance-json").value = JSON.stringify(preference, null, 2);
}

function save(value, message, reset = false) {
  // Validate before changing the view or persisted preference
  const next = parsePreference(value);
  preference = next;
  formDirty = false;
  jsonDirty = false;
  render();
  try {
    if (reset) localStorage.removeItem(storageKey);
    else localStorage.setItem(storageKey, JSON.stringify(next));
    notice(message);
  } catch {
    notice("Appearance applied for this session. Browser storage is unavailable.");
  }
}

for (const preset of presets) element("appearance-theme").append(new Option(preset.label, preset.id));
try {
  const saved = localStorage.getItem(storageKey);
  if (saved) preference = parsePreference(saved);
} catch {
  notice("Saved appearance is invalid or unavailable. Using the system default.");
}
render();

element("appearance-form").addEventListener("input", () => { formDirty = true; });
element("appearance-json").addEventListener("input", () => { jsonDirty = true; });
element("appearance-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const selection = element("appearance-theme").value;
  const mode = selection === "system" ? "system" : "preset";
  const preset = mode === "system" ? preference.preset : selection;
  const changedPalette = mode !== preference.mode || (mode === "preset" && preset !== preference.preset);
  try {
    save({ ...preference, mode, preset, overrides: {
      ...(changedPalette ? {} : preference.overrides),
      density: element("appearance-density").value,
      radius: Number(element("appearance-radius").value),
    } }, changedPalette ? "Appearance saved. Custom colors were reset for the selected theme." : "Appearance saved.");
  } catch (error) { notice(error.message, true); }
});
element("appearance-reset").addEventListener("click", () => save(defaultPreference, "Following system appearance. Custom settings cleared.", true));
element("appearance-export").addEventListener("click", () => {
  jsonDirty = false;
  element("appearance-json").value = JSON.stringify(preference, null, 2);
  notice("Current appearance exported below. Copy it to save or share it.");
});
element("appearance-import").addEventListener("click", () => {
  try { save(element("appearance-json").value, "Appearance imported and saved."); }
  catch (error) { notice(error.message + ". Current appearance and your draft were kept.", true); }
});

system.addEventListener("change", render);
window.addEventListener("storage", (event) => {
  if (event.key !== storageKey && event.key !== null) return;
  try {
    const next = event.newValue === null ? parsePreference(defaultPreference) : parsePreference(event.newValue);
    preference = next;
    render();
    notice(formDirty || jsonDirty ? "Appearance changed in another tab. Your edits were kept." : "Appearance updated in another tab.");
  } catch { notice("Invalid appearance from another tab was ignored. Your current appearance was kept.", true); }
});
