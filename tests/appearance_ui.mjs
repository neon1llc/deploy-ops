// tests/appearance_ui.mjs - Check immediate appearance persistence and independent JSON drafts.


import assert from "node:assert/strict";

// Model native control events without a browser, network requests or deployment state
class Control {
  // Keep control values, listeners and accessible attributes observable
  constructor() {
    this.value = "";
    this.textContent = "";
    this.handlers = new Map();
    this.attributes = new Map();
    this.classList = {toggle() {}};
  }

  // Register the shipped event handlers without replacing their behavior
  addEventListener(type, handler) {
    const handlers = this.handlers.get(type) || [];
    handlers.push(handler);
    this.handlers.set(type, handlers);
  }

  // Bubble form events with the same originating control
  fire(type, target = this) {
    const event = {target, preventDefault() {}};
    for (const handler of this.handlers.get(type) || []) handler(event);
    if (this.parent) this.parent.fire(type, target);
  }

  // Preset options do not change a selection until the user chooses one
  append() {}

  // Retain accessible values separately from visible text
  setAttribute(name, value) { this.attributes.set(name, value); }
}


// Supply only the browser APIs consumed by the real appearance module
const ids = ["status", "summary", "theme", "density", "radius", "radius-value", "json", "form", "reset", "export", "import"];
const nodes = new Map(ids.map(id => [`appearance-${id}`, new Control()]));
const node = id => nodes.get(`appearance-${id}`);
for (const id of ["theme", "density", "radius"]) node(id).parent = node("form");
const variables = new Map();
const system = new Control();
system.matches = false;
const windowEvents = new Map();
const persisted = new Map();
const key = "deploy-ops.appearance.v1";
let storageUnavailable = false;
globalThis.document = {
  getElementById: id => nodes.get(id),
  documentElement: {style: {setProperty: (name, value) => variables.set(name, value)}, dataset: {}},
  querySelector: () => ({}),
};
globalThis.matchMedia = () => system;
globalThis.Option = class {};
globalThis.window = {addEventListener: (type, handler) => windowEvents.set(type, handler)};
globalThis.localStorage = {
  getItem: name => persisted.get(name) ?? null,
  setItem: (name, value) => {
    if (storageUnavailable) throw new Error("Storage unavailable");
    persisted.set(name, value);
  },
  removeItem: name => persisted.delete(name),
};
await import("../src/deploy_ops/ui/appearance.mjs");


// Save select and slider changes immediately, with the visible and spoken radius aligned
node("theme").value = "slate-dark";
node("theme").fire("input");
node("theme").fire("change");
assert.equal(JSON.parse(persisted.get(key)).preset, "slate-dark");
node("density").value = "compact";
node("density").fire("change");
assert.equal(JSON.parse(persisted.get(key)).overrides.density, "compact");
node("radius").value = "14";
node("radius").fire("input");
assert.equal(JSON.parse(persisted.get(key)).overrides.radius, 14);
assert.equal(node("radius-value").textContent, "14 px");
assert.equal(node("radius").attributes.get("aria-valuetext"), "14 pixels");

// Preserve an unfinished JSON import when controls, system preference or another tab changes
node("json").value = '{"unfinished":';
node("json").fire("input");
node("radius").value = "12";
node("radius").fire("input");
assert.equal(node("json").value, '{"unfinished":');
assert.match(node("status").textContent, /draft was kept/);
system.matches = true;
system.fire("change");
assert.equal(node("json").value, '{"unfinished":');
const external = {version: 1, mode: "preset", preset: "neon1-light", overrides: {radius: 8}};
windowEvents.get("storage")({key, newValue: JSON.stringify(external)});
assert.equal(node("radius-value").textContent, "8 px");
assert.equal(node("json").value, '{"unfinished":');
assert.match(node("status").textContent, /Your edits were kept/);

// Reject invalid changes before persistence and keep those control drafts across tabs
const before = persisted.get(key);
node("theme").value = "invalid-theme";
node("theme").fire("change");
assert.equal(persisted.get(key), before);
windowEvents.get("storage")({key, newValue: JSON.stringify(external)});
assert.equal(node("theme").value, "invalid-theme");
node("import").fire("click");
assert.equal(node("json").value, '{"unfinished":');
assert.equal(persisted.get(key), before);

// Explicit import, export and reset replace drafts only at their existing action boundaries
node("json").value = JSON.stringify(external);
node("import").fire("click");
assert.equal(JSON.parse(persisted.get(key)).preset, "neon1-light");
assert.equal(node("theme").value, "neon1-light");
node("json").value = "draft";
node("json").fire("input");
node("export").fire("click");
assert.equal(JSON.parse(node("json").value).overrides.radius, 8);
node("reset").fire("click");
assert.equal(persisted.has(key), false);
assert.equal(node("theme").value, "system");
assert.equal(node("radius-value").textContent, "10 px");

// Unavailable storage keeps the valid current-session appearance and explains its limited persistence
storageUnavailable = true;
node("radius").value = "6";
node("radius").fire("input");
assert.equal(node("radius-value").textContent, "6 px");
assert.match(node("status").textContent, /session.*storage is unavailable/);
console.log("Appearance controls, accessible radius, drafts and storage failure passed");
