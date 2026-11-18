// Тести background.js: кеш, ліміти, приватність, помилки бекенду.
// Запуск: cd tests && npm install && npm test
const test = require("node:test");
const assert = require("node:assert");
const { loadBackground } = require("./helpers/load-background");

const ok = (body = {}) => ({ status: 200, body });
const ids = (n, prefix = "v") => Array.from({ length: n }, (_, i) => `${prefix}${String(i).padStart(10, "0")}`);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

// ---------------------------------------------------------------- встановлення
test("onInstalled: створює installId (UUID v4), дефолтні налаштування і щоденний будильник", async () => {
  const bg = loadBackground();
  await bg.install();
  assert.match(bg.storage.data.installId, UUID);
  assert.strictEqual(bg.storage.data.enabled, true);
  assert.strictEqual(bg.storage.data.kidsMode, false);
  assert.strictEqual(bg.storage.data.shareKidsStats, false, "за замовчуванням статистику не надсилаємо");
  assert.strictEqual(bg.alarmsCreated[0].name, "daily");
});

test("onInstalled при оновленні розширення не перезаписує налаштування і installId", async () => {
  const bg = loadBackground({ storage: { installId: "keep-this-id-123", kidsMode: true, pinHash: "abc" } });
  await bg.install();
  assert.strictEqual(bg.storage.data.installId, "keep-this-id-123");
  assert.strictEqual(bg.storage.data.kidsMode, true);
  assert.strictEqual(bg.storage.data.pinHash, "abc");
});

// ---------------------------------------------------------------- /alternatives
test("alternatives: правильний URL, режим default, заголовок для ngrok", async () => {
  const bg = loadBackground({ fetchImpl: () => ok({ level: 1, items: [] }) });
  const r = await bg.send({ type: "alternatives", videoId: "dQw4w9WgXcQ" });
  assert.strictEqual(r.ok, true);
  const base = bg.run("MF_CONFIG.API_BASE");
  assert.strictEqual(bg.fetchCalls[0].url, `${base}/alternatives?videoId=dQw4w9WgXcQ&mode=default`);
  assert.strictEqual(bg.fetchCalls[0].headers["ngrok-skip-browser-warning"], "1");
});

test("alternatives: у дитячому режимі mode=kids", async () => {
  const bg = loadBackground({ storage: { kidsMode: true }, fetchImpl: () => ok({ level: 1, items: [] }) });
  await bg.send({ type: "alternatives", videoId: "dQw4w9WgXcQ" });
  assert.match(bg.fetchCalls[0].url, /mode=kids$/);
});

test("alternatives: другий запит іде з кешу, після 6 годин кеш прострочується", async () => {
  const bg = loadBackground({ fetchImpl: () => ok({ level: 2, items: [] }) });
  await bg.send({ type: "alternatives", videoId: "dQw4w9WgXcQ" });
  await bg.send({ type: "alternatives", videoId: "dQw4w9WgXcQ" });
  assert.strictEqual(bg.fetchCalls.length, 1, "повторний запит не має йти в мережу");
  bg.advance(6 * 3600 * 1000 + 1000);
  await bg.send({ type: "alternatives", videoId: "dQw4w9WgXcQ" });
  assert.strictEqual(bg.fetchCalls.length, 2);
});

test("alternatives: кеш окремий для default і kids", async () => {
  const bg = loadBackground({ fetchImpl: () => ok({ level: 1, items: [] }) });
  await bg.send({ type: "alternatives", videoId: "dQw4w9WgXcQ" });
  await bg.storage.local.set({ kidsMode: true });
  await bg.send({ type: "alternatives", videoId: "dQw4w9WgXcQ" });
  assert.strictEqual(bg.fetchCalls.length, 2);
});

test("alternatives: HTTP 500 → ok:false, у кеш нічого не потрапляє", async () => {
  const bg = loadBackground({ fetchImpl: () => ({ status: 500, body: { detail: "x" } }) });
  const r = await bg.send({ type: "alternatives", videoId: "dQw4w9WgXcQ" });
  assert.strictEqual(r.ok, false);
  assert.match(r.error, /HTTP 500/);
  assert.ok(!Object.keys(bg.storage.data).some((k) => k.startsWith("alt:")));
});

test("alternatives: мережа лягла (fetch кидає) → ok:false, без винятку", async () => {
  const bg = loadBackground({ fetchImpl: () => { throw new TypeError("Failed to fetch"); } });
  const r = await bg.send({ type: "alternatives", videoId: "dQw4w9WgXcQ" });
  assert.strictEqual(r.ok, false);
  assert.match(r.error, /Failed to fetch/);
});

test("alternatives: таймаут скасовує запит (AbortController)", async () => {
  const bg = loadBackground({ fetchImpl: () => ({ hang: true, status: 200 }), overrides: { FETCH_TIMEOUT_MS: 60 } });
  const t0 = Date.now();
  const r = await bg.send({ type: "alternatives", videoId: "dQw4w9WgXcQ" });
  assert.strictEqual(r.ok, false);
  assert.ok(Date.now() - t0 < 1500, "має відвалитися за таймаутом, а не висіти");
});

test("alternatives: порожня відповідь 200 без тіла не ламає розбір", async () => {
  const bg = loadBackground({ fetchImpl: () => ({ status: 200, body: undefined }) });
  const r = await bg.send({ type: "alternatives", videoId: "dQw4w9WgXcQ" });
  assert.strictEqual(r.ok, true);
  assert.deepEqual(r.data, {});
});

test("alternatives: USE_MOCK=true не ходить у мережу і віддає рівень 1–5", async () => {
  const bg = loadBackground({ overrides: { USE_MOCK: true } });
  const r = await bg.send({ type: "alternatives", videoId: "dQw4w9WgXcQ", hint: { lang: "ru", confidence: 0.9 } });
  assert.strictEqual(bg.fetchCalls.length, 0);
  assert.ok(r.data.level >= 1 && r.data.level <= 5);
  assert.strictEqual(r.data.source.lang, "ru");
});

// ---------------------------------------------------------------- /classify
test("classify: більше 50 id ділиться на пачки 50 + 50 + решта", async () => {
  const bg = loadBackground({ fetchImpl: ({ body }) => ok({ results: Object.fromEntries(body.videoIds.map((i) => [i, { lang: "ru", confidence: 0.9, source: "x" }])) }) });
  const r = await bg.send({ type: "classify", videoIds: ids(120) });
  assert.deepStrictEqual(bg.fetchCalls.map((c) => c.body.videoIds.length), [50, 50, 20]);
  assert.strictEqual(Object.keys(r.data.results).length, 120);
});

test("classify: уже відомі id беруться з кешу, у мережу йдуть лише нові", async () => {
  const bg = loadBackground({ fetchImpl: ({ body }) => ok({ results: Object.fromEntries(body.videoIds.map((i) => [i, { lang: "ru", confidence: 0.9, source: "x" }])) }) });
  await bg.send({ type: "classify", videoIds: ids(3) });
  await bg.send({ type: "classify", videoIds: ids(5) });
  assert.deepStrictEqual(bg.fetchCalls[1].body.videoIds, ids(5).slice(3));
});

test("classify: id, якого немає у відповіді, стає unknown (нічого не ховаємо)", async () => {
  const bg = loadBackground({ fetchImpl: () => ok({ results: {} }) });
  const r = await bg.send({ type: "classify", videoIds: ["aaaaaaaaaaa"] });
  assert.strictEqual(r.data.results.aaaaaaaaaaa.lang, "unknown");
  assert.strictEqual(r.data.results.aaaaaaaaaaa.confidence, 0);
});

test("classify: збій бекенду → ok:false і нічого не кешується", async () => {
  const bg = loadBackground({ fetchImpl: () => ({ status: 503, body: {} }) });
  const r = await bg.send({ type: "classify", videoIds: ids(2) });
  assert.strictEqual(r.ok, false);
  assert.ok(!Object.keys(bg.storage.data).some((k) => k.startsWith("cls:")));
});

test("classify: кеш живе 24 години", async () => {
  const bg = loadBackground({ fetchImpl: ({ body }) => ok({ results: { [body.videoIds[0]]: { lang: "ru", confidence: 0.9, source: "x" } } }) });
  await bg.send({ type: "classify", videoIds: ["aaaaaaaaaaa"] });
  bg.advance(23 * 3600 * 1000);
  await bg.send({ type: "classify", videoIds: ["aaaaaaaaaaa"] });
  assert.strictEqual(bg.fetchCalls.length, 1);
  bg.advance(2 * 3600 * 1000);
  await bg.send({ type: "classify", videoIds: ["aaaaaaaaaaa"] });
  assert.strictEqual(bg.fetchCalls.length, 2);
});

// ---------------------------------------------------------------- /wish
const WISH = { type: "wish", videoId: "dQw4w9WgXcQ", videoTitle: "Назва", level: 4, userText: "хочу огляд" };

test("wish: надсилає installId, videoId, level, текст", async () => {
  const bg = loadBackground({ storage: { installId: "install-id-12345" }, fetchImpl: () => ok({ ok: true }) });
  const r = await bg.send(WISH);
  assert.strictEqual(r.data.ok, true);
  assert.deepStrictEqual(bg.fetchCalls[0].body, { installId: "install-id-12345", videoId: "dQw4w9WgXcQ", videoTitle: "Назва", level: 4, userText: "хочу огляд" });
});

test("wish: текст обрізається до 280 символів, порожній → null", async () => {
  const bg = loadBackground({ fetchImpl: () => ok({ ok: true }) });
  await bg.send({ ...WISH, userText: "я".repeat(500) });
  assert.strictEqual(bg.fetchCalls[0].body.userText.length, 280);
  await bg.send({ ...WISH, userText: "" });
  assert.strictEqual(bg.fetchCalls[1].body.userText, null);
});

test("wish: у дитячому режимі текст користувача ніколи не надсилається", async () => {
  const bg = loadBackground({ storage: { kidsMode: true }, fetchImpl: () => ok({ ok: true }) });
  await bg.send({ ...WISH, userText: "якийсь текст дитини" });
  assert.strictEqual(bg.fetchCalls[0].body.userText, null);
});

test("wish: 21-й запит за день блокується локально без звернення до мережі", async () => {
  const bg = loadBackground({ fetchImpl: () => ok({ ok: true }) });
  for (let i = 0; i < 20; i++) assert.strictEqual((await bg.send(WISH)).data.ok, true);
  const r = await bg.send(WISH);
  assert.deepEqual(r.data, { ok: false, reason: "limit" });
  assert.strictEqual(bg.fetchCalls.length, 20);
});

test("wish: наступного дня ліміт скидається", async () => {
  const bg = loadBackground({ storage: { wishCount: { "2026-11-14": 20 } }, fetchImpl: () => ok({ ok: true }) });
  assert.strictEqual((await bg.send(WISH)).data.reason, "limit");
  bg.advance(24 * 3600 * 1000);
  assert.strictEqual((await bg.send(WISH)).data.ok, true);
});

test("wish: якщо бекенд не прийняв запит, лічильник ліміту не росте", async () => {
  const bg = loadBackground({ fetchImpl: () => ({ status: 500, body: {} }) });
  await bg.send(WISH);
  assert.ok(!bg.storage.data.wishCount, "невдалий запит не має з'їдати добовий ліміт");
});

// Відомі розбіжності з бекендом: тест позначений todo, тому не валить запуск, але видно у звіті.
test("wish: відповідь бекенду 429 (ліміт) має показати користувачу «ліміт», а не «помилка»", { todo: "server.py віддає 429, apiFetch кидає на будь-який !ok, панель покаже 'Не вдалося зберегти'" }, async () => {
  const bg = loadBackground({ fetchImpl: () => ({ status: 429, body: { ok: false, reason: "limit" } }) });
  const r = await bg.send(WISH);
  assert.strictEqual(r.data && r.data.reason, "limit");
});

test("wish: назва відео обрізається до 300 символів (ліміт сервера, інакше він відповість 422)", { todo: "P3: YouTube обмежує назву 100 символами, тож у житті не стається, але захисне обрізання коштує один рядок" }, async () => {
  const bg = loadBackground({ fetchImpl: () => ok({ ok: true }) });
  await bg.send({ ...WISH, videoTitle: "Н".repeat(500) });
  assert.ok(bg.fetchCalls[0].body.videoTitle.length <= 300);
});

// ---------------------------------------------------------------- /events
test("event: у дитячому режимі подій немає", async () => {
  const bg = loadBackground({ storage: { kidsMode: true }, fetchImpl: () => ok() });
  const r = await bg.send({ type: "event", eventType: "panel_shown", level: 2 });
  assert.strictEqual(r.data.skipped, true);
  assert.strictEqual(bg.fetchCalls.length, 0);
});

test("event: тіло відповідає контракту і не містить назв/id відео", async () => {
  const bg = loadBackground({ storage: { installId: "install-id-12345" }, fetchImpl: () => ok() });
  await bg.send({ type: "event", eventType: "alt_clicked", level: 3, videoId: "dQw4w9WgXcQ", videoTitle: "секрет" });
  const b = bg.fetchCalls[0].body;
  assert.deepStrictEqual(Object.keys(b).sort(), ["installId", "level", "ts", "type"]);
  assert.strictEqual(b.type, "alt_clicked");
  assert.ok(!JSON.stringify(b).includes("секрет") && !JSON.stringify(b).includes("dQw4w9WgXcQ"));
  assert.ok(!Number.isNaN(Date.parse(b.ts)));
});

test("event: всі типи подій, які шле розширення, дозволені бекендом", () => {
  const allowed = new Set(["panel_shown", "alt_clicked", "wish_sent", "kids_hidden_daily"]); // EVENT_TYPES з server.py
  const fs = require("node:fs"), path = require("node:path");
  const src = fs.readFileSync(path.join(__dirname, "..", "src", "content", "panel.js"), "utf8") + fs.readFileSync(path.join(__dirname, "..", "src", "content", "main.js"), "utf8");
  const used = [...src.matchAll(/eventType:\s*"([a-z_]+)"/g)].map((m) => m[1]);
  assert.ok(used.length >= 3);
  for (const t of used) assert.ok(allowed.has(t), `розширення шле невідомий бекенду тип "${t}"`);
});

// ---------------------------------------------------------------- /actions
test("videoActions: невалідні елементи відхиляються", async () => {
  const bg = loadBackground({ fetchImpl: () => ok() });
  for (const items of [[], null, [{ action: "click", videoId: "aaaaaaaaaaa" }], [{ action: "hid" }]]) {
    const r = await bg.send({ type: "videoActions", items });
    assert.strictEqual(r.ok, false, JSON.stringify(items));
  }
  assert.strictEqual(bg.fetchCalls.length, 0);
});

test("videoActions: 250 елементів → 3 запити по ≤100 (ліміт бекенду)", async () => {
  const bg = loadBackground({ fetchImpl: () => ok() });
  const items = ids(250).map((videoId) => ({ action: "suggested", videoId }));
  await bg.send({ type: "videoActions", items });
  assert.deepStrictEqual(bg.fetchCalls.map((c) => c.body.items.length), [100, 100, 50]);
});

test("PRIVACY: у дитячому режимі id прихованих відео НЕ мають іти на сервер без дозволу батьків", { todo: "README обіцяє «лише денна сума і лише з дозволу батьків», але filter.js шле /actions {action:'hid', videoId} завжди. Треба рішення команди: або перевірка shareKidsStats у handleVideoActions, або виправити README/privacy" }, async () => {
  const bg = loadBackground({ storage: { kidsMode: true, shareKidsStats: false }, fetchImpl: () => ok() });
  await bg.send({ type: "videoActions", items: [{ action: "hid", videoId: "aaaaaaaaaaa" }] });
  assert.strictEqual(bg.fetchCalls.length, 0);
});

// ---------------------------------------------------------------- локальні лічильники
test("kidsHidden: сумується за день, дні старші 30 видаляються", async () => {
  const bg = loadBackground({ storage: { kidsHidden: { "2026-10-01": 5, "2026-11-10": 2 } } });
  await bg.send({ type: "kidsHidden", count: 3 });
  await bg.send({ type: "kidsHidden", count: 4 });
  const d = bg.storage.data.kidsHidden;
  assert.strictEqual(d["2026-11-14"], 7);
  assert.strictEqual(d["2026-11-10"], 2);
  assert.ok(!("2026-10-01" in d), "запис старший за 30 днів має зникнути");
});

test("watched: рахує uk/ru/en, усе інше в other", async () => {
  const bg = loadBackground();
  for (const lang of ["uk", "uk", "ru", "en", "pl", undefined]) await bg.send({ type: "watched", lang });
  assert.deepEqual(bg.storage.data.balance["2026-11-14"], { uk: 2, ru: 1, en: 1, other: 2 });
});

test("невідомий тип повідомлення ігнорується (не висить канал)", async () => {
  const bg = loadBackground();
  assert.strictEqual(await bg.send({ type: "nope" }), undefined);
});

// ---------------------------------------------------------------- щоденні задачі
test("daily alarm: чистить прострочений кеш, свіжий лишає", async () => {
  const now = Date.parse("2026-11-14T12:00:00Z");
  const bg = loadBackground({ storage: {
    "alt:default:old": { ts: now - 7 * 3600e3, data: {} }, "alt:default:new": { ts: now - 1 * 3600e3, data: {} },
    "cls:old": { ts: now - 25 * 3600e3, data: {} }, "cls:new": { ts: now - 1 * 3600e3, data: {} },
    installId: "install-id-12345",
  } });
  await bg.alarm();
  const keys = Object.keys(bg.storage.data);
  assert.ok(!keys.includes("alt:default:old") && !keys.includes("cls:old"));
  assert.ok(keys.includes("alt:default:new") && keys.includes("cls:new"));
});

test("daily alarm: чужий будильник ігнорується", async () => {
  const bg = loadBackground({ storage: { "cls:old": { ts: 0, data: {} } } });
  await bg.alarm("other");
  assert.ok("cls:old" in bg.storage.data);
});

test("kids_hidden_daily: без дозволу батьків нічого не надсилається", async () => {
  const bg = loadBackground({ storage: { shareKidsStats: false, kidsHidden: { "2026-11-13": 9 }, installId: "install-id-12345" }, fetchImpl: () => ok() });
  await bg.alarm();
  assert.strictEqual(bg.fetchCalls.length, 0);
});

test("kids_hidden_daily: з дозволом шлеться лише число за ЗАВЕРШЕНІ дні, без id відео, один раз", async () => {
  const bg = loadBackground({ storage: { shareKidsStats: true, kidsHidden: { "2026-11-13": 9, "2026-11-14": 4 }, installId: "install-id-12345" }, fetchImpl: () => ok() });
  await bg.alarm();
  assert.strictEqual(bg.fetchCalls.length, 1, "сьогоднішній незавершений день не надсилаємо");
  const b = bg.fetchCalls[0].body;
  assert.strictEqual(b.type, "kids_hidden_daily");
  assert.strictEqual(b.count, 9);
  assert.strictEqual(b.date, "2026-11-13");
  assert.ok(!("videoIds" in b) && !("videoId" in b));
  await bg.alarm();
  assert.strictEqual(bg.fetchCalls.length, 1, "той самий день не надсилається вдруге");
});

test("kids_hidden_daily: якщо надсилання впало, день не вважається відправленим", async () => {
  const bg = loadBackground({ storage: { shareKidsStats: true, kidsHidden: { "2026-11-13": 9 }, installId: "install-id-12345" }, fetchImpl: () => ({ status: 500, body: {} }) });
  await bg.alarm();
  assert.deepEqual(bg.storage.data.kidsSentDays, []);
});

test("kids_hidden_daily: у режимі заглушки нічого не шлеться", async () => {
  const bg = loadBackground({ overrides: { USE_MOCK: true }, storage: { shareKidsStats: true, kidsHidden: { "2026-11-13": 9 } }, fetchImpl: () => ok() });
  await bg.alarm();
  assert.strictEqual(bg.fetchCalls.length, 0);
});
