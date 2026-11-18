// Інтеграційні тести main.js: сторінка відео, SPA-навігація, гонки, збої бекенду.
const test = require("node:test");
const assert = require("node:assert");
const { loadDom, sleep } = require("./helpers/load-dom");

const watchHtml = (id, title) =>
  `<body><ytd-watch-flexy video-id="${id}"><ytd-watch-metadata><h1><yt-formatted-string>${title}</yt-formatted-string></h1></ytd-watch-metadata></ytd-watch-flexy>
   <div id="secondary"><div id="secondary-inner"></div></div></body>`;

const RU_TITLE = "Что будет, если смешать колу и ментос? Эксперимент";

// backend: функція (videoId) → відповідь /alternatives
function setup({ id = "aaaaaaaaaaa", title = RU_TITLE, backend, storage = {} } = {}, t) {
  const sent = [];
  const env = loadDom({
    html: watchHtml(id, title), url: `https://www.youtube.com/watch?v=${id}`, withMain: true,
    chromeOpts: {
      storage,
      sendMessage: async (m) => {
        sent.push(m);
        if (m.type === "alternatives") return backend ? backend(m.videoId, m) : { ok: true, data: { source: { lang: "ru", confidence: 0.95 }, level: 1, items: [{ videoId: "altaaaaaaaa", title: "Альт", channel: "К", lang: "uk" }] } };
        return { ok: true, data: { ok: true } };
      },
    },
  });
  if (t) t.after(() => env.window.close()); // зупиняє setInterval з main.js
  return { ...env, sent };
}
const panelHost = (e) => e.document.getElementById("mf-panel-host");
const go = (e, id, title) => {
  e.document.body.innerHTML = watchHtml(id, title);
  e.window.history.pushState({}, "", `/watch?v=${id}`);
  e.document.dispatchEvent(new e.window.Event("yt-navigate-finish"));
};

test("російське відео: після завантаження з'являється панель, шлеться panel_shown і suggested", async (t) => {
  const e = setup({}, t);
  await sleep(700);
  assert.ok(panelHost(e), "панель має з'явитися");
  assert.ok(e.sent.some((m) => m.type === "event" && m.eventType === "panel_shown" && m.level === 1));
  const actions = e.sent.find((m) => m.type === "videoActions");
  assert.deepEqual(actions.items.map((i) => i.action), ["suggested"]);
  assert.ok(e.logs.some((l) => l.includes("запущено на цій вкладці")), "рядок із README для діагностики");
});

test("українське відео (бекенд: uk): панелі немає", async (t) => {
  const e = setup({ title: "Як приготувати борщ", backend: () => ({ ok: true, data: { source: { lang: "uk", confidence: 0.95 }, level: 0, items: [] } }) }, t);
  await sleep(700);
  assert.strictEqual(panelHost(e), null);
});

test("мовний баланс: впевнене відео рахується один раз", async (t) => {
  const e = setup({}, t);
  await sleep(700);
  assert.strictEqual(e.sent.filter((m) => m.type === "watched").length, 1);
  assert.strictEqual(e.sent.find((m) => m.type === "watched").lang, "ru");
});

const alts = (e) => e.sent.filter((m) => m.type === "alternatives");
const OK_DATA = { ok: true, data: { source: { lang: "ru", confidence: 0.95 }, level: 1, items: [{ videoId: "altaaaaaaaa", title: "Альт", channel: "К", lang: "uk" }] } };
const shadow = (e) => panelHost(e) && panelHost(e).shadowRoot;

// ---------------------------------------------------------------- ранній показ панелі й повтор запиту
test("впевнено не українське за назвою: панель з'являється ОДРАЗУ із заготовками, до відповіді сервера", async (t) => {
  let release;
  const gate = new Promise((r) => (release = r));
  const e = setup({ backend: async () => { await gate; return OK_DATA; } }, t);
  await sleep(600);
  assert.ok(panelHost(e), "панель має бути на екрані до відповіді сервера");
  assert.ok(shadow(e).querySelector("ul[aria-busy='true']"), "заготовки");
  assert.match(shadow(e).textContent, /Шукаємо українські альтернативи/);
  assert.match(shadow(e).textContent, /Це відео російською/, "мова за оцінкою розширення вже написана");
  assert.strictEqual(shadow(e).querySelectorAll("a.item").length, 0);
  assert.ok(!e.sent.some((m) => m.type === "event" && m.eventType === "panel_shown"), "panel_shown рахується лише коли показано справжній результат");
  release();
  await sleep(300);
  assert.strictEqual(shadow(e).querySelectorAll("a.item").length, 1);
  assert.strictEqual(shadow(e).querySelector("ul[aria-busy='true']"), null);
  assert.strictEqual(e.document.querySelectorAll("#mf-panel-host").length, 1);
  assert.strictEqual(e.sent.filter((m) => m.type === "event" && m.eventType === "panel_shown").length, 1);
});

test("назва нейтральна (клієнт не впевнений): заготовок немає, панель лише після відповіді сервера", async (t) => {
  let release;
  const gate = new Promise((r) => (release = r));
  const e = setup({ title: "Ремонт ноутбука", backend: async () => { await gate; return OK_DATA; } }, t);
  await sleep(600);
  assert.strictEqual(panelHost(e), null);
  release();
  await sleep(300);
  assert.ok(panelHost(e));
});

test("ранні заготовки, а сервер каже «українське»: панель зникає", async (t) => {
  const e = setup({ title: RU_TITLE, backend: async () => { await sleep(500); return { ok: true, data: { source: { lang: "uk", confidence: 0.95 }, level: 0, items: [] } }; } }, t);
  await sleep(300);
  assert.ok(panelHost(e));
  await sleep(700);
  assert.strictEqual(panelHost(e), null);
});

test("showPanel=false: ні заготовок, ні панелі", async (t) => {
  const e = setup({ storage: { showPanel: false } }, t);
  await sleep(600);
  assert.strictEqual(panelHost(e), null);
});

test("перший запит упав, другий (через ~3 с) успішний: заготовки замінюються результатом", { timeout: 9000 }, async (t) => {
  let n = 0;
  const e = setup({ backend: () => (++n === 1 ? { ok: false, error: "HTTP 500" } : OK_DATA) }, t);
  await sleep(1000);
  assert.ok(shadow(e).querySelector("ul[aria-busy='true']"), "поки чекаємо повтор, заготовки на місці");
  await sleep(3000);
  assert.strictEqual(alts(e).length, 2);
  assert.strictEqual(shadow(e).querySelectorAll("a.item").length, 1);
});

test("сервер не відповідає взагалі: 3 спроби (одразу, +3 с, +8 с), потім повідомлення з кнопкою «Спробувати ще раз»", { timeout: 16000 }, async (t) => {
  const e = setup({ backend: () => ({ ok: false, error: "HTTP 503" }) }, t);
  await sleep(2000);
  assert.strictEqual(alts(e).length, 1);
  await sleep(2000); // ~4 с
  assert.strictEqual(alts(e).length, 2, "друга спроба має бути через ~3 с");
  await sleep(8500); // ~12.5 с
  assert.strictEqual(alts(e).length, 3, "третя і остання через ~8 с після другої");
  assert.match(shadow(e).textContent, /Не вдалося завантажити альтернативи/);
  assert.strictEqual(shadow(e).querySelector("ul[aria-busy='true']"), null);
  // «Спробувати ще раз» запускає все спочатку
  const retry = [...shadow(e).querySelectorAll("button")].find((b) => /Спробувати ще раз/.test(b.textContent));
  assert.ok(retry);
  retry.click();
  await sleep(300);
  assert.strictEqual(alts(e).length, 4);
});

test("нейтральна назва і сервер недоступний: тиша (без заготовок і без повідомлення про помилку)", { timeout: 16000 }, async (t) => {
  const e = setup({ title: "Ремонт ноутбука", backend: () => ({ ok: false, error: "HTTP 503" }) }, t);
  await sleep(12500);
  assert.strictEqual(alts(e).length, 3);
  assert.strictEqual(panelHost(e), null);
});

test("користувач перейшов на інше відео під час повторів: старі повтори припиняються", { timeout: 12000 }, async (t) => {
  const e = setup({ backend: (id) => (id === "aaaaaaaaaaa" ? { ok: false, error: "HTTP 503" } : OK_DATA) }, t);
  await sleep(800);
  go(e, "bbbbbbbbbbb", "Как жить дальше? Это важно");
  await sleep(4500); // повтор для A (на 3-й секунді) не має відбутися
  const forA = alts(e).filter((m) => m.videoId === "aaaaaaaaaaa").length;
  assert.strictEqual(forA, 1, "для A після переходу нових запитів бути не має");
  assert.strictEqual(panelHost(e).dataset.videoId, "bbbbbbbbbbb");
  assert.strictEqual(shadow(e).querySelectorAll("a.item").length, 1);
});

test("розширення вимкнене (enabled=false): нічого не робить", async (t) => {
  const e = setup({ storage: { enabled: false } }, t);
  await sleep(500);
  assert.strictEqual(panelHost(e), null);
  assert.ok(!e.sent.some((m) => m.type === "alternatives"));
});

test("showPanel=false: запит є (для балансу), панелі немає", async (t) => {
  const e = setup({ storage: { showPanel: false } }, t);
  await sleep(700);
  assert.strictEqual(panelHost(e), null);
});

test("SPA-перехід на інше відео: стара панель зникає, з'являється нова для нового id", async (t) => {
  const e = setup({}, t);
  await sleep(700);
  assert.strictEqual(panelHost(e).dataset.videoId, "aaaaaaaaaaa");
  go(e, "bbbbbbbbbbb", "Как жить дальше? Это важно");
  await sleep(900);
  assert.strictEqual(panelHost(e).dataset.videoId, "bbbbbbbbbbb");
  assert.strictEqual(e.document.querySelectorAll("#mf-panel-host").length, 1);
});

test("перехід зі сторінки відео на головну прибирає панель", async (t) => {
  const e = setup({}, t);
  await sleep(700);
  e.window.history.pushState({}, "", "/");
  e.document.dispatchEvent(new e.window.Event("yt-navigate-finish"));
  await sleep(100);
  assert.strictEqual(panelHost(e), null);
});

test("гонка: повільна відповідь для відео A не малює панель, коли вже відкрито B", async (t) => {
  let releaseA;
  const gateA = new Promise((r) => (releaseA = r));
  const e = setup({ backend: async (id) => {
    if (id === "aaaaaaaaaaa") { await gateA; return { ok: true, data: { source: { lang: "ru", confidence: 0.95 }, level: 1, items: [{ videoId: "altAAAAAAAA", title: "Для A", channel: "К", lang: "uk" }] } }; }
    return { ok: true, data: { source: { lang: "ru", confidence: 0.95 }, level: 1, items: [{ videoId: "altBBBBBBBB", title: "Для B", channel: "К", lang: "uk" }] } };
  } }, t);
  await sleep(500); // A чекає на бекенд
  go(e, "bbbbbbbbbbb", "Как жить дальше? Это важно");
  await sleep(700);
  releaseA();
  await sleep(300);
  const host = panelHost(e);
  assert.strictEqual(host.dataset.videoId, "bbbbbbbbbbb");
  assert.match(host.shadowRoot.textContent, /Для B/);
  assert.doesNotMatch(host.shadowRoot.textContent, /Для A/);
});

test("зміна налаштувань у popup (storage.onChanged) застосовується без перезавантаження", async (t) => {
  const e = setup({ storage: { showPanel: false } }, t);
  await sleep(600);
  assert.strictEqual(panelHost(e), null);
  await e.cm.chrome.storage.local.set({ showPanel: true });
  await sleep(900);
  assert.ok(panelHost(e));
});

test("popup питає mf-ping: скрипт відповідає і повідомляє поточне відео", async (t) => {
  const e = setup({}, t);
  await sleep(300);
  let reply;
  const listeners = e.cm.onMessage.listeners;
  listeners.forEach((fn) => fn({ type: "mf-ping" }, {}, (r) => (reply = r)));
  assert.strictEqual(reply.ok, true);
  assert.strictEqual(reply.videoId, "aaaaaaaaaaa");
});

test("дитячий режим вмикається разом зі сторінкою відео: фільтр активний у сайдбарі", async (t) => {
  const e = setup({ storage: { kidsMode: true } }, t);
  e.document.querySelector("#secondary").insertAdjacentHTML("beforeend", `<div id="related"><ytd-compact-video-renderer><a id="thumbnail" href="/watch?v=ccccccccccc"></a><span id="video-title" title="${RU_TITLE}">${RU_TITLE}</span></ytd-compact-video-renderer></div>`);
  await sleep(700);
  assert.ok(e.document.querySelector("ytd-compact-video-renderer").classList.contains("mf-hidden"));
});
