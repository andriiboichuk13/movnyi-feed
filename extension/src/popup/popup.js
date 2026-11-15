// =====================================================================
// POPUP: налаштування, дитячий режим з PIN, лічильники.
// Усе зберігається в chrome.storage.local, content script підхоплює зміни сам.
// =====================================================================

const $ = (id) => document.getElementById(id);

const LANG_LABELS = { uk: "Українською", en: "Англійською", ru: "Російською", other: "Інші" };

function today() {
  return new Date().toISOString().slice(0, 10);
}

function lastDays(n) {
  const days = [];
  for (let i = 0; i < n; i++) days.push(new Date(Date.now() - i * 86400000).toISOString().slice(0, 10));
  return days;
}

async function hashPin(pin) {
  const { installId = "" } = await chrome.storage.local.get("installId");
  const data = new TextEncoder().encode(`${installId}:${pin}`);
  const buf = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function load() {
  const s = await chrome.storage.local.get({ ...MF_DEFAULTS, kidsHidden: {}, balance: {} });

  $("showPanel").checked = s.showPanel;
  $("kidsMode").checked = s.kidsMode;
  $("shareKidsStats").checked = s.shareKidsStats;
  $("kidsDetails").hidden = !s.kidsMode;
  $("pinSetOpen").hidden = !!s.pinHash;
  $("pinRemoveOpen").hidden = !s.pinHash;

  // Лічильник прихованих
  const week = lastDays(7);
  $("hiddenToday").textContent = s.kidsHidden[today()] || 0;
  $("hiddenWeek").textContent = week.reduce((sum, d) => sum + (s.kidsHidden[d] || 0), 0);

  renderBalance(s.balance);

  $("mockNote").hidden = !MF_CONFIG.USE_MOCK;
  $("privacyLink").href = MF_CONFIG.WEBSITE_URL.replace(/\/$/, "") + "/privacy";
}

function renderBalance(balance) {
  const totals = { uk: 0, en: 0, ru: 0, other: 0 };
  for (const d of lastDays(MF_CONFIG.BALANCE_DAYS)) {
    const day = balance[d];
    if (!day) continue;
    for (const k of Object.keys(totals)) totals[k] += day[k] || 0;
  }
  const sum = Object.values(totals).reduce((a, b) => a + b, 0);

  const bar = $("balanceBar");
  const legend = $("balanceLegend");
  bar.replaceChildren();
  legend.replaceChildren();
  $("balanceEmpty").hidden = sum > 0;
  bar.hidden = sum === 0;
  legend.hidden = sum === 0;
  if (sum === 0) return;

  for (const k of ["uk", "en", "ru", "other"]) {
    const seg = document.createElement("span");
    seg.style.width = `${(totals[k] / sum) * 100}%`;
    seg.style.background = `var(--${k})`;
    bar.append(seg);

    const li = document.createElement("li");
    const sw = document.createElement("i");
    sw.style.background = `var(--${k})`;
    const label = document.createElement("span");
    label.textContent = LANG_LABELS[k];
    const n = document.createElement("b");
    n.textContent = totals[k];
    li.append(sw, label, n);
    legend.append(li);
  }
}

// ---------- Обробники ----------

$("showPanel").addEventListener("change", (e) => chrome.storage.local.set({ showPanel: e.target.checked }));
$("shareKidsStats").addEventListener("change", (e) => chrome.storage.local.set({ shareKidsStats: e.target.checked }));

$("kidsMode").addEventListener("change", async (e) => {
  const turningOn = e.target.checked;
  const { pinHash } = await chrome.storage.local.get({ pinHash: null });

  if (!turningOn && pinHash) {
    // Вимкнення під PIN: повертаємо перемикач і просимо PIN.
    e.target.checked = true;
    $("pinGate").hidden = false;
    $("pinGateInput").value = "";
    $("pinGateError").hidden = true;
    $("pinGateInput").focus();
    return;
  }
  await chrome.storage.local.set({ kidsMode: turningOn });
  // Чи працює content script на відкритій вкладці YouTube.
async function checkActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url || !tab.url.startsWith("https://www.youtube.com/")) return;
    await chrome.tabs.sendMessage(tab.id, { type: "mf-ping" });
  } catch (e) {
    // Відповіді немає: скрипт на вкладці не запущено.
    $("reloadNotice").hidden = false;
  }
}

load();
checkActiveTab();
});

$("pinGateBtn").addEventListener("click", async () => {
  const { pinHash } = await chrome.storage.local.get({ pinHash: null });
  const entered = await hashPin($("pinGateInput").value.trim());
  if (entered !== pinHash) {
    $("pinGateError").hidden = false;
    return;
  }
  $("pinGate").hidden = true;
  await chrome.storage.local.set({ kidsMode: false });
  // Чи працює content script на відкритій вкладці YouTube.
async function checkActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url || !tab.url.startsWith("https://www.youtube.com/")) return;
    await chrome.tabs.sendMessage(tab.id, { type: "mf-ping" });
  } catch (e) {
    // Відповіді немає: скрипт на вкладці не запущено.
    $("reloadNotice").hidden = false;
  }
}

load();
checkActiveTab();
});
$("pinGateInput").addEventListener("keydown", (e) => e.key === "Enter" && $("pinGateBtn").click());

// Встановити / прибрати PIN
let pinFormMode = "set"; // "set" | "remove"

function openPinForm(mode) {
  pinFormMode = mode;
  $("pinForm").hidden = false;
  $("pinFormLabel").textContent = mode === "set" ? "Новий PIN, 4–8 цифр" : "Поточний PIN";
  $("pinSave").textContent = mode === "set" ? "Зберегти" : "Прибрати";
  $("pinInput").value = "";
  $("pinError").hidden = true;
  $("pinInput").focus();
}
$("pinSetOpen").addEventListener("click", () => openPinForm("set"));
$("pinRemoveOpen").addEventListener("click", () => openPinForm("remove"));

$("pinSave").addEventListener("click", async () => {
  const pin = $("pinInput").value.trim();
  const showError = (t) => {
    $("pinError").textContent = t;
    $("pinError").hidden = false;
  };

  if (pinFormMode === "set") {
    if (!/^\d{4,8}$/.test(pin)) return showError("PIN має містити від 4 до 8 цифр");
    await chrome.storage.local.set({ pinHash: await hashPin(pin) });
  } else {
    const { pinHash } = await chrome.storage.local.get({ pinHash: null });
    if ((await hashPin(pin)) !== pinHash) return showError("Неправильний PIN");
    await chrome.storage.local.set({ pinHash: null });
  }
  $("pinForm").hidden = true;
  // Чи працює content script на відкритій вкладці YouTube.
async function checkActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url || !tab.url.startsWith("https://www.youtube.com/")) return;
    await chrome.tabs.sendMessage(tab.id, { type: "mf-ping" });
  } catch (e) {
    // Відповіді немає: скрипт на вкладці не запущено.
    $("reloadNotice").hidden = false;
  }
}

load();
checkActiveTab();
});
$("pinInput").addEventListener("keydown", (e) => e.key === "Enter" && $("pinSave").click());

// Чи працює content script на відкритій вкладці YouTube.
async function checkActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url || !tab.url.startsWith("https://www.youtube.com/")) return;
    await chrome.tabs.sendMessage(tab.id, { type: "mf-ping" });
  } catch (e) {
    // Відповіді немає: скрипт на вкладці не запущено.
    $("reloadNotice").hidden = false;
  }
}

load();
checkActiveTab();
