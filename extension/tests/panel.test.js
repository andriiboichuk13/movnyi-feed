// Тести допоміжних функцій (common.js) і панелі альтернатив (panel.js).
const test = require("node:test");
const assert = require("node:assert");
const { loadDom, sleep } = require("./helpers/load-dom");

const WATCH = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";
const PAGE = `<body><div id="secondary"><div id="secondary-inner"><div id="related">чужий вміст</div></div></div></body>`;

function setup({ url = WATCH, sendMessage, dark = false } = {}) {
  const sent = [];
  const env = loadDom({
    html: PAGE, url,
    chromeOpts: { sendMessage: async (m) => { sent.push(m); return sendMessage ? sendMessage(m) : { ok: true, data: { ok: true } }; } },
  });
  if (dark) env.document.documentElement.setAttribute("dark", "");
  return { ...env, sent, Panel: env.run("MF_Panel"), MF: env.run("MF") };
}
const item = (n, extra = {}) => ({ videoId: `alt${String(n).padStart(8, "0")}`, title: `Альтернатива ${n}`, channel: "Канал", lang: "uk", hasUkDub: false, score: 0.9, ...extra });
const mount = (t, data, settings = {}) => t.Panel.mount({ videoId: "dQw4w9WgXcQ", videoTitle: "Що це", data, settings: { kidsMode: false, ...settings } });
const root = (t) => t.document.getElementById("mf-panel-host").shadowRoot;
const text = (t) => root(t).textContent;

// ---------------------------------------------------------------- common.js
test("videoIdFromHref: watch, shorts, youtu.be, сміття", () => {
  const { MF } = setup();
  assert.strictEqual(MF.videoIdFromHref("/watch?v=dQw4w9WgXcQ&t=10s"), "dQw4w9WgXcQ");
  assert.strictEqual(MF.videoIdFromHref("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1"), "dQw4w9WgXcQ");
  assert.strictEqual(MF.videoIdFromHref("/shorts/abcdefghijk?feature=share"), "abcdefghijk");
  assert.strictEqual(MF.videoIdFromHref("https://youtu.be/dQw4w9WgXcQ?t=5"), "dQw4w9WgXcQ");
  assert.strictEqual(MF.videoIdFromHref("/channel/UC123"), null);
  assert.strictEqual(MF.videoIdFromHref("/watch"), null);
  assert.strictEqual(MF.videoIdFromHref(""), null);
  assert.strictEqual(MF.videoIdFromHref(null), null);
});

test("currentWatchVideoId: лише /watch, не shorts і не головна", () => {
  assert.strictEqual(setup().MF.currentWatchVideoId(), "dQw4w9WgXcQ");
  assert.strictEqual(setup({ url: "https://www.youtube.com/" }).MF.currentWatchVideoId(), null);
  assert.strictEqual(setup({ url: "https://www.youtube.com/shorts/abcdefghijk" }).MF.currentWatchVideoId(), null);
});

test("waitFor: повертає значення, коли воно з'явилось, і null за таймаутом; виняток у функції не валить", async () => {
  const { MF } = setup();
  let n = 0;
  assert.strictEqual(await MF.waitFor(() => (++n >= 3 ? "ok" : null), 1000, 10), "ok");
  assert.strictEqual(await MF.waitFor(() => null, 60, 10), null);
  assert.strictEqual(await MF.waitFor(() => { throw new Error("x"); }, 40, 10), null);
});

test("el(): назви з HTML не виконуються (textContent, не innerHTML)", () => {
  const { MF, document } = setup();
  const el = MF.el("div", { text: '<img src=x onerror="alert(1)">' });
  document.body.append(el);
  assert.strictEqual(document.querySelectorAll("img").length, 0);
});

// ---------------------------------------------------------------- вставка панелі
test("панель вставляється першою в #secondary-inner, у Shadow DOM, один раз", async () => {
  const t = setup();
  assert.strictEqual(await mount(t, { level: 1, source: { lang: "ru", confidence: 0.93 }, items: [item(1)] }), true);
  const host = t.document.getElementById("mf-panel-host");
  assert.strictEqual(host.parentElement.id, "secondary-inner");
  assert.strictEqual(host.parentElement.firstElementChild, host);
  await t.Panel.ensureMounted();
  assert.strictEqual(t.document.querySelectorAll("#mf-panel-host").length, 1);
});

test("якщо YouTube перемалював сайдбар і викинув панель, ensureMounted повертає її", async () => {
  const t = setup();
  await mount(t, { level: 1, source: { lang: "ru", confidence: 0.93 }, items: [item(1)] });
  t.document.getElementById("mf-panel-host").remove();
  assert.strictEqual(t.Panel.isMounted(), false);
  await t.Panel.ensureMounted();
  assert.ok(t.document.getElementById("mf-panel-host"));
});

test("після закриття × панель не повертається сама для цього відео", async () => {
  const t = setup();
  await mount(t, { level: 1, source: { lang: "ru", confidence: 0.93 }, items: [item(1)] });
  root(t).querySelector(".close").click();
  assert.strictEqual(t.document.getElementById("mf-panel-host"), null);
  await t.Panel.ensureMounted();
  assert.strictEqual(t.document.getElementById("mf-panel-host"), null);
});

test("панель не вставляється, якщо користувач уже перейшов на інше відео", async () => {
  const t = setup({ url: "https://www.youtube.com/watch?v=OTHERVIDEO1" });
  assert.strictEqual(await mount(t, { level: 1, items: [item(1)] }), false);
});

test("немає місця для вставки (#secondary зник) → false після таймауту, без винятку", { timeout: 9000 }, async () => {
  const t = setup();
  t.document.body.innerHTML = "";
  assert.strictEqual(await mount(t, { level: 1, items: [item(1)] }), false);
});

test("reset() прибирає панель зі сторінки", async () => {
  const t = setup();
  await mount(t, { level: 1, items: [item(1)] });
  t.Panel.reset();
  assert.strictEqual(t.document.getElementById("mf-panel-host"), null);
});

// ---------------------------------------------------------------- вміст
test("показує не більше 3 альтернатив, з дубляжем першими", async () => {
  const t = setup();
  await mount(t, { level: 4, source: { lang: "ru", confidence: 0.9 }, items: [item(1, { lang: "en" }), item(2, { lang: "en" }), item(3, { lang: "en" }), item(4, { lang: "en", hasUkDub: true })] });
  const links = [...root(t).querySelectorAll("a.item")];
  assert.strictEqual(links.length, 3);
  assert.match(links[0].getAttribute("href"), /alt00000004/, "відео з українським дубляжем має бути першим");
  assert.match(text(t), /Є український дубляж/);
});

test("текст джерела: впевнено → «Це відео російською», невпевнено → «Мову не визначено»", async () => {
  let t = setup();
  await mount(t, { level: 1, source: { lang: "ru", confidence: 0.93 }, items: [item(1)] });
  assert.match(text(t), /Це відео російською/);
  t = setup();
  await mount(t, { level: 1, source: { lang: "ru", confidence: 0.5 }, items: [item(1)] });
  assert.match(text(t), /Мову цього відео не визначено/);
  assert.doesNotMatch(text(t), /російською/);
});

test("рівень 5 (items порожній): повідомлення «нічого не знайшли» і велика кнопка", async () => {
  const t = setup();
  await mount(t, { level: 5, source: { lang: "ru", confidence: 0.9 }, items: [] });
  assert.match(text(t), /поки нічого не знайшли/);
  assert.ok(root(t).querySelector("button.btn"));
  assert.match(root(t).querySelector("button.btn").textContent, /Хочу українське/);
});

test("рівні 1–3: лише маленьке посилання; рівні 4–5: велика кнопка", async () => {
  for (const [level, big] of [[1, false], [2, false], [3, false], [4, true], [5, true]]) {
    const t = setup();
    await mount(t, { level, source: { lang: "ru", confidence: 0.9 }, items: level === 5 ? [] : [item(1)] });
    assert.strictEqual(!!root(t).querySelector("button.btn"), big, `рівень ${level}`);
    assert.strictEqual(!!root(t).querySelector("button.link"), !big, `рівень ${level}`);
  }
});

test("XSS: назва і канал з HTML показуються текстом", async () => {
  const t = setup();
  await mount(t, { level: 1, source: { lang: "ru", confidence: 0.9 }, items: [item(1, { title: '<img src=x onerror="window.pwned=1">', channel: "<b>x</b>" })] });
  assert.strictEqual(root(t).querySelectorAll("img.thumb").length, 1, "лише наша мініатюра");
  assert.strictEqual(root(t).querySelectorAll("b").length, 0);
  assert.strictEqual(t.window.pwned, undefined);
});

test("посилання на альтернативу екранується, мініатюра береться з i.ytimg.com", async () => {
  const t = setup();
  await mount(t, { level: 1, source: { lang: "ru", confidence: 0.9 }, items: [item(1, { videoId: "a&b=c#d" })] });
  const a = root(t).querySelector("a.item");
  assert.strictEqual(a.getAttribute("href"), "/watch?v=a%26b%3Dc%23d");
  assert.match(root(t).querySelector("img.thumb").getAttribute("src"), /^https:\/\/i\.ytimg\.com\/vi\//);
});

test("клік по альтернативі шле подію alt_clicked з рівнем, без id і назви", async () => {
  const t = setup();
  await mount(t, { level: 2, source: { lang: "ru", confidence: 0.9 }, items: [item(1)] });
  root(t).querySelector("a.item").addEventListener("click", (e) => e.preventDefault());
  root(t).querySelector("a.item").click();
  const ev = t.sent.find((m) => m.type === "event");
  assert.deepEqual({ ...ev }, { type: "event", eventType: "alt_clicked", level: 2 });
});

test("темна тема YouTube → клас dark на панелі", async () => {
  const t = setup({ dark: true });
  await mount(t, { level: 1, items: [item(1)] });
  assert.ok(root(t).querySelector(".mf.dark"));
});

// ---------------------------------------------------------------- «Хочу українське»
test("звичайний режим: кнопка відкриває поле до 280 символів, відправка шле текст", async () => {
  const t = setup();
  await mount(t, { level: 4, source: { lang: "ru", confidence: 0.9 }, items: [item(1, { lang: "en" })] });
  root(t).querySelector("button.btn").click();
  const ta = root(t).querySelector("textarea");
  assert.strictEqual(ta.getAttribute("maxlength"), "280");
  ta.value = "огляд ноутбуків";
  ta.dispatchEvent(new t.window.Event("input"));
  assert.match(root(t).querySelector(".counter").textContent, /^15 \/ 280$/);
  [...root(t).querySelectorAll("button.btn")].find((b) => /Надіслати/.test(b.textContent)).click();
  await sleep(20);
  const wish = t.sent.find((m) => m.type === "wish");
  assert.strictEqual(wish.userText, "огляд ноутбуків");
  assert.strictEqual(wish.level, 4);
  assert.match(text(t), /Запит збережено/);
  assert.ok(t.sent.some((m) => m.type === "event" && m.eventType === "wish_sent"));
});

test("дитячий режим: текстового поля немає, кнопка одразу шле запит з порожнім текстом", async () => {
  const t = setup();
  await mount(t, { level: 5, source: { lang: "ru", confidence: 0.9 }, items: [] }, { kidsMode: true });
  root(t).querySelector("button.btn").click();
  assert.strictEqual(root(t).querySelector("textarea"), null);
  await sleep(20);
  assert.strictEqual(t.sent.find((m) => m.type === "wish").userText, "");
});

test("«Скасувати» повертає кнопку без відправки", async () => {
  const t = setup();
  await mount(t, { level: 4, source: { lang: "ru", confidence: 0.9 }, items: [item(1)] });
  root(t).querySelector("button.btn").click();
  [...root(t).querySelectorAll("button")].find((b) => /Скасувати/.test(b.textContent)).click();
  assert.strictEqual(root(t).querySelector("textarea"), null);
  assert.strictEqual(t.sent.filter((m) => m.type === "wish").length, 0);
});

test("ліміт: після відповіді reason=limit показано «На сьогодні запитів досить»", async () => {
  const t = setup({ sendMessage: (m) => (m.type === "wish" ? { ok: true, data: { ok: false, reason: "limit" } } : { ok: true, data: {} }) });
  await mount(t, { level: 5, source: { lang: "ru", confidence: 0.9 }, items: [] }, { kidsMode: true });
  root(t).querySelector("button.btn").click();
  await sleep(20);
  assert.match(text(t), /запитів досить/);
});

test("помилка бекенду: показано повідомлення, кнопка лишається для повтору", async () => {
  const t = setup({ sendMessage: (m) => (m.type === "wish" ? { ok: false, error: "HTTP 500" } : { ok: true, data: {} }) });
  await mount(t, { level: 5, source: { lang: "ru", confidence: 0.9 }, items: [] }, { kidsMode: true });
  root(t).querySelector("button.btn").click();
  await sleep(20);
  assert.match(text(t), /Не вдалося зберегти запит/);
  assert.ok(root(t).querySelector("button.btn"), "має бути можливість спробувати ще раз");
});

test("клавіші в нашому полі не доходять до гарячих клавіш YouTube (пробіл, k, f)", async () => {
  const t = setup();
  await mount(t, { level: 4, source: { lang: "ru", confidence: 0.9 }, items: [item(1)] });
  root(t).querySelector("button.btn").click();
  const ta = root(t).querySelector("textarea");
  let youtubeSaw = 0;
  t.document.addEventListener("keydown", () => youtubeSaw++);
  t.window.addEventListener("keydown", () => youtubeSaw++);
  for (const key of [" ", "k", "f"]) ta.dispatchEvent(new t.window.KeyboardEvent("keydown", { key, bubbles: true, composed: true, cancelable: true }));
  assert.strictEqual(youtubeSaw, 0);
  // а поза нашим полем клавіші працюють як зазвичай
  t.document.body.dispatchEvent(new t.window.KeyboardEvent("keydown", { key: "k", bubbles: true }));
  assert.strictEqual(youtubeSaw >= 1, true);
});

// ---------------------------------------------------------------- заготовки, update(), showError()
const mountLoading = (t, hint = { lang: "ru", confidence: 0.93 }, settings = {}) =>
  t.Panel.mount({ videoId: "dQw4w9WgXcQ", videoTitle: "Що це", data: null, hint, settings: { kidsMode: false, ...settings } });

test("data=null: заготовки (3 штуки), підпис «Шукаємо…», мова з оцінки розширення, жодних посилань і кнопок бажання", async () => {
  const t = setup();
  assert.strictEqual(await mountLoading(t), true);
  assert.strictEqual(root(t).querySelectorAll("li.sk").length, 3);
  assert.ok(root(t).querySelector("ul[aria-busy='true']"));
  assert.match(text(t), /Шукаємо українські альтернативи/);
  assert.match(text(t), /Це відео російською/);
  assert.strictEqual(root(t).querySelectorAll("a.item").length, 0);
  assert.strictEqual(root(t).querySelector("button.btn"), null);
  assert.strictEqual(root(t).querySelector("button.link"), null);
});

test("data=null без hint: не падає, мову не вигадує", async () => {
  const t = setup();
  assert.strictEqual(await mountLoading(t, null), true);
  assert.doesNotMatch(text(t), /російською/);
});

test("update(): заготовки замінюються результатом, панель одна, подія подій не шлеться панеллю сама", async () => {
  const t = setup();
  await mountLoading(t);
  assert.strictEqual(await t.Panel.update("dQw4w9WgXcQ", { level: 1, source: { lang: "ru", confidence: 0.95 }, items: [item(1), item(2)] }), true);
  assert.strictEqual(root(t).querySelectorAll("li.sk").length, 0);
  assert.strictEqual(root(t).querySelectorAll("a.item").length, 2);
  assert.strictEqual(t.document.querySelectorAll("#mf-panel-host").length, 1);
  assert.ok(!t.sent.some((m) => m.type === "event" && m.eventType === "panel_shown"));
});

test("update() для іншого відео ігнорується", async () => {
  const t = setup();
  await mountLoading(t);
  assert.strictEqual(await t.Panel.update("OTHERVIDEO1", { level: 1, items: [item(1)] }), false);
  assert.strictEqual(root(t).querySelectorAll("li.sk").length, 3);
});

test("update() без попереднього mount() нічого не робить", async () => {
  const t = setup();
  assert.strictEqual(await t.Panel.update("dQw4w9WgXcQ", { level: 1, items: [item(1)] }), false);
  assert.strictEqual(t.document.getElementById("mf-panel-host"), null);
});

test("закрили × під час завантаження: відповідь сервера панель не повертає", async () => {
  const t = setup();
  await mountLoading(t);
  root(t).querySelector(".close").click();
  await t.Panel.update("dQw4w9WgXcQ", { level: 1, items: [item(1)] });
  assert.strictEqual(t.document.getElementById("mf-panel-host"), null);
});

test("showError(): повідомлення і кнопка, клік викликає повтор", async () => {
  const t = setup();
  await mountLoading(t);
  let retried = 0;
  t.Panel.showError("dQw4w9WgXcQ", () => retried++);
  assert.match(text(t), /Не вдалося завантажити альтернативи/);
  assert.strictEqual(root(t).querySelectorAll("li.sk").length, 0);
  [...root(t).querySelectorAll("button")].find((b) => /Спробувати ще раз/.test(b.textContent)).click();
  assert.strictEqual(retried, 1);
});

test("showError() для іншого відео ігнорується; після помилки update() знімає повідомлення", async () => {
  const t = setup();
  await mountLoading(t);
  t.Panel.showError("OTHERVIDEO1", () => {});
  assert.doesNotMatch(text(t), /Не вдалося/);
  t.Panel.showError("dQw4w9WgXcQ", () => {});
  await t.Panel.update("dQw4w9WgXcQ", { level: 1, source: { lang: "ru", confidence: 0.95 }, items: [item(1)] });
  assert.doesNotMatch(text(t), /Не вдалося/);
  assert.strictEqual(root(t).querySelectorAll("a.item").length, 1);
});

test("YouTube перемалював сайдбар під час завантаження: заготовки повертаються на місце", async () => {
  const t = setup();
  await mountLoading(t);
  t.document.getElementById("mf-panel-host").remove();
  await t.Panel.ensureMounted();
  assert.strictEqual(root(t).querySelectorAll("li.sk").length, 3);
});

test("швидкі повторні кліки «Надіслати» створюють один запит, а не дублікати", { todo: "P2: немає захисту від повторного натискання: 3 швидкі кліки = 3 wish (3 рядки в БД і 3 одиниці добового ліміту)" }, async () => {
  const t = setup({ sendMessage: async (m) => { if (m.type === "wish") await sleep(50); return { ok: true, data: { ok: true } }; } });
  await mount(t, { level: 5, source: { lang: "ru", confidence: 0.9 }, items: [] });
  root(t).querySelector("button.btn").click();
  root(t).querySelector("textarea").value = "текст";
  const send = [...root(t).querySelectorAll("button.btn")].find((b) => /Надіслати/.test(b.textContent));
  send.click(); send.click(); send.click();
  await sleep(200);
  assert.strictEqual(t.sent.filter((m) => m.type === "wish").length, 1);
});
