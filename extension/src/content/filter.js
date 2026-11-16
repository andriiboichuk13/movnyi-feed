// =====================================================================
// ДИТЯЧИЙ РЕЖИМ: ховає російськомовні картки на головній
// (і в сайдбарі та пошуку, якщо KIDS_FILTER_SIDEBAR_AND_SEARCH = true).
//
// Як працює (за ТЗ):
// 1. MutationObserver ловить нові картки (нескінченний скрол).
// 2. З картки беремо videoId і назву.
// 3. Перший прохід миттєво на клієнті (літери + модель Chrome).
// 4. Невпевнені картки пачкою йдуть на POST /classify.
// 5. Ховаємо CSS-класом, вузли НЕ видаляємо:
//    на головній (сітка) картка стає плиткою «Приховано дитячим режимом» того ж розміру,
//    щоб не лишалося дір перед полицею Shorts; у сайдбарі й пошуку (списки) картка зникає.
// Ховаємо лише lang = ru з впевненістю ≥ порогу. unknown лишаємо.
// =====================================================================

const MF_Filter = (() => {
  const STYLE_ID = "mf-filter-style";
  const HIDDEN = "mf-hidden"; // зникає повністю (списки)
  const MASKED = "mf-masked"; // плитка-заглушка (сітка на головній)

  let active = false;
  let observer = null;
  let scanTimer = null;
  let classifyTimer = null;
  const pending = new Map(); // videoId → Set<card>
  const counted = new Set(); // videoId, які вже пораховані як приховані
  let hiddenToReport = 0;
  let reportTimer = null;

  function cardSelector() {
    const s = MF_SELECTORS.cards;
    const list = [...s.home];
    if (MF_CONFIG.KIDS_FILTER_SIDEBAR_AND_SEARCH) list.push(...s.sidebar, ...s.search);
    return list.join(", ");
  }

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      .${HIDDEN} { display: none !important; }
      .${MASKED} { position: relative !important; pointer-events: none !important; }
      .${MASKED} > * { visibility: hidden !important; }
      .${MASKED}::after {
        content: "Приховано дитячим режимом\\Aросійськомовне відео";
        white-space: pre-line;
        position: absolute; inset: 0;
        display: flex; align-items: center; justify-content: center; text-align: center;
        padding: 12px; box-sizing: border-box;
        border-radius: 12px; border-left: 4px solid #ffd500;
        background: #f2f2f2; color: #606060;
        font: 500 14px/20px "Roboto", Arial, sans-serif;
      }
      html[dark] .${MASKED}::after { background: #272727; color: #aaaaaa; }
    `;
    document.documentElement.append(style);
  }

  function readCard(card) {
    const link = MF.qs(card, MF_SELECTORS.cardLink);
    const videoId = link ? MF.videoIdFromHref(link.getAttribute("href")) : null;
    const titleEl = MF.qs(card, MF_SELECTORS.cardTitle);
    let title = titleEl ? (titleEl.getAttribute("title") || titleEl.textContent || "").trim() : "";
    if (!title && link) title = (link.getAttribute("aria-label") || link.getAttribute("title") || "").trim();
    const channelEl = MF.qs(card, MF_SELECTORS.cardChannel);
    const channel = channelEl ? channelEl.textContent.trim() : "";
    return { videoId, title, channel };
  }

  function isGridCard(card) {
    return MF_SELECTORS.cards.home.some((sel) => card.matches(sel));
  }

  function setHidden(card, hide) {
    const cls = isGridCard(card) ? MASKED : HIDDEN;
    card.classList.remove(HIDDEN, MASKED);
    if (hide) card.classList.add(cls);
  }

  function apply(card, videoId, result) {
    if (card.dataset.mfVid !== videoId) return; // YouTube вже переписав картку під інше відео
    const hide = result.lang === "ru" && result.confidence >= MF_CONFIG.LANG_THRESHOLD;
    setHidden(card, hide);
    card.dataset.mfLang = result.lang;
    if (hide && !counted.has(videoId)) {
      counted.add(videoId);
      reportHidden();
      MF.send("videoAction", {
        action: "hid",
        videoUrl: `https://www.youtube.com/watch?v=${encodeURIComponent(videoId)}`,
      });
    }
  }

  function reportHidden() {
    hiddenToReport += 1;
    clearTimeout(reportTimer);
    reportTimer = setTimeout(() => {
      const count = hiddenToReport;
      hiddenToReport = 0;
      if (count > 0) MF.send("kidsHidden", { count });
    }, 2000);
  }

  async function processCard(card) {
    const { videoId, title } = readCard(card);
    if (!videoId) return;
    if (card.dataset.mfVid === videoId) return; // вже обробили
    card.dataset.mfVid = videoId;
    setHidden(card, false);

    // Крок 2: літери і службові слова (синхронно, без мерехтіння).
    const quick = MF_LANG.quick(title);
    if (quick && MF_LANG.isConfident(quick, MF_CONFIG.LANG_THRESHOLD)) {
      return apply(card, videoId, quick);
    }

    // Крок 3: модель Chrome.
    const model = await MF_LANG.byModel(title);
    if (model && MF_LANG.isConfident(model, MF_CONFIG.LANG_THRESHOLD)) {
      return apply(card, videoId, model);
    }

    // Крок 4: невпевнені → бекенд пачкою.
    if (!pending.has(videoId)) pending.set(videoId, new Set());
    pending.get(videoId).add(card);
    scheduleClassify();
  }

  function scheduleClassify() {
    clearTimeout(classifyTimer);
    classifyTimer = setTimeout(flushClassify, MF_CONFIG.CLASSIFY_DEBOUNCE_MS);
  }

  async function flushClassify() {
    if (!active || pending.size === 0) return;
    const batch = new Map(pending);
    pending.clear();
    const ids = [...batch.keys()];

    for (let i = 0; i < ids.length; i += MF_CONFIG.CLASSIFY_BATCH_SIZE) {
      const chunk = ids.slice(i, i + MF_CONFIG.CLASSIFY_BATCH_SIZE);
      const res = await MF.send("classify", { videoIds: chunk });
      if (!active) return;
      if (!res.ok || !res.data) continue; // бекенд недоступний → нічого не ховаємо
      const results = res.data.results || {};
      for (const id of chunk) {
        const r = results[id];
        if (!r) continue;
        for (const card of batch.get(id)) apply(card, id, r);
      }
    }
  }

  function scan() {
    if (!active) return;
    const cards = document.querySelectorAll(cardSelector());
    for (const card of cards) processCard(card);
  }

  function scheduleScan() {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(scan, 150);
  }

  function start() {
    if (active) return;
    active = true;
    injectStyle();
    observer = new MutationObserver(scheduleScan);
    // attributes/href: YouTube перевикористовує картки й міняє лише посилання.
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["href"] });
    scan();
  }

  function stop() {
    if (!active) return;
    active = false;
    if (observer) observer.disconnect();
    observer = null;
    clearTimeout(scanTimer);
    clearTimeout(classifyTimer);
    pending.clear();
    for (const card of document.querySelectorAll("[data-mf-vid]")) {
      card.classList.remove(HIDDEN, MASKED);
      delete card.dataset.mfVid;
      delete card.dataset.mfLang;
    }
  }

  function setActive(on) {
    if (on) start();
    else stop();
  }

  return { setActive, scan };
})();
