// Карта покриття дитячого режиму: на яких сторінках YouTube російська картка ховається, а на яких ні.
// Це не «баги тестів»: тести фіксують, ЩО ПОКРИВАЄ код зараз. Непокриті поверхні позначені todo (P2),
// щоб їх було видно у звіті; коли додасте селектор у selectors.js, відповідний тест почне проходити
// (а node покаже, що todo-тест неочікувано пройшов, і можна зняти позначку).
const test = require("node:test");
const assert = require("node:assert");
const { loadDom, vid } = require("./helpers/load-dom");

const RU = "Что будет, если смешать колу и ментос? Эксперимент";
const A = (id) => `<a id="thumbnail" href="/watch?v=${id}"></a>`;
const T = `<h3><a id="video-title" title="${RU}">${RU}</a></h3>`;
const NEW = (id) => `<a href="/watch?v=${id}"></a><h3><a class="yt-lockup-metadata-view-model__title" href="/watch?v=${id}" aria-label="${RU}"><span>${RU}</span></a></h3>`;

// [назва поверхні, HTML, селектор картки, причина, якщо не покрито]
const SURFACES = [
  ["головна (FYP)", () => `<ytd-browse page-subtype="home"><ytd-rich-item-renderer>${A(vid())}${T}</ytd-rich-item-renderer></ytd-browse>`, "ytd-rich-item-renderer"],
  ["сторінка відео: рекомендації збоку (стара верстка)", () => `<div id="related"><ytd-compact-video-renderer>${A(vid())}${T}</ytd-compact-video-renderer></div>`, "ytd-compact-video-renderer"],
  ["сторінка відео: рекомендації збоку (нова верстка)", () => `<div id="related"><yt-lockup-view-model>${NEW(vid())}</yt-lockup-view-model></div>`, "yt-lockup-view-model"],
  ["пошук (стара верстка)", () => `<ytd-search><ytd-video-renderer>${A(vid())}${T}</ytd-video-renderer></ytd-search>`, "ytd-video-renderer"],
  ["пошук (нова верстка yt-lockup-view-model)", () => `<ytd-search><yt-lockup-view-model>${NEW(vid())}</yt-lockup-view-model></ytd-search>`, "yt-lockup-view-model", "P2: у пошуку з новою версткою картки не відповідають селектору ytd-search ytd-video-renderer"],
  ["підписки (/feed/subscriptions)", () => `<ytd-browse page-subtype="subscriptions"><ytd-rich-item-renderer>${A(vid())}${T}</ytd-rich-item-renderer></ytd-browse>`, "ytd-rich-item-renderer", "P2: селектор головної вимагає page-subtype='home'"],
  ["сторінка каналу, вкладка «Відео»", () => `<ytd-browse page-subtype="channels"><ytd-rich-item-renderer>${A(vid())}${T}</ytd-rich-item-renderer></ytd-browse>`, "ytd-rich-item-renderer", "P2: селектор головної вимагає page-subtype='home'"],
  ["історія переглядів", () => `<ytd-browse page-subtype="history"><ytd-video-renderer>${A(vid())}${T}</ytd-video-renderer></ytd-browse>`, "ytd-video-renderer", "P2: ytd-video-renderer фільтрується лише всередині ytd-search"],
  ["тренди / «Навігатор»", () => `<ytd-browse page-subtype="trending"><ytd-video-renderer>${A(vid())}${T}</ytd-video-renderer></ytd-browse>`, "ytd-video-renderer", "P2: те саме"],
  ["плейлист (список відео)", () => `<ytd-browse page-subtype="playlist"><ytd-playlist-video-renderer>${A(vid())}${T}</ytd-playlist-video-renderer></ytd-browse>`, "ytd-playlist-video-renderer", "P2: немає селектора для ytd-playlist-video-renderer"],
  ["Shorts-полиця на головній (reel)", () => `<ytd-browse page-subtype="home"><ytm-shorts-lockup-view-model><a href="/shorts/abcdefghijk"></a><h3><span>${RU}</span></h3></ytm-shorts-lockup-view-model></ytd-browse>`, "ytm-shorts-lockup-view-model", "P2: немає селектора для ytm-shorts-lockup-view-model"],
];

for (const [name, html, cardSel, todo] of SURFACES) {
  test(`дитячий режим ховає російську картку: ${name}`, { todo: todo || false }, async () => {
    const env = loadDom({ html: `<body>${html()}</body>`, chromeOpts: { sendMessage: async () => ({ ok: true, data: { results: {} } }) } });
    env.run("MF_Filter").setActive(true);
    const card = env.document.querySelector(cardSel);
    const hidden = card.classList.contains("mf-masked") || card.classList.contains("mf-hidden");
    assert.ok(hidden, `російська картка на «${name}» лишилась видимою`);
  });
}

test("поверхні поза DOM-фільтром (автовідтворення, кінцеві заставки, прямі посилання) розширенням не блокуються", { todo: "P2: це межа архітектури: фільтр ховає картки, а не блокує перегляд; перевіряється вручну, див. BYPASS.md" }, () => {
  assert.fail("автовідтворення наступного відео, кінцеві заставки й вставлене в адресний рядок посилання на російське відео фільтром не охоплюються");
});
