// Запускає справжній src/background.js у ізольованому контексті з підміненими chrome, fetch і годинником.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { makeChrome } = require("./chrome-mock");

const SRC = path.join(__dirname, "..", "..", "src");

function loadBackground({ fetchImpl, storage, now = Date.parse("2026-11-14T12:00:00Z"), overrides = {}, realFetch = false } = {}) {
  const cm = makeChrome({ storage });
  const clock = { now };
  const fetchCalls = [];

  class FakeDate extends Date {
    constructor(...a) { if (a.length) super(...a); else super(clock.now); }
    static now() { return clock.now; }
  }

  const sandbox = {
    chrome: cm.chrome, console: { log() {}, warn() {}, error() {} },
    setTimeout, clearTimeout, AbortController, URL, URLSearchParams,
    crypto: require("node:crypto").webcrypto, Date: FakeDate,
  };
  sandbox.fetch = realFetch
    ? (url, opts) => { fetchCalls.push({ url, method: (opts && opts.method) || "GET", body: opts && opts.body ? JSON.parse(opts.body) : null }); return fetch(url, opts); }
    : async (url, opts = {}) => {
        const call = { url, method: opts.method || "GET", headers: opts.headers, body: opts.body ? JSON.parse(opts.body) : null, signal: opts.signal };
        fetchCalls.push(call);
        const r = fetchImpl ? await fetchImpl(call) : { status: 200, body: {} };
        if (r.hang) await new Promise((_, rej) => opts.signal && opts.signal.addEventListener("abort", () => rej(Object.assign(new Error("aborted"), { name: "AbortError" }))));
        return {
          ok: r.status >= 200 && r.status < 300, status: r.status,
          text: async () => (r.body === undefined ? "" : typeof r.body === "string" ? r.body : JSON.stringify(r.body)),
        };
      };
  sandbox.importScripts = (...files) => {
    for (const f of files) vm.runInContext(fs.readFileSync(path.join(SRC, f), "utf8"), ctx, { filename: f });
  };
  const ctx = vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(SRC, "background.js"), "utf8"), ctx, { filename: "background.js" });
  const config = (k, v) => vm.runInContext(`MF_CONFIG.${k} = ${JSON.stringify(v)}`, ctx);
  for (const [k, v] of Object.entries(overrides)) config(k, v);

  // Шлях, яким справжні content script і popup розмовляють з background.
  const listener = cm.onMessage.listeners[0];
  const send = (msg) => new Promise((resolve) => {
    const keep = listener(msg, {}, resolve);
    if (!keep) resolve(undefined);
  });

  return {
    ...cm, send, fetchCalls, clock, ctx, config,
    run: (code) => vm.runInContext(code, ctx),
    advance: (ms) => { clock.now += ms; },
    install: () => Promise.all(cm.onInstalled.emit()),
    alarm: (name = "daily") => Promise.all(cm.onAlarm.emit({ name })),
  };
}

module.exports = { loadBackground, SRC };
