// =====================================================================
// ПАНЕЛЬ АЛЬТЕРНАТИВ на сторінці відео.
// Живе в Shadow DOM: стилі YouTube не ламають панель, а наші не ламають YouTube.
// =====================================================================

const MF_Panel = (() => {
  const HOST_ID = "mf-panel-host";
  let state = null; // { videoId, videoTitle, data, settings, dismissed }
  let mounting = false; // захист від подвійної вставки

  const LANG_NAMES = { uk: "українською", ru: "російською", en: "англійською", other: "іншою мовою" };

  const LEVEL_TEXT = {
    1: "Українською на ту саму тему",
    2: "Українською, знайдено в пошуку YouTube",
    3: "Українською на близьку тему",
    4: "Українською не знайшли. Англійською на ту саму тему",
    5: "Українською на цю тему поки нічого не знайшли",
  };

  const CSS = `
    :host { all: initial; display: block; margin: 0 0 16px; }
    .mf {
      --bg: #ffffff; --fg: #0f0f0f; --muted: #606060; --line: #e5e5e5;
      --accent: #0057b7; --accent-fg: #ffffff; --stripe: #ffd500; --soft: #eef4fc;
      font-family: "Roboto", "Arial", sans-serif; font-size: 14px; line-height: 20px;
      color: var(--fg); background: var(--bg);
      border: 1px solid var(--line); border-left: 4px solid var(--stripe);
      border-radius: 12px; padding: 12px 12px 12px 14px;
    }
    .mf.dark {
      --bg: #212121; --fg: #f1f1f1; --muted: #aaaaaa; --line: #3a3a3a;
      --accent: #6ea8ff; --accent-fg: #0f0f0f; --soft: #1c2a3f;
    }
    header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .brand { font-size: 16px; font-weight: 500; line-height: 22px; }
    .close {
      border: 0; background: transparent; color: var(--muted); cursor: pointer;
      width: 28px; height: 28px; border-radius: 50%; font-size: 18px; line-height: 28px;
    }
    .close:hover { background: var(--line); }
    .source { margin: 2px 0 0; color: var(--muted); font-size: 13px; }
    .level { margin: 10px 0 8px; font-weight: 500; }
    ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
    a.item {
      display: grid; grid-template-columns: 120px 1fr; gap: 10px; align-items: start;
      color: inherit; text-decoration: none; border-radius: 8px; padding: 4px;
      margin: -4px;
    }
    a.item:hover { background: var(--soft); }
    a.item:focus-visible, button:focus-visible, textarea:focus-visible {
      outline: 2px solid var(--accent); outline-offset: 2px;
    }
    .thumb {
      width: 120px; aspect-ratio: 16 / 9; border-radius: 8px; object-fit: cover;
      background: var(--line); display: block;
    }
    .title {
      font-weight: 500; display: -webkit-box; -webkit-line-clamp: 2;
      -webkit-box-orient: vertical; overflow: hidden;
    }
    .channel { color: var(--muted); font-size: 12px; line-height: 18px; margin-top: 2px; }
    .tag {
      display: inline-block; margin-top: 4px; padding: 0 6px; border-radius: 4px;
      font-size: 12px; line-height: 18px; background: var(--soft); color: var(--accent);
    }
    .wish { margin-top: 12px; }
    .btn {
      border: 0; border-radius: 18px; padding: 8px 16px; font: inherit; font-weight: 500;
      cursor: pointer; background: var(--accent); color: var(--accent-fg);
    }
    .btn.secondary { background: transparent; color: var(--muted); }
    .link {
      border: 0; background: none; padding: 0; font: inherit; font-size: 13px;
      color: var(--muted); text-decoration: underline; cursor: pointer;
    }
    .hint { color: var(--muted); font-size: 13px; margin: 0 0 8px; }
    textarea {
      width: 100%; box-sizing: border-box; min-height: 64px; resize: vertical;
      font: inherit; color: var(--fg); background: var(--bg);
      border: 1px solid var(--line); border-radius: 8px; padding: 8px;
    }
    .counter { color: var(--muted); font-size: 12px; text-align: right; }
    .row { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
    .done { color: var(--muted); font-size: 13px; margin: 0; }
    .mock { margin-top: 10px; color: var(--muted); font-size: 12px; }
    @media (prefers-reduced-motion: no-preference) {
      .mf { animation: mf-in 160ms ease-out; }
      @keyframes mf-in { from { opacity: 0; } to { opacity: 1; } }
    }
  `;

  function remove() {
    const host = document.getElementById(HOST_ID);
    if (host) host.remove();
  }

  function isMounted() {
    const host = document.getElementById(HOST_ID);
    return !!host && host.isConnected && !!state && host.dataset.videoId === state.videoId;
  }

  function sourceText(source) {
    // Невпевнений результат не показуємо як факт.
    if (!MF_LANG.isConfident(source, MF_CONFIG.LANG_THRESHOLD)) return "Мову цього відео не визначено";
    return `Це відео ${LANG_NAMES[source.lang] || LANG_NAMES.other}`;
  }

  // Показати панель. data = відповідь GET /alternatives.
  async function mount({ videoId, videoTitle, data, settings }) {
    state = { videoId, videoTitle, data, settings, dismissed: false, wishState: "idle" };
    return ensureMounted();
  }

  // Вставити панель, якщо її немає (YouTube іноді перемальовує сайдбар).
  async function ensureMounted() {
    if (!state || state.dismissed) return false;
    if (isMounted()) return true;
    if (mounting) return false;
    mounting = true;
    try {
      return await insert();
    } finally {
      mounting = false;
    }
  }

  async function insert() {
    remove();
    const target = await MF.waitFor(() => MF.qsVisible(document, MF_SELECTORS.panelHost), 6000);
    if (!target || !state || state.dismissed || MF.currentWatchVideoId() !== state.videoId) return false;

    const host = document.createElement("div");
    host.id = HOST_ID;
    host.dataset.videoId = state.videoId;
    const root = host.attachShadow({ mode: "open" });
    root.append(MF.el("style", { text: CSS }));
    root.append(build());
    target.prepend(host);
    return true;
  }

  function rerender() {
    const host = document.getElementById(HOST_ID);
    if (!host || !host.shadowRoot) return;
    const old = host.shadowRoot.querySelector(".mf");
    if (old) old.replaceWith(build());
  }

  function build() {
    const { data, settings } = state;
    const level = data.level || 5;
    const items = (data.items || [])
      .slice()
      .sort((a, b) => (b.hasUkDub ? 1 : 0) - (a.hasUkDub ? 1 : 0)) // з дубляжем першим
      .slice(0, MF_CONFIG.MAX_ALTERNATIVES);

    const box = MF.el("section", { class: "mf" + (MF.isDarkTheme() ? " dark" : ""), "aria-label": "Мовний фід" });

    box.append(
      MF.el("header", {}, [
        MF.el("div", {}, [
          MF.el("div", { class: "brand", text: "Мовний фід" }),
          MF.el("p", { class: "source", text: sourceText(data.source) }),
        ]),
        MF.el("button", {
          class: "close",
          title: "Сховати для цього відео",
          "aria-label": "Сховати для цього відео",
          text: "×",
          onclick: () => {
            state.dismissed = true;
            remove();
          },
        }),
      ])
    );

    if (items.length > 0) {
      box.append(MF.el("p", { class: "level", text: LEVEL_TEXT[level] || LEVEL_TEXT[1] }));
      const list = MF.el("ul");
      for (const it of items) list.append(MF.el("li", {}, [buildItem(it, level)]));
      box.append(list);
    } else {
      box.append(MF.el("p", { class: "level", text: LEVEL_TEXT[5] }));
    }

    box.append(buildWish(level, settings));

    if (MF_CONFIG.USE_MOCK) {
      box.append(MF.el("p", { class: "mock", text: "Демо-дані: бекенд ще не підключено." }));
    }
    return box;
  }

  function buildItem(it, level) {
    const tags = [];
    if (it.hasUkDub) tags.push("Є український дубляж");
    if (it.lang === "en" && !it.hasUkDub) tags.push("Англійською");

    return MF.el(
      "a",
      {
        class: "item",
        href: `/watch?v=${encodeURIComponent(it.videoId)}`,
        onclick: () => {
          MF.send("event", { eventType: "alt_clicked", level });
        },
      },
      [
        MF.el("img", { class: "thumb", src: it.thumbnail || MF.thumbUrl(it.videoId), alt: "", loading: "lazy" }),
        MF.el("div", {}, [
          MF.el("div", { class: "title", text: it.title || "Без назви" }),
          MF.el("div", { class: "channel", text: it.channel || "" }),
          ...tags.map((t) => MF.el("span", { class: "tag", text: t })),
        ]),
      ]
    );
  }

  // Кнопка «Хочу українське». Велика, коли драбина дійшла до англійського
  // або нічого не знайшла (рівні 4–5). Інакше маленьке посилання «Це не те».
  function buildWish(level, settings) {
    const wrap = MF.el("div", { class: "wish" });
    const kids = settings.kidsMode;

    if (state.wishState === "sent") {
      wrap.append(MF.el("p", { class: "done", text: "Запит збережено. Він допоможе зрозуміти, чого бракує українською." }));
      return wrap;
    }
    if (state.wishState === "limit") {
      wrap.append(MF.el("p", { class: "done", text: "На сьогодні запитів досить. Спробуйте завтра." }));
      return wrap;
    }
    if (state.wishState === "error") {
      wrap.append(MF.el("p", { class: "done", text: "Не вдалося зберегти запит. Перевірте інтернет і спробуйте ще раз." }));
    }

    if (state.wishState === "open" && !kids) {
      const counter = MF.el("div", { class: "counter", text: `0 / ${MF_CONFIG.WISH_TEXT_MAX}` });
      const ta = MF.el("textarea", {
        maxlength: String(MF_CONFIG.WISH_TEXT_MAX),
        placeholder: "Що саме хочете подивитися українською? Можна не заповнювати.",
        "aria-label": "Що саме хочете подивитися українською",
        oninput: (e) => (counter.textContent = `${e.target.value.length} / ${MF_CONFIG.WISH_TEXT_MAX}`),
      });
      wrap.append(
        ta,
        counter,
        MF.el("div", { class: "row" }, [
          MF.el("button", { class: "btn", text: "Надіслати запит", onclick: () => sendWish(level, ta.value) }),
          MF.el("button", {
            class: "btn secondary",
            text: "Скасувати",
            onclick: () => {
              state.wishState = "idle";
              rerender();
            },
          }),
        ])
      );
      setTimeout(() => ta.focus(), 0);
      return wrap;
    }

    // У дитячому режимі текстового поля немає: кнопка одразу надсилає запит.
    const open = () => {
      if (kids) return sendWish(level, "");
      state.wishState = "open";
      rerender();
    };

    if (level >= 4) {
      wrap.append(
        MF.el("p", { class: "hint", text: "Натисніть, якщо хочете таке відео українською. Ми збираємо ці теми для українських авторів." }),
        MF.el("button", { class: "btn", text: "Хочу українське", onclick: open })
      );
    } else {
      wrap.append(MF.el("button", { class: "link", text: "Це не те, хочу українське", onclick: open }));
    }
    return wrap;
  }

  async function sendWish(level, userText) {
    const res = await MF.send("wish", {
      videoId: state.videoId,
      videoTitle: state.videoTitle,
      level,
      userText,
    });
    if (res.ok && res.data && res.data.ok) {
      state.wishState = "sent";
      MF.send("event", { eventType: "wish_sent", level });
    } else if (res.ok && res.data && res.data.reason === "limit") {
      state.wishState = "limit";
    } else {
      state.wishState = "error";
    }
    rerender();
  }

  function reset() {
    state = null;
    remove();
  }

  return { mount, ensureMounted, remove, reset, isMounted };
})();
