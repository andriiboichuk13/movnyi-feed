// Контрактні тести: справжній background.js ↔ справжній HTTP ↔ tests/tools/mock-backend.py.
// Мок повторює перевірки server.py (довжина installId, формат videoId, типи подій, ліміти пачок),
// тож тут видно, чи прийме справжній сервер те, що шле розширення. Потрібні python3 і вільний порт 8791.
const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");
const { loadBackground } = require("./helpers/load-background");

const PORT = 8791;
const BASE = `http://127.0.0.1:${PORT}`;
const PY = ["python3", "python"].find((p) => spawnSync(p, ["--version"]).status === 0);
const skip = PY ? false : "python3 не знайдено";
const get = async (p) => (await fetch(BASE + p)).json();
const VID = "dQw4w9WgXcQ";

let server;
test.before(async () => {
  if (skip) return;
  server = spawn(PY, [path.join(__dirname, "tools", "mock-backend.py"), "--port", String(PORT)], { stdio: "ignore" });
  for (let i = 0; i < 50; i++) {
    try { if ((await fetch(BASE + "/health")).ok) return; } catch (e) { /* ще не піднявся */ }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error("мок-бекенд не стартував");
});
test.after(() => server && server.kill());
test.beforeEach(async () => { if (!skip) await get("/__reset"); });

const bg = (storage = {}) => loadBackground({ realFetch: true, storage, overrides: { API_BASE: BASE, FETCH_TIMEOUT_MS: 1500 } });

test("/alternatives: розширення розуміє відповідь і режим kids", { skip }, async () => {
  const b = bg({ kidsMode: true });
  await get("/__set?level=1");
  const r = await b.send({ type: "alternatives", videoId: VID });
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.data.level, 1);
  assert.strictEqual(r.data.items.length, 3);
  const st = await get("/__state");
  assert.match(st.log[0].path, /mode=kids/);
});

test("/alternatives: некоректний videoId → 400 → ok:false, без падіння", { skip }, async () => {
  const r = await bg().send({ type: "alternatives", videoId: "short" });
  assert.strictEqual(r.ok, false);
  assert.match(r.error, /HTTP 400/);
});

test("/classify: 120 id → три запити, ru з бекенду потрапляє у відповідь, решта unknown", { skip }, async () => {
  const ids = Array.from({ length: 120 }, (_, i) => `id${String(i).padStart(9, "0")}`);
  await get(`/__set?ru=${ids[0]},${ids[119]}`);
  const r = await bg().send({ type: "classify", videoIds: ids });
  assert.strictEqual(r.data.results[ids[0]].lang, "ru");
  assert.strictEqual(r.data.results[ids[119]].lang, "ru");
  assert.strictEqual(r.data.results[ids[5]].lang, "unknown");
  assert.deepEqual((await get("/__state")).classify_calls, [50, 50, 20]);
});

test("/wish: сервер приймає тіло; installId — UUID (≥ 8 символів)", { skip }, async () => {
  const b = bg();
  await b.install();
  const r = await b.send({ type: "wish", videoId: VID, videoTitle: "Назва", level: 4, userText: "огляд" });
  assert.strictEqual(r.data.ok, true);
  const st = await get("/__state");
  assert.strictEqual(st.wishes.length, 1);
  assert.match(st.wishes[0].installId, /^[0-9a-f-]{36}$/);
});

test("/wish: дитячий режим → на сервер приходить userText = null", { skip }, async () => {
  await bg({ kidsMode: true, installId: "kid-install-id-1" }).send({ type: "wish", videoId: VID, videoTitle: "Назва", level: 5, userText: "секрет дитини" });
  assert.strictEqual((await get("/__state")).wishes[0].userText, null);
});

test("/events: усі типи подій розширення приймаються сервером", { skip }, async () => {
  const b = bg({ installId: "install-id-12345" });
  for (const eventType of ["panel_shown", "alt_clicked", "wish_sent"]) assert.strictEqual((await b.send({ type: "event", eventType, level: 2 })).ok, true, eventType);
  assert.strictEqual((await get("/__state")).events.length, 3);
});

test("/events kids_hidden_daily: денна сума проходить валідацію (count, date)", { skip }, async () => {
  const b = bg({ shareKidsStats: true, kidsHidden: { "2026-11-13": 9 }, installId: "install-id-12345" });
  await b.alarm();
  const ev = (await get("/__state")).events;
  assert.strictEqual(ev.length, 1);
  assert.strictEqual(ev[0].count, 9);
  assert.strictEqual(ev[0].date, "2026-11-13");
});

test("/actions: пачки по ≤100, сервер приймає suggested і hid", { skip }, async () => {
  const b = bg({ installId: "install-id-12345" });
  const items = Array.from({ length: 130 }, (_, i) => ({ action: i % 2 ? "hid" : "suggested", videoId: `id${String(i).padStart(9, "0")}` }));
  assert.strictEqual((await b.send({ type: "videoActions", items })).ok, true);
  assert.strictEqual((await get("/__state")).actions.length, 130);
});

test("збій бекенду (500) → ok:false; після відновлення все працює, невдала відповідь не кешується", { skip }, async () => {
  const b = bg();
  await get("/__set?fail=alternatives:500");
  assert.strictEqual((await b.send({ type: "alternatives", videoId: VID })).ok, false);
  await get("/__set?fail=off");
  assert.strictEqual((await b.send({ type: "alternatives", videoId: VID })).ok, true, "після збою невдала відповідь не має лишитися в кеші");
});

test("затримка бекенду довша за таймаут → ok:false за ~таймаут, а не зависання", { skip }, async () => {
  await get("/__set?delay=3000");
  const t0 = Date.now();
  const r = await bg().send({ type: "alternatives", videoId: VID });
  assert.strictEqual(r.ok, false);
  assert.ok(Date.now() - t0 < 2500);
  await get("/__set?delay=0");
});

test("ліміт /wish: сервер віддає 429, розширення сприймає це як помилку (відомо, див. todo в background.test.js)", { skip }, async () => {
  const b = bg({ installId: "install-id-12345" });
  await get("/__set?fail=wish:429");
  const r = await b.send({ type: "wish", videoId: VID, videoTitle: "x", level: 4, userText: "" });
  assert.strictEqual(r.ok, false);
  assert.match(r.error, /HTTP 429/);
});
