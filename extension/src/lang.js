// =====================================================================
// ВИЗНАЧЕННЯ МОВИ НА КЛІЄНТІ (кроки 2 і 3 каскаду з ТЗ).
// Це лише швидкий перший прохід, щоб картки не мерехтіли.
// Остаточне рішення для невпевнених випадків дає бекенд (POST /classify).
//
// Результат завжди: { lang: "uk"|"ru"|"en"|"other"|"unknown", confidence: 0..1, source }
// =====================================================================

const MF_LANG = (() => {
  // Літери, які є лише в одній з мов.
  const UK_ONLY = /[іїєґІЇЄҐ]/g;
  const RU_ONLY = /[ыэёъЫЭЁЪ]/g;
  const CYRILLIC = /[\u0400-\u04FF]/g;
  const LATIN = /[a-zA-Z]/g;

  const UNKNOWN = { lang: "unknown", confidence: 0, source: "none" };

  function count(re, text) {
    const m = text.match(re);
    return m ? m.length : 0;
  }

  // Крок 2: літери. Миттєво, без моделі.
  // Повертає результат або null, якщо літери нічого не кажуть.
  function byLetters(text) {
    const t = text || "";
    const uk = count(UK_ONLY, t);
    const ru = count(RU_ONLY, t);

    if (uk > 0 && ru > 0) {
      // І ті, і ті літери: суміш мов або білоруська. Не вгадуємо.
      return { lang: "unknown", confidence: 0, source: "letters-mixed" };
    }
    if (uk > 0) return { lang: "uk", confidence: uk >= 2 ? 0.95 : 0.9, source: "letters" };
    if (ru > 0) return { lang: "ru", confidence: ru >= 2 ? 0.95 : 0.9, source: "letters" };
    return null;
  }

  // Крок 2б: службові слова, які є лише в одній мові.
  // Ловить назви без «особливих» літер: «КАК Я БРОСИЛ ПИТЬ» → ru.
  // Слова, що пишуться однаково в обох мовах (просто, все, тут, та…), сюди НЕ додаємо.
  const RU_WORDS = new Set([
    "как", "что", "чтобы", "это", "этот", "эта", "его", "она", "они", "мы", "вы", "ты",
    "или", "если", "только", "когда", "где", "почему", "зачем", "сейчас", "теперь",
    "очень", "тоже", "нет", "меня", "тебя", "себя", "мой", "моя", "свой", "твой",
    "который", "которые", "будет", "было", "был", "были", "сделать", "сегодня",
    "лучший", "лучшие", "самый", "ничего", "хорошо", "лет", "пить", "есть", "можно",
    "нужно", "надо", "всегда", "никогда", "видео", "обзор", "новый", "новые",
    "из", "еще", "чем", "мне", "кто", "куда", "здесь", "всех", "всем", "хуже",
    "лучше", "больше", "меньше", "жизнь", "человек", "деньги", "бесплатно",
  ]);
  const UK_WORDS = new Set([
    "що", "щоб", "як", "це", "цей", "ця", "ці", "але", "або", "від", "якщо", "коли",
    "чому", "дуже", "зараз", "мене", "буде", "було", "був", "він", "вона", "вони",
    "може", "можна", "треба", "завжди", "ніколи", "найкращий", "огляд", "новий",
    "нові", "сьогодні", "років", "рік", "також", "тільки", "лише", "своїми",
    "хто", "де", "куди", "мені", "ще", "чим", "життя", "людина", "гроші", "краще",
    "гірше", "більше", "менше", "всіх", "безкоштовно",
  ]);

  // Закінчення, типові лише для однієї мови. Працюють там, де слова немає в списку.
  const RU_ENDINGS = [
    /[а-я]{2,}ая$/, // новая, перманентная (укр. -а)
    /[а-я]{3,}ия$/, // португалия, история (укр. -ія)
    /[а-я]{2,}ние$/, // решение, обучение (укр. -ння)
    /[а-я]{2,}[ауяе]ет$/, // умирает, делает, рисует, умеет (укр. -ає, -ує, -іє)
    /[а-я]{2,}[ауяе]ют$/, // делают, рисуют (укр. -ають, -ують)
    /[а-я]{2,}тся$/, // делается (укр. -ться)
    /[а-я]{3,}ость$/, // радость (укр. -ість)
  ];
  const UK_ENDINGS = [
    /[а-я]{2,}ння$/, // навчання, кохання (рос. -ние)
  ];

  function byWords(text) {
    const words = (text || "").toLowerCase().match(/[\u0400-\u04FF'’ʼ]+/g) || [];
    let ru = 0;
    let uk = 0;
    for (const w of words) {
      if (RU_WORDS.has(w) || RU_ENDINGS.some((re) => re.test(w))) ru++;
      if (UK_WORDS.has(w) || UK_ENDINGS.some((re) => re.test(w))) uk++;
    }
    if (ru > 0 && uk > 0) return null; // суміш: хай вирішує модель або бекенд
    if (ru > 0) return { lang: "ru", confidence: Math.min(0.95, 0.8 + ru * 0.05), source: "words" };
    if (uk > 0) return { lang: "uk", confidence: Math.min(0.95, 0.8 + uk * 0.05), source: "words" };
    return null;
  }

  // Крок 3: вбудована в Chrome модель (chrome.i18n.detectLanguage).
  async function byModel(text) {
    const t = (text || "").trim();
    if (t.length < 12) return null; // на дуже короткому тексті модель вгадує
    if (typeof chrome === "undefined" || !chrome.i18n || !chrome.i18n.detectLanguage) return null;

    let r;
    try {
      // Модель гірше працює з ВЕЛИКИМИ ЛІТЕРАМИ, тому передаємо в нижньому регістрі.
      r = await chrome.i18n.detectLanguage(t.toLowerCase());
    } catch (e) {
      return null;
    }
    if (!r || !r.languages || r.languages.length === 0) return null;

    const top = r.languages[0];
    const known = { uk: "uk", ru: "ru", en: "en" };
    const lang = known[top.language] || "other";
    let confidence = top.percentage / 100;

    // Модель погано відрізняє uk від ru на коротких назвах без «особливих» літер.
    // Якщо вона сама не впевнена, опускаємо оцінку нижче порогу.
    if (!r.isReliable) confidence = Math.min(confidence, 0.6);

    // Якщо в тексті кирилиця, а модель каже "en" — не віримо.
    if (lang === "en" && count(CYRILLIC, t) > count(LATIN, t)) return null;

    return { lang, confidence, source: "chrome-model" };
  }

  // Швидкий синхронний прохід: літери, потім слова.
  function quick(text) {
    const letters = byLetters(text);
    if (letters && letters.lang !== "unknown") return letters;
    const words = byWords(text);
    if (words) return words;
    return letters; // або null, або unknown через суміш літер
  }

  // Повний клієнтський каскад: літери → слова → модель → unknown.
  async function detect(text) {
    const q = quick(text);
    if (q) return q;
    const model = await byModel(text);
    if (model) return model;
    return { ...UNKNOWN };
  }

  // Чи достатньо впевнений результат.
  function isConfident(result, threshold) {
    return !!result && result.lang !== "unknown" && result.confidence >= threshold;
  }

  return { byLetters, byWords, quick, byModel, detect, isConfident };
})();

// Щоб тести (node) могли імпортувати файл.
if (typeof module !== "undefined") module.exports = MF_LANG;
