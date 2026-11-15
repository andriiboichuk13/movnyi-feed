// Запуск: node --test tests/lang.test.js
// Нічого встановлювати не треба, потрібен лише Node.js 18+.
// Тестувальник може додавати сюди нові випадки з тестового набору.

const test = require("node:test");
const assert = require("node:assert");
const MF_LANG = require("../src/lang.js");

const cases = [
  // [назва, очікувана мова або null (= літери нічого не кажуть)]
  ["Як приготувати борщ: рецепт від бабусі", "uk"],
  ["Їжак у лісі", "uk"],
  ["Ґанок своїми руками", "uk"],
  ["Что будет, если смешать колу и ментос? Эксперимент", "ru"],
  ["Мы снова в Москве", "ru"],
  ["Подъезд и всё такое", "ru"],
  ["How to fix a laptop", null],
  ["Ремонт ноутбука", null], // однаково пишеться обома мовами
  ["Я і ты", "unknown"], // суміш літер → не вгадуємо
];

for (const [title, expected] of cases) {
  test(`byLetters: "${title}" → ${expected}`, () => {
    const r = MF_LANG.byLetters(title);
    if (expected === null) assert.strictEqual(r, null);
    else assert.strictEqual(r.lang, expected);
  });
}

const quickCases = [
  // [назва, очікувана мова або null] — швидкий прохід: літери + службові слова
  ["КАК Я БРОСИЛ ПИТЬ", "ru"], // реальний випадок: немає ы/э/ё/ъ
  ["Что посмотреть на выходных", "ru"],
  ["Як я кинув пити", "uk"],
  ["ЗУМЕРАМ ХУЖЕ ВСЕХ", "ru"], // реальний випадок
  ["Перманентная тревога", "ru"], // закінчення -ая
  ["ПОРТУГАЛИЯ УМИРАЕТ", "ru"], // реальний випадок: -ия, -ает
  ["Решение задачи", "ru"], // -ние
  ["Кохання на відстані", "uk"],
  ["Навчання програмування", "uk"], // -ння без особливих літер
  ["Бюджет проекту", null], // не повинно спрацювати
  ["Ремонт ноутбука", null],
  ["How to fix a laptop", null],
];

for (const [title, expected] of quickCases) {
  test(`quick: "${title}" → ${expected}`, () => {
    const r = MF_LANG.quick(title);
    if (expected === null) assert.strictEqual(r, null);
    else assert.strictEqual(r.lang, expected);
  });
}

test("isConfident враховує поріг", () => {
  assert.strictEqual(MF_LANG.isConfident({ lang: "ru", confidence: 0.9 }, 0.8), true);
  assert.strictEqual(MF_LANG.isConfident({ lang: "ru", confidence: 0.7 }, 0.8), false);
  assert.strictEqual(MF_LANG.isConfident({ lang: "unknown", confidence: 1 }, 0.8), false);
});

test("detect без Chrome повертає unknown для нейтрального тексту", async () => {
  const r = await MF_LANG.detect("Ремонт ноутбука");
  assert.strictEqual(r.lang, "unknown");
});
