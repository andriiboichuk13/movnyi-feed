// Піднімає jsdom і виконує в ньому справжні скрипти розширення в тому ж порядку, що й manifest.json.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { JSDOM } = require("jsdom");
const { makeChrome } = require("./chrome-mock");

const EXT = path.join(__dirname, "..", "..");
const manifest = JSON.parse(fs.readFileSync(path.join(EXT, "manifest.json"), "utf8"));
const ALL_FILES = manifest.content_scripts[0].js;
const NO_MAIN = ALL_FILES.filter((f) => !f.endsWith("main.js"));

function loadDom({ html = "<body></body>", url = "https://www.youtube.com/", files, chromeOpts = {}, overrides = {}, withMain = false } = {}) {
  const dom = new JSDOM(html, { url, runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom;
  // jsdom не рахує розміри, тому «видимим» вважаємо все.
  window.Element.prototype.getClientRects = function () { return [{}]; };
  const cm = makeChrome(chromeOpts);
  window.chrome = cm.chrome;
  if (!window.TextEncoder) window.TextEncoder = TextEncoder;
  try { Object.defineProperty(window, "crypto", { value: require("node:crypto").webcrypto, configurable: true }); } catch (e) { /* ignore */ }
  const ctx = dom.getInternalVMContext();
  const logs = [];
  const log = (...a) => logs.push(a.join(" "));
  window.console = { log, warn: log, error: log, table: () => {} };
  const list = files || (withMain ? ALL_FILES : NO_MAIN);
  for (const f of list) {
    new vm.Script(fs.readFileSync(path.join(EXT, f), "utf8"), { filename: f }).runInContext(ctx);
  }
  for (const [k, v] of Object.entries(overrides)) vm.runInContext(`MF_CONFIG.${k} = ${JSON.stringify(v)}`, ctx);
  return { dom, window, document: window.document, ctx, cm, logs, run: (code) => vm.runInContext(code, ctx) };
}

// Генератори карток у верстці YouTube (стара і нова).
let n = 0;
const vid = () => `vid${String(++n).padStart(8, "0")}`; // рівно 11 символів
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");

function homeCard(title, id = vid(), { newLayout = false } = {}) {
  return newLayout
    ? `<ytd-rich-item-renderer><yt-lockup-view-model><a href="/watch?v=${id}" class="yt-lockup-view-model__content-image"></a><h3><a class="yt-lockup-metadata-view-model__title" href="/watch?v=${id}" aria-label="${esc(title)}"><span>${esc(title)}</span></a></h3></yt-lockup-view-model></ytd-rich-item-renderer>`
    : `<ytd-rich-item-renderer><a id="thumbnail" href="/watch?v=${id}"></a><h3><a id="video-title" title="${esc(title)}">${esc(title)}</a></h3><ytd-channel-name><a>Канал</a></ytd-channel-name></ytd-rich-item-renderer>`;
}
const homePage = (cards) => `<body><ytd-browse page-subtype="home"><div id="contents">${cards.join("")}</div></ytd-browse></body>`;
const sidebarCard = (title, id = vid()) => `<ytd-compact-video-renderer><a id="thumbnail" href="/watch?v=${id}"></a><span id="video-title" title="${esc(title)}">${esc(title)}</span></ytd-compact-video-renderer>`;
const searchCard = (title, id = vid()) => `<ytd-video-renderer><a id="thumbnail" href="/watch?v=${id}"></a><h3><a id="video-title" title="${esc(title)}">${esc(title)}</a></h3></ytd-video-renderer>`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

module.exports = { loadDom, homeCard, homePage, sidebarCard, searchCard, vid, sleep, EXT, manifest };
