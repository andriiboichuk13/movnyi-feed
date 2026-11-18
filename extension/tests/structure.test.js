// Статична перевірка пакета розширення: manifest, файли, іконки, селектори, порядок скриптів,
// готовність до здачі. Нічого не запускає в браузері, тож ловить «розширення навіть не встановиться».
//
// Перевірки «готовності до здачі» (release) за замовчуванням позначені todo: показують, що ще не зроблено,
// але не валять запуск. Перед здачею журі запустіть із RELEASE=1, тоді вони стають обов'язковими:
//   RELEASE=1 npm test            (Linux/macOS)      $env:RELEASE=1; npm test     (PowerShell)
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const { loadDom, manifest, EXT } = require("./helpers/load-dom");

const read = (rel) => fs.readFileSync(path.join(EXT, rel), "utf8");
const exists = (rel) => fs.existsSync(path.join(EXT, rel));
const STRICT = process.env.RELEASE === "1";
const release = (name, reason, fn) => test(`[release] ${name}`, { todo: STRICT ? false : reason }, fn);

// ---------------------------------------------------------------- manifest
test("manifest: Manifest V3, назва, версія, опис", () => {
  assert.strictEqual(manifest.manifest_version, 3);
  assert.ok(manifest.name && /^\d+\.\d+\.\d+$/.test(manifest.version));
  assert.ok(manifest.description.length > 0 && manifest.description.length <= 132, "опис у Chrome Web Store ≤ 132 символи");
});

test("manifest: усі згадані файли існують", () => {
  const files = [
    manifest.background.service_worker, manifest.action.default_popup,
    ...manifest.content_scripts.flatMap((c) => c.js), ...Object.values(manifest.icons),
  ];
  const missing = files.filter((f) => !exists(f));
  assert.deepStrictEqual(missing, [], `немає файлів: ${missing}`);
});

test("manifest: іконки справжні PNG потрібного розміру", () => {
  for (const [size, rel] of Object.entries(manifest.icons)) {
    const buf = fs.readFileSync(path.join(EXT, rel));
    assert.strictEqual(buf.subarray(1, 4).toString(), "PNG", rel);
    assert.strictEqual(buf.readUInt32BE(16), Number(size), `${rel}: ширина`);
    assert.strictEqual(buf.readUInt32BE(20), Number(size), `${rel}: висота`);
  }
});

test("manifest: дозволи мінімальні (лише storage і alarms), без широких host_permissions", () => {
  assert.deepStrictEqual([...manifest.permissions].sort(), ["alarms", "storage"]);
  assert.ok(!manifest.host_permissions.some((h) => h === "<all_urls>" || h === "*://*/*" || h === "https://*/*"));
});

test("manifest: content script працює лише на https://www.youtube.com", () => {
  assert.deepStrictEqual(manifest.content_scripts[0].matches, ["https://www.youtube.com/*"]);
});

test("manifest: API_BASE з config.js покритий host_permissions (інакше запити мовчки не підуть)", () => {
  const cfg = loadDom({ files: ["src/config.js"] }).run("MF_CONFIG");
  const u = new URL(cfg.API_BASE);
  const covered = manifest.host_permissions.some((p) => {
    const m = p.match(/^(https?|\*):\/\/([^/]+)\/\*$/);
    if (!m) return false;
    const [, scheme, host] = m;
    if (scheme !== "*" && scheme + ":" !== u.protocol) return false;
    return host.startsWith("*.") ? u.hostname.endsWith(host.slice(1)) : host === u.hostname;
  });
  assert.ok(covered, `${cfg.API_BASE} не покрито host_permissions: ${manifest.host_permissions.join(", ")}`);
});

test("manifest: чи не лишилося у host_permissions зайвого сайту; усі https окрім localhost", () => {
  for (const h of manifest.host_permissions) assert.ok(h.startsWith("https://") || h.startsWith("http://localhost/"), h);
});

// ---------------------------------------------------------------- скрипти
test("усі JS-файли розширення проходять перевірку синтаксису", () => {
  const { execFileSync } = require("node:child_process");
  const walk = (d) => fs.readdirSync(path.join(EXT, d), { withFileTypes: true }).flatMap((e) => (e.name === "tests" || e.name === "node_modules" ? [] : e.isDirectory() ? walk(path.join(d, e.name)) : e.name.endsWith(".js") ? [path.join(d, e.name)] : []));
  for (const f of walk("src")) execFileSync(process.execPath, ["--check", path.join(EXT, f)], { stdio: "pipe" });
});

test("порядок content scripts: усе, що потрібне, оголошено до використання", () => {
  const env = loadDom({ withMain: false }); // жодних ReferenceError під час завантаження
  for (const g of ["MF_CONFIG", "MF_DEFAULTS", "MF_SELECTORS", "MF_LANG", "MF", "MF_Panel", "MF_Filter"]) {
    assert.doesNotThrow(() => env.run(g), `${g} не визначено після завантаження скриптів у порядку з manifest`);
  }
  const order = manifest.content_scripts[0].js;
  assert.ok(order.indexOf("src/config.js") < order.indexOf("src/content/main.js"));
  assert.strictEqual(order[order.length - 1], "src/content/main.js", "main.js має йти останнім");
});

test("background.js підтягує існуючі файли через importScripts", () => {
  const m = read("src/background.js").match(/importScripts\(([^)]*)\)/);
  for (const f of [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1])) assert.ok(exists(path.join("src", f)), f);
});

test("MV3: background не тримає стан у змінних верхнього рівня (service worker засинає)", () => {
  const src = read("src/background.js").replace(/\/\/.*$/gm, "");
  const lets = [...src.matchAll(/^(?:let|var)\s+(\w+)/gm)].map((m) => m[1]);
  assert.deepStrictEqual(lets, [], `змінні верхнього рівня зникнуть при засинанні: ${lets}`);
});

test("обробники повідомлень background: кожен тип, що шлють content/popup, має обробник", () => {
  const sentTypes = new Set();
  for (const f of ["src/content/main.js", "src/content/panel.js", "src/content/filter.js"])
    for (const m of read(f).matchAll(/MF\.send\("([A-Za-z]+)"/g)) sentTypes.add(m[1]);
  const handlers = new Set([...read("src/background.js").match(/const HANDLERS = \{([\s\S]*?)\};/)[1].matchAll(/(\w+):/g)].map((m) => m[1]));
  for (const t of sentTypes) assert.ok(handlers.has(t), `немає обробника "${t}" у background.js`);
});

// ---------------------------------------------------------------- селектори
test("селектори: усі валідні CSS (інакше querySelector кидає виняток і все ламається)", () => {
  const { document } = new JSDOM("<body></body>").window;
  const S = loadDom({ files: ["src/selectors.js"] }).run("MF_SELECTORS");
  const all = [];
  (function walk(o) { for (const v of Object.values(o)) Array.isArray(v) ? all.push(...v) : walk(v); })(S);
  assert.ok(all.length > 15);
  for (const sel of all) assert.doesNotThrow(() => document.querySelector(sel), `невалідний селектор: ${sel}`);
});

test("селектори: кожна група — непорожній список", () => {
  const S = loadDom({ files: ["src/selectors.js"] }).run("MF_SELECTORS");
  const check = (o, p = "") => { for (const [k, v] of Object.entries(o)) { if (Array.isArray(v)) assert.ok(v.length > 0, p + k); else check(v, p + k + "."); } };
  check(S);
});

// ---------------------------------------------------------------- пакування
test("папка розширення не містить секретів (.env, ключів, паролів у коді)", () => {
  const bad = [];
  const walk = (d) => { for (const e of fs.readdirSync(path.join(EXT, d), { withFileTypes: true })) {
    if (e.name === "node_modules") continue;
    const rel = path.join(d, e.name);
    if (e.isDirectory()) walk(rel);
    else {
      if (/^\.env/.test(e.name)) bad.push(rel);
      if (/\.(js|json|html)$/.test(e.name)) {
        const txt = read(rel);
        if (/AIza[0-9A-Za-z_-]{30,}/.test(txt) || /postgres(ql)?:\/\/[^\s"']+:[^\s"']+@/.test(txt)) bad.push(rel + " (схоже на ключ/пароль)");
      }
    }
  } };
  walk(".");
  assert.deepStrictEqual(bad, []);
});

// ---------------------------------------------------------------- готовність до здачі
const cfg = () => loadDom({ files: ["src/config.js"] }).run("MF_CONFIG");

release("API_BASE не localhost (журі не запустить ваш ноутбук)", "зараз API_BASE вказує на localhost: у тестувальників/журі розширення мовчки нічого не покаже", () => {
  assert.doesNotMatch(cfg().API_BASE, /localhost|127\.0\.0\.1/);
});
release("USE_MOCK вимкнено", "USE_MOCK=true у збірці для журі покаже вигадані «Заглушка» відео замість справжніх", () => {
  assert.strictEqual(cfg().USE_MOCK, false);
});
release("WEBSITE_URL — справжній сайт, а не example.com", "посилання «Конфіденційність» у popup веде на https://example.com/privacy", () => {
  assert.doesNotMatch(cfg().WEBSITE_URL, /example\.com/);
});
release("у manifest немає localhost у host_permissions", "для публікації в Chrome Web Store localhost краще прибрати", () => {
  assert.ok(!manifest.host_permissions.some((h) => h.includes("localhost")));
});
release("в коді немає console.log з назвами відео (приватність)", "main.js логує назву відео в консоль сторінки — для релізу краще прибрати", () => {
  assert.doesNotMatch(read("src/content/main.js"), /console\.log\([^)\n]*videoTitle/);
});
