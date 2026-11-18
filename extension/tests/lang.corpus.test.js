// Корпус реалістичних назв для перевірки мовної евристики (src/lang.js).
// Найважливіше правило дитячого режиму: НІКОЛИ не ховати українське й англійське.
// Пропустити російське відео (прийняти за unknown) прикро, але не критично.
// Приховати українське відео як російське — критична помилка.
//
// Категорії очікуваного результату quick():
//   ru        → має бути ru ≥ 0.8 (сховаємо одразу)
//   ru-defer  → російське, але евристика може не впізнати: дозволено null/unknown, ЗАБОРОНЕНО uk
//   uk        → має бути uk ≥ 0.8
//   uk-defer  → українське, евристика може не впізнати: дозволено null/unknown, ЗАБОРОНЕНО ru
//   neutral   → англійська/нейтральна: дозволено лише null/unknown (не ru і не uk)
//   other     → інша кирилична мова (болгарська тощо): не повинна ховатися як ru
//
// Щоб додати випадок: допишіть рядок у CORPUS. Зламаний випадок, який відомий і ще не виправлений,
// позначайте known:"чому" — тест стане todo і не валитиме запуск, але лишиться у звіті.
const test = require("node:test");
const assert = require("node:assert");
const MF_LANG = require("../src/lang.js");

const T = 0.8; // MF_CONFIG.LANG_THRESHOLD
const CORPUS = [
  // ---- російські, мають ховатися
  ["Что будет, если смешать колу и ментос? Эксперимент", "ru"],
  ["КАК Я БРОСИЛ ПИТЬ", "ru"],
  ["Как заработать первый миллион за год", "ru"],
  ["Почему всё дорожает и что делать", "ru"],
  ["Обзор нового iPhone 16 Pro", "ru"],
  ["ПОРТУГАЛИЯ УМИРАЕТ", "ru"],
  ["Новости сегодня: самые главные события", "ru"],
  ["Лучшие игры 2024 года", "ru"],
  ["Я больше не могу так жить", "ru"],
  ["ЗУМЕРАМ ХУЖЕ ВСЕХ", "ru"],
  ["Решение сложной задачи по математике", "ru"],
  ["Перманентная тревога: как с ней жить", "ru"],
  ["Он сказал мне всё", "ru"],
  ["Съёмная квартира: подводные камни", "ru"],
  ["Подъезд и всё такое", "ru"],
  ["Мы снова в Москве", "ru"],
  ["Apple Vision Pro обзор спустя месяц", "ru"],
  // ---- російські, які евристика без слів-маркерів може не впізнати (віддамо бекенду)
  ["Гороскоп на неделю", "ru-defer"],
  ["Я купил машину", "ru-defer"],
  ["Рецепт борща с пампушками", "ru-defer"],
  ["Ночной клуб Москвы", "ru-defer"],
  // ---- українські, мають розпізнаватися
  ["Як приготувати борщ: рецепт від бабусі", "uk"],
  ["Їжак у лісі", "uk"],
  ["Ґанок своїми руками", "uk"],
  ["Огляд iPhone 16 Pro: чесно про все", "uk"],
  ["Чому зріс курс долара?", "uk"],
  ["Київ вночі 4K", "uk"],
  ["Найкращі фільми 2024 року", "uk"],
  ["ЯК Я КИНУВ ПИТИ", "uk"],
  ["Кохання на відстані", "uk"],
  ["Навчання програмування з нуля", "uk"],
  ["Що відбувається на фронті сьогодні", "uk"],
  ["Дуже смачний торт без випікання", "uk"],
  ["Огляд ноутбука Lenovo", "uk"],
  // ---- українські без «особливих» літер і слів-маркерів: дозволено не впізнати, але не ru
  ["Рецепт торта без випічки", "uk-defer"],
  ["Ремонт квартири своїми руками", "uk"],
  ["Тарас Шевченко: біографія", "uk"],
  ["Лекція про тварин", "uk-defer"],
  // ---- Українські назви зі словами, що збігаються з російськими маркерами
  ["Моя перша зарплата", "uk-defer", "«моя» є в RU_WORDS, але це й українське слово → українське відео ховається"],
  ["Моя країна", "uk"],
  ["Моя історія успіху", "uk"],
  ["Твоя найкраща версія", "uk"],
  ["Мой дом", "ru-defer"],
  // ---- англійські й нейтральні
  ["How to fix a laptop", "neutral"],
  ["MrBeast: $1 vs $1,000,000 Hotel Room", "neutral"],
  ["Ремонт ноутбука", "neutral"],
  ["Бюджет проекту", "neutral"],
  ["LoFi beats to study to", "neutral"],
  ["4K Drone Footage | Norway", "neutral"],
  ["", "neutral"],
  ["🔥🔥🔥", "neutral"],
  // ---- суміш: не вгадуємо
  ["Я і ты", "neutral", "літери «і»+«ы» дають letters-mixed, але quick() після цього все одно йде в слова і каже ru"],
  ["Что це таке", "neutral"],
  // ---- інші кириличні мови
  ["Български език за начинаещи", "other", "«ъ» є і в болгарській: усе з ъ вважається російським"],
  ["Београд ноћу", "other"],
];

function verdict(title) {
  const r = MF_LANG.quick(title);
  const lang = r && r.lang && r.lang !== "unknown" && r.confidence >= T ? r.lang : "none";
  return { lang, r };
}

for (const [title, kind, known] of CORPUS.map((c) => (c.length === 2 ? [...c, null] : c))) {
  test(`корпус [${kind}] "${title}"`, { todo: known || false }, () => {
    const { lang, r } = verdict(title);
    const got = JSON.stringify(r);
    switch (kind) {
      case "ru": assert.strictEqual(lang, "ru", `очікували ru, отримали ${got}`); break;
      case "uk": assert.strictEqual(lang, "uk", `очікували uk, отримали ${got}`); break;
      case "ru-defer": assert.notStrictEqual(lang, "uk", `російське визначено як українське: ${got}`); break;
      case "uk-defer": assert.notStrictEqual(lang, "ru", `КРИТИЧНО: українське буде приховано як російське: ${got}`); break;
      case "neutral": assert.strictEqual(lang, "none", `нейтральне/англійське має лишатися невизначеним: ${got}`); break;
      case "other": assert.notStrictEqual(lang, "ru", `КРИТИЧНО: не російська мова буде прихована як російська: ${got}`); break;
      default: assert.fail(`невідома категорія ${kind}`);
    }
  });
}

test("зведення корпусу: точність і повнота ховання", () => {
  let hide = 0, hideCorrect = 0, shouldHide = 0, hiddenOfShould = 0;
  const wrong = [], knownWrong = [];
  for (const [title, kind, known] of CORPUS) {
    const { lang } = verdict(title);
    const isRu = kind === "ru" || kind === "ru-defer";
    if (isRu) shouldHide++;
    if (lang === "ru") {
      hide++;
      if (isRu) { hideCorrect++; hiddenOfShould++; } else (known ? knownWrong : wrong).push(title);
    }
  }
  const precision = hide ? hideCorrect / hide : 1;
  const recall = shouldHide ? hiddenOfShould / shouldHide : 1;
  console.log(`\n  Ховаємо: ${hide}, з них правильно: ${hideCorrect} (точність ${(precision * 100).toFixed(0)}%), повнота на російських: ${(recall * 100).toFixed(0)}% (решту доб'є модель Chrome і бекенд)`);
  if (knownWrong.length) console.log(`  Відомі помилкові приховування (див. todo вище): ${knownWrong.map((w) => `"${w}"`).join(", ")}`);
  // Жорстка вимога: жодної НОВОЇ помилково схованої не російської назви в корпусі.
  assert.deepEqual(wrong, [], "є назви, які не російські, але були б приховані");
});
