// Design review refined reading planes and chart hues while keeping action and status roles fixed.
const presets = [
  {
    id: "neon1-dark",
    label: "Neon 1 Dark",
    scheme: "dark",
    tokens: {
      surfaceCanvas: "#070a08",
      surfacePanel: "#0d130f",
      surfaceRaised: "#121a15",
      surfaceInset: "#060908",
      textPrimary: "#e8efea",
      textMuted: "#b4c3ba",
      textFaint: "#a0b0a6",
      border: "#1f2a23",
      borderStrong: "#35463c",
      brandAction: "#2f7940",
      brandHover: "#286936",
      brandPress: "#20572c",
      onBrand: "#ffffff",
      accent: "#6fdc8a",
      focus: "#8cf0a5",
      statusWarning: "#e0a64a",
      statusError: "#ff8a7a",
      statusUnknown: "#9aa7c7",
      chart1: "#6fdc8a",
      chart2: "#7cb8ff",
      chart3: "#c9a2f2",
      chart4: "#e0a64a",
      chart5: "#ff8a7a",
      radius: 10,
      density: "comfortable",
    },
  },
  {
    id: "neon1-light",
    label: "Neon 1 Light",
    scheme: "light",
    tokens: {
      surfaceCanvas: "#f4f7f4",
      surfacePanel: "#fbfcfb",
      surfaceRaised: "#ffffff",
      surfaceInset: "#e9efea",
      textPrimary: "#17211a",
      textMuted: "#3d5144",
      textFaint: "#53685a",
      border: "#c5d1c8",
      borderStrong: "#9eafa3",
      brandAction: "#2f7940",
      brandHover: "#286936",
      brandPress: "#20572c",
      onBrand: "#ffffff",
      accent: "#286936",
      focus: "#27673a",
      statusWarning: "#805000",
      statusError: "#a42c20",
      statusUnknown: "#485b85",
      chart1: "#286936",
      chart2: "#145d78",
      chart3: "#66539b",
      chart4: "#805000",
      chart5: "#a42c20",
      radius: 10,
      density: "comfortable",
    },
  },
  {
    id: "slate-dark",
    label: "Slate Dark",
    scheme: "dark",
    tokens: {
      surfaceCanvas: "#0b1018",
      surfacePanel: "#121a25",
      surfaceRaised: "#1a2634",
      surfaceInset: "#080d14",
      textPrimary: "#e9eff7",
      textMuted: "#bac7d6",
      textFaint: "#a1b1c5",
      border: "#293746",
      borderStrong: "#40566a",
      brandAction: "#246c89",
      brandHover: "#267b9d",
      brandPress: "#1d5b74",
      onBrand: "#ffffff",
      accent: "#65c8e8",
      focus: "#8cdbf2",
      statusWarning: "#e7b65b",
      statusError: "#ff8d87",
      statusUnknown: "#aeb8cd",
      chart1: "#65c8e8",
      chart2: "#9dafff",
      chart3: "#bca4f4",
      chart4: "#e7b65b",
      chart5: "#ff8d87",
      radius: 10,
      density: "comfortable",
    },
  },
  {
    id: "sand-light",
    label: "Sand Light",
    scheme: "light",
    tokens: {
      surfaceCanvas: "#f7f3eb",
      surfacePanel: "#fbf8f1",
      surfaceRaised: "#fffdf8",
      surfaceInset: "#eee7da",
      textPrimary: "#27251f",
      textMuted: "#514c40",
      textFaint: "#625b4d",
      border: "#d8cfbf",
      borderStrong: "#b7aa94",
      brandAction: "#68502d",
      brandHover: "#594326",
      brandPress: "#49371f",
      onBrand: "#ffffff",
      accent: "#68502d",
      focus: "#594326",
      statusWarning: "#765000",
      statusError: "#9c3124",
      statusUnknown: "#4e5d7d",
      chart1: "#68502d",
      chart2: "#28604f",
      chart3: "#62528a",
      chart4: "#765000",
      chart5: "#9c3124",
      radius: 10,
      density: "comfortable",
    },
  },
  {
    id: "forest-light",
    label: "Forest Light",
    scheme: "light",
    tokens: {
      surfaceCanvas: "#f4f7f4",
      surfacePanel: "#fbfcfb",
      surfaceRaised: "#ffffff",
      surfaceInset: "#e9efea",
      textPrimary: "#101010",
      textMuted: "#3d5144",
      textFaint: "#53685a",
      border: "#c5d1c8",
      borderStrong: "#9eafa3",
      brandAction: "#107107",
      brandHover: "#327641",
      brandPress: "#035704",
      onBrand: "#ffffff",
      accent: "#035704",
      focus: "#100795",
      statusWarning: "#805000",
      statusError: "#951007",
      statusUnknown: "#485b85",
      chart1: "#007a78",
      chart2: "#2855a6",
      chart3: "#951007",
      chart4: "#805000",
      chart5: "#66539b",
      radius: 10,
      density: "comfortable",
    },
  },
  {
    id: "matrix-dark",
    label: "Matrix Dark",
    scheme: "dark",
    tokens: {
      surfaceCanvas: "#0d0208",
      surfacePanel: "#101010",
      surfaceRaised: "#0d1b10",
      surfaceInset: "#080a08",
      textPrimary: "#e8efea",
      textMuted: "#b4c3ba",
      textFaint: "#a0b0a6",
      border: "#24402a",
      borderStrong: "#47664f",
      brandAction: "#107107",
      brandHover: "#327641",
      brandPress: "#035704",
      onBrand: "#ffffff",
      accent: "#03a062",
      focus: "#00ff41",
      statusWarning: "#e0a64a",
      statusError: "#ff8a7a",
      statusUnknown: "#9aa7c7",
      chart1: "#00a878",
      chart2: "#4da3ff",
      chart3: "#ff726f",
      chart4: "#e0a64a",
      chart5: "#bc9cff",
      radius: 10,
      density: "comfortable",
    },
  },
];

for (const preset of presets) {
  Object.freeze(preset.tokens);
  Object.freeze(preset);
}
Object.freeze(presets);

export { presets };

export const defaultPreference = Object.freeze({
  version: 1,
  mode: "system",
  preset: "neon1-dark",
  overrides: Object.freeze({}),
});

const presetById = new Map(presets.map((preset) => [preset.id, preset]));
const colorKeys = new Set([
  "brandAction",
  "brandHover",
  "brandPress",
  "accent",
  "focus",
]);
const overrideKeys = new Set([...colorKeys, "radius", "density"]);
const tokenKeys = [
  "surfaceCanvas",
  "surfacePanel",
  "surfaceRaised",
  "surfaceInset",
  "textPrimary",
  "textMuted",
  "textFaint",
  "border",
  "borderStrong",
  "brandAction",
  "brandHover",
  "brandPress",
  "onBrand",
  "accent",
  "focus",
  "statusWarning",
  "statusError",
  "statusUnknown",
  "chart1",
  "chart2",
  "chart3",
  "chart4",
  "chart5",
  "radius",
  "density",
];

function fail(message) {
  throw new Error(`Invalid theme preference: ${message}`);
}

function isRecord(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}

function checkKeys(value, allowed, label) {
  for (const key of Object.keys(value))
    if (!allowed.has(key)) fail(`unknown ${label} key “${key}”`);
}

function checkOverride(key, value) {
  if (colorKeys.has(key)) {
    if (typeof value !== "string" || !/^#[0-9a-fA-F]{6}$/.test(value))
      fail(`${key} must be a #RRGGBB color`);
  } else if (key === "radius") {
    if (
      typeof value !== "number" ||
      !Number.isFinite(value) ||
      value < 0 ||
      value > 20
    )
      fail("radius must be a number from 0 to 20 pixels");
  } else if (
    key === "density" &&
    value !== "comfortable" &&
    value !== "compact"
  ) {
    fail("density must be “comfortable” or “compact”");
  }
}

export function parsePreference(input) {
  let value = input;
  if (typeof input === "string") {
    if (input.length > 8192)
      fail("JSON input exceeds the 8192 character limit");
    try {
      value = JSON.parse(input);
    } catch {
      fail("provide valid JSON or a preference object");
    }
  }
  if (!isRecord(value)) fail("expected an object");
  checkKeys(
    value,
    new Set(["version", "mode", "preset", "overrides"]),
    "preference",
  );
  if (value.version !== 1) fail("version must be 1");
  if (value.mode !== "system" && value.mode !== "preset")
    fail("mode must be “system” or “preset”");
  if (typeof value.preset !== "string" || !presetById.has(value.preset))
    fail(`preset must be one of ${[...presetById.keys()].join(", ")}`);
  const overrides = value.overrides ?? {};
  if (!isRecord(overrides)) fail("overrides must be an object");
  checkKeys(overrides, overrideKeys, "override");
  for (const [key, override] of Object.entries(overrides))
    checkOverride(key, override);
  const parsed = {
    version: 1,
    mode: value.mode,
    preset: value.preset,
    overrides: { ...overrides },
  };
  const candidates =
    parsed.mode === "system" ? ["neon1-dark", "neon1-light"] : [parsed.preset];
  for (const id of candidates)
    validateContrast({ ...presetById.get(id).tokens, ...parsed.overrides }, id);
  return parsed;
}

function luminance(hex) {
  const channels = hex
    .slice(1)
    .match(/../g)
    .map((part) => parseInt(part, 16) / 255)
    .map((channel) =>
      channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
    );
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

export function contrastRatio(foreground, background) {
  if (
    typeof foreground !== "string" ||
    !/^#[0-9a-fA-F]{6}$/.test(foreground) ||
    typeof background !== "string" ||
    !/^#[0-9a-fA-F]{6}$/.test(background)
  ) {
    throw new Error("contrastRatio expects two #RRGGBB colors");
  }
  const values = [luminance(foreground), luminance(background)].sort(
    (a, b) => b - a,
  );
  return (values[0] + 0.05) / (values[1] + 0.05);
}

function validateContrast(tokens, presetId) {
  const surfaces = [
    "surfaceCanvas",
    "surfacePanel",
    "surfaceRaised",
    "surfaceInset",
  ];
  for (const text of ["textPrimary", "textMuted", "textFaint"]) {
    for (const surface of surfaces) {
      if (contrastRatio(tokens[text], tokens[surface]) < 4.5)
        fail(`${presetId}: ${text} needs 4.5:1 contrast on ${surface}`);
    }
  }
  for (const action of ["brandAction", "brandHover", "brandPress"]) {
    if (contrastRatio(tokens.onBrand, tokens[action]) < 4.5)
      fail(`${presetId}: onBrand needs 4.5:1 contrast on ${action}`);
  }
  for (const surface of surfaces) {
    if (contrastRatio(tokens.accent, tokens[surface]) < 4.5)
      fail(`${presetId}: accent needs 4.5:1 contrast on ${surface}`);
    if (contrastRatio(tokens.focus, tokens[surface]) < 3)
      fail(`${presetId}: focus needs 3:1 contrast on ${surface}`);
  }
}

export function resolveTheme(
  preference = defaultPreference,
  darkSystem = false,
) {
  const parsed = parsePreference(preference);
  const selectedId =
    parsed.mode === "system"
      ? darkSystem
        ? "neon1-dark"
        : "neon1-light"
      : parsed.preset;
  const base = presetById.get(selectedId);
  const tokens = { ...base.tokens, ...parsed.overrides };
  validateContrast(tokens, base.id);
  return { id: base.id, label: base.label, scheme: base.scheme, tokens };
}

export function cssVariables(theme) {
  const tokens = theme?.tokens;
  if (!isRecord(tokens) || tokenKeys.some((key) => !(key in tokens)))
    throw new Error("cssVariables expects a resolved theme with all tokens");
  const variables = {};
  for (const key of tokenKeys)
    variables[
      `--theme-${key.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}`
    ] = key === "radius" ? `${tokens[key]}px` : tokens[key];
  Object.assign(variables, {
    "--bg": tokens.surfaceCanvas,
    "--rail": tokens.surfaceCanvas,
    "--panel": tokens.surfacePanel,
    "--panel-raised": tokens.surfaceRaised,
    "--term": tokens.surfaceInset,
    "--neon1": tokens.brandAction,
    "--neon1-hover": tokens.brandHover,
    "--neon1-press": tokens.brandPress,
    "--neon-text": tokens.accent,
    "--neon-focus": tokens.focus,
    "--text": tokens.textPrimary,
    "--muted": tokens.textMuted,
    "--faint": tokens.textFaint,
    "--line": tokens.border,
    "--line-strong": tokens.borderStrong,
    "--warn": tokens.statusWarning,
    "--error": tokens.statusError,
    "--unknown": tokens.statusUnknown,
    "--radius": `${tokens.radius}px`,
  });
  Object.assign(variables, {
    "--on-brand": tokens.onBrand,
    "--accent": tokens.accent,
    "--font":
      'system-ui, -apple-system, "SF Pro Text", "Segoe UI", Roboto, sans-serif',
    "--mono":
      'ui-monospace, "SF Mono", "Cascadia Mono", "Roboto Mono", Menlo, Consolas, monospace',
  });
  for (let index = 1; index <= 5; index++)
    variables[`--chart-${index}`] = tokens[`chart${index}`];
  variables["--bg-0"] =
    theme.id === "neon1-dark" ? "#040605" : tokens.surfaceInset;
  ["surfaceCanvas", "surfacePanel", "surfaceRaised"].forEach((key, index) => {
    variables[`--bg-${index + 1}`] = tokens[key];
  });
  return variables;
}
