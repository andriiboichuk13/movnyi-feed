// Тести popup: перемикачі, PIN дитячого режиму, мовний баланс, підказка «оновіть вкладку».
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { loadDom, sleep, EXT } = require("./helpers/load-dom");

const HTML = fs.readFileSync(path.join(EXT, "src", "popup", "popup.html"), "utf8");
const day = (offset = 0) => new Date(Date.now() - offset * 86400000).toISOString().slice(0, 10);
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");

async function open({ storage = {}, tabs } = {}, t) {
  const env = loadDom({
    html: HTML, url: "chrome-extension://abc/src/popup/popup.html",
    files: ["src/config.js", "src/popup/popup.js"],
    chromeOpts: { storage: { installId: "install-id-12345", ...storage }, tabs },
  });
  if (t) t.after(() => env.window.close());
  await sleep(60); // load() асинхронний
  const $ = (id) => env.document.getElementById(id);
  const stored = () => env.cm.storage.data;
  const click = async (id) => { $(id).click(); await sleep(60); };
  const toggle = async (id, checked) => { $(id).checked = checked; $(id).dispatchEvent(new env.window.Event("change")); await sleep(60); };
  return { ...env, $, stored, click, toggle };
}

test("popup.html: кожен id, який використовує popup.js, існує в розмітці", () => {
  const js = fs.readFileSync(path.join(EXT, "src", "popup", "popup.js"), "utf8");
  const used = new Set([...js.matchAll(/\$\("([A-Za-z]+)"\)/g)].map((m) => m[1]));
  const have = new Set([...HTML.matchAll(/id="([A-Za-z]+)"/g)].map((m) => m[1]));
  const missing = [...used].filter((i) => !have.has(i));
  assert.deepStrictEqual(missing, [], `popup.js звертається до неіснуючих елементів: ${missing}`);
});

test("стан перемикачів береться зі сховища, блок дитячого режиму прихований, поки він вимкнений", async (t) => {
  const p = await open({ storage: { showPanel: false, kidsMode: false } }, t);
  assert.strictEqual(p.$("showPanel").checked, false);
  assert.strictEqual(p.$("kidsMode").checked, false);
  assert.strictEqual(p.$("kidsDetails").hidden, true);
});

test("увімкнення дитячого режиму зберігається і показує лічильники", async (t) => {
  const p = await open({}, t);
  await p.toggle("kidsMode", true);
  assert.strictEqual(p.stored().kidsMode, true);
  assert.strictEqual(p.$("kidsDetails").hidden, false);
});

test("перемикач «Показувати альтернативи» зберігається", async (t) => {
  const p = await open({}, t);
  await p.toggle("showPanel", false);
  assert.strictEqual(p.stored().showPanel, false);
});

test("«Надсилати денну кількість» за замовчуванням вимкнено і зберігається лише за згодою", async (t) => {
  const p = await open({ storage: { kidsMode: true } }, t);
  assert.strictEqual(p.$("shareKidsStats").checked, false);
  await p.toggle("shareKidsStats", true);
  assert.strictEqual(p.stored().shareKidsStats, true);
});

// ---------------------------------------------------------------- PIN
test("PIN: короткий (3 цифри), літери і задовгий (9) відхиляються, нічого не зберігається", async (t) => {
  const p = await open({ storage: { kidsMode: true } }, t);
  for (const bad of ["123", "12ab", "123456789", ""]) {
    await p.click("pinSetOpen");
    p.$("pinInput").value = bad;
    await p.click("pinSave");
    assert.strictEqual(p.$("pinError").hidden, false, `"${bad}" мав бути відхилений`);
    assert.ok(!p.stored().pinHash);
  }
});

test("PIN: валідний зберігається як SHA-256(installId:pin), а не відкритим текстом", async (t) => {
  const p = await open({ storage: { kidsMode: true } }, t);
  await p.click("pinSetOpen");
  p.$("pinInput").value = "4821";
  await p.click("pinSave");
  assert.strictEqual(p.stored().pinHash, sha("install-id-12345:4821"));
  assert.ok(!JSON.stringify(p.stored()).includes('"4821"'));
});

test("з PIN вимкнути дитячий режим без коду не можна: перемикач повертається, показано поле PIN", async (t) => {
  const p = await open({ storage: { kidsMode: true, pinHash: sha("install-id-12345:4821") } }, t);
  await p.toggle("kidsMode", false);
  assert.strictEqual(p.stored().kidsMode, true, "режим не мав вимкнутися");
  assert.strictEqual(p.$("kidsMode").checked, true);
  assert.strictEqual(p.$("pinGate").hidden, false);
});

test("неправильний PIN не вимикає режим, правильний вимикає", async (t) => {
  const p = await open({ storage: { kidsMode: true, pinHash: sha("install-id-12345:4821") } }, t);
  await p.toggle("kidsMode", false);
  p.$("pinGateInput").value = "0000";
  await p.click("pinGateBtn");
  assert.strictEqual(p.stored().kidsMode, true);
  assert.strictEqual(p.$("pinGateError").hidden, false);
  p.$("pinGateInput").value = "4821";
  await p.click("pinGateBtn");
  assert.strictEqual(p.stored().kidsMode, false);
});

test("прибрати PIN можна лише з правильним поточним PIN", async (t) => {
  const hash = sha("install-id-12345:4821");
  const p = await open({ storage: { kidsMode: true, pinHash: hash } }, t);
  await p.click("pinRemoveOpen");
  p.$("pinInput").value = "1111";
  await p.click("pinSave");
  assert.strictEqual(p.stored().pinHash, hash);
  p.$("pinInput").value = "4821";
  await p.click("pinSave");
  assert.strictEqual(p.stored().pinHash, null);
});

test("увімкнути дитячий режим можна й під PIN (PIN потрібен лише для вимкнення)", async (t) => {
  const p = await open({ storage: { kidsMode: false, pinHash: "x" } }, t);
  await p.toggle("kidsMode", true);
  assert.strictEqual(p.stored().kidsMode, true);
});

// ---------------------------------------------------------------- лічильники й баланс
test("лічильник прихованих: сьогодні і сума за 7 днів", async (t) => {
  const p = await open({ storage: { kidsMode: true, kidsHidden: { [day(0)]: 5, [day(3)]: 7, [day(20)]: 100 } } }, t);
  assert.strictEqual(p.$("hiddenToday").textContent, "5");
  assert.strictEqual(p.$("hiddenWeek").textContent, "12");
});

test("мовний баланс: порожній стан, потім сегменти за 7 днів (старші ігноруються)", async (t) => {
  let p = await open({}, t);
  assert.strictEqual(p.$("balanceEmpty").hidden, false);
  p = await open({ storage: { balance: { [day(0)]: { uk: 3, ru: 1, en: 0, other: 0 }, [day(2)]: { uk: 1, ru: 0, en: 3, other: 0 }, [day(30)]: { ru: 99 } } } }, t);
  assert.strictEqual(p.$("balanceEmpty").hidden, true);
  const nums = [...p.$("balanceLegend").querySelectorAll("li")].map((li) => li.querySelector("b").textContent);
  assert.deepStrictEqual(nums, ["4", "3", "1", "0"]); // uk, en, ru, other
});

test("посилання на конфіденційність будується з WEBSITE_URL", async (t) => {
  const p = await open({}, t);
  assert.match(p.$("privacyLink").href, /\/privacy$/);
});

// ---------------------------------------------------------------- «оновіть вкладку»
test("на вкладці YouTube без content script показується підказка «Оновіть вкладку»", async (t) => {
  const p = await open({ tabs: { query: async () => [{ id: 1, url: "https://www.youtube.com/watch?v=x" }], sendMessage: async () => { throw new Error("Receiving end does not exist"); } } }, t);
  assert.strictEqual(p.$("reloadNotice").hidden, false);
});

test("якщо content script відповідає, підказки немає; на не-YouTube вкладці теж", async (t) => {
  let p = await open({ tabs: { query: async () => [{ id: 1, url: "https://www.youtube.com/" }], sendMessage: async () => ({ ok: true }) } }, t);
  assert.strictEqual(p.$("reloadNotice").hidden, true);
  p = await open({ tabs: { query: async () => [{ id: 1, url: "https://example.org/" }], sendMessage: async () => { throw new Error("x"); } } }, t);
  assert.strictEqual(p.$("reloadNotice").hidden, true);
});

// ---------------------------------------------------------------- стійкість PIN до обходу (P2, див. BYPASS.md)
test("PIN: хеш зі сховища не підбирається перебором 4–8 цифр швидше ніж за секунду", { todo: "P2: хеш = SHA-256(installId:pin) без солі й повільного алгоритму, installId лежить поруч у тому ж сховищі → 4-значний PIN підбирається за мілісекунди" }, () => {
  const installId = "install-id-12345", pin = "7391";
  const target = sha(`${installId}:${pin}`);
  const t0 = Date.now();
  let found = null;
  for (let n = 0; n < 10000 && !found; n++) { const c = String(n).padStart(4, "0"); if (sha(`${installId}:${c}`) === target) found = c; }
  const ms = Date.now() - t0;
  console.log(`  підбір 4-значного PIN: знайдено ${found} за ${ms} мс`);
  assert.ok(found === null || ms >= 1000, `PIN підібрано за ${ms} мс`);
});

test("PIN: після 5 помилкових спроб поспіль вимкнення блокується на деякий час", { todo: "P2: ліміту спроб немає, можна вводити PIN без обмежень" }, async (t) => {
  const p = await open({ storage: { kidsMode: true, pinHash: sha("install-id-12345:4821") } }, t);
  await p.toggle("kidsMode", false);
  for (let i = 0; i < 5; i++) { p.$("pinGateInput").value = String(1000 + i); await p.click("pinGateBtn"); }
  p.$("pinGateInput").value = "4821"; // навіть правильний PIN одразу після серії помилок має бути відхилений
  await p.click("pinGateBtn");
  assert.strictEqual(p.stored().kidsMode, true, "після серії помилок правильний PIN одразу спрацював: ліміту спроб немає");
});
