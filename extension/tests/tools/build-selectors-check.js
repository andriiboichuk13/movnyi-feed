// Генерує tools/selectors-check.console.js: один файл, який вставляють у консоль (F12) на youtube.com.
// Він містить актуальні селектори з src/selectors.js, тому вручну нічого копіювати не треба.
// Запуск: node tools/build-selectors-check.js   (перегенеруйте після кожної зміни selectors.js)
const fs = require("node:fs");
const path = require("node:path");

const selectors = fs.readFileSync(path.join(__dirname, "..", "..", "src", "selectors.js"), "utf8");
const out = `// Автогенеровано tools/build-selectors-check.js. Не редагуйте вручну.
// Вставте в консоль (F12 → Console) на youtube.com: на головній, на сторінці відео і в пошуку.
(() => {
${selectors}
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
`;
fs.writeFileSync(path.join(__dirname, "selectors-check.console.js"), out);
console.log("OK → tools/selectors-check.console.js");
