// =====================================================================
// ЗАГЛУШКА БЕКЕНДУ. Працює, коли MF_CONFIG.USE_MOCK = true.
// Повертає відповіді в тому ж форматі, що й справжній бекенд за ТЗ,
// тож коли бекенд буде готовий, у розширенні нічого міняти не треба.
//
// Відео в заглушці вигадані: картинок і справжніх роликів за ними немає.
// Рівень драбини залежить від videoId, тому на різних відео ви побачите
// різні стани панелі (рівні 1–5).
// =====================================================================

const MF_MOCK = (() => {
  function hash(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
    return Math.abs(h);
  }

  function fakeItems(videoId, level, kids) {
    if (level === 5) return [];
    const lang = level === 4 ? "en" : "uk";
    const names = {
      1: "українською на ту саму тему",
      2: "українською з пошуку YouTube",
      3: "українською на близьку тему",
      4: "англійською на ту саму тему",
    };
    const n = 1 + (hash(videoId + "n") % 3); // 1..3 відео
    const items = [];
    for (let i = 0; i < n; i++) {
      items.push({
        videoId: "mock" + String(hash(videoId + i)).padStart(7, "0").slice(0, 7),
        title: `Заглушка ${i + 1}: відео ${names[level]}${kids ? " (для дітей)" : ""}`,
        channel: lang === "uk" ? "Тестовий український канал" : "Test English Channel",
        lang,
        hasUkDub: level === 4 && i === 0,
        score: Math.round((0.9 - i * 0.07) * 100) / 100,
      });
    }
    return items;
  }

  // GET /alternatives?videoId=…&mode=default|kids
  function alternatives(videoId, mode, hint) {
    const level = 1 + (hash(videoId) % 5);
    // Мову вихідного відео заглушка бере з клієнтської підказки.
    const source = hint && hint.lang ? { lang: hint.lang, confidence: hint.confidence } : { lang: "unknown", confidence: 0 };
    return { source, level, items: fakeItems(videoId, level, mode === "kids") };
  }

  // POST /classify → { results: { [videoId]: {lang, confidence, source} } }
  // Заглушка нічого не знає про мову, тому повертає unknown:
  // у дитячому режимі ховатимуться лише картки, які впевнено визначив клієнт.
  function classify(videoIds) {
    const results = {};
    for (const id of videoIds) results[id] = { lang: "unknown", confidence: 0, source: "mock" };
    return { results };
  }

  return { alternatives, classify };
})();
