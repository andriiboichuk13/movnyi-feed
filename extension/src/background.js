// =====================================================================
// BACKGROUND (service worker).
// Лише він ходить на бекенд. Content script і popup надсилають йому
// повідомлення, він робить запит, кешує і повертає відповідь.
//
// Важливо про Manifest V3: service worker «засинає» через ~30 с простою,
// тому жодного стану в змінних, усе зберігаємо в chrome.storage.
// =====================================================================

importScripts("config.js", "api-mock.js");

// ---------- Утиліти ----------

function today() {
  return new Date().toISOString().slice(0, 10); // "2026-11-14"
}

async function getSettings() {
  return chrome.storage.local.get(MF_DEFAULTS);
}

async function getInstallId() {
  const { installId } = await chrome.storage.local.get("installId");
  if (installId) return installId;
  const id = crypto.randomUUID(); // випадковий UUID, жодних даних акаунта
  await chrome.storage.local.set({ installId: id });
  return id;
}

async function apiFetch(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), MF_CONFIG.FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(MF_CONFIG.API_BASE + path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        // Прибирає сторінку-попередження безкоштовного ngrok, коли бекенд на ноутбуці.
        // Для інших серверів цей заголовок просто ігнорується.
        "ngrok-skip-browser-warning": "1",
        ...(options.headers || {}),
      },
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = await res.text();
    return text ? JSON.parse(text) : {};
  } finally {
    clearTimeout(timer);
  }
}

// Кеш: ключ → { ts, data }
async function cacheGet(key, ttl) {
  const obj = await chrome.storage.local.get(key);
  const entry = obj[key];
  if (entry && Date.now() - entry.ts < ttl) return entry.data;
  return null;
}

async function cacheSetMany(map) {
  const now = Date.now();
  const toSave = {};
  for (const [k, data] of Object.entries(map)) toSave[k] = { ts: now, data };
  await chrome.storage.local.set(toSave);
}

// ---------- Обробники ----------

// Альтернативи для відео: GET /alternatives?videoId=…&mode=default|kids
async function handleAlternatives({ videoId, hint }) {
  const settings = await getSettings();
  const mode = settings.kidsMode ? "kids" : "default";
  const key = `alt:${mode}:${videoId}`;

  // Заглушку не кешуємо: вона залежить від клієнтської підказки, яка може змінитися.
  if (MF_CONFIG.USE_MOCK) return MF_MOCK.alternatives(videoId, mode, hint);

  const cached = await cacheGet(key, MF_CONFIG.ALT_CACHE_TTL_MS);
  if (cached) return cached;

  const data = await apiFetch(`/alternatives?videoId=${encodeURIComponent(videoId)}&mode=${mode}`);
  await cacheSetMany({ [key]: data });
  return data;
}

// Пакетна класифікація карток: POST /classify { videoIds: [...] }
async function handleClassify({ videoIds }) {
  const results = {};
  const missing = [];

  for (const id of videoIds) {
    const cached = await cacheGet(`cls:${id}`, MF_CONFIG.CLASSIFY_CACHE_TTL_MS);
    if (cached) results[id] = cached;
    else missing.push(id);
  }

  for (let i = 0; i < missing.length; i += MF_CONFIG.CLASSIFY_BATCH_SIZE) {
    const chunk = missing.slice(i, i + MF_CONFIG.CLASSIFY_BATCH_SIZE);
    const resp = MF_CONFIG.USE_MOCK
      ? MF_MOCK.classify(chunk)
      : await apiFetch("/classify", { method: "POST", body: JSON.stringify({ videoIds: chunk }) });

    const toCache = {};
    for (const id of chunk) {
      const r = (resp.results && resp.results[id]) || { lang: "unknown", confidence: 0, source: "missing" };
      results[id] = r;
      toCache[`cls:${id}`] = r;
    }
    await cacheSetMany(toCache);
  }
  return { results };
}

// «Хочу українське»: POST /wish
async function handleWish({ videoId, videoTitle, level, userText }) {
  const settings = await getSettings();

  // Ліміт 20 запитів на день з одного встановлення (бекенд теж перевіряє).
  const { wishCount = {} } = await chrome.storage.local.get("wishCount");
  const used = wishCount[today()] || 0;
  if (used >= MF_CONFIG.WISH_DAILY_LIMIT) return { ok: false, reason: "limit" };

  const body = {
    installId: await getInstallId(),
    videoId,
    videoTitle,
    level,
    // У дитячому режимі текстового поля немає і текст не надсилаємо.
    userText: settings.kidsMode ? null : (userText || "").slice(0, MF_CONFIG.WISH_TEXT_MAX) || null,
  };

  if (!MF_CONFIG.USE_MOCK) {
    await apiFetch("/wish", { method: "POST", body: JSON.stringify(body) });
  } else {
    console.log("[Мовний фід] mock /wish", body);
  }
  await chrome.storage.local.set({ wishCount: { [today()]: used + 1 } });
  return { ok: true };
}

// Анонімні події: POST /events { installId, type, level, ts }
// Назв відео в подіях немає. У дитячому режимі подій не збираємо.
async function handleEvent({ eventType, level }) {
  const settings = await getSettings();
  if (settings.kidsMode) return { ok: true, skipped: true };

  const body = {
    installId: await getInstallId(),
    type: eventType, // panel_shown | alt_clicked | wish_sent
    level: level ?? null,
    ts: new Date().toISOString(),
  };
  if (!MF_CONFIG.USE_MOCK) {
    await apiFetch("/events", { method: "POST", body: JSON.stringify(body) });
  } else {
    console.log("[Мовний фід] mock /events", body);
  }
  return { ok: true };
}

// Дія з відео для таблиці video_actions: POST /actions { user, action, videoUrl, timestamp }
async function handleVideoAction({ action, videoUrl, videoId }) {
  if (action !== "suggested" && action !== "hid") {
    throw new Error(`Unsupported action: ${action}`);
  }
  const url = videoUrl || (videoId ? `https://www.youtube.com/watch?v=${encodeURIComponent(videoId)}` : null);
  if (!url) return { ok: false, error: "missing videoUrl" };

  const body = {
    user: await getInstallId(),
    action,
    videoUrl: url,
    timestamp: new Date().toISOString(),
  };
  return apiFetch("/actions", { method: "POST", body: JSON.stringify(body) });
}

// Лічильник прихованих карток у дитячому режимі (локально, по днях).
async function handleKidsHidden({ count }) {
  const { kidsHidden = {} } = await chrome.storage.local.get("kidsHidden");
  const d = today();
  kidsHidden[d] = (kidsHidden[d] || 0) + count;
  await chrome.storage.local.set({ kidsHidden: pruneDays(kidsHidden, 30) });
  return { ok: true };
}

// «Мовний баланс»: скільки відео якою мовою переглянуто (локально).
async function handleWatched({ lang }) {
  const { balance = {} } = await chrome.storage.local.get("balance");
  const d = today();
  balance[d] = balance[d] || { uk: 0, ru: 0, en: 0, other: 0 };
  const key = ["uk", "ru", "en"].includes(lang) ? lang : "other";
  balance[d][key] += 1;
  await chrome.storage.local.set({ balance: pruneDays(balance, 30) });
  return { ok: true };
}

function pruneDays(byDay, keepDays) {
  const cutoff = new Date(Date.now() - keepDays * 86400000).toISOString().slice(0, 10);
  const out = {};
  for (const [d, v] of Object.entries(byDay)) if (d >= cutoff) out[d] = v;
  return out;
}

// ---------- Маршрутизатор повідомлень ----------

const HANDLERS = {
  alternatives: handleAlternatives,
  classify: handleClassify,
  wish: handleWish,
  event: handleEvent,
  videoAction: handleVideoAction,
  kidsHidden: handleKidsHidden,
  watched: handleWatched,
};

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  const handler = HANDLERS[msg && msg.type];
  if (!handler) return false;
  handler(msg)
    .then((data) => sendResponse({ ok: true, data }))
    .catch((err) => {
      console.warn("[Мовний фід]", msg.type, err);
      sendResponse({ ok: false, error: String(err && err.message ? err.message : err) });
    });
  return true; // відповідь буде асинхронно
});

// ---------- Встановлення і щоденні задачі ----------

chrome.runtime.onInstalled.addListener(async () => {
  await getInstallId();
  const current = await chrome.storage.local.get(Object.keys(MF_DEFAULTS));
  const missing = {};
  for (const [k, v] of Object.entries(MF_DEFAULTS)) if (!(k in current)) missing[k] = v;
  await chrome.storage.local.set(missing);
  chrome.alarms.create("daily", { periodInMinutes: 60 * 24, delayInMinutes: 1 });
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== "daily") return;
  await cleanupCache();
  await sendKidsDailySums();
});

// Видаляємо прострочений кеш (дані YouTube не зберігаємо довше, ніж треба).
async function cleanupCache() {
  const all = await chrome.storage.local.get(null);
  const toRemove = [];
  for (const [k, v] of Object.entries(all)) {
    if (k.startsWith("alt:") && Date.now() - v.ts > MF_CONFIG.ALT_CACHE_TTL_MS) toRemove.push(k);
    if (k.startsWith("cls:") && Date.now() - v.ts > MF_CONFIG.CLASSIFY_CACHE_TTL_MS) toRemove.push(k);
  }
  if (toRemove.length) await chrome.storage.local.remove(toRemove);
}

// Денна сума прихованих карток: лише число, без id відео, лише з дозволу батьків.
async function sendKidsDailySums() {
  const settings = await getSettings();
  if (!settings.shareKidsStats || MF_CONFIG.USE_MOCK) return;

  const { kidsHidden = {}, kidsSentDays = [] } = await chrome.storage.local.get(["kidsHidden", "kidsSentDays"]);
  const d = today();
  const installId = await getInstallId();
  const sent = new Set(kidsSentDays);

  for (const [day, count] of Object.entries(kidsHidden)) {
    if (day >= d || sent.has(day)) continue; // лише завершені дні
    try {
      await apiFetch("/events", {
        method: "POST",
        body: JSON.stringify({ installId, type: "kids_hidden_daily", count, date: day, ts: new Date().toISOString() }),
      });
      sent.add(day);
    } catch (e) {
      console.warn("[Мовний фід] kids daily sum", e);
    }
  }
  await chrome.storage.local.set({ kidsSentDays: [...sent].slice(-60) });
}
