// Перевіряє сам інструмент перевірки селекторів: він актуальний і справді працює на фікстурах.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { execFileSync } = require("node:child_process");
const { loadDom, homeCard, homePage, sidebarCard, EXT } = require("./helpers/load-dom");

const SNIPPET = path.join(__dirname, "tools", "selectors-check.console.js");

test("selectors-check.console.js актуальний (перегенеруйте: node tools/build-selectors-check.js)", () => {
  const before = fs.readFileSync(SNIPPET, "utf8");
  execFileSync(process.execPath, [path.join(__dirname, "tools", "build-selectors-check.js")], { stdio: "pipe" });
  assert.strictEqual(fs.readFileSync(SNIPPET, "utf8"), before, "selectors.js змінився, а скрипт для консолі ні");
});

function runSnippet(html, url) {
  const env = loadDom({ html, url, files: [] });
  const tables = [];
  env.window.console.table = (rows) => tables.push(rows);
  const rows = vm.runInContext(fs.readFileSync(SNIPPET, "utf8"), env.dom.getInternalVMContext());
  return rows;
}

test("на головній з фікстурою: усі рядки OK", () => {
  const rows = runSnippet(homePage([homeCard("Як приготувати борщ")]), "https://www.youtube.com/");
  assert.ok(rows.length >= 4);
  assert.deepEqual(rows.filter((r) => r.статус !== "OK"), [], JSON.stringify(rows));
});

test("якщо YouTube змінив верстку (карток немає), скрипт чесно каже НЕ ЗНАЙДЕНО", () => {
  const rows = runSnippet("<body><div>порожньо</div></body>", "https://www.youtube.com/");
  assert.strictEqual(rows[0].статус, "НЕ ЗНАЙДЕНО");
});

test("на сторінці відео перевіряє panelHost, назву і сайдбар", () => {
  const html = `<body><ytd-watch-flexy video-id="x"><ytd-watch-metadata><h1><yt-formatted-string>Назва</yt-formatted-string></h1></ytd-watch-metadata></ytd-watch-flexy><div id="secondary"><div id="secondary-inner"><div id="related">${sidebarCard("Назва")}</div></div></div></body>`;
  const rows = runSnippet(html, "https://www.youtube.com/watch?v=dQw4w9WgXcQ");
  const names = rows.map((r) => r.група);
  for (const n of ["panelHost", "watchFlexy", "watchTitle", "cards.sidebar"]) assert.ok(names.includes(n), n);
  assert.strictEqual(rows.find((r) => r.група === "panelHost").статус, "OK");
});

test("feed-report.console.js працює на фікстурі й показує стан карток після фільтрації", () => {
  const { homeCard, homePage, sleep } = require("./helpers/load-dom");
  const env = loadDom({ html: homePage([homeCard("Что будет, если смешать колу и ментос? Эксперимент", "aaaaaaaaaaa"), homeCard("Як приготувати борщ", "bbbbbbbbbbb")]) });
  env.run("MF_Filter").setActive(true);
  const rows = vm.runInContext(fs.readFileSync(path.join(__dirname, "tools", "feed-report.console.js"), "utf8"), env.dom.getInternalVMContext());
  assert.deepEqual(rows.map((r) => [r.стан, r.lang, r.videoId]), [["masked", "ru", "aaaaaaaaaaa"], ["shown", "uk", "bbbbbbbbbbb"]]);
});
