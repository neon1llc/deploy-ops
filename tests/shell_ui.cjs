// tests/shell_ui.cjs - Exercise retained navigation and real settings discovery without browser automation.


const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");


// Keep native node identity and event handlers observable across every view
class Node {
  // Retain the small DOM surface used by the shipped shell and settings forms
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.attributes = {};
    this.listeners = new Map(); this.hidden = false; this.disabled = false; this.open = false;
    this.parentElement = null; this._text = ""; this._value = undefined;
  }

  // Resolve text from descendants as native label and summary searches do
  get textContent() { return this._text + this.children.map(node => node.textContent).join(""); }
  set textContent(value) { this._text = String(value); this.replaceChildren(); }

  // Select the first option until an explicit value is chosen
  get value() { return this._value ?? (this.tagName === "SELECT" ? this.children[0]?.value || "" : ""); }
  set value(value) { this._value = String(value); }

  // Attach existing nodes without cloning their fields or drafts
  append(...nodes) {
    for (const node of nodes) { node.parentElement = this; this.children.push(node); }
  }

  // Remove old nodes only when the actual renderer requests it
  replaceChildren(...nodes) {
    for (const node of this.children) node.parentElement = null;
    this.children = [];
    if (this.tagName === "SELECT") this._value = undefined;
    this.append(...nodes);
  }

  // Preserve attribute and dataset semantics used by the shell
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = value;
    else if (["hidden", "disabled", "open"].includes(name)) this[name] = true;
    else this[name] = value;
  }
  removeAttribute(name) { delete this.attributes[name]; }

  // Keep map-row operations and browser validity state observable
  get lastElementChild() { return this.children.at(-1) || null; }
  setCustomValidity(message) { this.validationMessage = message; }
  remove() {
    this.parentElement.children = this.parentElement.children.filter(node => node !== this);
    this.parentElement = null;
  }

  // Match only native selectors exercised by these production handlers
  matches(selector) {
    const attribute = selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
    if (attribute?.[1].startsWith("data-")) {
      const key = attribute[1].slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      return key in this.dataset && (attribute[2] === undefined || this.dataset[key] === attribute[2]);
    }
    if (attribute) return attribute[1] in this.attributes || attribute[1] in this
      ? attribute[2] === undefined || String(this.attributes[attribute[1]] ?? this[attribute[1]]) === attribute[2] : false;
    if (selector === "input:checked") return this.tagName === "INPUT" && this.checked;
    return this.tagName.toLowerCase() === selector;
  }
  querySelectorAll(selector) {
    const choices = selector.split(",").map(value => value.trim());
    return this.children.flatMap(node => [...(choices.some(value => node.matches(value)) ? [node] : []), ...node.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }

  // Dispatch native and custom events without introducing navigation side effects
  addEventListener(type, handler) {
    const values = this.listeners.get(type) || []; values.push(handler); this.listeners.set(type, values);
  }
  dispatchEvent(event) {
    event.target ||= this; event.preventDefault ||= () => {};
    for (const handler of this.listeners.get(event.type) || []) handler(event);
    this["on" + event.type]?.(event);
    return true;
  }
  focus() { this.document.activeElement = this; }
}


// Build retained nodes from the shipped HTML rather than a separate hand-maintained view inventory
function documentFromHtml() {
  const document = new Node("document"); const stack = [document];
  const voids = new Set(["meta", "link", "input", "br", "hr", "img"]);
  for (const token of fs.readFileSync("src/deploy_ops/ui/index.html", "utf8").match(/<!--[\s\S]*?-->|<[^>]+>|[^<]+/g)) {
    if (token.startsWith("<!")) continue;
    if (token.startsWith("</")) { stack.pop(); continue; }
    if (token.startsWith("<")) {
      const tag = token.match(/^<([\w-]+)/)[1]; const node = new Node(tag); node.document = document;
      const attributes = token.slice(tag.length + 1, -1);
      for (const match of attributes.matchAll(/([^\s=]+)(?:="([^"]*)"|'([^']*)'|=([^\s]+))?/g)) {
        node.setAttribute(match[1], match[2] ?? match[3] ?? match[4] ?? "");
      }
      stack.at(-1).append(node);
      if (!voids.has(tag)) stack.push(node);
    } else stack.at(-1)._text += token;
  }
  document.getElementById = id => document.querySelectorAll("[id]").find(node => node.id === id) || null;
  document.createElement = tag => { const node = new Node(tag); node.document = document; return node; };
  document.createTextNode = value => { const node = document.createElement("text"); node.textContent = value; return node; };
  return document;
}


// Queue hashchange separately so focus assertions catch later navigation regressions
function harness(protocol = "http:") {
  const document = documentFromHtml(); const window = new Node("window"); const pending = [];
  let hash = "";
  const location = {protocol, get hash() { return hash; }, set hash(value) {
    const next = value.startsWith("#") ? value : "#" + value;
    if (next !== hash) { hash = next; pending.push(() => window.dispatchEvent({type: "hashchange"})); }
  }};
  const context = vm.createContext({document, window, location, console, structuredClone,
    CSS: {escape: value => value}, CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    Option: function(label, value) { const node = document.createElement("option"); node.textContent = label; node.value = value; return node; }});
  vm.runInContext(fs.readFileSync("src/deploy_ops/ui/shell.js", "utf8"), context);
  return {document, window, location, context, flush() { while (pending.length) pending.shift()(); }, node: id => document.getElementById(id)};
}


// Keep original form nodes and values through every sidebar destination
const app = harness();
const draft = app.node("config"); draft.value = "unsaved TOML";
const image = app.node("image"); image.value = "registry.invalid/app@sha256:draft";
const incident = app.node("incident-note"); incident.value = "ongoing investigation";
const forms = app.document.querySelectorAll("form");
for (const link of app.document.querySelectorAll("[data-view-link]")) {
  link.dispatchEvent({type: "click"}); app.flush();
  assert.ok(app.document.querySelectorAll("form").every((form, index) => form === forms[index]));
  assert.equal(app.node("config"), draft); assert.equal(draft.value, "unsaved TOML");
  assert.equal(app.node("image"), image); assert.match(image.value, /draft$/);
  assert.equal(app.node("incident-note"), incident); assert.equal(incident.value, "ongoing investigation");
  assert.equal(link.attributes["aria-current"], "page");
  assert.equal(app.document.activeElement, app.node("view-title"));
  assert.equal(app.document.querySelectorAll("[data-view]").filter(view => !view.hidden).length, 1);
}

// Treat inherited object property names as unknown destinations rather than executable view entries
for (const name of ["constructor", "__proto__", "missing-view"]) {
  app.location.hash = name; app.flush();
  assert.equal(app.node("view-title").textContent, "Home");
  const before = app.location.hash;
  app.document.dispatchEvent({type: "deployops:navigate", detail: {view: name}});
  app.flush(); assert.equal(app.location.hash, before);
  assert.equal(draft.value, "unsaved TOML");
}

// Source overview counts from recorded facts without claiming provider health
app.document.dispatchEvent({type: "deployops:workspace", detail: {name: "Fixture", environments: {dev: {}, prod: {}}, components: {web: {}, base: {}, jobs: {}}}});
app.document.dispatchEvent({type: "deployops:releases", detail: [{status: "succeeded"}, {status: "uncertain"}, {status: "waiting"}, {status: "failed"}]});
assert.equal(app.node("environment-count").textContent, "2");
assert.equal(app.node("component-count").textContent, "3");
assert.equal(app.node("release-count").textContent, "4");
assert.equal(app.node("release-summary").textContent, "3 waiting, failed or uncertain");
assert.equal(app.node("session-label").textContent, "Fixture");
assert.equal(app.node("unlock").hidden, true);

// Load the real settings router so a search result must select and focus its actual target field
const contract = {forms: {
  repositories: {path: {title: "Source path", type: "string"}},
  environments: {production: {title: "Production protection", type: "boolean", description: "Require explicit production review"}},
  components: {adapter: {title: "Deployment adapter", type: "string"},
    needs: {title: "Deployment dependencies", type: "array", items: {enum: ["web", "base", "jobs"]}}},
}, configuration: {repositories: {app: {path: "source"}}, environments: {dev: {production: false}, prod: {production: true}}, components: {web: {adapter: "ecs", needs: ["base"]}}},
option_forms: {foundation: {adapter: "cloudformation", component: "base", environment: "dev", required: [],
  fields: {stack_name: {title: "Stack name", type: "string", description: "CloudFormation identity"},
    parameters: {title: "Public parameters", type: "object", additionalProperties: {anyOf: [{type: "string"}, {type: "integer"}, {type: "boolean"}]}},
    capabilities: {title: "Stack capabilities", type: "array", items: {enum: ["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"]}}},
  values: {stack_name: "original", parameters: {Enabled: true, Count: 3, Label: "alpha"}, capabilities: ["CAPABILITY_IAM"]}}}};
app.context.contract = contract;
vm.runInContext('const element=id=>document.getElementById(id); const formDrafts=new Map(); let formContract=contract; function text(parent,tag,value){const node=document.createElement(tag);node.textContent=value;parent.append(node);return node;}', app.context);
const source = fs.readFileSync("src/deploy_ops/ui/app.js", "utf8");
vm.runInContext(source.slice(source.indexOf("// Keep drafts separate for every settings group"), source.indexOf("// Reload forms only from the exact")), app.context);
app.node("config-section").value = "repositories";
vm.runInContext("fillConfigNames()", app.context);
app.document.dispatchEvent({type: "deployops:configuration", detail: contract});
const path = app.node("setting-path"); path.value = "edited source"; path.dispatchEvent({type: "input"});
app.node("settings-search").value = "production protection"; app.node("settings-search").dispatchEvent({type: "input"});
assert.equal(app.node("settings-matches").children.length, 2);
const production = app.node("settings-matches").children.find(node => node.textContent.includes("/ prod /"));
assert.ok(production); production.dispatchEvent({type: "click"});
assert.equal(app.node("config-section").value, "environments"); assert.equal(app.node("config-name").value, "prod");
assert.equal(app.document.activeElement, app.node("setting-production")); assert.equal(app.node("setting-production").checked, true);
assert.equal(draft.value, "unsaved TOML");

// Find native fields hidden in advanced settings and restore a different entry's saved form draft
app.node("settings-search").value = "CloudFormation identity"; app.node("settings-search").dispatchEvent({type: "input"});
assert.equal(app.node("settings-matches").children.length, 1);
app.node("settings-matches").children[0].dispatchEvent({type: "click"});
assert.equal(app.node("config-section").value, "options"); assert.equal(app.node("config-name").value, "foundation");
assert.equal(app.document.activeElement, app.node("option-stack_name")); assert.equal(app.node("option-stack_name").parentElement.open, true);
app.node("settings-search").value = "Source path"; app.node("settings-search").dispatchEvent({type: "input"});
app.node("settings-matches").children[0].dispatchEvent({type: "click"});
assert.equal(app.node("setting-path").value, "edited source");
app.node("settings-search").value = "no-such-setting"; app.node("settings-search").dispatchEvent({type: "input"});
assert.equal(app.node("settings-matches").children.length, 0);
assert.match(app.node("settings-results").textContent, /^0 workspace settings and 0 groups/);

// Follow the actual search results rather than calling the field renderer directly
function searchField(query, id) {
  app.node("settings-search").value = query;
  app.node("settings-search").dispatchEvent({type: "input"});
  assert.equal(app.node("settings-matches").children.length, 1);
  app.node("settings-matches").children[0].dispatchEvent({type: "click"});
  const field = app.node(id);
  assert.equal(app.document.activeElement, field);
  return field;
}


// Editing an unrelated text row must never coerce retained boolean or integer values
let parameters = searchField("Public parameters", "option-parameters");
assert.equal(parameters.tabIndex, -1); assert.equal(parameters.parentElement.open, true);
const row = name => parameters.querySelectorAll('[data-map-name]').find(input => input.value === name).parentElement.parentElement;
const scalar = name => row(name).querySelector('[data-map-value]');
const readParameters = () => JSON.parse(vm.runInContext('JSON.stringify(formDrafts.get("options/foundation").parameters)', app.context));
assert.equal(scalar("Enabled").tagName, "SELECT");
assert.equal(scalar("Enabled").value, "true");
assert.equal(scalar("Count").tagName, "INPUT");
assert.equal(scalar("Count").type, "number"); assert.equal(scalar("Count").step, "1");
assert.equal(scalar("Count").required, true);
scalar("Label").value = "beta"; scalar("Label").dispatchEvent({type: "input"});
assert.deepEqual(readParameters(), {Enabled: true, Count: 3, Label: "beta"});
scalar("Enabled").value = "false"; scalar("Enabled").dispatchEvent({type: "input"});
scalar("Count").value = "12"; scalar("Count").dispatchEvent({type: "input"});
assert.deepEqual(readParameters(), {Enabled: false, Count: 12, Label: "beta"});

// An explicit type change uses a new native control while preserving every other row
const chooser = row("Label").querySelector('[name="parameters-type"]');
chooser.value = "boolean"; chooser.dispatchEvent({type: "change"});
assert.equal(scalar("Label").tagName, "SELECT");
assert.deepEqual(readParameters(), {Enabled: false, Count: 12, Label: false});
chooser.value = "integer"; chooser.dispatchEvent({type: "change"});
assert.equal(scalar("Label").type, "number");
scalar("Label").value = "7"; scalar("Label").dispatchEvent({type: "input"});
assert.deepEqual(readParameters(), {Enabled: false, Count: 12, Label: 7});

// Search map and list fieldsets directly, including collapsed native options and ordinary dependencies
const capabilities = searchField("Stack capabilities", "option-capabilities");
assert.equal(capabilities.tagName, "FIELDSET"); assert.equal(capabilities.tabIndex, -1);
assert.equal(capabilities.parentElement.open, true);
const capability = capabilities.querySelectorAll("input").find(input => input.value === "CAPABILITY_NAMED_IAM");
capability.checked = true; capability.dispatchEvent({type: "change"});
assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(formDrafts.get("options/foundation").capabilities)', app.context)),
  ["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"]);
const dependencies = searchField("Deployment dependencies", "setting-needs");
assert.equal(dependencies.tagName, "FIELDSET"); assert.equal(dependencies.tabIndex, -1);
assert.deepEqual(dependencies.querySelectorAll("input").map(input => input.value), ["base", "jobs"]);
const jobs = dependencies.querySelectorAll("input").find(input => input.value === "jobs");
jobs.checked = true; jobs.dispatchEvent({type: "change"});
assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(formDrafts.get("components/web").needs)', app.context)), ["base", "jobs"]);
parameters = searchField("Public parameters", "option-parameters");
assert.deepEqual(readParameters(), {Enabled: false, Count: 12, Label: 7});
assert.equal(scalar("Enabled").tagName, "SELECT"); assert.equal(scalar("Enabled").value, "false");
assert.equal(scalar("Label").type, "number"); assert.equal(scalar("Label").value, "7");

// Preserve public map names that overlap JavaScript prototype keys without changing their types
const prototypeName = row("Label").querySelector('[data-map-name]');
prototypeName.value = "__proto__"; prototypeName.dispatchEvent({type: "input"});
const constructorName = row("Count").querySelector('[data-map-name]');
constructorName.value = "constructor"; constructorName.dispatchEvent({type: "input"});
const namedValues = readParameters();
assert.equal(Object.hasOwn(namedValues, "__proto__"), true);
assert.equal(Object.hasOwn(namedValues, "constructor"), true);
assert.deepEqual(namedValues, JSON.parse('{"Enabled":false,"constructor":12,"__proto__":7}'));
parameters = searchField("Public parameters", "option-parameters");
assert.equal(scalar("__proto__").type, "number"); assert.equal(scalar("__proto__").value, "7");
assert.equal(scalar("constructor").type, "number"); assert.equal(scalar("constructor").value, "12");

// Contextual navigation keeps feature-selected field focus after the queued hashchange
app.document.dispatchEvent({type: "deployops:navigate", detail: {view: "releases", compose: true}});
image.focus(); app.flush();
assert.equal(app.document.activeElement, image); assert.equal(app.node("release-composer").open, true);
assert.equal(image.value, "registry.invalid/app@sha256:draft");

// Direct file previews expose launch help while credentials and unavailable application controls stay hidden
const file = harness("file:");
assert.equal(file.node("file-help").hidden, false); assert.equal(file.node("unlock").hidden, true);
for (const control of file.node("login").querySelectorAll("input, button")) assert.equal(control.disabled, true);
for (const link of file.document.querySelectorAll("[data-view-link]")) {
  link.dispatchEvent({type: "click"}); file.flush();
  assert.equal(file.node("workspace").hidden, true);
  assert.equal(file.node("unlock").hidden, true);
  assert.equal(file.node("appearance-view").hidden, true);
}
assert.equal(file.node("appearance-view").hidden, true);
assert.equal(file.node("session-label").textContent, "Server not connected");
console.log("Shell navigation, complete settings discovery, retained drafts, facts and file preview passed");


// Check creation request types, retained drafts and failure recovery through the shipped handlers
(async () => {
  contract.creation_forms = {
    repositories: {section: "repositories", values: {}, required: ["path"], fields: {path: {type: "string", title: "Path"}}},
    static: {section: "components", values: {adapter: "static", kind: "application"}, required: ["repository"], fields: {
      repository: {type: "string", title: "Repository", enum: ["app"]},
      "build.command": {type: "array", title: "Build arguments", items: {type: "string"}},
      "build.checks": {type: "array", title: "Checks", items: {type: "array"}},
      "options.directory": {type: "string", title: "Artifact directory", description: "Inside the component source"},
    }},
  };
  const calls = [];
  app.context.capture = async (path, method, body) => { calls.push({path, method, body}); throw new Error("duplicate name"); };
  vm.runInContext('const creationDrafts=new Map(); let formSource="unsaved TOML"; let reviewedConfiguration=null; const savedSourceHash="original-saved"; let task; const safe=action=>{task=action().catch(error=>error);}; const api=(...args)=>capture(...args);', app.context);
  app.node("config-add-type").value = "repositories"; app.node("config-add-type").dispatchEvent({type: "change"});
  app.node("config-add-name").value = "unfinished-repo"; app.node("config-add-name").dispatchEvent({type: "input"});
  app.node("create-path").value = "new-source"; app.node("create-path").dispatchEvent({type: "input"});
  app.node("config-add-type").value = "static"; app.node("config-add-type").dispatchEvent({type: "change"});
  app.node("config-add-name").value = "new-site"; app.node("config-add-name").dispatchEvent({type: "input"});
  for (const [id, value] of [["create-repository", "app"], ["create-build-command", '["python3","build.py"]'],
    ["create-build-checks", '[["python3","check.py"]]'], ["create-options-directory", "dist"]]) {
    app.node(id).value = value; app.node(id).dispatchEvent({type: "input"});
  }
  assert.equal(app.node("create-build-command").tagName, "TEXTAREA");
  assert.equal(app.node("create-options-directory").attributes["aria-describedby"], "create-options-directory-help");
  app.node("config-add-form").dispatchEvent({type: "submit"}); await vm.runInContext("task", app.context);
  assert.equal(calls.length, 1); assert.equal(calls[0].path, "/config/add");
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0].body.values)), {adapter: "static", kind: "application", repository: "app",
    build: {command: ["python3", "build.py"], checks: [["python3", "check.py"]]}, options: {directory: "dist"}});
  assert.equal(draft.value, "unsaved TOML"); assert.equal(app.node("config-add-name").value, "new-site");
  assert.equal(app.node("config-add-controls").disabled, false); assert.equal(app.document.activeElement, app.node("config-status"));
  assert.match(app.node("config-status").textContent, /duplicate name/);

  // A successful retry changes only the TOML draft, preserving other creation and edit drafts
  app.context.capture = async () => ({...contract, toml: "expanded draft", diff: "+new-site"});
  app.node("config-add-form").dispatchEvent({type: "submit"}); await vm.runInContext("task", app.context);
  assert.equal(draft.value, "expanded draft"); assert.equal(vm.runInContext("savedSourceHash", app.context), "original-saved");
  assert.equal(app.node("config-add-name").value, "");
  assert.equal(vm.runInContext('formDrafts.get("repositories/app").path', app.context), "edited source");
  app.node("config-add-type").value = "repositories"; app.node("config-add-type").dispatchEvent({type: "change"});
  assert.equal(app.node("config-add-name").value, "unfinished-repo"); assert.equal(app.node("create-path").value, "new-source");
  assert.equal(app.node("config-diff").textContent, "+new-site");
  console.log("Creation DOM: typed requests, failure recovery and independent draft retention passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
