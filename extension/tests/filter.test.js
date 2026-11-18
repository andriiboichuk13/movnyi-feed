// Тести дитячого режиму (filter.js) на імітованій верстці YouTube.
const test = require("node:test");
const assert = require("node:assert");
const { loadDom, homeCard, homePage, sidebarCard, searchCard, sleep } = require("./helpers/load-dom");

const RU = "Что будет, если смешать колу и ментос? Эксперимент"; // літери ы/э → ru
const UK = "Як приготувати борщ: рецепт від бабусі";
const EN = "How to fix a laptop";
const NEUTRAL = "Ремонт ноутбука"; // однаково пишеться обома мовами → йде на бекенд

// Створює сторінку; sendMessage імітує background.
function setup(html, { classify, detect, overrides } = {}) {
  const classifyCalls = [];
  const sent = [];
  const env = loadDom({
    html,
    overrides: { CLASSIFY_DEBOUNCE_MS: 10, ...overrides },
    chromeOpts: {
      detectLanguage: detect,
      sendMessage: async (msg) => {
        sent.push(msg);
        if (msg.type === "classify") {
          classifyCalls.push(msg.videoIds);
          if (!classify) return { ok: false, error: "no backend" };
          return classify(msg.videoIds);
        }
        return { ok: true, data: {} };
      },
    },
  });
  return { ...env, classifyCalls, sent, Filter: env.run("MF_Filter") };
}
const cards = (document) => [...document.querySelectorAll("ytd-rich-item-renderer, ytd-compact-video-renderer, ytd-video-renderer")];
const state = (el) => (el.classList.contains("mf-masked") ? "masked" : el.classList.contains("mf-hidden") ? "hidden" : "shown");
const idOf = (el) => el.querySelector("a[href]").getAttribute("href").split("v=")[1];

// ---------------------------------------------------------------- базова поведінка
test("головна: російська картка перетворюється на плитку-заглушку, українська й англійська лишаються", async () => {
  const t = setup(homePage([homeCard(RU), homeCard(UK), homeCard(EN)]));
  t.Filter.setActive(true);
  assert.deepStrictEqual(cards(t.document).map(state), ["masked", "shown", "shown"]);
});

test("сайдбар і пошук: російська картка зникає повністю (mf-hidden), а не маскується", () => {
  const t = setup(`<body><div id="related">${sidebarCard(RU)}${sidebarCard(UK)}</div><ytd-search>${searchCard(RU)}${searchCard(UK)}</ytd-search></body>`);
  t.Filter.setActive(true);
  assert.deepStrictEqual(cards(t.document).map(state), ["hidden", "shown", "hidden", "shown"]);
});

test("KIDS_FILTER_SIDEBAR_AND_SEARCH=false: сайдбар і пошук не чіпаємо, головну фільтруємо", () => {
  const t = setup(`<body><ytd-browse page-subtype="home">${homeCard(RU)}</ytd-browse><div id="related">${sidebarCard(RU)}</div></body>`, { overrides: { KIDS_FILTER_SIDEBAR_AND_SEARCH: false } });
  t.Filter.setActive(true);
  assert.deepStrictEqual(cards(t.document).map(state), ["masked", "shown"]);
});

test("нова верстка YouTube (yt-lockup-view-model) читається так само", () => {
  const t = setup(homePage([homeCard(RU, undefined, { newLayout: true }), homeCard(UK, undefined, { newLayout: true })]));
  t.Filter.setActive(true);
  assert.deepStrictEqual(cards(t.document).map(state), ["masked", "shown"]);
});

test("Shorts: посилання /shorts/ID теж розпізнається", () => {
  const html = `<body><ytd-browse page-subtype="home"><ytd-rich-item-renderer><a href="/shorts/abcdefghijk"></a><h3><a id="video-title" title="${RU}">${RU}</a></h3></ytd-rich-item-renderer></ytd-browse></body>`;
  const t = setup(html);
  t.Filter.setActive(true);
  assert.strictEqual(state(cards(t.document)[0]), "masked");
});

test("картка без посилання на відео (реклама, полиця) ігнорується без помилок", () => {
  const t = setup(`<body><ytd-browse page-subtype="home"><ytd-rich-item-renderer><div>Реклама</div></ytd-rich-item-renderer></ytd-browse></body>`);
  assert.doesNotThrow(() => t.Filter.setActive(true));
  assert.strictEqual(state(cards(t.document)[0]), "shown");
});

// ---------------------------------------------------------------- «unknown лишається видимим»
test("нейтральна назва без бекенду (недоступний) не ховається", async () => {
  const t = setup(homePage([homeCard(NEUTRAL)]));
  t.Filter.setActive(true);
  await sleep(80);
  assert.strictEqual(state(cards(t.document)[0]), "shown");
  assert.strictEqual(t.classifyCalls.length, 1, "невпевнені картки мали піти на /classify");
});

test("бекенд каже ru 0.95 → ховаємо; ru 0.79 → лишаємо (поріг 0.8); uk → лишаємо", async () => {
  const a = homeCard(NEUTRAL, "aaaaaaaaaaa"), b = homeCard(NEUTRAL, "bbbbbbbbbbb"), c = homeCard(NEUTRAL, "ccccccccccc");
  const t = setup(homePage([a, b, c]), {
    classify: (ids) => ({ ok: true, data: { results: {
      aaaaaaaaaaa: { lang: "ru", confidence: 0.95, source: "x" },
      bbbbbbbbbbb: { lang: "ru", confidence: 0.79, source: "x" },
      ccccccccccc: { lang: "uk", confidence: 0.99, source: "x" },
    } } }),
  });
  t.Filter.setActive(true);
  await sleep(80);
  assert.deepStrictEqual(cards(t.document).map(state), ["masked", "shown", "shown"]);
});

test("бекенд повернув unknown/відсутній id → не ховаємо", async () => {
  const t = setup(homePage([homeCard(NEUTRAL, "aaaaaaaaaaa")]), { classify: () => ({ ok: true, data: { results: {} } }) });
  t.Filter.setActive(true);
  await sleep(80);
  assert.strictEqual(state(cards(t.document)[0]), "shown");
});

test("модель Chrome впевнена, що ru → ховаємо без звернення до бекенду", async () => {
  const t = setup(homePage([homeCard(NEUTRAL)]), { detect: async () => ({ isReliable: true, languages: [{ language: "ru", percentage: 97 }] }) });
  t.Filter.setActive(true);
  await sleep(30);
  assert.strictEqual(state(cards(t.document)[0]), "masked");
  assert.strictEqual(t.classifyCalls.length, 0);
});

test("модель Chrome не впевнена (isReliable=false) → не ховаємо, питаємо бекенд", async () => {
  const t = setup(homePage([homeCard(NEUTRAL)]), { detect: async () => ({ isReliable: false, languages: [{ language: "ru", percentage: 97 }] }) });
  t.Filter.setActive(true);
  await sleep(80);
  assert.strictEqual(state(cards(t.document)[0]), "shown");
  assert.strictEqual(t.classifyCalls.length, 1);
});

test("впевнені en / other / uk від бекенду НІКОЛИ не ховаються (ховаємо лише ru)", async () => {
  const t = setup(homePage([homeCard(NEUTRAL, "aaaaaaaaaaa"), homeCard(NEUTRAL, "bbbbbbbbbbb"), homeCard(NEUTRAL, "ccccccccccc")]), {
    classify: () => ({ ok: true, data: { results: {
      aaaaaaaaaaa: { lang: "en", confidence: 0.99, source: "defaultAudioLanguage" },
      bbbbbbbbbbb: { lang: "other", confidence: 0.99, source: "defaultAudioLanguage" },
      ccccccccccc: { lang: "uk", confidence: 0.99, source: "defaultAudioLanguage" },
    } } }),
  });
  t.Filter.setActive(true);
  await sleep(80);
  assert.deepStrictEqual(cards(t.document).map(state), ["shown", "shown", "shown"]);
});

test("модель Chrome впевнена, що це en/uk → не ховаємо", async () => {
  for (const language of ["en", "uk"]) {
    const t = setup(homePage([homeCard(NEUTRAL)]), { detect: async () => ({ isReliable: true, languages: [{ language, percentage: 99 }] }) });
    t.Filter.setActive(true);
    await sleep(30);
    assert.strictEqual(state(cards(t.document)[0]), "shown", language);
  }
});

// ---------------------------------------------------------------- пачки
test("120 невпевнених карток → /classify пачками 50 + 50 + 20", async () => {
  const many = Array.from({ length: 120 }, (_, i) => homeCard(NEUTRAL, `id${String(i).padStart(9, "0")}`));
  const t = setup(homePage(many), { classify: () => ({ ok: true, data: { results: {} } }) });
  t.Filter.setActive(true);
  await sleep(120);
  assert.deepStrictEqual(t.classifyCalls.map((c) => c.length), [50, 50, 20]);
});

test("одне й те саме відео двічі на сторінці: один запит, обидві картки сховані", async () => {
  const t = setup(homePage([homeCard(NEUTRAL, "aaaaaaaaaaa"), homeCard(NEUTRAL, "aaaaaaaaaaa")]), {
    classify: () => ({ ok: true, data: { results: { aaaaaaaaaaa: { lang: "ru", confidence: 0.95, source: "x" } } } }),
  });
  t.Filter.setActive(true);
  await sleep(80);
  assert.deepStrictEqual(t.classifyCalls.flat(), ["aaaaaaaaaaa"]);
  assert.deepStrictEqual(cards(t.document).map(state), ["masked", "masked"]);
});

// ---------------------------------------------------------------- динаміка YouTube
test("нескінченний скрол: нова картка, додана пізніше, фільтрується MutationObserver", async () => {
  const t = setup(homePage([homeCard(UK)]));
  t.Filter.setActive(true);
  t.document.querySelector("#contents").insertAdjacentHTML("beforeend", homeCard(RU));
  await sleep(350);
  assert.deepStrictEqual(cards(t.document).map(state), ["shown", "masked"]);
});

test("YouTube перевикористав картку під інше відео: стара заглушка знімається", async () => {
  const t = setup(homePage([homeCard(RU, "aaaaaaaaaaa")]));
  t.Filter.setActive(true);
  const card = cards(t.document)[0];
  assert.strictEqual(state(card), "masked");
  card.querySelector("a#thumbnail").setAttribute("href", "/watch?v=bbbbbbbbbbb");
  card.querySelector("#video-title").setAttribute("title", UK);
  card.querySelector("#video-title").textContent = UK;
  await sleep(350);
  assert.strictEqual(state(card), "shown", "українське відео не має лишатися під заглушкою");
});

test("запізніла відповідь бекенду для старого відео не чіпає картку, що вже показує інше", async () => {
  let release;
  const gate = new Promise((r) => (release = r));
  const t = setup(homePage([homeCard(NEUTRAL, "aaaaaaaaaaa")]), {
    classify: async () => { await gate; return { ok: true, data: { results: { aaaaaaaaaaa: { lang: "ru", confidence: 0.95, source: "x" } } } }; },
  });
  t.Filter.setActive(true);
  await sleep(60); // /classify вже пішов і чекає
  const card = cards(t.document)[0];
  card.querySelector("a#thumbnail").setAttribute("href", "/watch?v=bbbbbbbbbbb");
  card.querySelector("#video-title").setAttribute("title", UK);
  card.querySelector("#video-title").textContent = UK;
  await sleep(350);
  release();
  await sleep(50);
  assert.strictEqual(state(card), "shown");
});

test("вимкнення дитячого режиму знімає всі заглушки й службові атрибути", () => {
  const t = setup(homePage([homeCard(RU), homeCard(UK)]) );
  t.Filter.setActive(true);
  t.Filter.setActive(false);
  assert.deepStrictEqual(cards(t.document).map(state), ["shown", "shown"]);
  assert.strictEqual(t.document.querySelectorAll("[data-mf-vid]").length, 0);
});

test("повторне вмикання після вимкнення працює (немає «залиплого» стану)", () => {
  const t = setup(homePage([homeCard(RU)]));
  t.Filter.setActive(true);
  t.Filter.setActive(false);
  t.Filter.setActive(true);
  assert.strictEqual(state(cards(t.document)[0]), "masked");
});

test("setActive(true) двічі не створює два спостерігачі й не падає", () => {
  const t = setup(homePage([homeCard(RU)]));
  t.Filter.setActive(true);
  assert.doesNotThrow(() => t.Filter.setActive(true));
  assert.strictEqual(t.document.querySelectorAll("#mf-filter-style").length, 1);
});

// ---------------------------------------------------------------- лічильники
test("лічильник прихованих: одне відео рахується один раз, звіт іде пачкою через ~2 с", async () => {
  const t = setup(homePage([homeCard(RU, "aaaaaaaaaaa"), homeCard(RU, "aaaaaaaaaaa"), homeCard(RU, "bbbbbbbbbbb")]));
  t.Filter.setActive(true);
  assert.strictEqual(t.sent.filter((m) => m.type === "kidsHidden").length, 0, "звіт не має йти миттєво");
  await sleep(2200);
  const reports = t.sent.filter((m) => m.type === "kidsHidden");
  assert.strictEqual(reports.length, 1);
  assert.strictEqual(reports[0].count, 2);
  const actions = t.sent.find((m) => m.type === "videoActions");
  assert.deepEqual(actions.items.map((i) => i.videoId).sort(), ["aaaaaaaaaaa", "bbbbbbbbbbb"]);
});

// ---------------------------------------------------------------- безпека DOM
test("вузли карток не видаляються з DOM (лише клас), щоб не ламати YouTube", () => {
  const t = setup(homePage([homeCard(RU)]));
  const before = cards(t.document).length;
  t.Filter.setActive(true);
  assert.strictEqual(cards(t.document).length, before);
});
