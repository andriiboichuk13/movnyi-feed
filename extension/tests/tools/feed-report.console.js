// Звіт про стрічку для ручного тестування. Вставте в консоль (F12 → Console) на youtube.com
// (головна, пошук або сторінка відео, де є рекомендації). Нічого не змінює на сторінці.
//
// Показує для кожної картки: назву, videoId і що з нею зробило розширення:
//   shown  = показана,  masked = плитка «Приховано дитячим режимом» (головна),  hidden = зникла (сайдбар/пошук)
// «lang» береться з data-mf-lang, який ставить розширення після визначення мови.
// Ще друкує готове посилання для мок-бекенду, щоб позначити перші N карток російськими.
(() => {
  const SEL = "ytd-browse[page-subtype='home'] ytd-rich-item-renderer, #related ytd-compact-video-renderer, #related yt-lockup-view-model, ytd-search ytd-video-renderer";
  const idOf = (el) => { const a = el.querySelector("a[href*='/watch?v='], a[href*='/shorts/']"); const h = a && a.getAttribute("href"); const m = h && (h.match(/[?&]v=([\w-]{11})/) || h.match(/\/shorts\/([\w-]{11})/)); return m ? m[1] : null; };
  const titleOf = (el) => { const t = el.querySelector("#video-title, a.yt-lockup-metadata-view-model__title, h3 a, h3"); return t ? (t.getAttribute("title") || t.textContent || "").trim().slice(0, 70) : ""; };
  const cards = [...document.querySelectorAll(SEL)];
  const rows = cards.map((el, i) => ({
    "№": i + 1,
    стан: el.classList.contains("mf-masked") ? "masked" : el.classList.contains("mf-hidden") ? "hidden" : "shown",
    lang: el.dataset.mfLang || "(не оброблено)",
    videoId: idOf(el) || "(немає посилання)",
    назва: titleOf(el),
  }));
  console.table(rows);
  const by = (k, v) => rows.filter((r) => r[k] === v).length;
  console.log(`Карток: ${rows.length} | показано: ${by("стан", "shown")} | заглушок: ${by("стан", "masked")} | зникло: ${by("стан", "hidden")} | не оброблено: ${by("lang", "(не оброблено)")}`);
  const ids = rows.filter((r) => /^[\w-]{11}$/.test(r.videoId)).slice(0, 5).map((r) => r.videoId);
  console.log("Позначити перші 5 карток російськими в мок-бекенді (відкрийте в новій вкладці):\nhttp://localhost:8000/__set?ru=" + ids.join(","));
  return rows;
})();
