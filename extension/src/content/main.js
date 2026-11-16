// =====================================================================
// ГОЛОВНИЙ CONTENT SCRIPT. Запускається на youtube.com.
// YouTube це SPA: при переході між відео сторінка не перезавантажується,
// тому слухаємо подію yt-navigate-finish і додатково стежимо за URL.
// =====================================================================

(() => {
  let settings = { ...MF_DEFAULTS };
  let currentVideoId = null;
  let navToken = 0; // щоб відповідь для старого відео не намалювала панель на новому
  let lastHref = location.href;
  let lastTitle = "";
  let lastTitleVideoId = null;
  const countedWatched = new Set();

  async function refreshSettings() {
    settings = await MF.settings();
    MF_Filter.setActive(settings.enabled && settings.kidsMode);
  }

  // Назва відео, яке зараз відкрите. Чекаємо, доки YouTube оновить заголовок.
  async function readWatchTitle(videoId) {
    if (videoId === lastTitleVideoId && lastTitle) return lastTitle; // те саме відео, назва вже відома

    await MF.waitFor(() => {
      const flexy = MF.qs(document, MF_SELECTORS.watchFlexy);
      return flexy && flexy.getAttribute("video-id") === videoId;
    }, 3000);

    const title = await MF.waitFor(() => {
      const el = MF.qs(document, MF_SELECTORS.watchTitle);
      const t = el ? el.textContent.trim() : "";
      return t && t !== lastTitle ? t : null;
    }, 1500);

    const result = title || document.title.replace(/\s*-\s*YouTube\s*$/, "").trim();
    lastTitle = result;
    lastTitleVideoId = videoId;
    return result;
  }

  // Початок опису відео. Читаємо після назви, щоб не взяти опис попереднього відео.
  // YouTube має кілька елементів з описом (короткий і розгорнутий), частина з них
  // буває порожньою, тому беремо найдовший текст серед усіх збігів.
  async function readWatchDescription() {
    await new Promise((r) => setTimeout(r, 300));
    let best = "";
    for (const sel of MF_SELECTORS.watchDescription) {
      for (const el of document.querySelectorAll(sel)) {
        const t = (el.textContent || "").trim();
        if (t.length > best.length) best = t;
      }
    }
    return best.slice(0, 600);
  }

  // Мова за назвою; якщо з назви не зрозуміло, додаємо опис.
  async function detectWatchLang(videoTitle) {
    const byTitle = await MF_LANG.detect(videoTitle);
    if (MF_LANG.isConfident(byTitle, MF_CONFIG.LANG_THRESHOLD)) return byTitle;
    const description = await readWatchDescription();
    console.log("[Мовний фід] опис для визначення мови:", description ? description.slice(0, 80) + "…" : "(не знайдено)");
    if (!description) return byTitle;
    const both = await MF_LANG.detect(videoTitle + "\n" + description);
    return MF_LANG.isConfident(both, MF_CONFIG.LANG_THRESHOLD) ? { ...both, source: both.source + "+description" } : byTitle;
  }

  async function onNavigate() {
    lastHref = location.href;
    await refreshSettings();

    const videoId = MF.currentWatchVideoId();
    if (!videoId) {
      currentVideoId = null;
      MF_Panel.reset();
      return;
    }
    if (videoId === currentVideoId) return;

    currentVideoId = videoId;
    const token = ++navToken;
    MF_Panel.reset();

    if (!settings.enabled) return;

    const videoTitle = await readWatchTitle(videoId);
    if (token !== navToken) return;

    // Клієнтська підказка про мову. Бекенд вирішує остаточно.
    const hint = await detectWatchLang(videoTitle);
    if (token !== navToken) return;

    const res = await MF.send("alternatives", { videoId, hint });
    if (token !== navToken) return;
    if (!res.ok || !res.data) {
      console.warn("[Мовний фід] alternatives:", res.error);
      return; // бекенд недоступний → мовчки нічого не показуємо
    }

    const data = res.data;
    const lang = data.source && data.source.lang;
    console.log("[Мовний фід] відео", videoId, "| назва:", videoTitle, "| клієнт:", hint, "| бекенд:", data.source, "| рівень:", data.level);

    // Лічильник «мовного балансу» (локально).
    if (MF_LANG.isConfident(data.source, MF_CONFIG.LANG_THRESHOLD) && !countedWatched.has(videoId)) {
      countedWatched.add(videoId);
      MF.send("watched", { lang });
    }

    // Українське відео: альтернативи не потрібні.
    if (lang === "uk") return;
    if (!settings.showPanel) return;

    const shown = await MF_Panel.mount({ videoId, videoTitle, data, settings });
    if (shown && token === navToken) {
      MF.send("event", { eventType: "panel_shown", level: data.level });
      const suggested = (data.items || [])
        .slice(0, MF_CONFIG.MAX_ALTERNATIVES)
        .filter((item) => item && item.videoId)
        .map((item) => ({ action: "suggested", videoId: item.videoId }));
      if (suggested.length > 0) {
        MF.send("videoActions", { items: suggested });
      }
    }
  }

  // Подія YouTube після SPA-переходу.
  document.addEventListener("yt-navigate-finish", onNavigate);

  // Запасний варіант, якщо YouTube перейменує подію: перевіряємо URL раз на секунду.
  // Заодно повертаємо панель, якщо YouTube перемалював сайдбар і викинув її.
  setInterval(() => {
    if (location.href !== lastHref) onNavigate();
    else if (currentVideoId) MF_Panel.ensureMounted();
  }, 1000);

  // Налаштування змінили в popup → застосовуємо одразу, без перезавантаження.
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    const keys = Object.keys(changes);
    if (!keys.some((k) => k in MF_DEFAULTS)) return;
    currentVideoId = null; // перемалювати панель з новим режимом
    onNavigate();
  });

  // Popup питає «ти тут?», щоб підказати оновити вкладку, якщо скрипт не запущено.
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "mf-ping") {
      sendResponse({ ok: true, videoId: currentVideoId });
    }
  });

  console.log("[Мовний фід] запущено на цій вкладці");
  onNavigate();
})();
