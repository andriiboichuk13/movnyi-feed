// Автогенеровано tools/build-selectors-check.js. Не редагуйте вручну.
// Вставте в консоль (F12 → Console) на youtube.com: на головній, на сторінці відео і в пошуку.
(() => {
// =====================================================================
// СЕЛЕКТОРИ YOUTUBE. YouTube часто змінює верстку.
// Якщо щось перестало працювати, найімовірніше, зламався селектор тут.
// Як перевірити: відкрийте YouTube → F12 → вкладка Elements →
// Ctrl+F у дереві й вставте селектор. Має знайтися потрібний елемент.
//
// Кожен пункт це СПИСОК: беремо перший, що спрацював.
// Новий варіант верстки просто додавайте в початок списку.
// =====================================================================

const MF_SELECTORS = {
  // Куди вставляти панель альтернатив на сторінці відео (перший видимий).
  panelHost: ["#secondary-inner", "#secondary", "#below"],

  // Контейнер сторінки відео, у нього є атрибут video-id.
  watchFlexy: ["ytd-watch-flexy"],

  // Назва відео на сторінці перегляду.
  watchTitle: [
    "ytd-watch-metadata h1 yt-formatted-string",
    "ytd-watch-metadata h1",
    "h1.title",
  ],

  // Текст опису відео (лише сам опис, без рядка «1,2 млн переглядів 6 дн. тому»,
  // бо той рядок написаний мовою інтерфейсу YouTube і заплутає визначення мови).
  watchDescription: [
    "ytd-watch-metadata ytd-text-inline-expander yt-attributed-string",
    "#description-inline-expander yt-attributed-string",
    "#description-inline-expander #attributed-snippet-text",
    "#description-inline-expander #expanded",
    "#description-inline-expander",
  ],

  // Картки відео, які фільтрує дитячий режим.
  cards: {
    home: ["ytd-browse[page-subtype='home'] ytd-rich-item-renderer"],
    sidebar: [
      "#related ytd-compact-video-renderer",
      "#related yt-lockup-view-model",
    ],
    search: ["ytd-search ytd-video-renderer"],
  },

  // Усередині картки: посилання на відео, назва, канал.
  cardLink: [
    "a#thumbnail[href]",
    "a[href*='/watch?v=']",
    "a[href*='/shorts/']",
  ],
  cardTitle: [
    "#video-title",
    "a.yt-lockup-metadata-view-model__title",
    "h3 a",
    "h3",
  ],
  cardChannel: [
    "ytd-channel-name a",
    "#channel-name a",
    ".yt-content-metadata-view-model__metadata-text",
  ],
};

  const page = location.pathname === "/watch" ? "watch" : location.pathname === "/results" ? "search" : location.pathname === "/" ? "home" : "other";
  const first = (list, root = document) => { for (const s of list) { const n = root.querySelectorAll(s).length; if (n) return { s, n }; } return null; };
  const rows = [];
  const check = (name, list, root = document) => {
    const per = list.map((s) => ({ s, n: (() => { try { return root.querySelectorAll(s).length; } catch (e) { return "INVALID"; } })() }));
    const hit = per.find((p) => typeof p.n === "number" && p.n > 0);
    rows.push({ група: name, статус: hit ? "OK" : "НЕ ЗНАЙДЕНО", "спрацював": hit ? hit.s : "-", кількість: hit ? hit.n : 0, "усі варіанти": per.map((p) => p.n).join(" / ") });
  };
  const groups = {
    home: [["cards.home", MF_SELECTORS.cards.home]],
    watch: [["panelHost", MF_SELECTORS.panelHost], ["watchFlexy", MF_SELECTORS.watchFlexy], ["watchTitle", MF_SELECTORS.watchTitle], ["watchDescription", MF_SELECTORS.watchDescription], ["cards.sidebar", MF_SELECTORS.cards.sidebar]],
    search: [["cards.search", MF_SELECTORS.cards.search]],
    other: [],
  }[page];
  groups.forEach(([n, l]) => check(n, l));

  // Усередині знайденої картки мають знаходитись посилання, назва і канал.
  const cardList = { home: MF_SELECTORS.cards.home, watch: MF_SELECTORS.cards.sidebar, search: MF_SELECTORS.cards.search, other: [] }[page];
  const card = cardList.map((s) => document.querySelector(s)).find(Boolean);
  if (card) {
    for (const [n, l] of [["cardLink", MF_SELECTORS.cardLink], ["cardTitle", MF_SELECTORS.cardTitle], ["cardChannel", MF_SELECTORS.cardChannel]]) check(n + " (у першій картці)", l, card);
    const link = first(MF_SELECTORS.cardLink, card);
    const a = link && card.querySelector(link.s);
    console.log("Перша картка: href =", a && a.getAttribute("href"), "| назва =", (() => { const t = first(MF_SELECTORS.cardTitle, card); const e = t && card.querySelector(t.s); return e && (e.getAttribute("title") || e.textContent || "").trim(); })());
  }
  console.log("Сторінка:", page, "| Якщо рядок НЕ ЗНАЙДЕНО — знайдіть новий елемент у F12 → Elements і додайте селектор НА ПОЧАТОК списку в src/selectors.js");
  console.table(rows);
  const bad = rows.filter((r) => r.статус !== "OK").length;
  console.log(bad ? "❌ Проблем: " + bad : "✅ Усі селектори для цієї сторінки працюють");
  return rows;
})();
