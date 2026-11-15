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
